# tests/test_curated_gap_views.py
"""The curated overlay's worklist (db/029 sections 4-5, slice 5c.1).

TWO PREDICATES THAT LOOK THE SAME AND ARE NOT. A gap view asks "is there a LIVE row?"
-- because every ruling, including `spurious` and `applies = false`, means a curator
LOOKED and the question is answered. A read view asks "is there a live ASSERTING row?"
-- because a retired ruling must reach no consumer. Collapse the two and whichever end
you collapse toward breaks: db/027 met this as its `_current`-versus-`_live`
distinction, where folding `withdrawn` into `allow` silently retired a question nobody
had answered.
"""
import pytest

from drugref import curation, ids, interactions, questions
from tests.test_curated_overlay import _a_class, _a_condition
from tests.test_curated_read_path import a_contradicted_pair, a_graded_rule  # noqa: F401


def test_a_contradicted_pair_is_queued(conn, a_contradicted_pair):  # noqa: F811
    """Issue 51's 168 pairs, in miniature: a pair asserted as BOTH an indication and a
    contraindication, with nobody having ruled on it."""
    rows = conn.execute(
        "SELECT subject_moiety, object_condition FROM "
        "drugref.gap_uncurated_condition_contradiction").fetchall()
    assert rows == [(a_contradicted_pair["moiety"], a_contradicted_pair["condition"])]


def test_a_pair_with_only_one_side_is_not_queued(conn, a_moiety, ingest_run_id):
    """The queue is the CONTRADICTION, not uncurated contraindications at large --
    13,463 of those exist and a queue nobody can finish is the stale generated document
    these views replace."""
    condition = _a_condition(conn, ingest_run_id)
    interactions.add_condition_contraindication(
        conn, a_moiety, condition, "CI_with", "MED-RT", ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_condition_contradiction"
    ).fetchone() == (0,)


@pytest.mark.parametrize("ruling", ["context_dependent", "spurious"])
def test_any_ruling_retires_the_pair_from_the_queue(conn, a_contradicted_pair, ruling):  # noqa: F811
    """EVERY ruling means a curator looked, including the one that says the upstream is
    wrong. A `spurious` row that stayed on the worklist would be asked about every
    release forever -- the exact nagging failure db/027's `withdrawn` exists to stop."""
    extra = {} if ruling == "spurious" else {"severity": "major",
                                             "evidence_grade": "established"}
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"], ruling,
        reviewed_by="test", reviewed_against="2026.07.06", **extra)
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_condition_contradiction"
    ).fetchone() == (0,)


def test_an_uncurated_rule_is_queued_with_the_pairs_at_stake(conn, a_graded_rule):  # noqa: F811
    """RANKED BY MEMBERS ACTUALLY AT STAKE, not by tree bushiness. Issue #36 measured
    the cost of the other metric: gap_unreviewed_expansion_root spent a curator's
    explicit decision on a root whose expansion was a provable no-op."""
    assert conn.execute(
        "SELECT subject_moiety, object_class, pair_count FROM "
        "drugref.gap_uncurated_interaction_rule").fetchall() == [
            (a_graded_rule["subject"], a_graded_rule["class"], 1)]


def test_a_rule_pairing_with_nobody_is_not_queued(conn, a_moiety, ingest_run_id):
    """Grading a rule that reaches no pair changes nothing, so asking about it spends a
    curator's attention for a provable no-op -- #36's finding, applied before it can be
    repeated. gap_unpopulated_contraindication already owns the "why does this class
    have no members" question."""
    klass = _a_class(conn, ingest_run_id, code="N0000000002", name="Empty MoA [MoA]")
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (a_moiety, klass, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_interaction_rule "
        "WHERE object_class = %s", (klass,)).fetchone() == (0,)


def test_a_retired_rule_leaves_the_queue(conn, a_graded_rule):  # noqa: F811
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", False,
        reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_interaction_rule").fetchone() == (0,)


def test_both_kinds_mint_questions(conn, a_contradicted_pair, a_graded_rule, ingest_run_id):  # noqa: F811
    """The gap_key formats are FROZEN on first mint -- question_uuid is
    uuid5(gap_kind, gap_key), immortal and externally citable -- so they are pinned by
    a test literal rather than left to whatever the view happens to emit."""
    questions.register_from_gaps(conn, ingest_run_id)
    expected = ids.mint_question_uuid(
        "uncurated_condition_contradiction",
        f"MOIETY:{a_contradicted_pair['moiety']}/"
        f"CONDITION:{a_contradicted_pair['condition']}")
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question WHERE question_uuid = %s",
        (expected,)).fetchone() == (1,)
    expected_rule = ids.mint_question_uuid(
        "uncurated_interaction_rule",
        f"MOIETY:{a_graded_rule['subject']}/CLASS:{a_graded_rule['class']}"
        f"/CI_AXIS:CI_MoA")
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question WHERE question_uuid = %s",
        (expected_rule,)).fetchone() == (1,)


def test_a_closing_gap_does_not_abort_the_ingest_when_curated(
        conn, a_contradicted_pair, ingest_run_id):  # noqa: F811
    """THE FAILURE MODE THIS SLICE COULD EASILY HAVE SHIPPED. register_from_gaps
    DELETEs a question whose gap has closed. curated_condition cascades from
    open_question and refuses DELETE, so the cascade would RAISE and abort the whole
    ingest -- and curating a pair is exactly what CLOSES its gap. The guard, not the
    cascade, is what keeps curator work: a cited question is RETAINED and marked
    is_current = false."""
    questions.register_from_gaps(conn, ingest_run_id)
    question_uuid = ids.mint_question_uuid(
        "uncurated_condition_contradiction",
        f"MOIETY:{a_contradicted_pair['moiety']}/"
        f"CONDITION:{a_contradicted_pair['condition']}")
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        question_uuid=question_uuid, reviewed_by="test", reviewed_against="2026.07.06")
    questions.register_from_gaps(conn, ingest_run_id)      # must not raise
    assert conn.execute(
        "SELECT is_current FROM drugref.open_question WHERE question_uuid = %s",
        (question_uuid,)).fetchone() == (False,)


def test_a_curated_row_whose_candidate_vanished_is_reported(conn, a_graded_rule):  # noqa: F811
    """The orphan detector. A curated row references its candidate by NATURAL KEY, not
    by foreign key, precisely so a per-source rebuild cannot cascade curator judgement
    away -- which means a rebuild CAN leave a judgement pointing at nothing, and an
    operator must be told. expansion_policy_unresolved is the same shape and reports 0.

    NOT a gap kind: a vanished candidate is an upstream-change signal for an operator,
    not a clinical question for a curator."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_target_unresolved").fetchone() == (0,)
    conn.execute("DELETE FROM drugref.class_contraindication")
    assert conn.execute(
        "SELECT target_table, subject_moiety FROM drugref.curated_target_unresolved"
    ).fetchall() == [("curated_interaction", a_graded_rule["subject"])]
