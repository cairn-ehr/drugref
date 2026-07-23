# src/drugref/db.py
"""Connection helper and migration applier for the drugref schema.

Kept deliberately thin: the schema (db/001_*.sql) is the source of truth for
structure and the append-only floor; this module only opens connections and
replays the SQL files in filename order (mirroring Cairn's connect-and-load
convention, so the schema is re-applied idempotently on a fresh database).
"""
import os
import pathlib
import psycopg

_DB_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "db"


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
    """Replay every db/*.sql in filename order. Idempotent (CREATE ... IF NOT EXISTS
    where it matters); intended for a schema that has been dropped fresh in tests."""
    for path in sorted(_DB_DIR.glob("*.sql")):
        conn.execute(path.read_text())
    conn.commit()
