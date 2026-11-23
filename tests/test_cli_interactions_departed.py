"""`drugref interactions` must say when a class ruling no longer reaches a pair it
graded when the curator ruled (#256).

THE DEFECT. #121's banner covers a class-grain grade whose CANDIDATE a re-ingest
dropped. A grade also vanishes while the candidate survives: a drug leaves the subject
or partner class (membership is delete-and-rebuild), or an expansion-policy deny cuts
the descendant class it was reached through. `class_rule_member` (db/079) applies both,
so both read the same way: the pair is in the ruling's reach snapshot (db/080) and no
longer in `class_rule_member`. The list then said `no curated grade` for a pair the
curator graded, and nothing said why.

PAIR-LEVEL AND DIRECTIONAL, like the list it qualifies: a departure is reported for
(subject, partner) exactly when the ruling reached that pair when made and does not
now. DB-gated.
"""
from drugref import class_ruling_reach, cli_interactions, curation
from tests.test_class_subject_read_path import (_a_descent_and_policy_fixture,
                                                 _a_graded_class_rule, _deny)
from tests.test_cli_interactions import _args


def _rerule(conn, sc, oc, applies=True):
    """Correct the live ruling on A x B (append-then-point, re-snapshotting reach)."""
    grade = (dict(severity="major", evidence_grade="established") if applies else {})
    curation.record_class_interaction_judgement(
        conn, sc, oc, "CI_MoA", applies, reviewed_by="test", reviewed_against="2026",
        **grade)


def _live_ruling(conn, sc):
    return conn.execute(
        "SELECT curated_class_interaction_id FROM drugref.curated_class_interaction "
        "WHERE subject_class_uuid = %s AND superseded_by IS NULL", (sc,)).fetchone()[0]


def _a_rule_x_left(conn, ingest_run_id, n, *, before_leaving=None):
    """Rule A x B graded while X and Z are in A and P is in B; then X alone leaves A.
    The candidate survives. `before_leaving(sc, oc)` runs while X is still in A.
    Returns (ruling id, X, Z, P, A, B)."""
    sc, oc, (x, z), (p,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code=f"N00000256{n}0",
        object_code=f"N00000256{n}1",
        subject_members=[(f"TESTUN256{n}A", "leaver"), (f"TESTUN256{n}B", "stayer")],
        object_members=[(f"TESTUN256{n}C", "partner")], severity="contraindicated")
    if before_leaving:
        before_leaving(sc, oc)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    return _live_ruling(conn, sc), x, z, p, sc, oc


# ============================================================================
# 1. the read
# ============================================================================


def test_a_drug_that_left_its_class_is_a_departure_of_each_pair_it_was_in(
        conn, ingest_run_id):
    ruling, x, z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 1)

    (d,) = class_ruling_reach.departed_reach(conn, x)
    assert (d.curated_class_interaction_id, d.pairs) == (ruling, frozenset({(x, p)}))
    assert (d.severity, d.captured) == ("contraindicated", "at_ruling")
    assert class_ruling_reach.departed_reach(conn, x, p) == [d]
    # The stayer still reaches P; the mirror direction was never ruled.
    assert class_ruling_reach.departed_reach(conn, z) == []
    assert class_ruling_reach.departed_reach(conn, p, x) == []


def test_a_partner_that_left_its_class_departs_its_subjects_pairs(conn, ingest_run_id):
    """Partner side: S's pairs with P depart, and P has none as subject."""
    sc, _oc, (s,), (p, w) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025690", object_code="N0000025691",
        subject_members=[("TESTUN2569A", "subject")],
        object_members=[("TESTUN2569B", "leaver"), ("TESTUN2569C", "stayer")])
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))

    (d,) = class_ruling_reach.departed_reach(conn, s)
    assert d.pairs == frozenset({(s, p)})
    assert class_ruling_reach.departed_reach(conn, s, w) == []
    assert class_ruling_reach.departed_reach(conn, p) == []


