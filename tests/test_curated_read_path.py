# tests/test_curated_read_path.py
"""The curated overlay's read path (db/029 section 3, slice 5c.1).

INNER JOINS THROUGHOUT, AND THAT IS THE STRUCTURAL POINT. db/019 split `induces` into
its own table rather than adding a WHERE clause, arguing that a consumer who forgets a
filter on a shared table reads a therapeutic claim off the wrong row. The same
forgetfulness here -- a LEFT JOIN returning every candidate with a NULL severity beside
it -- renders an UNREVIEWED candidate as though a curator had passed it. A consumer must
ASK for graded advice and receive only graded advice.
"""
from drugref import curation

# a_graded_rule and a_contradicted_pair are conftest.py fixtures (moved there once
# tests/test_curated_gap_views.py needed them too) -- pytest resolves them by name
# with no import required here.


def test_a_graded_rule_reaches_every_pair_it_expands_to(conn, a_graded_rule):
    """THE PAYOFF OF RULE-LEVEL CURATION. One curated row must grade the pair the rule
    expands to -- if it does not, curating 635 rules buys nothing over curating 21,664
    pairs. (635 measured, not the "~739" earlier drafts quoted: that was the raw
    pre-gate MED-RT terminology count, never class_contraindication's own row count --
    see PROJECT-NOTES.md "Slice 5c.1".)"""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established",
        management="avoid", reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT partner_moiety, severity, management FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [(a_graded_rule["partner"], "major", "avoid")]


def test_an_ungraded_rule_reaches_the_curated_view_never(conn, a_graded_rule):
    """The forgetfulness db/019 refuses to allow. An unreviewed candidate must not
    appear here at all -- not with a NULL severity, not at all."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair").fetchone() == (0,)


def test_a_retired_rule_stops_reaching_the_view(conn, a_graded_rule):
    """`applies = false` is live and binds NOTHING. Rendering it would tell a
    prescriber about an interaction a curator explicitly ruled unreal."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", False,
        reviewed_by="test", reviewed_against="2026.08.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair").fetchone() == (0,)


def test_a_superseded_grade_stops_reaching_the_view(conn, a_graded_rule):
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="minor", evidence_grade="suspected", reviewed_by="test",
        reviewed_against="2026.08.06")
    assert conn.execute(
        "SELECT severity FROM drugref.curated_ddi_pair").fetchall() == [("minor",)]


def test_the_candidate_view_is_untouched_by_curation(conn, a_graded_rule):
    """ddi_candidate_pair answers "what did the release say" and must keep answering it
    after drugref disagrees. db/027 does let curation gate this view -- a `deny` policy
    withholds 233 pairs -- but that governs drugref's own reading of the DAG, which is
    a different act from contradicting an upstream assertion."""
    before = conn.execute("SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", False,
        reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone() == before


def test_one_ruling_returns_a_row_per_candidate_it_reconciles(conn, a_contradicted_pair):
    """THE VIEW'S GRAIN, pinned. The beta-blocker case must return TWO rows carrying
    the SAME ruling -- one naming may_treat, one naming CI_with. Aggregating the
    candidates into an array would hide which relationships the ruling reconciles, and
    #41's finding was that folding a key component under an aggregate breaks a view's
    grain."""
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="test", reviewed_against="2026.07.06")
    rows = conn.execute(
        "SELECT ruling, candidate_kind, relationship FROM "
        "drugref.curated_condition_ruling ORDER BY candidate_kind").fetchall()
    assert rows == [("context_dependent", "contraindication", "CI_with"),
                    ("context_dependent", "indication", "may_treat")]


def test_a_spurious_ruling_reaches_no_consumer(conn, a_contradicted_pair):
    """It records a disagreement WITHOUT acting on it: nothing renders it as advice,
    and the candidates stay in their projections."""
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "spurious", reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_condition_ruling").fetchone() == (0,)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone() == (1,)
