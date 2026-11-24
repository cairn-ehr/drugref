"""A reviewed grade that stops reaching a pair departs, both grains (#281, #279).

THE READ. db/083 keeps one observation per (ruling key, pair) episode. A pair
DEPARTS when an open (unacknowledged) episode is one `curated_ddi_pair` no longer
returns under that same ruling key, while the key's live version still exists,
still `applies`, and still has its candidate (an orphan is #121's banner, not
this one). `graded_pairs.departed` lists those, one entry per (grain, live
ruling).

WHAT THIS FIXES OVER #256's db/080 READ (which these tests replace): a correction
made after the drug left (#281 section 1), a drug that joined after the ruling and
then left (#281 section 2), and every moiety-grain ruling (#279) were invisible.

PER RULING, NOT PER PAIR (spec 5.3): a class rule that stops reaching a pair is a
departure even if a moiety rule still grades the pair -- the clinician may now see
a weaker grade, and must be told. DB-gated; `commit_point` stands in for COMMIT.
"""
from drugref import classes, curation, graded_pairs
from tests.graded_pair_helpers import commit_point, observations
from tests.test_class_subject_read_path import (_a_descent_and_policy_fixture,
                                                _a_graded_class_rule, _a_moiety,
                                                _deny, _file_member)
from tests.test_cli_interactions import _a_graded_pair
from tests.test_curated_overlay import _a_class
from tests.test_graded_pairs_refresh import _a_finished_run


def _rerule(conn, sc, oc, applies=True):
    """Correct the live ruling on A x B (append-then-point)."""
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
    The candidate survives. The ruling is committed (observed) while X is still in
    A; `before_leaving(sc, oc)` then runs, and is committed too, before X leaves.
    Returns (ruling id, X, Z, P, A, B)."""
    sc, oc, (x, z), (p,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code=f"N00000256{n}0",
        object_code=f"N00000256{n}1",
        subject_members=[(f"TESTUN256{n}A", "leaver"), (f"TESTUN256{n}B", "stayer")],
        object_members=[(f"TESTUN256{n}C", "partner")], severity="contraindicated")
    commit_point(conn)
    if before_leaving:
        before_leaving(sc, oc)
        commit_point(conn)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    return _live_ruling(conn, sc), x, z, p, sc, oc


def _moiety_rule(conn, ingest_run_id, n):
    """A graded moiety-grain rule S -> class B with partner P filed under B,
    committed. Returns (ruling id, S, P, B)."""
    s, p = _a_graded_pair(
        conn, ingest_run_id, subject_code=f"N00000279{n}0",
        object_code=f"N00000279{n}1", severity="major",
        subject_unii=f"TESTUN279{n}A", partner_unii=f"TESTUN279{n}B",
        subject_name="moiety-rule subject", partner_name="moiety-rule partner")
    ruling, b = conn.execute(
        "SELECT curated_interaction_id, object_class_uuid "
        "FROM drugref.curated_interaction "
        "WHERE subject_moiety_uuid = %s AND superseded_by IS NULL", (s,)).fetchone()
    commit_point(conn)
    return ruling, s, p, b


# ============================================================================
# 1. #256's cases, now read from db/083
# ============================================================================


def test_a_drug_that_left_its_class_is_a_departure_of_each_pair_it_was_in(
        conn, ingest_run_id):
    ruling, x, z, p, a, b = _a_rule_x_left(conn, ingest_run_id, 1)

    (d,) = graded_pairs.departed(conn, x)
    assert (d.rule_grain, d.ruling_id, d.pairs) == (
        "class_rule", ruling, frozenset({(x, p)}))
    assert (d.subject_key, d.object_class, d.relationship) == (a, b, "CI_MoA")
    assert (d.severity, d.backfilled) == ("contraindicated", False)
    assert graded_pairs.departed(conn, x, p) == [d]
    # The stayer still reaches P; the mirror direction was never ruled.
    assert graded_pairs.departed(conn, z) == []
    assert graded_pairs.departed(conn, p, x) == []


def test_a_partner_that_left_its_class_departs_its_subjects_pairs(conn, ingest_run_id):
    """Partner side: S's pairs with P depart, and P has none as subject."""
    sc, _oc, (s,), (p, w) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025690", object_code="N0000025691",
        subject_members=[("TESTUN2569A", "subject")],
        object_members=[("TESTUN2569B", "leaver"), ("TESTUN2569C", "stayer")])
    commit_point(conn)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))

    (d,) = graded_pairs.departed(conn, s)
    assert (d.rule_grain, d.ruling_id, d.pairs) == (
        "class_rule", _live_ruling(conn, sc), frozenset({(s, p)}))
    assert graded_pairs.departed(conn, s, w) == []
    assert graded_pairs.departed(conn, p) == []


