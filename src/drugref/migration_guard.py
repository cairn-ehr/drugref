"""Turn "I could not read that relation" into a diagnosis worth acting on (issue 122).

WHAT WAS WRONG WITH THE FOUR GUARDS THIS REPLACES. Each caught psycopg's
`UndefinedTable` and answered with ONE cause, stated as fact:

    "drugref.curated_unrankable_severity is missing: this database predates db/038 ...
     Run `drugref migrate` and re-run status."

42P01 has more causes than a pending migration -- a view dropped by a manual repair, a
partial restore, or a `DROP ... CASCADE` that took the view with its base table -- and
the guard asserted the one it imagined rather than the one it could confirm.

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

⇒ AND THE PROBES THEMSELVES MAY NOT OUTRANK THE ERROR THEY DIAGNOSE. Both of them read
the database, so both can fail -- most reachably `drugref.schema_migration`, which
`db.apply_migrations` creates with `CREATE TABLE IF NOT EXISTS` rather than any
`db/*.sql`, so a database bootstrapped by replaying the SQL by hand has every view and
no ledger. An unguarded probe then raises from inside the guard and the surviving
traceback names `schema_migration` -- not the relation the operator was actually reading
-- while `cli.main` catches only `RuntimeError` and so never renders it as a sentence at
all. `raise_missing` therefore treats a failed probe as a FIFTH state: it says the
diagnosis could not be completed and hands back what Postgres said about the ORIGINAL
failure, which is strictly the thing the operator came for.

THE WORDING LIVES HERE, ONCE, FOR EVERY CALLER. They differ only in which relation they
read, which migration ships it, and what an operator loses meanwhile. Five states
written out by hand at each site would be five sentences per site with as many chances
to disagree -- the shape this project has already paid for many times (db/006's
vocabulary, db/037's ordering rule written twice, issue 116 when the two drifted).
`guarded` owns
the exception tuple for the same reason: which psycopg errors mean "this database is the
wrong shape" is one fact, and it was already written once per site with two of them
disagreeing.

⇒ AND THE COUNT OF THOSE SITES IS DELIBERATELY NOT WRITTEN DOWN HERE. This paragraph
said "ALL FIVE CALLERS. Two `cli.py` blocks, two in `cli_status.py` and the clinician
path in `cli_interactions.py`" -- a hand-listed tally of a population that grows, which
was already wrong at six when db/054 arrived to make it seven. That is the same defect
db/053 removed from db/025's view comment and the same one this project has now found
repeatedly; `grep -rn "migration_guard.guarded(" src/` answers it, and cannot go stale.
"""
import contextlib
from dataclasses import dataclass
from typing import NoReturn

import psycopg

from drugref import db

# WHAT "THE DATABASE IS THE WRONG SHAPE" LOOKS LIKE TO PSYCOPG, in one place. db/035
# wrote the standing rule the hard way: a migration widening a view a guarded block
# reads must widen that block's exception tuple in the same commit. That rule was prose,
# and prose lost -- of the five call sites this module replaced, THREE caught both
# classes and TWO caught only `UndefinedTable`, so the "relations exist but the
# migration is not applied" branch below was unreachable from those two. A tuple written
# once cannot disagree with itself.
#
# `UndefinedColumn` is a SIBLING of `UndefinedTable` under `ProgrammingError`, not a
# subclass, so catching the latter alone misses every database holding the view in an
# older shape -- which is every deployment between pulling this code and migrating.
WRONG_SHAPE = (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn)


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


def _said(exc: psycopg.Error) -> str:
    """What Postgres actually said, as ONE line fit to splice into a sentence.

    `message_primary` FIRST, and it is the half of issue 122 that reached nobody:
    `relation "drugref.severity_kind" does not exist` named the real missing relation
    all along, but `raise ... from exc` only sets `__cause__` and `cli.main` prints the
    outer message alone. `str(exc)` is the fallback for a client-side error, which
    carries no `diag` fields.

    WHITESPACE IS COLLAPSED because that fallback is multi-line: psycopg renders an
    error as its primary message plus `LINE 1: ...` and a caret row, and splicing three
    lines into the middle of a paragraph breaks the sentence around it. Collapsing keeps
    every word and costs only the alignment of a caret nobody can use here anyway.
    """
    return " ".join((exc.diag.message_primary or str(exc)).split())


