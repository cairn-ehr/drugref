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


def test_migration_003_renames_populated_columns_and_keeps_edges(conn):
    """The test above replays 003 over a table ALREADY in the target shape, so it
    exercises the guard's "already renamed" branch. This one exercises the branch
    that actually does the rename -- 002's medrt_nui/medrt_code columns present and
    populated, no source column yet -- because a RENAME of a column with rows in it,
    joined to by a foreign key, is the exact operation the whole migration risks.

    It reconstructs the pre-003 shape on the (empty) live table, populates it with a
    class_parent edge, applies ONLY db/003, and proves the edge still joins its two
    class rows. All DDL here is transactional, so the `conn` fixture's rollback
    restores the shared schema -- nothing is committed.
    """
    # Reconstruct db/002's column shape by reversing 003 on the empty table.
    conn.execute("ALTER TABLE drugref.substance_class DROP COLUMN source CASCADE")
    conn.execute("ALTER TABLE drugref.substance_class RENAME COLUMN source_code TO medrt_nui")
    conn.execute("ALTER TABLE drugref.substance_class RENAME COLUMN published_code TO medrt_code")

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('MED-RT', 'rename-test', 'deadbeef') RETURNING ingest_run_id").fetchone()[0]
    parent = ids.mint_class_uuid("MED-RT", "N0000000010")
    child = ids.mint_class_uuid("MED-RT", "N0000000011")
    for cu, nui, name in ((parent, "N0000000010", "Parent [APC]"),
                          (child, "N0000000011", "Child [EPC]")):
        conn.execute(
            "INSERT INTO drugref.substance_class "
            "(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (cu, nui, nui, name, "APC" if cu == parent else "EPC", run_id))
    conn.execute(
        "INSERT INTO drugref.class_parent (child_class_uuid, parent_class_uuid, ingest_run) "
        "VALUES (%s, %s, %s)", (child, parent, run_id))

    # Apply ONLY db/003 over that populated, pre-rename table.
    conn.execute((db._DB_DIR / "003_class_registry_source_neutral.sql").read_text())

    # The rows survived the rename with their UUIDs intact (a rebuilt table would
    # have dropped them and broken the class_parent foreign key), the columns are
    # renamed, and source was backfilled.
    assert conn.execute(
        "SELECT source, source_code, published_code, class_name FROM drugref.substance_class "
        "WHERE class_uuid = %s", (child,)).fetchone() == (
            "MED-RT", "N0000000011", "N0000000011", "Child [EPC]")
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (child, parent)).fetchone()[0] == 1
