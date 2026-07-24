# tests/test_db.py
"""Focused tests for drugref.db: connect() and apply_migrations() idempotency.

apply_migrations()'s docstring claims the migration SQL is idempotent (safe to
replay on an already-migrated database, mirroring Cairn's connect-and-load
convention). These tests hold that claim to account: a second apply must not
raise, and a fresh connection must be genuinely usable.
"""
import pytest
from drugref import db, ids


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
    and every drugref table must still be present afterwards."""
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
    assert tables == {
        # slice 1: the identity spine
        "ingest_run", "substance_moiety", "identity_claim",
        # slice 2a: the classification DAG
        "substance_class", "class_parent", "class_membership",
    }


def test_replaying_migrations_preserves_existing_classes(conn):
    """db/003 generalises the class registry by RENAMING columns, which is the
    riskiest kind of migration to replay: a guard that misfires either errors on
    the second pass or silently rebuilds the table and drops every class row --
    taking the class_uuids that class_parent and class_membership join on with it.

    apply_migrations() commits, so this test cannot rely on the `conn` fixture's
    rollback and cleans up after itself explicitly (the same reason the
    orchestrator test modules carry their own cleanup -- see conftest).
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('MED-RT', 'replay-test', 'deadbeef') RETURNING ingest_run_id").fetchone()[0]
    class_uuid = ids.mint_class_uuid("MED-RT", "N0000999999")
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, 'MED-RT', 'N0000999999', 'N0000999999', "
        " 'Replay Probe [MoA]', 'MoA', %s)", (class_uuid, run_id))
    conn.commit()
    try:
        db.apply_migrations(conn)
        assert conn.execute(
            "SELECT source, source_code, class_name FROM drugref.substance_class "
            "WHERE class_uuid = %s", (class_uuid,)).fetchone() == (
                "MED-RT", "N0000999999", "Replay Probe [MoA]")
    finally:
        conn.execute("DELETE FROM drugref.substance_class WHERE class_uuid = %s", (class_uuid,))
        conn.execute("DELETE FROM drugref.ingest_run WHERE ingest_run_id = %s", (run_id,))
        conn.commit()
