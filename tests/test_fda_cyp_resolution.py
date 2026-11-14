# tests/test_fda_cyp_resolution.py
"""FDA-CYP name resolution: the ambiguous disposition (#132) and the floor (#135).

Kept out of test_fda_cyp_run.py, which is already far past the ~500-line
guideline. The fixtures are imported from there BY NAME, which is what makes
pytest run its two autouse ones (`_registry`, `_clean`) for this module too.
"""
import argparse

import psycopg
import pytest

from drugref import classes, cli_fda_cyp, ids
from drugref.ingest import fda_cyp, fda_cyp_disposition, fda_cyp_run
from tests.test_fda_cyp_run import (  # noqa: F401  (fixtures are used by name)
    _KEYED_GAP_VIEW, FIXTURE, _clean, _registry, _wider_registry)

RELEASE = "2026-05-29T14:00"


# ---------------------------------------------------------------------------
# #132: two or more claimants is its own disposition, not "no match".
# ---------------------------------------------------------------------------

def test_classify_tells_no_match_from_several_matches():
    """Zero candidates and two candidates used to collapse onto one `None`, so
    both were filed as unresolved_substance, whose text says no name matches.
    For the ambiguous case that sentence is false."""
    a, b = ids.mint_moiety_uuid("UA"), ids.mint_moiety_uuid("UB")
    assert fda_cyp_disposition._classify("x", [], None) == ("unresolved_substance", None)
    assert fda_cyp_disposition._classify("x", [a, b], None) == ("ambiguous_substance", None)
    assert fda_cyp_disposition._classify("x", [a, b], "2") == ("ambiguous_substance", None)
    assert fda_cyp_disposition._classify("x", [a], None) == ("member", a)
    # The two FDA-asserted subject dispositions still win over ambiguity.
    assert fda_cyp_disposition._classify("curcumin", [a, b], None) == ("non_drug_entity", None)
    assert fda_cyp_disposition._classify("a and b", [a, b], None) == ("combination_regimen", None)


@pytest.mark.usefixtures("_wider_registry")
def test_an_ambiguous_name_is_stored_with_its_claimants_and_asked_honestly(conn):
    """The registry holds two moieties named alprazolam (case-folded). The row
    stores both claimants, and the question says how many share the name --
    never "no moiety's name matches it"."""
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    rows = conn.execute(
        "SELECT disposition, resolved_moiety_uuid, candidate_moiety_uuids "
        "FROM drugref.fda_cyp_assertion WHERE substance = 'alprazolam'").fetchall()
    expected = sorted([ids.mint_moiety_uuid("TESTUNII_ALPRAZOLAM_A"),
                       ids.mint_moiety_uuid("TESTUNII_ALPRAZOLAM_B")])
    assert rows and all(d == "ambiguous_substance" and m is None
                        and sorted(c) == expected for d, m, c in rows)
    assert summary.ambiguous_substances == len(rows)

    text = conn.execute(
        "SELECT q.question_text FROM " + _KEYED_GAP_VIEW + " "
        "JOIN drugref.open_question q ON q.gap_kind = 'fda_cyp_unadjudicated' "
        " AND q.gap_key = g.gap_key WHERE g.substance = 'alprazolam'").fetchone()[0]
    assert "2 drugref moieties" in text
    assert "No moiety" not in text
    assert all(str(m) in text for m in expected)


def test_an_unresolved_name_says_both_indexes_were_searched(conn):
    """Since #226 resolution reads UNII official names too, so the old text
    ("No moiety's display name matches it") understated what was searched."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    text = conn.execute(
        "SELECT q.question_text FROM " + _KEYED_GAP_VIEW + " "
        "JOIN drugref.open_question q ON q.gap_kind = 'fda_cyp_unadjudicated' "
        " AND q.gap_key = g.gap_key "
        "WHERE g.disposition = 'unresolved_substance' LIMIT 1").fetchone()[0]
    assert "display name or UNII official name" in text


# ---------------------------------------------------------------------------
# #135: a re-ingest that suddenly resolves far less is refused.
# ---------------------------------------------------------------------------

def _resolved_count(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE resolved_moiety_uuid IS NOT NULL").fetchone()[0]


def _runs(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM drugref.ingest_run WHERE source = 'FDA-CYP'").fetchone()[0]


@pytest.mark.usefixtures("_wider_registry")
def test_a_collapsed_resolution_index_is_refused_before_any_run_row(conn, monkeypatch):
    """The issue's scenario: the name index comes back empty (fresh database,
    a skipped seed, a changed display-name convention). Every tuple would land
    unresolved, the old edges would be cleared, and the run would report
    success. It must refuse instead, leaving the stored projection untouched
    and no run row to explain."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    before = _resolved_count(conn)
    assert before > 0

    monkeypatch.setattr(classes, "moieties_by_display_name", lambda conn: {})
    with pytest.raises(fda_cyp_run.FdaCypResolutionError,
                       match="--allow-resolution-drop"):
        fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    assert _resolved_count(conn) == before
    assert _runs(conn) == 1


@pytest.mark.usefixtures("_wider_registry")
def test_the_resolution_drop_can_be_authorised(conn, monkeypatch):
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    monkeypatch.setattr(classes, "moieties_by_display_name", lambda conn: {})
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE,
                                         allow_resolution_drop=True)
    assert summary.memberships_written == 0
    assert _resolved_count(conn) == 0


def test_a_first_ingest_that_resolves_nothing_is_never_blocked(conn, monkeypatch):
    """Nothing is stored, so nothing is destroyed: the floor has no baseline."""
    monkeypatch.setattr(classes, "moieties_by_display_name", lambda conn: {})
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    assert summary.assertions_written > 0


