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

from drugref import cli, db, interactions

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


def test_policy_record_refuses_a_blank_rationale_and_names_the_flag(committed, capsys):
    """IMPORTANT 3: a blank string satisfies argparse's `required=True` (presence,
    not content) AND db/010's NOT NULL (no non-blank CHECK), so without this guard
    the CLI could write a row test_every_seeded_root_carries_a_reviewable_justification
    asserts cannot exist -- and the append-only floor would make it uncorrectable.
    Whitespace-only, not merely empty, to prove the guard strips before checking."""
    with psycopg.connect(committed) as c:
        before = interactions.decision_history(c, "MED-RT", CODE)
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "allow", "--class-name", NAME, "--rationale", "   ",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 2
    err = capsys.readouterr().err
    assert "--rationale" in err
    assert "Traceback" not in err
    with psycopg.connect(committed) as c:
        after = interactions.decision_history(c, "MED-RT", CODE)
    assert after == before                            # nothing written


def test_policy_record_an_unrecognised_decision_exits_two_without_a_traceback(
        committed, capsys):
    """IMPORTANT 4: an unrecognised --decision reaches db/027's CHECK as
    psycopg.errors.CheckViolation, which main did not used to catch -- printing a
    raw traceback and exiting 1, unlike every other operator error on this surface.
    Not asserting the message's exact wording: it comes from postgres, and pinning
    it here would be a second copy of the CHECK's text to go stale."""
    with psycopg.connect(committed) as c:
        before = interactions.decision_history(c, "MED-RT", CODE)
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "Deny", "--class-name", NAME, "--rationale", "r",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    with psycopg.connect(committed) as c:
        after = interactions.decision_history(c, "MED-RT", CODE)
    assert after == before                            # nothing written


def test_an_unrecognised_decision_is_told_what_the_constraint_accepts(
        committed, capsys):
    """"violates check constraint class_expansion_policy_decision" names the rule and
    not one thing to do about it -- an operator would have to open a migration to learn
    that `Deny` should have been `deny`.

    THE VALUES ARE QUOTED FROM THE CATALOGUE, NOT RESTATED IN PYTHON. Hand-writing
    "one of deny, allow, withdrawn" into this message would be the second-vocabulary
    defect db/006 exists to warn about; pg_get_constraintdef keeps db/027's CHECK the
    single home AND makes the message actionable, because what it prints IS the CHECK.
    So this asserts the values are present without being a copy of them: it reads them
    from the constraint the same way the message does.
    """
    with psycopg.connect(committed) as c:
        expected = db.constraint_definition(
            c, "class_expansion_policy", "class_expansion_policy_decision")
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "Deny", "--class-name", NAME, "--rationale", "r",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 2
    assert expected in capsys.readouterr().err


def test_the_connection_survives_a_rejected_write(committed, capsys):
    """_write ROLLS BACK before reading the catalogue, and this is what would fail if
    it did not: a CHECK violation aborts the transaction, so the pg_get_constraintdef
    lookup that makes the message actionable would itself raise InFailedSqlTransaction
    -- turning a tidy rejection into the traceback the whole guard exists to prevent.

    Asserted through the second line of output rather than by inspecting the
    connection: that line only exists if the catalogue read succeeded after the abort.
    """
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "nonsense", "--class-name", NAME, "--rationale", "r",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 2
    err = capsys.readouterr().err
    assert "that constraint is" in err
    assert "InFailedSqlTransaction" not in err and "Traceback" not in err


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


def test_policy_withdraw_refuses_a_blank_rationale_and_names_the_flag(committed, capsys):
    """IMPORTANT 3, the withdraw arm: the same blank-after-strip guard applies here,
    since withdraw's carry-forward would otherwise propagate a blank straight into
    the audit trail exactly as record's would."""
    with psycopg.connect(committed) as c:
        before = interactions.decision_history(c, "MED-RT", CODE)
    assert cli.main([
        "policy", "withdraw", "--source", "MED-RT", "--code", CODE,
        "--rationale", "", "--reviewed-by", "operator",
        "--reviewed-against", "2026.07.06"]) == 2
    err = capsys.readouterr().err
    assert "--rationale" in err
    assert "Traceback" not in err
    with psycopg.connect(committed) as c:
        after = interactions.decision_history(c, "MED-RT", CODE)
    assert after == before                            # nothing written


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
    """Absent means UNREVIEWED, which expands -- a real answer, not an empty result an
    operator should read as an error.

    THE QUESTION IS HEDGED, and this is the half worth pinning. `withdraw`'s message
    reasons that "raises a question" does not always follow, because
    gap_unreviewed_expansion_root ALSO requires a substance_class row for the code --
    and then `show` stated it flatly. N0000000404 is exactly that case: no class of
    that code exists, so nothing is raised, and the unhedged sentence was false for the
    very input this test drives. "expands" is unconditional and stays unconditional.
    """
    assert cli.main(["policy", "show", "--source", "MED-RT",
                     "--code", "N0000000404"]) == 0
    out = capsys.readouterr().out.lower()
    assert "no decision" in out
    assert "expands by default" in out
    # The claim about the worklist must arrive as a conditional, not an assertion.
    assert "if a loaded release defines the class" in out


def test_policy_show_refuses_a_blank_half_of_the_key(committed, capsys):
    """A blank pair passes _Parser's both-or-neither check -- '' is present -- and then
    matched nothing, so `show` printed its no-decision answer about a class that does
    not exist, at exit 0. Nothing is corrupted on a read path; being told something
    false is the part worth refusing, and it is the same guard the writers use."""
    assert cli.main(["policy", "show", "--source", "MED-RT", "--code", "  "]) == 2
    err = capsys.readouterr().err
    assert "--code" in err
    assert "Traceback" not in err


def test_policy_show_needs_both_halves_of_the_key_or_neither():
    """--code alone cannot identify a class: `source` is half the natural key, and
    means "who defines the class" rather than "who ruled on it"."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["policy", "show", "--code", CODE])


def test_policy_show_needs_both_halves_of_the_key_or_neither_source_arm():
    """The OTHER half of the XOR. _Parser.parse_args checks
    `(args.source is None) != (args.code is None)`, which is symmetric in source and
    code -- but a test that only ever drives the --code-alone arm would stay green if
    that condition were inverted to `==`, since --code alone would then be the one
    case still refused while --source alone silently fell through to the global
    `policy show` listing instead of erroring. Both arms have to be pinned for the
    XOR to mean what the docstring says."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["policy", "show", "--source", "MED-RT"])