def test_a_deny_that_cuts_a_subclass_is_a_departure(conn, ingest_run_id):
    """The second cause #256 names: membership unchanged, a policy deny now stops
    the descent the ruling reached the drug through."""
    f = _a_descent_and_policy_fixture(conn, ingest_run_id, expanding_side="subject")
    assert class_ruling_reach.departed_reach(conn, f["descendant_only"]) == []
    _deny(conn, f["expanding_class"], "Subject Parent [MoA]")

    (d,) = class_ruling_reach.departed_reach(conn, f["descendant_only"])
    assert d.pairs == frozenset({(f["descendant_only"], f["fixed"])})
    assert class_ruling_reach.departed_reach(conn, f["direct"]) == []


def test_a_correction_after_the_drug_left_is_not_a_departure(conn, ingest_run_id):
    """The live version is the one being read, and it was ruled without X: only
    the superseded version's reach holds X, and that ruling no longer grades."""
    _ruling, x, _z, _p, sc, oc = _a_rule_x_left(conn, ingest_run_id, 2)
    _rerule(conn, sc, oc)
    assert class_ruling_reach.departed_reach(conn, x) == []


def test_an_orphaned_rule_is_left_to_the_orphan_banner(conn, ingest_run_id):
    """Candidate gone: #121's banner already names the ruling; one ruling, one banner."""
    _ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 3)
    conn.execute("DELETE FROM drugref.class_pair_contraindication")
    assert class_ruling_reach.departed_reach(conn, x) == []


def test_an_applies_false_ruling_dropped_no_grade(conn, ingest_run_id):
    """Re-ruled "does not apply" while X was still in A: the reach holds X, but
    the list never showed an applies = false ruling, so nothing fell out of it."""
    _ruling, x, _z, _p, _a, _b = _a_rule_x_left(
        conn, ingest_run_id, 4,
        before_leaving=lambda sc, oc: _rerule(conn, sc, oc, applies=False))
    assert class_ruling_reach.departed_reach(conn, x) == []


def test_a_backfilled_reach_says_so(conn, ingest_run_id):
    ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 5)
    conn.execute("ALTER TABLE drugref.curated_class_interaction_reach "
                 "DISABLE TRIGGER curated_class_interaction_reach_insert_only")
    conn.execute("UPDATE drugref.curated_class_interaction_reach "
                 "SET captured = 'backfill_db080' WHERE curated_class_interaction_id "
                 "= %s", (ruling,))
    (d,) = class_ruling_reach.departed_reach(conn, x)
    assert d.captured == "backfill_db080"


# ============================================================================
# 2. the command
# ============================================================================


def test_the_one_drug_form_says_a_reviewed_grade_no_longer_reaches_it(
        conn, ingest_run_id, capsys):
    ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 6)

    assert cli_interactions._handle_interactions(
        conn, _args(x)) == cli_interactions.EXIT_INCOMPLETE
    out = capsys.readouterr().out
    assert "CHANGED SINCE REVIEW" in out
    assert f"ruling {ruling}" in out and "contraindicated" in out
    assert f"drugref audit class-ruling {ruling}" in out


def test_the_pair_form_does_not_claim_an_absent_rule(conn, ingest_run_id, capsys):
    """Both argument orders: the departed pair is (X, P), asked either way round."""
    _ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 7)

    for a, b in ((x, p), (p, x)):
        assert cli_interactions._handle_interactions(
            conn, _args(a, b)) == cli_interactions.EXIT_INCOMPLETE
        out = capsys.readouterr().out
        assert "CHANGED SINCE REVIEW" in out
        assert "ABSENT RULE" not in out


def test_a_stayer_and_a_stranger_get_no_banner(conn, ingest_run_id, capsys):
    _ruling, _x, z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 8)

    assert cli_interactions._handle_interactions(conn, _args(z, p)) == 0
    assert "CHANGED SINCE REVIEW" not in capsys.readouterr().out


def test_without_db080_the_read_says_it_cannot_tell(conn, ingest_run_id, capsys):
    """An unmigrated database has no reach to compare: say so, do not guess."""
    _ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 9)
    conn.execute("DROP TABLE drugref.curated_class_interaction_reach")

    assert cli_interactions._handle_interactions(conn, _args(x)) == 0
    captured = capsys.readouterr()
    assert "CHANGED SINCE REVIEW" not in captured.out
    assert "db/080" in captured.err
