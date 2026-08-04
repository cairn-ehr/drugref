# tests/test_cli_policy.py
"""`drugref policy` -- the operator surface for expansion decisions (#61).

WHY THIS EXISTS. medrt_run warns an operator when a release stops defining a class
somebody ruled on, and tells them to "re-key or withdraw". Since db/027 both verbs are
unavailable as raw SQL -- DELETE raises, and so does UPDATE ... SET source_code -- so
following the warning meant writing Python against the library.

THESE TESTS COMMIT, AND COMMITTED POLICY ROWS CANNOT BE DELETED. The append-only floor
refuses it, so there is no teardown that erases them; the only way to undo a revision
is to record a FURTHER correction. That is why every write test restores in a
`finally`, exactly as test_a_second_apply_does_not_stomp_a_locally_revised_decision
does. Nothing leaks between sessions -- conftest's session-scoped _migrated drops the
schema -- but WITHIN a session these rows are visible to every later test, and
test_the_seed_holds_the_fourteen_roots_the_measurement_found asserts the live decision
of all fourteen. A missing restore would fail it, and which test failed would depend on
collection order: the worst shape a failure can have.

WHY A SEEDED ROOT RATHER THAN AN INVENTED CODE. An invented source_code names no class,
so it would appear in expansion_policy_unresolved -- the view that reports "this deny
matches no class" -- and several tests assert that view's contents. Revising a real
seeded root keeps every one of those views answering what it answered before, provided
the restore runs.

CODE is Dermatologic Activity Alteration [PE]: seeded, denied, and referenced by
exactly one other test, as a member of a frozen set that a restore-to-`deny` keeps
satisfied.
"""
import psycopg
import pytest

from drugref import cli, interactions

CODE = "N0000009020"
NAME = "Dermatologic Activity Alteration [PE]"
SEED_RATIONALE = "restored by tests/test_cli_policy.py"


@pytest.fixture
def committed(_migrated, monkeypatch):
    """A DSN the CLI will pick up, plus a restore of CODE's seeded `deny`.

    The restore is a third row, not a rollback: nothing can be deleted or revised in
    place, so undoing a test's revision means recording a further correction.
    """
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    try:
        yield _migrated
    finally:
        with psycopg.connect(_migrated) as c:
            live = c.execute(
                "SELECT decision FROM drugref.class_expansion_policy_current "
                "WHERE source = 'MED-RT' AND source_code = %s", (CODE,)).fetchone()
            if live is None or live[0] != "deny":
                interactions.record_expansion_decision(
                    c, "MED-RT", CODE, "deny", NAME, SEED_RATIONALE,
                    "test", "2026.07.06")
                c.commit()


def _live(dsn, code=CODE):
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT decision, rationale FROM drugref.class_expansion_policy_current "
            "WHERE source = 'MED-RT' AND source_code = %s", (code,)).fetchone()


def test_policy_record_revises_a_binding_decision_and_commits(committed, capsys):
    """The handler COMMITS, unlike every library function in these modules. The CLI is
    the caller, and the caller owns the transaction -- an operator's ruling that
    vanished when the process exited would be worse than no surface at all."""
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "allow", "--class-name", NAME,
        "--rationale", "subtree measured narrow", "--reviewed-by", "operator",
        "--reviewed-against", "2026.07.06"]) == 0
    assert _live(committed) == ("allow", "subtree measured narrow")
    assert "allow" in capsys.readouterr().out


def test_policy_record_refuses_withdrawn_and_names_the_other_subcommand(committed, capsys):
    """record_expansion_decision accepts `withdrawn` by design -- rejecting it in
    Python would put a member of the decision vocabulary back into a second place. But
    that path bypasses BOTH guarantees withdraw_expansion_decision provides: the
    NoLiveDecisionError that catches a caller believing something false, and carrying
    class_name forward so a withdrawal cannot introduce a name nobody reviewed.

    The library keeps that door open. An operator surface should not, and refusing by
    comparison to interactions.WITHDRAWN adds no second literal.
    """
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "withdrawn", "--class-name", NAME, "--rationale", "r",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 2
    assert "policy withdraw" in capsys.readouterr().err
    assert _live(committed)[0] == "deny"        # a refused command changed nothing


def test_policy_withdraw_returns_the_class_to_unreviewed(committed):
    """WITHDRAWN IS NOT `allow`. It means no current judgement, so the class goes back
    to gap_unreviewed_expansion_root -- which is what medrt_run's warning is asking an
    operator to do when a rationale has gone stale."""
    assert cli.main([
        "policy", "withdraw", "--source", "MED-RT", "--code", CODE,
        "--rationale", "the measurement no longer holds",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 0
    assert _live(committed) is None


def test_policy_withdraw_without_a_live_decision_exits_two(committed, capsys):
    """NoLiveDecisionError is a LookupError, which main did not catch -- so this
    printed a psycopg-free but equally unhelpful traceback. Withdrawing a decision
    nobody made means the caller believes something false; saying so plainly is the
    whole point of the error."""
    assert cli.main([
        "policy", "withdraw", "--source", "MED-RT", "--code", "N0000000404",
        "--rationale", "r", "--reviewed-by", "operator",
        "--reviewed-against", "2026.07.06"]) == 2
    err = capsys.readouterr().err
    assert "no live expansion decision" in err
    assert "Traceback" not in err


def test_policy_show_lists_what_binds(committed, capsys):
    assert cli.main(["policy", "show"]) == 0
    out = capsys.readouterr().out
    assert CODE in out and "deny" in out


def test_policy_show_prints_one_classes_history(committed, capsys):
    """The read that makes `record` usable: an operator writing a rationale needs to
    see the one they are replacing, and the superseded row keeps its ORIGINAL text."""
    cli.main(["policy", "record", "--source", "MED-RT", "--code", CODE,
              "--decision", "allow", "--class-name", NAME,
              "--rationale", "subtree measured narrow", "--reviewed-by", "operator",
              "--reviewed-against", "2026.07.06"])
    capsys.readouterr()
    assert cli.main(["policy", "show", "--source", "MED-RT", "--code", CODE]) == 0
    out = capsys.readouterr().out
    assert "subtree measured narrow" in out
    assert "allow" in out and "deny" in out      # the live ruling AND its predecessor


def test_policy_show_says_so_when_nobody_has_ruled(committed, capsys):
    """Absent means UNREVIEWED, which expands and raises a question -- a real answer,
    not an empty result an operator should read as an error."""
    assert cli.main(["policy", "show", "--source", "MED-RT",
                     "--code", "N0000000404"]) == 0
    assert "no decision" in capsys.readouterr().out.lower()


def test_policy_show_needs_both_halves_of_the_key_or_neither():
    """--code alone cannot identify a class: `source` is half the natural key, and
    means "who defines the class" rather than "who ruled on it"."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["policy", "show", "--code", CODE])
