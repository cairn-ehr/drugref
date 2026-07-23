# tests/test_db.py
"""Focused tests for drugref.db: connect() and apply_migrations() idempotency.

apply_migrations()'s docstring claims the migration SQL is idempotent (safe to
replay on an already-migrated database, mirroring Cairn's connect-and-load
convention). These tests hold that claim to account: a second apply must not
raise, and a fresh connection must be genuinely usable.
"""
import pytest
from drugref import db


def test_connect_without_dsn_raises_clear_error(monkeypatch):
    """No dsn arg and no DRUGREF_DSN env -> a clear RuntimeError, not a bare
    KeyError. Runs anywhere (no database needed)."""
    monkeypatch.delenv("DRUGREF_DSN", raising=False)
    with pytest.raises(RuntimeError, match="DRUGREF_DSN"):
        db.connect()


def test_connect_returns_usable_connection(_dsn):
    """db.connect(dsn) with an explicit DSN opens a connection that can run a query."""
    conn = db.connect(_dsn)
    try:
        row = conn.execute("SELECT 1").fetchone()
        assert row == (1,)
    finally:
        conn.close()


def test_apply_migrations_is_idempotent(conn):
    """Re-running apply_migrations on an already-migrated database must not error,
    and the three drugref tables must still be present afterwards."""
    # `conn` (from conftest) is already migrated once via the session-scoped
    # `_migrated` fixture. Applying again must be a no-op, not a crash.
    db.apply_migrations(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'drugref'"
        ).fetchall()
    }
    assert tables == {"ingest_run", "substance_moiety", "identity_claim"}