def test_the_cli_threads_the_override_flag(tmp_path, monkeypatch):
    parser = argparse.ArgumentParser()
    cli_fda_cyp.add_parser(parser.add_subparsers())
    args = parser.parse_args(["fda-cyp", "--page", str(tmp_path / "p.html"),
                              "--allow-resolution-drop"])
    seen = {}

    def _stub(conn, *, page_path, allow_shrink, allow_resolution_drop):
        seen["flag"] = allow_resolution_drop
        return "stub"

    monkeypatch.setattr(cli_fda_cyp.fda_cyp_run, "ingest_fda_cyp", _stub)
    assert cli_fda_cyp.handle_fda_cyp(object(), args) == 0
    assert seen == {"flag": True}


# ---------------------------------------------------------------------------
# PR #242 review: the floor's arithmetic, and refusals that leave no run row.
# ---------------------------------------------------------------------------

def _resolves(conn, t) -> bool:
    """Would this tuple resolve against the registry as it stands now?"""
    fold = fda_cyp_disposition._fold_by_lower(classes.moieties_by_display_name(conn),
                                               classes.moieties_by_official_name(conn))
    candidates = fold.get(t.substance.lower(), [])
    return fda_cyp_disposition._classify(t.substance, candidates,
                                         t.footnote_markers)[1] is not None


@pytest.mark.usefixtures("_wider_registry")
def test_the_floor_compares_shares_so_an_authorised_shrink_passes_it(conn, monkeypatch):
    """A genuine FDA shrink, authorised with --allow-shrink, keeps about the
    same resolved SHARE while the resolved COUNT falls by two thirds. A floor
    that compared counts would refuse it, and operators would learn to pass
    both flags together -- which defeats the floor."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    tuples = fda_cyp.parse_table(FIXTURE.read_text(encoding="utf-8"))
    resolved = [t for t in tuples if _resolves(conn, t)]
    unresolved = [t for t in tuples if not _resolves(conn, t)]
    assert len(resolved) >= 3 and len(unresolved) >= 3
    kept = resolved[:len(resolved) // 3] + unresolved[:len(unresolved) // 3]
    assert len(kept) < len(tuples) * fda_cyp_run.MIN_RETAINED_FRACTION

    monkeypatch.setattr(fda_cyp, "parse_table", lambda page: kept)
    # The shrink flag alone must NOT bypass the floor, nor the floor the shrink
    # guard: this passes only because the share held.
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                         upstream_release=RELEASE, allow_shrink=True)
    assert summary.assertions_written == len(kept)


@pytest.mark.usefixtures("_wider_registry")
def test_a_partial_resolution_drop_above_the_floor_is_not_refused(conn, monkeypatch):
    """One renamed moiety is ordinary drift, not a collapsed index."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    before = _resolved_count(conn)
    real = classes.moieties_by_display_name
    monkeypatch.setattr(classes, "moieties_by_display_name",
                        lambda conn: {k: v for k, v in real(conn).items()
                                      if k.lower() != "abiraterone"})
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    assert 0 < _resolved_count(conn) < before


@pytest.mark.usefixtures("_wider_registry")
def test_claimants_are_stored_only_on_ambiguous_rows(conn):
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    assert conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE disposition <> 'ambiguous_substance' "
        "  AND candidate_moiety_uuids IS NOT NULL").fetchone()[0] == 0


def test_the_database_refuses_claimants_that_break_the_ambiguity_rule(conn):
    """db/076: the rule held only in Python; the question text papered over a
    break with 'Several ... (unrecorded)'. Now the row itself is refused."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    one = [ids.mint_moiety_uuid("UA")]
    two = one + [ids.mint_moiety_uuid("UB")]
    for disposition, claimants in [("ambiguous_substance", None),
                                   ("ambiguous_substance", one),
                                   ("unresolved_substance", two)]:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE drugref.fda_cyp_assertion SET disposition = %s, "
                "candidate_moiety_uuids = %s, resolved_moiety_uuid = NULL "
                "WHERE row_ordinal = (SELECT min(row_ordinal) "
                "                     FROM drugref.fda_cyp_assertion)",
                (disposition, claimants))
        conn.rollback()


def test_a_failed_index_read_rolls_back_and_names_the_feed(conn, monkeypatch, caplog):
    """The fold reads the registry before the run opens, outside the writer's
    try. A failure there must still roll back -- a programmatic caller must not
    be left holding an aborted transaction -- and log which feed failed."""
    monkeypatch.setattr(classes, "moieties_by_official_name",
                        lambda conn: conn.execute("SELECT * FROM drugref.no_such_table"))
    with pytest.raises(psycopg.errors.UndefinedTable):
        fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    assert conn.execute("SELECT 1").fetchone() == (1,)
    assert "FDA-CYP ingest failed" in caplog.text
    assert _runs(conn) == 0


def test_a_disposition_refusal_leaves_no_run_row(conn, monkeypatch):
    """Every refusal decided from the parse and the index fires before
    open_run, as the module docstring promises."""
    monkeypatch.setattr(fda_cyp_run, "_classify",
                        lambda substance, candidates, markers: ("testonly_seventh", None))
    with pytest.raises(fda_cyp_run.FdaCypDispositionError):
        fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    assert _runs(conn) == 0


def test_a_member_without_a_moiety_is_refused_before_any_run_row(conn, monkeypatch):
    monkeypatch.setattr(fda_cyp_run, "_classify",
                        lambda substance, candidates, markers: ("member", None))
    with pytest.raises(fda_cyp_run.FdaCypDispositionError, match="ordering"):
        fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release=RELEASE)
    assert _runs(conn) == 0