def test_a_deny_that_cuts_a_subclass_is_a_departure(conn, ingest_run_id):
    """The second cause #256 names: membership unchanged, a policy deny now stops
    the descent the ruling reached the drug through."""
    f = _a_descent_and_policy_fixture(conn, ingest_run_id, expanding_side="subject")
    commit_point(conn)
    assert graded_pairs.departed(conn, f["descendant_only"]) == []
    _deny(conn, f["expanding_class"], "Subject Parent [MoA]")

    (d,) = graded_pairs.departed(conn, f["descendant_only"])
    assert (d.rule_grain, d.pairs) == (
        "class_rule", frozenset({(f["descendant_only"], f["fixed"])}))
    assert graded_pairs.departed(conn, f["direct"]) == []


def test_an_orphaned_rule_is_left_to_the_orphan_banner(conn, ingest_run_id):
    """Candidate gone: #121's banner already names the ruling; one ruling, one banner."""
    _ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 3)
    conn.execute("DELETE FROM drugref.class_pair_contraindication")
    assert graded_pairs.departed(conn, x) == []


def test_an_applies_false_ruling_dropped_no_grade(conn, ingest_run_id):
    """Re-ruled "does not apply" while X was still in A: X's episode is open, but
    the list no longer shows an applies = false ruling, so nothing fell out of it."""
    _ruling, x, _z, p, a, b = _a_rule_x_left(
        conn, ingest_run_id, 4,
        before_leaving=lambda sc, oc: _rerule(conn, sc, oc, applies=False))
    assert ("class_rule", a, b, "CI_MoA", x, p, "observed") in observations(conn)
    assert graded_pairs.departed(conn, x) == []


def test_a_backfilled_episode_says_so(conn, ingest_run_id):
    _ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 5)
    conn.execute(f"ALTER TABLE {graded_pairs.OBSERVATION_TABLE} "
                 "DISABLE TRIGGER graded_pair_observation_insert_only")
    conn.execute(f"UPDATE {graded_pairs.OBSERVATION_TABLE} "
                 "SET captured = 'backfill_db083' WHERE subject_moiety = %s", (x,))
    (d,) = graded_pairs.departed(conn, x)
    assert d.backfilled is True


# ============================================================================
# 2. what #256 could not see (#281, #279)
# ============================================================================


def test_a_correction_after_the_drug_left_is_a_departure(conn, ingest_run_id):
    """#281 section 1. The correction is a new version of the same KEY, and the
    key's history holds X: the departure is reported against the live version."""
    old, x, _z, p, sc, oc = _a_rule_x_left(conn, ingest_run_id, 2)
    _rerule(conn, sc, oc)
    commit_point(conn)

    (d,) = graded_pairs.departed(conn, x)
    assert d.ruling_id == _live_ruling(conn, sc) != old
    assert d.pairs == frozenset({(x, p)})


def test_a_drug_that_joined_after_the_ruling_and_left_is_a_departure(
        conn, ingest_run_id):
    """#281 section 2. Y was never in any snapshot: it joined after the ruling, an
    ingest finished (observing it), and a later ingest dropped it."""
    sc, _oc, (_x,), (p,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000028110", object_code="N0000028111",
        subject_members=[("TESTUN2811A", "original")],
        object_members=[("TESTUN2811B", "partner")])
    commit_point(conn)
    y = _a_moiety(conn, ingest_run_id, "TESTUN2811C", "joined later")
    _file_member(conn, y, sc, ingest_run_id)
    _a_finished_run(conn)
    commit_point(conn)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (y,))

    (d,) = graded_pairs.departed(conn, y)
    assert (d.ruling_id, d.pairs) == (_live_ruling(conn, sc), frozenset({(y, p)}))


def test_a_moiety_rule_partner_that_left_its_class_is_a_departure(conn, ingest_run_id):
    """#279: the moiety grain had no snapshot at all, so this was never reported."""
    ruling, s, p, b = _moiety_rule(conn, ingest_run_id, 1)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))

    (d,) = graded_pairs.departed(conn, s)
    assert (d.rule_grain, d.ruling_id, d.subject_key, d.object_class) == (
        "moiety_rule", ruling, s, b)
    assert (d.pairs, d.severity, d.backfilled) == (
        frozenset({(s, p)}), "major", False)
    assert (d.reviewed_by, d.reviewed_against) == ("a curator", "2026.07.06")


def test_a_deny_that_cuts_a_moiety_rules_subclass_is_a_departure(conn, ingest_run_id):
    """#279, second cause: Q is reached through B's child class; a deny on B stops
    the descent, while the direct member P keeps its grade."""
    _ruling, s, p, b = _moiety_rule(conn, ingest_run_id, 2)
    child = _a_class(conn, ingest_run_id, code="N0000027922", name="Child [MoA]")
    classes.add_parent_edge(conn, child, b, ingest_run_id)
    q = _a_moiety(conn, ingest_run_id, "TESTUN2792C", "descendant-only partner")
    _file_member(conn, q, child, ingest_run_id)
    graded_pairs.refresh(conn)     # observe (s, q), as the ingest's commit would
    _deny(conn, b, "Moiety-rule object [MoA]")

    (d,) = graded_pairs.departed(conn, s)
    assert (d.rule_grain, d.pairs) == ("moiety_rule", frozenset({(s, q)}))
    assert graded_pairs.departed(conn, s, p) == []


