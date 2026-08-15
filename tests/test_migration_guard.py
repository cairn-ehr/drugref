"""What a guard may assert about a relation it could not read (issue 122).

THE DEFECT THIS PINS. Four blocks in `cli.py` and `cli_status.py` caught psycopg's
`UndefinedTable` and answered with ONE cause stated as fact -- "this database predates
db/038 ... Run `drugref migrate`". 42P01 has more causes than a pending migration: a
wrong `search_path`, a role without USAGE on the schema, a view dropped by a manual
repair, or a BASE TABLE of the view being gone.

AND THE WORST CASE IS SELF-REFERENTIAL, which is what makes this worth a module. "A
restore that lost the vocabulary table" is one of the three faults
`curated_unrankable_severity` was written to REPORT. Drop `severity_kind` and the view
goes with it (`DROP ... CASCADE`, or a partial restore) -> UndefinedTable -> the operator
is told the database predates db/038 and should run `drugref migrate`. Migrations are
ledger-backed and db/038 is recorded applied, so that command is a NO-OP. Status repeats
the same sentence. The operator is in a closed loop, confidently misdiagnosed by the very
block whose purpose is diagnosing that fault.

PROBING THE RELATION ALONE DOES NOT CLOSE IT, and that is the finding this file exists to
hold: in the CASCADE case the view really is absent, so absence-alone still reads as
"behind on migrations". THE LEDGER IS THE ONLY DISCRIMINATOR -- absent while its migration
is recorded applied means DROPPED, and nothing else does.

PURE, SO THE WORDING IS TESTED WITHOUT A DATABASE. Two booleans give four states and each
needs a different sentence; a test that had to construct four broken schemas to reach
them would be written once and never extended.
"""
import pytest

from drugref import migration_guard

_CONSEQUENCE = ("a curated ruling whose severity drugref cannot rank would go "
                "unreported")
_DETAIL = 'relation "drugref.severity_kind" does not exist'
_RELATION = "drugref.curated_unrankable_severity"


def _message(*, absent: bool, applied: bool) -> str:
    """One of the four states, with everything else held constant."""
    return migration_guard.guard_message(
        migration_guard.Diagnosis(
            absent=(_RELATION,) if absent else (),
            migration_applied=applied,
            detail=_DETAIL),
        migration="038", consequence=_CONSEQUENCE)


def test_absent_and_the_migration_not_applied_is_the_ordinary_case():
    """A DEPLOYMENT THAT HAS PULLED THE CODE AND NOT YET MIGRATED -- the common one.

    This is the case the original message assumed universally, and it must keep saying
    exactly what it said: run the migration.
    """
    message = _message(absent=True, applied=False)
    assert "drugref migrate" in message
    assert _RELATION in message
    assert _CONSEQUENCE in message


def test_absent_while_the_migration_is_recorded_applied_says_dropped_not_pending():
    """THE CLOSED LOOP, AND THE ONE ASSERTION THAT MATTERS IN THIS FILE.

    The ledger records db/038 applied and the view is gone anyway: no migration can
    restore it, so telling the operator to run one sends them round the loop again. The
    message must name the state (dropped) and must NOT prescribe the no-op.
    """
    message = _message(absent=True, applied=True)
    assert "DROPPED" in message
    assert "no-op" in message.lower(), (
        "the operator must be told the command they would reach for does nothing -- "
        "that is the difference between a diagnosis and the loop")
    assert "Run `drugref migrate`" not in message, (
        "prescribing the no-op is the defect: db/038 is recorded applied, so migrate "
        "has nothing to do and status will print this same sentence again")


def test_present_and_applied_refuses_to_blame_a_migration_at_all():
    """THE RELATION IS THERE AND SO IS ITS MIGRATION, so the fault is something else.

    A wrong `search_path`, a role without USAGE, a base table gone from underneath a
    view that still exists. The guard has confirmed its assumed cause is FALSE, and the
    only honest thing left is to say so and hand over what Postgres said.
    """
    message = _message(absent=False, applied=True)
    assert "not a missing migration" in message.lower()
    assert _DETAIL in message


def test_present_but_the_migration_is_not_applied_is_the_undefined_column_case():
    """`UndefinedColumn`: the view exists, one migration short of the shape read.

    db/035 widened a view with `subject_class` and db/038 added `effective_rank`; a
    database holding the older view fails one COLUMN short, not one relation short. The
    relation probe finds it present, and only the ledger says why the read still failed.
    """
    message = _message(absent=False, applied=False)
    assert "drugref migrate" in message
    assert "038" in message


@pytest.mark.parametrize("absent,applied", [(True, False), (True, True),
                                            (False, True), (False, False)])
def test_every_state_carries_what_postgres_said(absent: bool, applied: bool):
    """ISSUE 122'S SECOND HALF, and it is not optional in any of the four states.

    `raise ... from exc` LOOKS like it preserves the cause, and `cli.main` prints only
    `f"drugref: {exc}"` -- `__cause__` is never rendered, so Postgres's own
    `relation "drugref.severity_kind" does not exist`, the one string that resolves this
    in five seconds, reached nobody. Every branch must carry it or the branch that
    forgets is the one an operator meets.
    """
    assert _DETAIL in _message(absent=absent, applied=applied)
