"""`drugref interactions` must say when a curated grade has fallen out of its list (#121).

THE DEFECT. `curated_ddi_pair`'s moiety-grain half reaches a curated grade only through an
INNER JOIN to `ddi_candidate_pair`, a delete-and-rebuild projection over
`class_contraindication`. A re-ingest that drops the candidate row leaves the grade alive
in `curated_interaction` and gone from the read, so the command printed `no curated
grade` for a pair drugref had judged contraindicated. `curated_target_unresolved`
already detected that state, but only `drugref status` read it: the operator was told,
the person being under-warned was not.

ONLY THE MOIETY GRAIN. The class-grain half of the view expands class membership
directly and never joins `class_pair_contraindication`, so a class-grain orphan still
renders; and `curated_condition` rulings are not interaction grades. Hence the filter
below keeps `curated_interaction` rows only. DB-gated except the pure-filter tests.
"""
import uuid

from drugref import curation
from tests.test_cli_interactions import _a_graded_pair, _args
from drugref import cli_interactions


def _orphan(target_table="curated_interaction", subject_moiety=None, subject_class=None):
    """A hand-built record: the filter is pure, so its tests need no database."""
    return curation.UnresolvedTarget(
        target_table=target_table, subject_moiety=subject_moiety,
        object_uuid=uuid.uuid4(), relationship="CI_MoA", reviewed_by="a curator",
        reviewed_against="2026.07.06", subject_class=subject_class)


# ============================================================================
# 1. the pure filter
# ============================================================================


def test_the_filter_keeps_moiety_grain_orphans_of_the_asked_subjects_only():
    """Subject match, moiety grain only. A condition ruling about the same drug is not
    an interaction grade, and a class-grain orphan still renders through membership."""
    x, y, z = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mine = _orphan(subject_moiety=x)
    theirs = _orphan(subject_moiety=y)
    other = _orphan(subject_moiety=z)
    condition = _orphan(target_table="curated_condition", subject_moiety=x)
    class_grain = _orphan(target_table="curated_class_interaction",
                          subject_class=uuid.uuid4())

    orphans = [mine, theirs, other, condition, class_grain]
    assert curation.orphaned_interaction_grades(orphans, {x}) == [mine]
    assert curation.orphaned_interaction_grades(orphans, {x, y}) == [mine, theirs]
    assert curation.orphaned_interaction_grades(orphans, set()) == []


# ============================================================================
# 2. the command banners it
# ============================================================================


def _orphaned_pair(conn, ingest_run_id):
    """A graded pair whose candidate a re-ingest then dropped: the #121 sequence."""
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000012100", object_code="N0000012101",
        severity="contraindicated", subject_unii="TESTUNI121", partner_unii="TESTUNI122",
        subject_name="the orphaned subject", partner_name="the orphaned partner")
    conn.execute("DELETE FROM drugref.class_contraindication")
    return subject, partner


def test_the_one_drug_form_says_its_list_is_incomplete(conn, ingest_run_id, capsys):
    """THE HEADLINE: `no curated grade` must not stand alone for a judged drug."""
    subject, _ = _orphaned_pair(conn, ingest_run_id)

    assert cli_interactions._handle_interactions(conn, _args(subject)) == 0
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out
    assert "curated_target_unresolved" in out
    assert "CI_MoA" in out


def test_the_pair_form_does_not_claim_an_absent_rule(conn, ingest_run_id, capsys):
    """The pair form's closing sentence ("an ABSENT RULE") is exactly the reassurance
    an orphan makes false, so it must give way to the banner. Asked in BOTH argument
    orders: the orphan's subject is the second drug in one of them."""
    subject, partner = _orphaned_pair(conn, ingest_run_id)

    for a, b in ((subject, partner), (partner, subject)):
        assert cli_interactions._handle_interactions(conn, _args(a, b)) == 0
        out = capsys.readouterr().out
        assert "INCOMPLETE" in out
        assert "ABSENT RULE" not in out


def test_no_banner_when_nothing_is_orphaned(conn, ingest_run_id, capsys):
    """The banner is a signal only while it is rare: a healthy read prints none."""
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000012102", object_code="N0000012103",
        severity="major", subject_unii="TESTUNI123", partner_unii="TESTUNI124",
        subject_name="a healthy subject", partner_name="a healthy partner")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    assert "INCOMPLETE" not in capsys.readouterr().out
