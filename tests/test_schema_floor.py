# tests/test_schema_floor.py
"""The append-only floor must reject rewrites even from raw SQL."""
import psycopg
import pytest


def _seed_one(conn):
    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('TEST','r1','x') RETURNING ingest_run_id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','amlodipine', %s)", (run,))
    cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','UNII','ABC123', %s) "
        "RETURNING identity_claim_id", (run,)).fetchone()[0]
    return run, cid


def test_moiety_delete_forbidden(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.substance_moiety")


def test_moiety_uuid_immutable(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.substance_moiety "
                     "SET moiety_uuid = '00000000-0000-0000-0000-0000000000bb'")


def test_claim_delete_forbidden(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.identity_claim")


def test_claim_value_immutable_but_supersede_allowed(conn):
    run, cid = _seed_one(conn)
    # Changing value in place is forbidden...
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.identity_claim SET value = 'XYZ' WHERE identity_claim_id = %s", (cid,))
    conn.rollback()
    run, cid = _seed_one(conn)
    # ...but the real overlay path is permitted: insert a SECOND claim (the
    # correction) and point the FIRST claim's superseded_by at it. A claim
    # superseding itself is not a valid overlay (see identity_claim_no_self_supersede)
    # -- correction is always insert-new-then-point-old-at-new.
    new_cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','UNII','ABC123-CORRECTED', %s) "
        "RETURNING identity_claim_id", (run,)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s",
                 (new_cid, cid))


def test_claim_cannot_supersede_itself(conn):
    run, cid = _seed_one(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s",
                     (cid, cid))
