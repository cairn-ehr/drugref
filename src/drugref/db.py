# src/drugref/db.py
"""Connection helper and migration applier for the drugref schema.

Kept deliberately thin: the schema (db/001_*.sql) is the source of truth for
structure and the append-only floor; this module only opens connections and
replays the SQL files in filename order (mirroring Cairn's connect-and-load
convention, so the schema is re-applied idempotently on a fresh database).
"""
import hashlib
import os
import pathlib
from collections.abc import Mapping, Sequence

import psycopg
from psycopg import sql

from drugref import server_messages

# WHERE THE MIGRATIONS LIVE, in the two layouts this package is ever run from, and
# why the answer cannot be a single constant. In a source checkout the .sql files sit
# at the repository root in db/; in an INSTALLED WHEEL there is no repository, so
# pyproject's force-include ships the same directory inside the package as
# drugref/migrations/. Resolving the packaged copy FIRST matters: an installed
# drugref may sit three parents below something unrelated, and the checkout path is a
# guess about a directory this package does not own.
#
# Named `migrations` and not `db`, deliberately: a drugref/db/ directory would shadow
# this very module's name on the import path. Python resolves db.py ahead of a
# namespace directory today, but relying on that ordering to keep `import drugref.db`
# working is a trap nobody would think to look for.
_PACKAGED_MIGRATIONS = pathlib.Path(__file__).resolve().parent / "migrations"
_SOURCE_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent.parent / "db"


class MissingMigrationsError(RuntimeError):
    """No migration SQL could be found, so apply_migrations has nothing to apply.

    A DEDICATED TYPE BECAUSE THE ALTERNATIVE WAS SILENCE. `Path.glob` on a directory
    that does not exist yields nothing and raises nothing, so before this the wheel
    install ran the ledger DDL, applied zero files, committed, and printed "migrations
    applied" -- and the next command died with UndefinedTable on a database the
    operator had just been told was migrated. A no-op reporting success is the failure
    mode this project forbids outright.

    Subclasses RuntimeError so cli.main's existing handler prints the message instead
    of a traceback; the message is written to be the whole diagnosis.
    """