def test_a_class_rule_departing_while_a_moiety_rule_still_grades_the_pair_is_a_departure(
        conn, ingest_run_id):
    """Per ruling: the moiety rule X -> B still grades (X, P), but the class rule
    A x B -- which may have graded it more severely -- no longer does."""
    _ruling, x, _z, p, _a, b = _a_rule_x_left(conn, ingest_run_id, 6)
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (x, b, ingest_run_id))
    curation.record_interaction_judgement(
        conn, x, b, "CI_MoA", True, severity="minor", evidence_grade="established",
        reviewed_by="test", reviewed_against="2026")
    commit_point(conn)
    assert conn.execute(
        "SELECT rule_grain FROM drugref.curated_ddi_pair WHERE subject_moiety = %s "
        "AND partner_moiety = %s", (x, p)).fetchall() == [("moiety_rule",)]

    (d,) = graded_pairs.departed(conn, x, p)
    assert (d.rule_grain, d.severity) == ("class_rule", "contraindicated")


def test_withdrawn_then_reinstated_banners_again(conn, ingest_run_id):
    """Review Focus 4: withdrawing stops the departure; reinstating while X is
    still gone brings it back, because the episode was never acknowledged."""
    _ruling, x, _z, p, sc, oc = _a_rule_x_left(conn, ingest_run_id, 7)
    assert len(graded_pairs.departed(conn, x)) == 1
    _rerule(conn, sc, oc, applies=False)
    commit_point(conn)
    assert graded_pairs.departed(conn, x) == []
    _rerule(conn, sc, oc, applies=True)
    commit_point(conn)

    (d,) = graded_pairs.departed(conn, x)
    assert (d.ruling_id, d.pairs) == (_live_ruling(conn, sc), frozenset({(x, p)}))


def test_a_drug_that_comes_back_is_no_longer_departed(conn, ingest_run_id):
    _ruling, x, _z, _p, sc, _oc = _a_rule_x_left(conn, ingest_run_id, 8)
    assert len(graded_pairs.departed(conn, x)) == 1
    _file_member(conn, x, sc, ingest_run_id)
    assert graded_pairs.departed(conn, x) == []


def test_no_subject_lists_every_departure(conn, ingest_run_id):
    """`drugref audit departures` (Task 6) reads with no subject. Both rulings are
    reviewed in one transaction (same reviewed_at), so grain breaks the tie."""
    class_ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 9)
    moiety_ruling, s, q, _b2 = _moiety_rule(conn, ingest_run_id, 3)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (q,))

    got = graded_pairs.departed(conn)
    assert [(d.rule_grain, d.ruling_id, d.pairs) for d in got] == [
        ("class_rule", class_ruling, frozenset({(x, p)})),
        ("moiety_rule", moiety_ruling, frozenset({(s, q)}))]
    assert graded_pairs.departed(conn, partner=q) == [got[1]]


def test_first_seen_at_is_the_earliest_open_episode(conn, ingest_run_id):
    """Two pairs of one ruling depart; the entry dates from the older episode."""
    _sc, _oc, (s,), (p, q) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000028120", object_code="N0000028121",
        subject_members=[("TESTUN2812A", "subject")],
        object_members=[("TESTUN2812B", "older"), ("TESTUN2812C", "newer")])
    commit_point(conn)
    conn.execute(f"ALTER TABLE {graded_pairs.OBSERVATION_TABLE} "
                 "DISABLE TRIGGER graded_pair_observation_insert_only")
    older = conn.execute(
        f"UPDATE {graded_pairs.OBSERVATION_TABLE} "
        "SET first_seen_at = first_seen_at - interval '30 days' "
        "WHERE partner_moiety = %s RETURNING first_seen_at", (p,)).fetchone()[0]
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid IN (%s, %s)",
                 (p, q))

    (d,) = graded_pairs.departed(conn, s)
    assert d.pairs == frozenset({(s, p), (s, q)})
    assert d.first_seen_at == older


def test_most_recently_reviewed_ruling_first_then_highest_id(conn, ingest_run_id):
    """Rulings made in one transaction share reviewed_at (now()), so the id breaks
    the tie, highest first; an earlier reviewed_at sorts after both."""
    first, *_ = _a_rule_x_left(conn, ingest_run_id, 1)
    second, *_ = _a_rule_x_left(conn, ingest_run_id, 2)
    third, *_ = _a_rule_x_left(conn, ingest_run_id, 3)
    assert [d.ruling_id for d in graded_pairs.departed(conn)] == [third, second, first]

    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")   # ALTER refuses pending events
    conn.execute("ALTER TABLE drugref.curated_class_interaction "
                 "DISABLE TRIGGER curated_class_interaction_append_only")
    conn.execute("UPDATE drugref.curated_class_interaction "
                 "SET reviewed_at = reviewed_at - interval '1 day' "
                 "WHERE curated_class_interaction_id = %s", (third,))
    assert [d.ruling_id for d in graded_pairs.departed(conn)] == [second, first, third]
