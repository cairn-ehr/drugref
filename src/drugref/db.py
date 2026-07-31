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

_DB_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "db"

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

    The narrowing is OPT-IN: five of the six writers own their whole table for a
    source and must keep clearing it wholesale, and a helper that quietly cleared less
    than asked would leave a projection growing a little on every ingest with nothing
    failing.

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
    """
    dsn = dsn or os.environ.get("DRUGREF_DSN")
    if not dsn:
        raise RuntimeError(
            "no database DSN: pass dsn= or set the DRUGREF_DSN environment variable")
    return psycopg.connect(dsn)


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
    """
    conn.execute(_LEDGER_DDL)
    applied = dict(conn.execute(
        "SELECT filename, checksum FROM drugref.schema_migration").fetchall())

    for path in sorted(_DB_DIR.glob("*.sql")):
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
                "this one, or the change will never reach an already-migrated database.")
        conn.execute(body)
        conn.execute(
            "INSERT INTO drugref.schema_migration (filename, checksum) VALUES (%s, %s)",
            (path.name, checksum))
    conn.commit()