# The ledger is created by the runner rather than by a migration file, because it
# has to exist BEFORE the first migration runs -- it is what decides whether that
# migration runs at all. It is the one piece of structure this module owns.
_LEDGER_DDL = """
CREATE SCHEMA IF NOT EXISTS drugref;
CREATE TABLE IF NOT EXISTS drugref.schema_migration (
    filename   text        PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


def clear_source_tables(conn: psycopg.Connection,
                        tables: Sequence[str], source: str,
                        match: Mapping[str, str] | None = None) -> None:
    """Delete every row `source` contributed to each of `tables`, in the order given.

    THE ONE STATEMENT THAT MAKES "REBUILDABLE PROJECTION" TRUE. An ingested feed is
    replaced wholesale on re-ingest -- a class that lost a parent upstream, a
    contraindication that was retracted, a de-listed PBS item all have to be able to
    DISAPPEAR, which an insert-only merge can never express. Scoping the delete
    through ingest_run.source is what lets one feed rebuild without touching another's
    rows, and it was written out six times in four modules before this (#43). Six
    restatements are six chances for one of them to quietly stop being per-source.

    ORDER IS PART OF THE CONTRACT and is preserved exactly: `tables` is deleted
    front to back, so a caller whose tables reference each other lists CHILDREN
    FIRST (local_product_moiety before local_product) or the foreign key refuses the
    delete. Nothing here sorts or de-duplicates.

    Callers keep their own named wrapper -- classes.clear_source_edges,
    local.clear_source_products and so on -- because the NAME and the "why this table
    and not that one" belong with the writer that owns the tables. Only the SQL is
    shared. Each wrapper's table tuple is a module constant with a test that restates
    it independently, so dropping a table from one fails loudly instead of leaving a
    projection that grows a little on every ingest.

    `match` NARROWS THE CLEAR TO ONE WRITER'S ROWS, for the one table a source has two
    writers for (#39). ingest_unmatched_ingredient is written both by medrt_run (the
    ingredients MED-RT classifies that no moiety carries) and by mesh_rel_run (the
    subjects of a MeSH-keyed rule that no moiety carries), and both open their runs
    under source 'MED-RT'. Neither set contains the other, so a source-only clear let
    whichever ran last delete the other's rows -- and be unable to re-add them.
    Passing {"reason": "classification"} scopes the same DELETE to the bucket the
    caller re-derives. It is a Mapping rather than another positional string so the
    call site names the column it narrows on.

    The narrowing is OPT-IN: SEVEN OF THE EIGHT declared table tuples own their whole
    table for a source and must keep clearing it wholesale, and a helper that quietly
    cleared less than asked would leave a projection growing a little on every ingest
    with nothing failing. (Eight wrappers, not seven -- the count is restated in
    tests/test_source_clear_contract.py's EXPECTED_TABLES, which is what makes it
    checkable rather than remembered.)

    Table AND column names are interpolated, not parameterised, because an identifier
    cannot be a bind parameter. BOTH MUST COME FROM A MODULE CONSTANT, never from
    input and never from a literal spelled at the call site -- classes.REASON_COLUMN
    exists for exactly that reason, next to the table tuple it travels with. Values
    are always bound.
    """
    extra = "".join(f" AND {column} = %s" for column in (match or {}))
    values = tuple((match or {}).values())
    for table in tables:
        conn.execute(
            f"DELETE FROM drugref.{table} WHERE ingest_run IN "
            "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)"
            + extra,
            (source, *values))


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Open a connection. Falls back to the DRUGREF_DSN env var.

    Raises a clear RuntimeError (not a bare KeyError) when neither a dsn argument
    nor the DRUGREF_DSN environment variable is provided, so a misconfigured caller
    gets an actionable message instead of an opaque traceback.

    ⇒ EVERY CONNECTION OPENED HERE CAN HEAR THE SERVER TALK (issue 174). psycopg
    delivers a NOTICE or a WARNING to registered handlers and to nothing else, so
    until this line every one of them was discarded -- including
    `WARNING: permission denied to analyze "t", skipping it`, which arrives beside
    a SUCCESSFUL ANALYZE command tag and cost issue 160's fix its whole effect
    under an admin-migrates/app-ingests role split. It is installed HERE, at the
    one function every orchestrator, CLI command and migration opens its
    connection through, because a channel that has to be remembered per call site
    is the "gate that exists and never fires" of issues 74, 66 and 76.

    REPORTING ONLY. psycopg swallows whatever a notice handler raises, so this can
    never refuse anything; a statement that must PROVE the server did the work
    collects the messages itself (`server_messages.collect`, used by
    `analyze.analyze_tables`).
    """
    dsn = dsn or os.environ.get("DRUGREF_DSN")
    if not dsn:
        raise RuntimeError(
            "no database DSN: pass dsn= or set the DRUGREF_DSN environment variable")
    conn = psycopg.connect(dsn)
    conn.add_notice_handler(server_messages.log_server_message)
    return conn


def constraint_definition(conn: psycopg.Connection, table: str | None,
                          name: str | None) -> str | None:
    """The SQL text of one named constraint on a drugref table, or None if absent.

    HOW AN ERROR MESSAGE TELLS AN OPERATOR WHAT A CHECK ACCEPTS WITHOUT BECOMING THE
    SECOND COPY OF IT. db/006's lesson, restated by cli_policy's `--decision` comment,
    is that a vocabulary written down twice is two things that can disagree -- so no
    caller may hand-write "one of deny, allow, withdrawn" into a message. Reading
    pg_get_constraintdef is the way out: what it prints IS the constraint, so there is
    still exactly one home for the vocabulary and it cannot go stale.

    Both arguments come from psycopg's error diagnostics (`exc.diag.table_name`,
    `exc.diag.constraint_name`), which is why the table is a parameter rather than the
    caller's knowledge -- AND why both are typed `str | None`: postgres populates them
    for a table CHECK, but the diag fields are optional in general, and a caller in the
    middle of reporting one failure must not be handed a second. THE TRANSACTION MUST
    ALREADY BE ROLLED BACK: the violation that supplies those names also aborts the
    transaction, and a query issued before the rollback fails with
    InFailedSqlTransaction.

    Scoped to the `drugref` schema and to a named table, because `conname` is unique
    only per table -- an unqualified lookup could return some other table's constraint
    of the same name. None rather than a raise, at every exit: a message that could not
    be improved is not a reason to lose the message it was improving.
    """
    if table is None or name is None:
        return None
    row = conn.execute(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = 'drugref' AND t.relname = %s AND c.conname = %s",
        (table, name)).fetchone()
    return row[0] if row else None


def referenced_vocabulary(conn: psycopg.Connection, table: str | None,
                          name: str | None) -> str | None:
    """Every value the FOREIGN KEY named `name` on `table` currently admits,
    read from the table it references -- or None if `table`/`name` name no
    such constraint. `constraint_definition`'s companion, for the one case
    that function alone does not make actionable.

    WHY THIS EXISTS BESIDE `constraint_definition`. `pg_get_constraintdef`
    degrades for a FOREIGN KEY the way it never does for a CHECK: a CHECK's
    definition ENUMERATES its vocabulary inline (spelling out every value the
    column may hold, as `class_expansion_policy_decision` does for
    `--decision`), so quoting it is already actionable on its own. An FK's
    definition only names the referenced table and column (`FOREIGN KEY
    (status) REFERENCES signing_key_status_kind(status)`) and stops there --
    an operator reading that message still has to open psql to learn what
    the table actually contains. This is that lookup, read from the
    database exactly as `constraint_definition` is: no second Python list
    of valid statuses, only two catalogue queries -- first to find WHICH
    table and column the FOREIGN KEY references (from `pg_constraint.
    confrelid`/`confkey`, not assumed), then to read that column's own
    live values.

    ONLY THE FIRST REFERENCED COLUMN (`confkey[1]`), on purpose: every
    FOREIGN KEY this function is called against today (`signing_key.status`)
    references a single-column PRIMARY KEY, and a composite-key vocabulary
    table is not a shape this project has anywhere else. Reading only the
    first column rather than trying to join all of them is a known
    simplification for that reason, not an oversight -- revisit if a
    composite-key vocabulary arrives.

    SAME SHAPE AS `constraint_definition` in every other respect: both
    arguments typed `str | None` and both None-safe for the identical reason
    (a caller in the middle of reporting one failure must not be handed a
    second), scoped to the `drugref` schema, and requiring the transaction to
    already be rolled back (the violation that supplies `table`/`name` also
    aborts the transaction, so a query issued before the rollback fails with
    InFailedSqlTransaction).
    """
    if table is None or name is None:
        return None
    row = conn.execute(
        "SELECT rn.nspname, rt.relname, ra.attname "
        "FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "JOIN pg_class rt ON rt.oid = c.confrelid "
        "JOIN pg_namespace rn ON rn.oid = rt.relnamespace "
        "JOIN pg_attribute ra "
        "  ON ra.attrelid = c.confrelid AND ra.attnum = c.confkey[1] "
        "WHERE n.nspname = 'drugref' AND t.relname = %s AND c.conname = %s "
        "AND c.contype = 'f'",
        (table, name)).fetchone()
    if row is None:
        return None
    ref_schema, ref_table, ref_column = row
    values = conn.execute(
        sql.SQL("SELECT {col} FROM {schema}.{table} ORDER BY {col}").format(
            col=sql.Identifier(ref_column), schema=sql.Identifier(ref_schema),
            table=sql.Identifier(ref_table))).fetchall()
    return ", ".join(str(v[0]) for v in values)


def missing_relations(conn: psycopg.Connection, *relations: str) -> tuple[str, ...]:
    """Of `relations` (schema-qualified), the ones Postgres does not have. Issue 122.

    ⇒ IT ROLLS BACK FIRST, AND THAT IS THE POINT OF THE FUNCTION rather than a detail
    of it. Every caller reaches here inside `except UndefinedTable`, and `connect` uses
    psycopg's default `autocommit=False`, so the failed statement has ABORTED the
    transaction: without the rollback this probe raises `InFailedSqlTransaction` from
    inside the guard meant to improve the diagnosis, replacing a wrong-but-readable
    sentence with an unrelated traceback.

    SAFE TO ROLL BACK HERE, and worth saying why rather than leaving it to be
    rediscovered: every caller is a READ path (`status`, `interactions`) whose work so
    far is SELECTs, and the transaction is already aborted anyway -- an aborted
    transaction can commit nothing, so there is no work left to lose.

    `to_regclass` RATHER THAN A `pg_class` JOIN because it returns NULL instead of
    raising for an absent name, and it resolves through `search_path` exactly as the
    failing query did -- so a relation that exists in some other schema the caller
    cannot see is correctly reported missing FOR THIS ROLE, one of the causes the old
    guards misattributed to a pending migration.
    """
    conn.rollback()
    return tuple(name for name in relations
                 if conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0]
                 is None)


