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
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (moiety_uuid) DO UPDATE SET display_name = EXCLUDED.display_name",
        (moiety_uuid, display_name, ingest_run_id))


def clear_admissions(conn: psycopg.Connection) -> None:
    """Drop the whole admission projection, ready for a rebuild (db/011, #26).

    Unqualified DELETE, deliberately: unlike class_membership or local_product
    there is no per-source key to scope by, because there is only ever one source
    of admission evidence -- the UNII gate. Scoping it by ingest_run would be
    worse than useless: rows written by the PREVIOUS run are precisely the ones a
    rebuild must retire, so a run-scoped delete would leave every stale signal in
    place while looking careful.
    """
    conn.execute("DELETE FROM drugref.moiety_admission")


def record_admission(conn: psycopg.Connection, moiety_uuid: uuid.UUID,
                     signals: list[str], ingest_run_id: int) -> None:
    """Record WHY a moiety passed the membership gate (db/011, #26).

    `signals` comes straight from gate.admission_signals, so the stored evidence
    and the admission decision cannot disagree -- they are the same computation,
    not two opinions about it.

    ON CONFLICT DO NOTHING guards the case of two UNII rows minting the same
    moiety_uuid within one run; across runs, clear_admissions has already emptied
    the table, so this is not the rebuild mechanism.
    """
    for signal in signals:
        conn.execute(
            "INSERT INTO drugref.moiety_admission (moiety_uuid, signal, ingest_run) "
            "VALUES (%s, %s, %s) ON CONFLICT (moiety_uuid, signal) DO NOTHING",
            (moiety_uuid, signal, ingest_run_id))


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
