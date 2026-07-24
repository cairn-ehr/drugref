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