def migration_applied(conn: psycopg.Connection, number: str) -> bool:
    """Whether `db/<number>_*.sql` is recorded applied in the ledger. Issue 122.

    THE DISCRIMINATOR BETWEEN "NOT MIGRATED YET" AND "DROPPED AFTER MIGRATING", which
    is the distinction `migration_guard` is built on: a relation absent while its
    migration is recorded applied cannot be restored by `drugref migrate`, and an
    operator told to run it anyway is in a loop.

    MATCHED ON THE NUMERIC PREFIX PLUS THE UNDERSCORE, never as a substring. Every
    filename is `NNN_description.sql`, and the description is prose a later round may
    reword -- but a bare `number in filename` test would also match `1500_` for "500",
    and match the number inside someone's description. That error runs in the harmful
    direction: it reports a migration applied when it is not, so the guard tells an
    operator NOT to run the migration that would fix them. The `\\_` escape is what
    makes the underscore literal rather than LIKE's single-character wildcard, which
    would let
    `500\\_%` match `5001_*.sql` -- the same harmful direction.

    ⇒ THREE DIGITS, VALIDATED, AND THE TYPO IT REFUSES IS THE WHOLE POINT. `"38"` for
    `"038"` builds `38\\_%`, which matches no row in a ledger whose filenames are
    zero-padded, so every caller is told its migration is NOT applied -- which reads as
    "this database predates db/38, run `drugref migrate`", a no-op, and the operator is
    back in exactly the closed loop `migration_guard` exists to break. `""` fails the
    same way and `"%"` fails the other way, reporting EVERY migration applied. All three
    are silent, so the check is loud instead: a guard passing a bad prefix fails the
    suite rather than misleading an operator.
    """
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM drugref.schema_migration "
        "WHERE filename LIKE %s)", (_ledger_pattern(number),)).fetchone()[0]


