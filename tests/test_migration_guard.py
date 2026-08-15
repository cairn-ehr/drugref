"""What a guard may assert about a relation it could not read (issue 122).

THE DEFECT THIS PINS. Four blocks in `cli.py` and `cli_status.py` caught psycopg's
`UndefinedTable` and answered with ONE cause stated as fact -- "this database predates
db/038 ... Run `drugref migrate`". 42P01 has more causes than a pending migration: a
view dropped by a manual repair, a partial restore, or a `DROP ... CASCADE` on a base
table that took the view with it.

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
import psycopg
import pytest

from drugref import migration_guard

_CONSEQUENCE = ("a curated ruling whose severity drugref cannot rank would go "
                "unreported, and such a ruling DISCARDS every real grade for its pair")
_DETAIL = 'relation "drugref.severity_kind" does not exist'
_RELATION = "drugref.curated_unrankable_severity"
_MIGRATION = "038"


def _message(*, absent: bool, applied: bool) -> str:
    """One of the four states, with everything else held constant."""
    return migration_guard.guard_message(
        migration_guard.Diagnosis(
            absent=(_RELATION,) if absent else (),
            migration_applied=applied,
            detail=_DETAIL),
        migration=_MIGRATION, consequence=_CONSEQUENCE)


def test_absent_and_the_migration_not_applied_is_the_ordinary_case():
    """A DEPLOYMENT THAT HAS PULLED THE CODE AND NOT YET MIGRATED -- the common one.

    This is the case the original message assumed universally, and it must keep saying
    exactly what it said: run the migration.

    ⇒ ASSERTS A STRING UNIQUE TO THIS BRANCH. An earlier version of this file matched
    `"drugref migrate"`, which appears in ALL FOUR messages -- twice as the prescription
    and twice as "`drugref migrate` would do nothing" / "is a NO-OP here". It therefore
    discriminated nothing: swapping this branch's wording for the DROPPED branch's
    shipped green, in the harmful direction, telling an operator NOT to run the
    migration that would in fact fix them.
    """
    message = _message(absent=True, applied=False)
    assert f"predates db/{_MIGRATION}" in message
    assert "Run `drugref migrate` and re-run" in message
    assert _RELATION in message
    assert _CONSEQUENCE in message
    assert "DROPPED" not in message and "NO-OP" not in message


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

    The guard has confirmed its assumed cause is FALSE, and the only honest thing left
    is to say so and hand over what Postgres said. What it must NOT do is offer a
    replacement guess -- see `test_the_refuted_branch_names_no_cause_it_cannot_confirm`,
    which is why the three causes an earlier version listed here are gone.
    """
    message = _message(absent=False, applied=True)
    assert "not a missing migration" in message.lower()
    assert _DETAIL in message


def test_present_but_the_migration_is_not_applied_is_the_undefined_column_case():
    """`UndefinedColumn`: the view exists, one migration short of the shape read.

    db/035 widened a view with `subject_class` and db/038 added `effective_rank`; a
    database holding the older view fails one COLUMN short, not one relation short. The
    relation probe finds it present, and only the ledger says why the read still failed.

    THE UNIQUE STRING IS "an older shape", for the reason the first test states: both
    `"drugref migrate"` and `"038"` appear in all four messages.
    """
    message = _message(absent=False, applied=False)
    assert "an older shape than this code reads" in message
    assert f"db/{_MIGRATION} is NOT recorded applied" in message
    assert "Run `drugref migrate`" in message


def test_the_consequence_keeps_the_emphasis_its_caller_wrote():
    """`str.capitalize()` LOWER-CASES THE REMAINDER, and three branches used to call it.

    Callers write in this project's house voice, where the load-bearing word is shouted:
    `cli_status` warns that an unrankable ruling "outranks and DISCARDS every real grade
    for its pair", `cli_interactions` that a severity "would reach a client as a NULL
    rank". Three of the four branches rendered those as "discards" and "null rank" --
    flattening the emphasis in exactly the sentences an operator most needs to read
    carefully, and doing it in the module whose thesis is that wording lives in one
    place so it cannot disagree with itself.

    ALL FOUR STATES, because the one branch that never capitalised is the only one the
    old suite asserted `_CONSEQUENCE` against, so the defect was untestable by
    construction.
    """
    for absent in (True, False):
        for applied in (True, False):
            message = _message(absent=absent, applied=applied)
            assert "DISCARDS" in message, (
                f"absent={absent} applied={applied} flattened the caller's emphasis: "
                f"{message}")


