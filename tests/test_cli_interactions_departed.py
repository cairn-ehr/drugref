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
from drugref import class_ruling_reach, cli_interactions
from tests.test_class_subject_read_path import _a_graded_class_rule
from tests.test_cli_interactions import _args
# The read's tests (this file's old section 1) moved to test_graded_pairs_departed
# with db/083's read (#281); the fixtures moved with them.
from tests.test_graded_pairs_departed import _a_rule_x_left, _live_ruling


# ============================================================================
# the command (the read itself: tests/test_graded_pairs_departed.py)
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
    """The stayer Z still reaches P. The stranger is the pair (X, Z): X departed, but
    the ruling never paired X with Z, so the banner must stay tied to the pair asked
    rather than to anything X lost."""
    _ruling, x, z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 8)

    assert cli_interactions._handle_interactions(conn, _args(z, p)) == 0
    assert "CHANGED SINCE REVIEW" not in capsys.readouterr().out
    assert cli_interactions._handle_interactions(conn, _args(x, z)) == 0
    out = capsys.readouterr().out
    assert "CHANGED SINCE REVIEW" not in out
    assert "ABSENT RULE" in out


def test_a_self_pair_is_never_a_departure(conn, ingest_run_id):
    """A x A (a class against itself, db/032 DECISION 2): X sat on both sides, so the
    reach holds X as subject AND partner. (X, X) never graded (db/038), so X leaving
    departs (X, Z) only."""
    sc, _oc, (x, z), _ = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025610", object_code="N0000025610",
        subject_members=[("TESTUN2561A", "leaver"), ("TESTUN2561B", "stayer")],
        object_members=[])
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))

    (d,) = class_ruling_reach.departed_reach(conn, x)
    assert d.pairs == frozenset({(x, z)})


def test_a_ruling_departing_both_directions_is_one_ruling(conn, ingest_run_id, capsys):
    """A x A again: asked as the pair (X, Z), the one ruling departs X -> Z AND
    Z -> X. The banner counts rulings, so it must say one, listing both pairs."""
    sc, _oc, (x, z), _ = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025620", object_code="N0000025620",
        subject_members=[("TESTUN2562A", "leaver"), ("TESTUN2562B", "stayer")],
        object_members=[])
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    ruling = _live_ruling(conn, sc)

    assert cli_interactions._handle_interactions(
        conn, _args(x, z)) == cli_interactions.EXIT_INCOMPLETE
    out = capsys.readouterr().out
    assert "CHANGED SINCE REVIEW: 1 curated class ruling(s)" in out
    assert out.count(f"ruling {ruling}:") == 1
    assert f"{x} -> {z}" in out and f"{z} -> {x}" in out


def test_an_orphan_does_not_hide_a_departure(conn, ingest_run_id, capsys):
    """X departs A x B, and a second ruling C x D reaching X loses its candidate.
    Both banners must print in both forms: the exit code alone would stay
    INCOMPLETE either way, so only the text shows the departure was not swallowed."""
    _ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 0)
    sc2, _oc2, _, _ = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025630", object_code="N0000025631",
        subject_members=[("TESTUN2560A", "leaver")],   # X again: same code, same uuid
        object_members=[("TESTUN2563C", "other partner")])
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (sc2,))

    for args in (_args(x), _args(x, p)):
        assert cli_interactions._handle_interactions(
            conn, args) == cli_interactions.EXIT_INCOMPLETE
        out = capsys.readouterr().out
        assert "** INCOMPLETE" in out
        assert "CHANGED SINCE REVIEW" in out


def test_without_db080_the_read_says_it_cannot_tell(conn, ingest_run_id, capsys):
    """No reach to compare: say so on stderr in migration_guard's own wording (which
    names a DROPPED table as dropped), and never conclude "ABSENT RULE" -- that
    sentence is reassurance, and the check behind it did not run.

    Nothing is rolled back (the probe is `to_regclass`, not a failed read), so the
    rollback-isolated fixture reaches the real DROPPED branch here.
    """
    _ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 9)
    conn.execute("DROP TABLE drugref.curated_class_interaction_reach")

    assert cli_interactions._handle_interactions(conn, _args(x)) == 0
    captured = capsys.readouterr()
    assert "CHANGED SINCE REVIEW" not in captured.out
    assert "db/080" in captured.err
    assert "DROPPED" in captured.err
    assert "dropped out" in captured.err   # the caller's consequence reaches the operator


def test_the_pair_form_withholds_absent_rule_when_it_cannot_tell(
        conn, ingest_run_id, capsys):
    """A pair with no surviving grade, on a database whose reach table is gone."""
    _ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 9)
    conn.execute("DROP TABLE drugref.curated_class_interaction_reach")

    assert cli_interactions._handle_interactions(conn, _args(x, p)) == 0
    captured = capsys.readouterr()
    assert "ABSENT RULE" not in captured.out
    assert "could not check" in captured.out
    assert "db/080" in captured.err