def _ledger_pattern(number: str) -> str:
    """PURE: the LIKE pattern matching `db/<number>_*.sql`, validated.

    ONE HOME for the rule the docstring above spends four paragraphs on, because there
    are now two readers of it and the failure is silent in both. Written down twice it
    would be two chances to keep the `\\_` escape and the three-digit check -- the
    argument that collapsed the per-source clear, the MeSH reader and the checksum into
    one place each (#40, #43), on a smaller rule.
    """
    if not (len(number) == 3 and number.isdigit()):
        raise ValueError(
            f"migration number must be the three-digit prefix as written in db/ "
            f"(e.g. '038'), not {number!r}: anything else matches no ledger row and "
            f"would report every migration unapplied")
    return f"{number}\\_%"


def migration_applied_at(conn: psycopg.Connection, number: str):
    """WHEN `db/<number>_*.sql` was applied here, or None if it has not been.

    THE WATERSHED READER (issue 159). `migration_applied` answers whether, which is
    what `migration_guard` needs; a column whose MEANING a migration changed needs
    when, so that rows written on either side of it can be told apart. db/053 and the
    Python that writes the two stamps ship in one commit, so this ledger row is the
    only durable record on a running database of which rows carry the new meaning.

    None is the safe answer and is why this returns a timestamp rather than raising:
    on a database that predates the migration, NO row carries the new meaning, and a
    reader comparing against None must fall through to "cannot say".

    A DATABASE WITH NO LEDGER AT ALL TAKES THAT SAME ANSWER, and must. The ledger is
    created by `apply_migrations` with CREATE TABLE IF NOT EXISTS rather than by any
    db/*.sql, so -- as migration_guard's own docstring says -- "a database bootstrapped
    by replaying the SQL by hand has every view and no ledger", and so does a partial
    restore. Reading it unguarded made `drugref status` print its first header and then
    a raw psycopg traceback, killing the five later blocks of a six-block command.
    "Nothing here can be dated against db/<number>" is the same answer as "db/<number>
    is unapplied", and the CALLER is required to say which out loud: silently
    withholding every runtime with no reason given is the wrong-answer-by-omission this
    whole round is about.

    ORDER BY, unlike `migration_applied`'s EXISTS, because a LIKE on a three-digit
    prefix can in principle match two files; an arbitrary row would make the watershed
    depend on the planner.
    """
    # `to_regclass` and NOT `missing_relations`, which ROLLS BACK before probing. That
    # rollback is right for its own callers -- they all arrive inside `except
    # UndefinedTable`, holding a transaction Postgres has already aborted -- but this
    # is a HAPPY-PATH read, and a lookup that silently discarded its caller's open
    # transaction would be a far worse surprise than the crash it is fixing.
    if conn.execute(
            "SELECT to_regclass('drugref.schema_migration')").fetchone()[0] is None:
        return None
    row = conn.execute(
        "SELECT applied_at FROM drugref.schema_migration WHERE filename LIKE %s "
        "ORDER BY filename LIMIT 1",
        (_ledger_pattern(number),)).fetchone()
    return row[0] if row else None


