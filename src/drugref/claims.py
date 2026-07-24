"""The ONLY module that writes to substance_moiety / identity_claim.

Concentrating writes here keeps the append-only discipline in one reviewable
place: we INSERT new facts and overlay corrections, never UPDATE the immortal
columns in place and never DELETE. The database floor (db/001) enforces the same
rule against any caller, so this module is the convenient path, not the only guard.
"""
import uuid
import psycopg

from drugref import ids


def upsert_moiety(conn: psycopg.Connection, moiety_uuid: uuid.UUID,
                  display_name: str, ingest_run_id: int) -> None:
    """Register a moiety, or refresh its display-name cache on re-ingest.

    The moiety_uuid is immortal (the DB floor forbids changing it); display_name
    is a convenience cache derived from claims, so ON CONFLICT refreshes it.
    """
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (moiety_uuid) DO UPDATE SET display_name = EXCLUDED.display_name",
        (moiety_uuid, display_name, ingest_run_id))


def add_claim(conn: psycopg.Connection, moiety_uuid: uuid.UUID,
              scheme: str, value: str, ingest_run_id: int) -> bool:
    """Append an external-identifier claim. Idempotent: re-asserting the same
    (moiety, scheme, value) is a no-op, so re-ingest never duplicates.

    Returns True if a new claim row was inserted, False if it already existed
    (the ON CONFLICT no-op path). Callers can use this to count genuinely-new
    claims without an extra existence query.

    Idempotency is scoped to LIVE claims (`WHERE superseded_by IS NULL`, matching
    db/005's partial unique index). That scoping is the point: a value that was
    once superseded and is later re-asserted by upstream has to be able to land
    as a live claim again. While the index covered superseded rows too, the
    re-assertion silently hit the conflict and reported "already present", leaving
    the identifier invisible to every join that filters on superseded_by.

    The value is canonicalised here (ids.canonical_claim_value) rather than by each
    caller, so a code-valued scheme is stored under exactly the spelling its lookups
    -- and, for a UNII, the minted moiety_uuid -- are keyed on.
    """
    value = ids.canonical_claim_value(scheme, value)
    cur = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (moiety_uuid, scheme, value) WHERE superseded_by IS NULL "
        "DO NOTHING",
        (moiety_uuid, scheme, value, ingest_run_id))
    return cur.rowcount == 1
