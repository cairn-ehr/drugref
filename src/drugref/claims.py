"""The ONLY module that writes to substance_moiety / identity_claim.

Concentrating writes here keeps the append-only discipline in one reviewable
place: we INSERT new facts and overlay corrections, never UPDATE-in-place or
DELETE. The database floor (db/001) enforces the same rule against any caller.
"""
import uuid
import psycopg


def upsert_moiety(cur: psycopg.Connection, moiety_uuid: uuid.UUID,
                  display_name: str, ingest_run_id: int) -> None:
    """Register a moiety, or refresh its display-name cache on re-ingest.

    The moiety_uuid is immortal (the DB floor forbids changing it); display_name
    is a convenience cache derived from claims, so ON CONFLICT refreshes it.
    """
    cur.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (moiety_uuid) DO UPDATE SET display_name = EXCLUDED.display_name",
        (moiety_uuid, display_name, ingest_run_id))


def add_claim(cur: psycopg.Connection, moiety_uuid: uuid.UUID,
              scheme: str, value: str, ingest_run_id: int) -> None:
    """Append an external-identifier claim. Idempotent: re-asserting the same
    (moiety, scheme, value) is a no-op, so re-ingest never duplicates."""
    cur.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (moiety_uuid, scheme, value) DO NOTHING",
        (moiety_uuid, scheme, value, ingest_run_id))
