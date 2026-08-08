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


def test_a_rekeyed_candidate_orphans_the_judgement(conn, a_graded_rule):
    """The REALISTIC orphan, and the one the whole view is named for: upstream
    re-projects the same (moiety, class) pair under a DIFFERENT relationship.

    The two tests above delete the candidate table wholesale, which exercises only the
    all-or-nothing case -- and against that case the view's `cc.relationship =
    c.relationship` predicate is dead weight, because with the table empty the NOT
    EXISTS holds whatever it compares. Mutating that predicate to `true` survived the
    whole suite. A partial mismatch is what a MED-RT re-key actually looks like
    (`unresolved_targets`' own docstring says "a candidate upstream has re-keyed"), and
    it is the only shape that can tell the predicate apart from a constant.
    """
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    # The rebuild: same subject, same class, same source -- only the predicate moved.
    conn.execute(
        "UPDATE drugref.class_contraindication SET relationship = 'CI_PE'")

    orphans = curation.unresolved_targets(conn)
    assert len(orphans) == 1, (
        "the candidate row still exists but no longer carries CI_MoA, so the judgement "
        "resolves to nothing -- the view must compare the relationship, not merely the "
        "(subject, object) pair")
    assert orphans[0].relationship == "CI_MoA"


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
    # ALL SIX FIELDS on THIS arm too, for the reason given on the interaction test
    # above. The two arms of a UNION ALL are two independent column lists, and pinning
    # only one leaves the other free to transpose: swapping reviewed_by with
    # reviewed_against in the second arm alone survived the whole suite while the first
    # arm's assertion stayed green.
    assert curation.unresolved_targets(conn) == [
        curation.UnresolvedTarget(
            target_table="curated_condition",
            subject_moiety=a_contradicted_pair["moiety"],
            object_uuid=a_contradicted_pair["condition"],
            relationship=None,
            reviewed_by="test",
            reviewed_against="2026.07.06")], (
        "a condition ruling is keyed on the PAIR and carries no relationship")


def test_a_superseded_judgement_is_not_an_orphan(conn, a_graded_rule):
    """Only LIVE rows. A corrected judgement's superseded predecessor still names the
    old candidate, and reporting it would make every correction look like breakage.

    THE CANDIDATE MUST BE DELETED FOR THIS TEST TO MEAN ANYTHING. An earlier version
    stopped after the correction and asserted the result was empty -- but with the
    candidate still projected BOTH rows resolve, so the empty result was
    over-determined and the assertion held whether or not the view filtered on
    `superseded_by`. Deleting `WHERE c.superseded_by IS NULL` from both arms of db/029's
    view survived the entire suite. Orphaning the candidate is what forces the
    distinction: two rows now name a candidate that is gone, and exactly one of them is
    live.
    """
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
    conn.execute("DELETE FROM drugref.class_contraindication")

    orphans = curation.unresolved_targets(conn)
    assert len(orphans) == 1, (
        "both rows name the vanished candidate, but only the LIVE one is an orphan -- "
        "reporting the superseded predecessor as well would make every correction look "
        "like breakage")
    assert orphans[0].reviewed_against == "2026.08.01", (
        "the row reported must be the CORRECTION, not the predecessor it replaced")


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
    at nothing.

    THE BANNER IS ASSERTED VERBATIM, and that is the whole difference between this test
    and a count check. "Loudly" is the word in the name; deleting the `**` marker left
    the count, the table and the curator all still printed, so an earlier version of
    this test passed with the loudness removed. Every field of the detail line is
    asserted for the same reason -- collapsing the format string to just the table and
    the curator also survived.
    """
    from drugref import cli

    row = ("curated_interaction", "11111111-1111-5111-8111-111111111111",
           "22222222-2222-5222-8222-222222222222", "CI_MoA", "ahoward", "2026.07.06")
    assert cli._handle_status(_Conn([row]), None) == 0
    out = capsys.readouterr().out
    assert "unresolved curated targets: 1" in out
    assert "** a rebuild left curator judgement pointing at nothing **" in out
    # Whitespace-normalised so the column padding stays free to change, while every
    # FIELD stays pinned -- dropping object_uuid, relationship or reviewed_against from
    # the format string is the mutation this has to kill.
    assert ("curated_interaction 11111111-1111-5111-8111-111111111111 -> "
            "22222222-2222-5222-8222-222222222222 [CI_MoA] reviewed by ahoward "
            "against 2026.07.06") in " ".join(out.split())


def test_status_renders_a_condition_orphan_without_a_relationship(capsys):
    """The `relationship is None` arm of the detail line, which no other test reaches.

    A condition ruling is keyed on the pair and carries no predicate, so this is the
    branch every `curated_condition` orphan will take at runtime -- and until this test
    it had never executed. The bracket must be absent entirely rather than rendered
    empty or as the string "None".
    """
    from drugref import cli

    row = ("curated_condition", "11111111-1111-5111-8111-111111111111",
           "33333333-3333-5333-8333-333333333333", None, "ahoward", "2026.07.06")
    assert cli._handle_status(_Conn([row]), None) == 0
    out = capsys.readouterr().out
    assert ("curated_condition 11111111-1111-5111-8111-111111111111 -> "
            "33333333-3333-5333-8333-333333333333 reviewed by ahoward "
            "against 2026.07.06") in " ".join(out.split())
    assert "None" not in out and "[]" not in out


@pytest.mark.parametrize("module", ["cli", "cli_policy"])
@pytest.mark.parametrize("table", ["curated_interaction", "curated_condition"])
def test_the_cli_embeds_no_sql_against_a_curated_table(module, table):
    """cli.py's own discipline, pinned. `_handle_status` is allowed to embed SELECTs
    against operational views, and that exception must not creep to cover the curated
    overlay -- a Python-embedded reader of an append-only curated table is invisible to
    the pg_rewrite sweep that finds every other reader.

    MATCHED OVER PARSED STRING CONSTANTS, NOT OVER SOURCE TEXT, and the difference is
    not academic. The earlier version asserted `f"FROM drugref.{table}" not in body`
    against raw source with comment lines stripped, which was blind to a SELECT split
    across two lines, to INSERT and UPDATE, to a JOIN, and to a double space after
    FROM. The line-split hole is the one that mattered: THIS ROUND'S OWN 88-COLUMN LINT
    GATE forces long SQL to wrap, and this branch splits SQL literals in eleven places,
    so the guard was being weakened by the same commit that relies on it. `ast.parse`
    folds implicit concatenation into a single Constant, so the wrapped form and the
    one-line form are the same string here, and matching the bare table name catches
    reads and WRITES alike -- a Python-embedded writer to an append-only table being
    strictly worse than a reader.

    `cli_policy.py` is scanned too. The rule the module docstring states is ABOUT
    cli_policy ("THE POLICY COMMANDS HOLD NO SQL"), and nothing checked it.
    """
    import ast
    import importlib
    import pathlib

    source = pathlib.Path(
        importlib.import_module(f"drugref.{module}").__file__).read_text()
    constants = [node.value for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    offenders = [s for s in constants if f"drugref.{table}".lower() in s.lower()]
    assert offenders == [], (
        f"{module}.py embeds SQL naming the curated table {table}: {offenders}. "
        f"Curated reads and writes belong in curation.py, where pg_rewrite's sweep "
        f"over views can still see them.")
