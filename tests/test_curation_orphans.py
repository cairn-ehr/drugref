# tests/test_curation_orphans.py
"""The orphan detector's CONSUMER (issue 76).

`db/029` shipped `drugref.curated_target_unresolved` -- live curated rows whose
candidate is no longer projected after a per-source rebuild -- and NOTHING READ IT.

That is the exact failure `interactions.unresolved_expansion_policy`'s own docstring
records this project having already made once: db/010 shipped
`expansion_policy_unresolved` with no consumer at all, "which is precisely the failure
mode it was written to catch." A detector nobody calls is not a detector. An orphaned
judgement would sit in the database being reported to no one, forever, and the whole
reason the view exists -- an operator must be TOLD when a rebuild leaves curator
judgement pointing at nothing -- goes unmet.

WHY THE READ LIVES IN curation.py AND NOT IN cli.py. cli.py's module docstring makes
the rule: a handler must not embed SQL against curated, append-only tables, because
`test_only_the_current_view_reads_the_policy_table_directly` finds readers through
pg_rewrite, which sees views and matviews and CANNOT see a query embedded in Python.
`_handle_status` is a stated exception for `loaded_release` and `ingest_run_incomplete`
-- operational views, not curated data -- and that exception does NOT stretch to cover
the curated overlay. So the read is a function in the module that owns the curated
write path, exactly as `unresolved_expansion_policy` sits in `interactions.py`, and the
handler calls it.
"""
import pytest

from drugref import curation


def test_no_orphans_when_the_candidate_is_still_projected(conn, a_graded_rule):
    """The control, and the normal state: expected EMPTY on a healthy database."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    assert curation.unresolved_targets(conn) == []


def test_an_orphaned_interaction_judgement_is_returned(conn, a_graded_rule):
    """A rebuild that drops the candidate must surface the judgement pointing at it.

    `DELETE FROM class_contraindication` is exactly what a per-source re-ingest does
    before rewriting the projection, so this is the ordinary sequence rather than a
    contrived one.
    """
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    conn.execute("DELETE FROM drugref.class_contraindication")

    # ALL SIX FIELDS, because UnresolvedTarget is built positionally from the SELECT
    # (`UnresolvedTarget(*row)`) and the stub-driven CLI tests below cannot see a
    # column-order mistake -- they supply a tuple already in the assumed order. Swapping
    # object_uuid with relationship, or reviewed_by with reviewed_against, would leave
    # every other assertion in this file passing.
    assert curation.unresolved_targets(conn) == [
        curation.UnresolvedTarget(
            target_table="curated_interaction",
            subject_moiety=a_graded_rule["subject"],
            object_uuid=a_graded_rule["class"],
            relationship="CI_MoA",
            reviewed_by="test",
            reviewed_against="2026.07.06")]


def test_an_orphaned_condition_ruling_is_returned(conn, a_contradicted_pair):
    """The UNION ALL's second arm. Both candidate tables must be gone before a
    condition ruling counts as orphaned, because either one alone keeps it resolved."""
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="test", reviewed_against="2026.07.06")
    conn.execute("DELETE FROM drugref.moiety_condition_contraindication")
    assert curation.unresolved_targets(conn) == [], (
        "one surviving candidate table still resolves the ruling")

    conn.execute("DELETE FROM drugref.moiety_condition_indication")
    orphans = curation.unresolved_targets(conn)
    assert len(orphans) == 1
    assert orphans[0].target_table == "curated_condition"
    assert orphans[0].relationship is None, (
        "a condition ruling is keyed on the PAIR and carries no relationship")


def test_a_superseded_judgement_is_not_an_orphan(conn, a_graded_rule):
    """Only LIVE rows. A corrected judgement's superseded predecessor still names the
    old candidate, and reporting it would make every correction look like breakage."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="minor", evidence_grade="probable", reviewed_by="test",
        reviewed_against="2026.08.01")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert curation.unresolved_targets(conn) == [], (
        "the candidate is still projected, so neither row is an orphan")


# ---- the CLI consumer -------------------------------------------------------


class _Conn:
    """A stub connection returning canned rows per query, for the DB-free CLI tests.

    tests/test_cli.py drives `_handle_status` the same way; the handler is a dispatch
    thin enough that a stub is a truer test of its OUTPUT than a live database would be.
    """

    def __init__(self, orphans=()):
        self._orphans = list(orphans)

    def execute(self, sql, params=None):
        self._last = sql
        return self

    def fetchall(self):
        if "curated_target_unresolved" in self._last:
            return self._orphans
        return []


def test_status_reports_no_orphans_on_a_healthy_database(capsys):
    """`none` rather than a bare header, matching the two blocks already there: a
    header with nothing under it reads as output that got cut off, not as an answer."""
    from drugref import cli

    assert cli._handle_status(_Conn(), None) == 0
    assert "unresolved curated targets: none" in capsys.readouterr().out


def test_status_reports_an_orphan_loudly(capsys):
    """THE POINT OF THE WHOLE ISSUE. An orphan must be visible in the one command an
    operator runs to ask "is this database healthy?", and must not be reported in the
    same neutral voice as a loaded release -- it means a curator's judgement now points
    at nothing."""
    from drugref import cli

    row = ("curated_interaction", "11111111-1111-5111-8111-111111111111",
           "22222222-2222-5222-8222-222222222222", "CI_MoA", "ahoward", "2026.07.06")
    assert cli._handle_status(_Conn([row]), None) == 0
    out = capsys.readouterr().out
    assert "unresolved curated targets: 1" in out
    assert "curated_interaction" in out
    assert "ahoward" in out


@pytest.mark.parametrize("table", ["curated_interaction", "curated_condition"])
def test_the_cli_embeds_no_sql_against_a_curated_table(table):
    """cli.py's own discipline, pinned. `_handle_status` is allowed to embed SELECTs
    against operational views, and that exception must not creep to cover the curated
    overlay -- a Python-embedded reader of an append-only curated table is invisible to
    the pg_rewrite sweep that finds every other reader."""
    import pathlib

    import drugref.cli

    source = pathlib.Path(drugref.cli.__file__).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#"))
    body = code.split('"""', 2)[-1]          # drop the module docstring, which names them
    assert f"FROM drugref.{table}" not in body
    assert f"from drugref.{table}" not in body
