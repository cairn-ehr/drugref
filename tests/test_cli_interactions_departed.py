"""`drugref interactions` must say when a reviewed ruling no longer reaches a pair it
graded (#256, widened to both grains by #281/#279).

THE DEFECT. A grade can vanish while its candidate survives: a drug leaves the
subject or partner class (membership is delete-and-rebuild), or an expansion-policy
deny cuts the descendant class it was reached through. The list then said `no curated
grade` for a pair the curator graded. The banner reads `graded_pairs.departed`
(db/083's observations compared with `curated_ddi_pair`), so class and moiety rulings,
corrections made after the drug left, and drugs that joined late are all covered; each
entry names its grain and the `acknowledge-departure` command that silences it.

PAIR-LEVEL AND DIRECTIONAL, like the list it qualifies: a departure is reported for
(subject, partner) exactly when a ruling graded that pair and does not now. Without
db/083 there is nothing to compare: a stderr note, exit code unchanged. DB-gated.
"""
import uuid

from drugref import cli_departures, cli_interactions, graded_pairs
from tests.graded_pair_helpers import commit_point
from tests.test_class_subject_read_path import _a_graded_class_rule
from tests.test_cli_interactions import _args
# The read's tests (this file's old section 1) moved to test_graded_pairs_departed
# with db/083's read (#281); the fixtures moved with them.
from tests.test_graded_pairs_departed import (_a_rule_x_left, _live_ruling,
                                              _moiety_rule)


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
    assert f"class ruling {ruling}" in out and "contraindicated" in out
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


def test_a_ruling_departing_both_directions_is_one_ruling(conn, ingest_run_id, capsys):
    """A x A again: asked as the pair (X, Z), the one ruling departs X -> Z AND
    Z -> X. The banner counts rulings, so it must say one, listing both pairs."""
    sc, _oc, (x, z), _ = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025620", object_code="N0000025620",
        subject_members=[("TESTUN2562A", "leaver"), ("TESTUN2562B", "stayer")],
        object_members=[])
    commit_point(conn)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    ruling = _live_ruling(conn, sc)

    assert cli_interactions._handle_interactions(
        conn, _args(x, z)) == cli_interactions.EXIT_INCOMPLETE
    out = capsys.readouterr().out
    assert "CHANGED SINCE REVIEW: 1 curated ruling(s)" in out
    assert out.count(f"class ruling {ruling}:") == 1
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


def test_without_db083_the_read_says_it_cannot_tell(conn, ingest_run_id, capsys):
    """No observations to compare: say so on stderr in migration_guard's own wording
    (which names a DROPPED table as dropped), and never conclude "ABSENT RULE" -- that
    sentence is reassurance, and the check behind it did not run.

    Nothing is rolled back (the probe is `to_regclass`, not a failed read), so the
    rollback-isolated fixture reaches the real DROPPED branch here.
    """
    _ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 9)
    conn.execute(f"DROP TABLE {graded_pairs.OBSERVATION_TABLE} CASCADE")

    assert cli_interactions._handle_interactions(conn, _args(x)) == 0
    captured = capsys.readouterr()
    assert "CHANGED SINCE REVIEW" not in captured.out
    assert "db/083" in captured.err
    assert "DROPPED" in captured.err
    # the caller's consequence reaches the operator
    assert "stopped reaching" in captured.err


def test_the_pair_form_withholds_absent_rule_when_it_cannot_tell(
        conn, ingest_run_id, capsys):
    """A pair with no surviving grade, on a database whose observation table is gone."""
    _ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 9)
    conn.execute(f"DROP TABLE {graded_pairs.OBSERVATION_TABLE} CASCADE")

    assert cli_interactions._handle_interactions(conn, _args(x, p)) == 0
    captured = capsys.readouterr()
    assert "ABSENT RULE" not in captured.out
    assert "could not check" in captured.out
    assert "db/083" in captured.err


def test_a_moiety_rule_departure_is_bannered_with_its_grain(
        conn, ingest_run_id, capsys):
    """#279 at the CLI: the partner P leaves class B, so the moiety rule S -> B
    stops reaching (S, P). The entry says `moiety ruling` and `drug S -> class B`."""
    ruling, s, p, b = _moiety_rule(conn, ingest_run_id, 2)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))

    assert cli_interactions._handle_interactions(
        conn, _args(s)) == cli_interactions.EXIT_INCOMPLETE
    out = capsys.readouterr().out
    assert "CHANGED SINCE REVIEW" in out
    assert f"moiety ruling {ruling}:" in out
    assert f"drug {s} -> class {b}" in out
    assert f"{s} -> {p}" in out