def migration_dir() -> pathlib.Path:
    """The directory holding the migration SQL: packaged copy first, checkout second.

    RESOLVED PER CALL, not once at import, so a test can point it somewhere else and
    so the answer is never baked into a stale module object.

    "CONTAINS AT LEAST ONE .sql", not "exists", is the test each candidate must pass.
    An empty-but-present directory is the same catastrophe as a missing one -- zero
    files applied -- and the whole point of this function is that zero can never be
    mistaken for done. Raising here rather than returning an empty directory keeps
    that decision in ONE place instead of at every call site.
    """
    for candidate in (_PACKAGED_MIGRATIONS, _SOURCE_MIGRATIONS):
        if any(candidate.glob("*.sql")):
            return candidate
    raise MissingMigrationsError(
        "no migration SQL found: looked in "
        f"{_PACKAGED_MIGRATIONS} (the packaged copy) and {_SOURCE_MIGRATIONS} (a "
        "source checkout), and neither holds a .sql file. An installed drugref whose "
        "wheel shipped no migrations cannot create the schema -- reinstall from a "
        "wheel built with pyproject's force-include, or run from a checkout.")


def apply_migrations(conn: psycopg.Connection) -> None:
    """Apply every db/*.sql in filename order, once each, recording what ran.

    A file is applied only if the ledger has not seen it. If the ledger HAS seen it
    but the file's content has changed since, this raises rather than proceeding.

    Why the checksum matters more than it looks. Before the ledger, every file was
    replayed on every call, so each one had to hand-write a guard inferring "has my
    change already landed?" from the system catalogs -- and those guards answer a
    subtly different question than the one that matters. db/003's source CHECK is
    guarded on the constraint merely EXISTING, so editing that file in place (which
    its own comment instructs the next author to do when a new authority lands)
    silently does nothing on a database that already ran it: a fresh database gets
    the edited constraint, a migrated one keeps the old, and nothing reports the
    divergence. Refusing to run a changed file turns that into a loud error, and
    makes "add a new file" the only way to change the schema -- which is what keeps
    fresh and long-lived databases identical.

    Everything runs in one transaction, so a failure part-way leaves neither the
    schema nor the ledger half-updated.

    THE DIRECTORY IS RESOLVED BEFORE THE LEDGER IS TOUCHED, which is the ordering that
    matters: a broken install must leave the database exactly as it found it, not
    holding an empty schema_migration table that makes the next run look partially
    complete.
    """
    sql_dir = migration_dir()
    conn.execute(_LEDGER_DDL)
    applied = dict(conn.execute(
        "SELECT filename, checksum FROM drugref.schema_migration").fetchall())

    for path in sorted(sql_dir.glob("*.sql")):
        body = path.read_text()
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        seen = applied.get(path.name)
        if seen == checksum:
            continue                       # already applied, unchanged
        if seen is not None:
            raise RuntimeError(
                f"migration {path.name} changed after it was applied "
                f"(recorded {seen[:12]}..., now {checksum[:12]}...). Migrations are "
                "immutable once applied: add a new db/*.sql file instead of editing "
                "this one, or the change will never reach an already-migrated "
                "database.")
        conn.execute(body)
        conn.execute(
            "INSERT INTO drugref.schema_migration (filename, checksum) VALUES (%s, %s)",
            (path.name, checksum))
    conn.commit()
