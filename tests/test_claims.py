# tests/test_claims.py
import uuid
from drugref import claims

M = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _new_run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('TEST','r1','x') RETURNING ingest_run_id").fetchone()[0]


def test_upsert_moiety_then_add_claims(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "amlodipine", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)
    claims.add_claim(conn, M, "INN", "amlodipine", run)
    rows = conn.execute(
        "SELECT scheme, value FROM drugref.identity_claim WHERE moiety_uuid = %s ORDER BY scheme",
        (M,)).fetchall()
    assert rows == [("INN", "amlodipine"), ("UNII", "1J444QC288")]


def test_add_claim_is_idempotent(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "amlodipine", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)  # duplicate -> no-op
    n = conn.execute("SELECT count(*) FROM drugref.identity_claim WHERE moiety_uuid = %s", (M,)).fetchone()[0]
    assert n == 1


def test_upsert_moiety_refreshes_display_name(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "acetaminophen", run)
    claims.upsert_moiety(conn, M, "paracetamol", run)   # display cache may refresh
    name = conn.execute("SELECT display_name FROM drugref.substance_moiety WHERE moiety_uuid = %s", (M,)).fetchone()[0]
    assert name == "paracetamol"