def _opening(consequence: str) -> str:
    """`consequence` with its first letter raised, and NOTHING ELSE TOUCHED.

    NOT `str.capitalize()`, which lower-cases the whole remainder. Callers write in this
    project's house voice, where the load-bearing word is shouted -- `cli_status` warns
    that an unrankable ruling "outranks and DISCARDS every real grade for its pair", and
    `cli_interactions` that a severity "would reach a client as a NULL rank". Three of
    the four branches used to render those as "discards" and "null rank", flattening the
    emphasis in exactly the sentences an operator most needs to read carefully.
    """
    return consequence[:1].upper() + consequence[1:]


def guard_message(diagnosis: Diagnosis, *, migration: str, consequence: str) -> str:
    """The operator's sentence for one unreadable relation. PURE -- no connection.

    `migration` is the numeric prefix as written in `db/` ("038"), not a filename: the
    descriptive half of `038_effective_rank_and_the_class_rule_count.sql` is prose that
    a later round may reword, and a guard that quoted it in full would be a second copy
    of a name with no test holding the two together. `db.migration_applied` REJECTS
    anything that is not three digits, because "38" for "038" matches no ledger row and
    so restores the exact closed loop this module exists to break.

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
            #
            # ⇒ AND IT NAMES NO CAUSES, which is a correction rather than an omission.
            # An earlier version listed three -- a wrong `search_path`, a role without
            # USAGE on schema drugref, a base table dropped from under a live view --
            # and NONE of them can produce the state this branch describes. Every
            # guarded read is schema-qualified, so `search_path` cannot resolve it
            # wrongly; a missing USAGE raises 42501, which `WRONG_SHAPE` does not catch;
            # and Postgres refuses to drop a base table while a view depends on it, so
            # `CASCADE` takes the view too and lands in the ABSENT branch instead. The
            # branch that advertises humility was asserting causes it had not confirmed
            # -- this module's founding defect, relocated. What is left is the truth:
            # the assumption is refuted, and Postgres's own sentence is the lead.
            return (
                f"a read failed although db/{migration} is recorded applied AND every "
                f"relation it reads exists, so this is NOT a missing migration -- "
                f"`drugref migrate` would do nothing. drugref cannot narrow it further "
                f"from here; Postgres's own message below is the diagnosis. "
                f"{_opening(consequence)} until it is fixed. Postgres said: "
                f"{diagnosis.detail}")
        # THE `UndefinedColumn` SHAPE: the view is there, one migration short of the
        # columns the reader selects. db/035 widened `curated_target_unresolved` with
        # `subject_class` and db/038 added `effective_rank` to
        # `curated_ddi_pair_effective`; such a database fails one COLUMN short, not one
        # relation short, so the relation probe finds everything present and only the
        # ledger explains the failure.
        return (
            f"the relations exist but db/{migration} is NOT recorded applied, so they "
            f"are an older shape than this code reads -- a column it selects is "
            f"missing. {_opening(consequence)} meanwhile. Run `drugref migrate` "
            f"and re-run. "
            f"Postgres said: {diagnosis.detail}")

    names = ", ".join(diagnosis.absent)
    # PLURAL AGREEMENT, because `relations` is a tuple and the class-grain site passes
    # THREE names: "A, B, C is missing" is the sentence a reader stops trusting.
    is_are = "is" if len(diagnosis.absent) == 1 else "are"
    if diagnosis.migration_applied:
        # THE CLOSED LOOP, BROKEN. Naming the no-op is the entire point: an operator
        # told "run drugref migrate" runs it, sees "migrations applied", re-runs it, and
        # reads this same sentence -- which is how a diagnosis becomes a loop.
        return (
            f"{names} {is_are} DROPPED, not pending: db/{migration} IS recorded "
            f"applied in drugref.schema_migration and it is gone anyway, so `drugref "
            f"migrate` is a NO-OP here and re-running it will print this again. "
            f"Something dropped it after the migration ran -- a manual repair, a "
            f"partial restore, or a `DROP ... CASCADE` on a base table that took the "
            f"view with it. {_opening(consequence)} until it is restored. Postgres "
            f"said: {diagnosis.detail}")
    return (
        f"{names} {is_are} missing: this database predates db/{migration}, so "
        f"{consequence}. Run `drugref migrate` and re-run. Postgres said: "
        f"{diagnosis.detail}")


def undiagnosed_message(*, detail: str, probe_detail: str, consequence: str) -> str:
    """When the PROBES failed too: say so, and lead with the original failure.

    THE ONE RULE THIS BRANCH EXISTS TO KEEP: a probe may never outrank the error it was
    called to explain. Without it, a database whose `drugref.schema_migration` is absent
    -- a hand-replayed bootstrap, or a selective restore -- turned a diagnosable read
    failure into a chained psycopg traceback whose SURVIVING exception named the ledger,
    whereupon `cli.main`, which catches only `RuntimeError`, printed no sentence at all.
    The operator was pointed at the one relation that was not their problem.
    """
    return (
        f"a read failed and drugref could not diagnose it: probing the schema failed "
        f"too ({probe_detail}), which usually means drugref.schema_migration is absent "
        f"-- a database built by replaying db/*.sql by hand, or a partial restore, has "
        f"the relations and no ledger. Run `drugref migrate` to create the ledger and "
        f"re-run; if that does not help, the original failure is the one to act on. "
        f"{_opening(consequence)} meanwhile. Postgres said: {detail}")


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

    EMPTY `relations` IS REFUSED RATHER THAN ANSWERED. `db.missing_relations(conn)`
    returns `()`, which `guard_message` would read as "every relation it reads exists"
    and report to the operator -- a fact nobody checked, asserted as confirmed, which is
    this module's own founding defect one level up. A caller with nothing to probe has a
    bug, and it should be loud in the suite rather than eloquent in production.
    """
    if isinstance(relations, str):
        # A MISSING TRAILING COMMA, WHICH IS SILENT AND ABSURD. Four call sites pass a
        # singleton tuple, and `relations=(X)` without the comma is just `X`; the splat
        # below then probes it CHARACTER BY CHARACTER, each one absent, and the operator
        # reads "d, r, u, g, r, e, f, ., s, ... are DROPPED, not pending". There is no
        # type checker in this project (issue 88), so this is the check that exists.
        raise TypeError(
            f"relations must be a tuple, not the string {relations!r} -- a singleton "
            f"needs its trailing comma, or it is probed one character at a time")
    if not relations:
        raise ValueError(
            "raise_missing needs at least one relation to probe: with none, the "
            "diagnosis would report that every relation exists without having looked")
    detail = _said(exc)
    try:
        absent = db.missing_relations(conn, *relations)
        applied = db.migration_applied(conn, migration)
    except psycopg.Error as probe_exc:
        raise RuntimeError(undiagnosed_message(
            detail=detail, probe_detail=_said(probe_exc),
            consequence=consequence)) from exc
    raise RuntimeError(guard_message(
        Diagnosis(absent=absent, migration_applied=applied, detail=detail),
        migration=migration, consequence=consequence)) from exc


@contextlib.contextmanager
def guarded(conn: psycopg.Connection, *, relations: tuple[str, ...], migration: str,
            consequence: str):
    """Run a read; turn "this database is the wrong shape" into the operator's sentence.

    THE `try` BODY HOLDS EXACTLY ONE CALL at every site, deliberately, and this wrapper
    keeps it that way: widening it would swallow an `UndefinedTable` that a genuinely
    mis-shaped view should still raise. What moves in here is only WHICH exceptions mean
    "wrong shape" -- see `WRONG_SHAPE` for why that had to stop being written five
    times.
    """
    try:
        yield
    except WRONG_SHAPE as exc:
        raise_missing(conn, exc, relations=relations, migration=migration,
                      consequence=consequence)
