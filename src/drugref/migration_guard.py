"""Turn "I could not read that relation" into a diagnosis worth acting on (issue 122).

WHAT WAS WRONG WITH THE FOUR GUARDS THIS REPLACES. Each caught psycopg's
`UndefinedTable` and answered with ONE cause, stated as fact:

    "drugref.curated_unrankable_severity is missing: this database predates db/038 ...
     Run `drugref migrate` and re-run status."

42P01 has more causes than a pending migration -- a wrong `search_path`, a role without
USAGE on schema `drugref`, a view dropped by a manual repair, or a BASE TABLE of the
view being gone -- and the guard asserted the one it imagined rather than the one it
could confirm.

⇒ THE WORST CASE IS SELF-REFERENTIAL, and it is why this module exists rather than a
one-line message tweak. "A restore that lost the vocabulary table" is one of the three
faults `curated_unrankable_severity` was written to REPORT. If `severity_kind` goes
missing, the view goes with it (`DROP ... CASCADE`, or a partial restore), so status
raises `UndefinedTable` and tells the operator the database predates db/038 and to run
`drugref migrate`. Migrations are ledger-backed and db/038 is recorded applied, so that
command does NOTHING. Status then prints the same sentence again. The operator is in a
closed loop, confidently misdiagnosed by the very block whose purpose is diagnosing that
exact fault.

⇒ AND PROBING THE RELATION ALONE DOES NOT CLOSE THE LOOP. In the CASCADE case the view
really is absent, so "absent" still reads as "behind on migrations". THE LEDGER IS THE
DISCRIMINATOR: absent *while its migration is recorded applied* means DROPPED, and
nothing else in the schema says so.

THE WORDING LIVES HERE, ONCE, FOR ALL FIVE CALLERS. Two `cli.py` blocks, two in
`cli_status.py` and the clinician path in `cli_interactions.py` differ only in which
relation they read, which migration ships it, and what an operator loses meanwhile. Four
states x five call sites written out by hand would be twenty sentences with twenty
chances to disagree -- the shape this project has already paid for repeatedly (db/006's
vocabulary, db/037's ordering rule written twice, and issue 116 when the two drifted).
"""
from dataclasses import dataclass
from typing import NoReturn

import psycopg

from drugref import db


@dataclass(frozen=True)
class Diagnosis:
    """What was CONFIRMED about the relations a guarded block could not read.

    Facts only, no voice: this record says what is true of the database, and
    `guard_message` turns it into a sentence. Splitting them is what lets the four
    messages be tested without constructing four broken schemas -- and a message nobody
    can reach is a message nobody has read.

    `absent` holds the relations that really are gone, of those asked about. EMPTY IS
    INFORMATIVE, not a null result: it means the guard's assumed cause is FALSE and the
    read failed for some other reason.

    `migration_applied` is the ledger's answer, and it is the only thing separating "not
    migrated yet" from "dropped after migrating".

    `detail` is Postgres's own primary message -- `relation "drugref.severity_kind" does
    not exist` -- which named the real missing relation all along and reached nobody,
    because `raise ... from exc` preserves `__cause__` and `cli.main` prints only the
    outer message.
    """
    absent: tuple[str, ...]
    migration_applied: bool
    detail: str


def guard_message(diagnosis: Diagnosis, *, migration: str, consequence: str) -> str:
    """The operator's sentence for one unreadable relation. PURE -- no connection.

    `migration` is the numeric prefix as written in `db/` ("038"), not a filename: the
    descriptive half of `038_effective_rank_and_the_class_rule_count.sql` is prose that
    a later round may reword, and a guard that quoted it in full would be a second copy
    of a name with no test holding the two together.

    `consequence` is what the OPERATOR loses while this block cannot run, supplied by
    the caller because only the caller knows. It is deliberately not derived from the
    relation name: "the class-grain detector views are missing" says nothing about what
    goes unreported, and an operator triaging at 3am needs the second half.
    """
    if not diagnosis.absent:
        if diagnosis.migration_applied:
            # THE GUARD'S OWN ASSUMPTION, REFUTED. Everything it would have said is now
            # known to be false, so it says that and gets out of the way: whatever is
            # broken, `detail` names it and this module does not know what it is.
            return (
                f"a read failed although db/{migration} is recorded applied AND every "
                f"relation it reads exists, so this is NOT a missing migration -- "
                f"`drugref migrate` would do nothing. Look at a wrong search_path, a "
                f"role without USAGE on schema drugref, or a base table dropped from "
                f"under a view that still stands. {consequence.capitalize()} until it "
                f"is fixed. Postgres said: {diagnosis.detail}")
        # THE `UndefinedColumn` SHAPE: the view is there, one migration short of the
        # columns the reader selects. db/035 widened a view with `subject_class` and
        # db/038 added `effective_rank`; such a database fails one COLUMN short, not one
        # relation short, so the relation probe finds everything present and only the
        # ledger explains the failure.
        return (
            f"the relations exist but db/{migration} is NOT recorded applied, so they "
            f"are an older shape than this code reads -- a column it selects is "
            f"missing. {consequence.capitalize()} meanwhile. Run `drugref migrate` "
            f"and re-run. "
            f"Postgres said: {diagnosis.detail}")

    names = ", ".join(diagnosis.absent)
    if diagnosis.migration_applied:
        # THE CLOSED LOOP, BROKEN. Naming the no-op is the entire point: an operator
        # told "run drugref migrate" runs it, sees "migrations applied", re-runs it, and
        # reads this same sentence -- which is how a diagnosis becomes a loop.
        return (
            f"{names} is DROPPED, not pending: db/{migration} IS recorded applied in "
            f"drugref.schema_migration and the relation is gone anyway, so `drugref "
            f"migrate` is a NO-OP here and re-running it will print this again. "
            f"Something dropped it after the migration ran -- a manual repair, a "
            f"partial restore, or a `DROP ... CASCADE` on a base table that took the "
            f"view with it. {consequence.capitalize()} until it is restored. Postgres "
            f"said: {diagnosis.detail}")
    return (
        f"{names} is missing: this database predates db/{migration}, so {consequence}. "
        f"Run `drugref migrate` and re-run. Postgres said: {diagnosis.detail}")


def raise_missing(conn: psycopg.Connection, exc: psycopg.Error, *,
                  relations: tuple[str, ...], migration: str,
                  consequence: str) -> NoReturn:
    """Diagnose, then raise the `RuntimeError` `cli.main` renders without a traceback.

    THE ROLLBACK IN `db.missing_relations` IS LOAD-BEARING, and doing this without it is
    worse than not doing it at all: the failed statement has ABORTED the transaction, so
    the very probe meant to improve the diagnosis would itself raise
    `InFailedSqlTransaction` -- replacing a wrong-but-readable sentence with an
    unrelated psycopg traceback from inside the guard.

    `NoReturn`, so a caller writing `raise_missing(...)` and a caller writing `raise
    raise_missing(...)` cannot disagree about whether this returns.
    """
    absent = db.missing_relations(conn, *relations)
    applied = db.migration_applied(conn, migration)
    raise RuntimeError(guard_message(
        Diagnosis(absent=absent, migration_applied=applied,
                  detail=(exc.diag.message_primary or str(exc)).strip()),
        migration=migration, consequence=consequence)) from exc