def test_three_absent_relations_read_as_plural():
    """"A, B, C is missing" is the sentence a reader stops trusting.

    The class-grain site passes THREE view names, deliberately -- they ship in one
    migration, so two present and one absent means a manual repair rather than an
    upgrade. The message that says so must agree with itself grammatically.
    """
    message = migration_guard.guard_message(
        migration_guard.Diagnosis(absent=("a.x", "a.y", "a.z"),
                                  migration_applied=False, detail=_DETAIL),
        migration=_MIGRATION, consequence=_CONSEQUENCE)
    assert "a.x, a.y, a.z are missing" in message


def test_the_refuted_branch_names_no_cause_it_cannot_confirm():
    """⇒ THIS MODULE'S FOUNDING DEFECT, RELOCATED INTO THE BRANCH THAT ADMITS DEFEAT.

    An earlier version of the present-and-applied message told the operator to "Look at
    a wrong search_path, a role without USAGE on schema drugref, or a base table dropped
    from under a view that still stands". NONE of the three can produce this state:

      * every guarded read is schema-qualified, so `search_path` cannot resolve it
        wrongly and cannot raise 42P01 for it;
      * a role without USAGE raises 42501, which `WRONG_SHAPE` does not catch, so the
        guard is never entered that way;
      * Postgres refuses to drop a base table while a view depends on it, and `CASCADE`
        takes the view too -- which lands in the ABSENT branch, not this one.

    A guard may not assert a cause it has not confirmed. That is the whole of issue 122,
    and it applies to this module's own prose.
    """
    message = _message(absent=False, applied=True)
    assert "search_path" not in message
    assert "USAGE" not in message
    assert "base table" not in message
    assert "cannot narrow it further" in message, (
        "the honest answer is that Postgres's own message is the diagnosis")


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


# ============================================================================
# the fifth state: the probes themselves failed (issue 122, review round)
# ============================================================================


def test_a_failed_probe_leads_with_the_original_failure_not_its_own():
    """⇒ A PROBE MAY NOT OUTRANK THE ERROR IT WAS CALLED TO EXPLAIN.

    `raise_missing` reads the database twice before it can speak, and the second read --
    `drugref.schema_migration` -- is the one most likely to be absent when a guard
    fires. `db.apply_migrations` creates the ledger with `CREATE TABLE IF NOT EXISTS`
    rather than from any `db/*.sql`, so a database bootstrapped by replaying the SQL by
    hand, or restored selectively, has every view and no ledger.

    Unguarded, that raised `UndefinedTable` from INSIDE the guard, and the surviving
    exception named `schema_migration` -- not the relation the operator was reading --
    while `cli.main`, which catches only `RuntimeError`, rendered no sentence at all.
    The operator got a double traceback pointing at the one relation that was not their
    problem. This message must therefore lead with the ORIGINAL failure.
    """
    message = migration_guard.undiagnosed_message(
        detail=_DETAIL,
        probe_detail='relation "drugref.schema_migration" does not exist',
        consequence=_CONSEQUENCE)
    assert _DETAIL in message, "the failure the operator actually hit must survive"
    assert "schema_migration" in message
    assert "could not diagnose" in message
    assert "DISCARDS" in message, "the caller's emphasis survives here too"


def test_raise_missing_refuses_to_diagnose_without_probing_anything():
    """Empty `relations` would report "every relation it reads exists" -- unchecked.

    `db.missing_relations(conn)` returns `()`, which `guard_message` reads as "the
    assumed cause is refuted", so a caller that passed nothing would have the operator
    told a fact nobody established. That is this module's founding defect one level up,
    and it belongs in the suite rather than in production prose.
    """
    with pytest.raises(ValueError, match="at least one relation"):
        migration_guard.raise_missing(
            None, psycopg.errors.UndefinedTable("x"),
            relations=(), migration="038", consequence=_CONSEQUENCE)