def _ack_lines(out):
    return [ln for ln in out.splitlines() if "acknowledge (" in ln]


def _leave(conn, *moieties):
    for m in moieties:
        conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s",
                     (m,))


def test_a_subject_side_leaver_is_named_as_the_drug(conn, ingest_run_id, capsys):
    """X left the SUBJECT class (single pair (X, P)): X is named, not P. The
    curator fills in the rationale, so it is printed literally."""
    ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 3)
    cli_interactions._handle_interactions(conn, _args(x))
    (line,) = _ack_lines(capsys.readouterr().out)
    assert ("acknowledge (closes 1 pair(s)): drugref curate acknowledge-departure "
            f"--grain class {ruling} {x} --rationale '...'") in line
    assert str(p) not in line


def test_a_partner_side_leaver_is_named_as_the_drug(conn, ingest_run_id, capsys):
    sc, _oc, (s,), (p, _w) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025640", object_code="N0000025641",
        subject_members=[("TESTUN2564A", "subject")],
        object_members=[("TESTUN2564B", "leaver"), ("TESTUN2564C", "stayer")])
    commit_point(conn)
    _leave(conn, p)
    cli_interactions._handle_interactions(conn, _args(s))
    (line,) = _ack_lines(capsys.readouterr().out)
    assert f"{_live_ruling(conn, sc)} {p} --rationale" in line


def test_the_one_drug_form_names_the_partner_leavers_not_the_asked_drug(
        conn, ingest_run_id, capsys):
    """Both partners P1, P2 left; the asked subject S stayed. Pairs share subject S,
    but S did not leave, so the lines name P1 and P2."""
    _sc, _oc, (s,), (p1, p2) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025650", object_code="N0000025651",
        subject_members=[("TESTUN2565A", "subject")],
        object_members=[("TESTUN2565B", "p1"), ("TESTUN2565C", "p2")])
    commit_point(conn)
    _leave(conn, p1, p2)
    cli_interactions._handle_interactions(conn, _args(s))
    lines = _ack_lines(capsys.readouterr().out)
    assert len(lines) == 2
    assert all(f" {s} --rationale" not in ln for ln in lines)
    assert {p1, p2} == {m for m in (p1, p2) if any(str(m) in ln for ln in lines)}


def test_an_acknowledge_that_reaches_an_unseen_departure_says_so(
        conn, ingest_run_id, capsys):
    """P left; subjects X and Y both depart with it, but the question asks only X.
    Naming P closes (Y, P) too, which the banner did not show."""
    _sc, _oc, (x, _y), (p,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025660", object_code="N0000025661",
        subject_members=[("TESTUN2566A", "asked"), ("TESTUN2566B", "unseen")],
        object_members=[("TESTUN2566C", "leaver")])
    commit_point(conn)
    _leave(conn, p)
    cli_interactions._handle_interactions(conn, _args(x))
    (line,) = _ack_lines(capsys.readouterr().out)
    assert "acknowledge (closes 2 pair(s), 1 not shown above)" in line
    assert f" {p} --rationale" in line


def test_a_moiety_entry_names_the_partner(conn, ingest_run_id, capsys):
    mruling, s, p, _b = _moiety_rule(conn, ingest_run_id, 3)
    _leave(conn, p)
    cli_interactions._handle_interactions(conn, _args(s))
    (line,) = _ack_lines(capsys.readouterr().out)
    assert ("--grain moiety " f"{mruling} {p} --rationale '...'") in line


def test_format_departure_is_pure_over_its_hints():
    x, p = uuid.uuid4(), uuid.uuid4()
    d = graded_pairs.Departure(
        "class_rule", 7, uuid.uuid4(), uuid.uuid4(), "CI_MoA", "major", "r", "2026",
        False, None, frozenset({(x, p)}))
    lines = cli_departures.format_departure(d, [(x, 3, 2)])
    assert lines[-1].startswith(
        "      acknowledge (closes 3 pair(s), 2 not shown above): ")
    assert lines[-1].endswith(
        f"--grain class 7 {x} --rationale '...' --reviewed-by NAME "
        "--reviewed-against REF")
    assert not any("acknowledge" in ln
                   for ln in cli_departures.format_departure(d, []))