def test_wrong_shape_catches_both_siblings():
    """The tuple five call sites used to spell for themselves, two of them wrongly.

    `UndefinedColumn` is a SIBLING of `UndefinedTable` under `ProgrammingError`, not a
    subclass. Three sites caught both and TWO caught only `UndefinedTable`, which made
    `guard_message`'s "relations exist but the migration is not applied" branch
    unreachable from those two -- so the next migration widening either view would have
    dropped a raw psycopg traceback on the operator. One tuple cannot disagree with
    itself.
    """
    assert psycopg.errors.UndefinedTable in migration_guard.WRONG_SHAPE
    assert psycopg.errors.UndefinedColumn in migration_guard.WRONG_SHAPE
    assert not issubclass(psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable)


class _LedgerlessConn:
    """A database with the views and NO `drugref.schema_migration`.

    NOT AN EXOTIC SHAPE. `db.apply_migrations` creates the ledger with
    `CREATE TABLE IF NOT EXISTS` rather than from any `db/*.sql` (db.py says so
    outright), so a database bootstrapped by replaying the SQL by hand with `psql -f`,
    or restored without that one table, is exactly this.

    ⇒ AND IT CANNOT BE BUILT ON THE SESSION FIXTURE, for the reason issue 122's own
    probe creates: `db.missing_relations` rolls back before probing, so a `DROP TABLE
    drugref.schema_migration` inside the rolled-back `conn` fixture is UNDONE by the
    guard itself before `migration_applied` ever runs. Committing it would break every
    test after this one. A stub is where this state can be reached honestly.
    """

    def rollback(self):
        """`missing_relations` rolls back first; the aborted transaction is real."""

    def execute(self, sql, params=None):
        if "schema_migration" in sql:
            raise psycopg.errors.UndefinedTable(
                'relation "drugref.schema_migration" does not exist')
        self._sql = sql
        return self

    def fetchone(self):
        return ("an_oid",)          # to_regclass: the view itself is present


def test_a_failed_ledger_probe_does_not_replace_the_diagnosis_with_its_own(monkeypatch):
    """⇒ THE PROBE OUTRANKING THE ERROR IT WAS CALLED TO EXPLAIN, end to end.

    `raise_missing` reads the database twice before it can speak. `missing_relations`
    was hardened against this (it rolls back first, and the docstring argues at length
    that an exception from inside the guard is worse than the defect it replaced) and
    the very next line, `migration_applied`, was left bare.

    Unguarded, this raised `UndefinedTable` from inside the `except` block, so the
    SURVIVING exception named `drugref.schema_migration` -- not the relation the
    operator was reading -- and `cli.main`, which catches only `RuntimeError`, rendered
    no sentence at all. The operator got a double traceback pointing at the one relation
    that was not their problem.
    """
    original = psycopg.errors.UndefinedTable(
        'relation "drugref.curated_unrankable_severity" does not exist')

    with pytest.raises(RuntimeError) as raised:
        migration_guard.raise_missing(
            _LedgerlessConn(), original, relations=(_RELATION,),
            migration=_MIGRATION, consequence=_CONSEQUENCE)

    message = str(raised.value)
    assert "could not diagnose" in message
    assert "schema_migration" in message, "name what stopped the diagnosis"
    assert _RELATION in message, (
        "the ORIGINAL failure is what the operator came for; the probe's own failure "
        "is context, not the headline")
    assert isinstance(raised.value.__cause__, psycopg.errors.UndefinedTable)


def test_raise_missing_refuses_a_bare_string_of_relations():
    """A MISSING TRAILING COMMA IS SILENT, AND ITS OUTPUT IS ABSURD.

    Four of the five call sites pass a singleton tuple. `relations=("drugref.x")` is
    not a tuple -- it is the string -- and `db.missing_relations(conn, *relations)` then
    probes it one CHARACTER at a time, finds every one absent, and tells the operator
    that `d, r, u, g, r, e, f, ., x are DROPPED, not pending`.

    Nothing else would catch it: this project runs no type checker (issue 88), `ruff`
    is configured for E/F/W only, and the message is generated rather than asserted at
    four of the five sites.
    """
    with pytest.raises(TypeError, match="trailing comma"):
        migration_guard.raise_missing(
            None, psycopg.errors.UndefinedTable("x"),
            relations=_RELATION, migration=_MIGRATION, consequence=_CONSEQUENCE)
