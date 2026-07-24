"""The ONLY module that writes the classification tables.

It mirrors claims.py's role for the identity tables -- concentrating writes in one
reviewable place -- but the discipline it enforces is DIFFERENT, and the
difference is the point:

* claims.py guards an APPEND-ONLY spine. Substance identity is immortal, and the
  database floor rejects UPDATE/DELETE outright.
* This module manages a REBUILDABLE PROJECTION. MED-RT is an upstream authority we
  re-ingest wholesale, and its edges are meant to be dropped and rebuilt -- so
  clear_source_edges() deliberately DELETEs. What survives a rebuild unchanged is
  class IDENTITY: class_uuid is a pure function of the MED-RT NUI, so every class
  comes back with exactly the UUID it had before.
"""
import uuid

import psycopg

from drugref import ids
from drugref.ingest.medrt import ClassConcept


def upsert_class(conn: psycopg.Connection, concept: ClassConcept,
                 ingest_run_id: int) -> uuid.UUID:
    """Register a class (or refresh its cached name) and return its UUID.

    The UUID is derived, never looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the name and type caches -- upstream does rename classes
    -- while first_seen_ingest is deliberately left out of the SET list, because it
    records when drugref FIRST saw the class, not when it was last confirmed.
    """
    class_uuid = ids.mint_class_uuid(concept.nui)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (class_uuid) DO UPDATE SET "
        "  class_name = EXCLUDED.class_name, concept_type = EXCLUDED.concept_type",
        (class_uuid, concept.nui, concept.nui, concept.name,
         concept.concept_type, ingest_run_id))
    return class_uuid


def clear_source_edges(conn: psycopg.Connection, source: str) -> None:
    """Drop every DAG and membership edge contributed by `source`.

    Called at the start of a re-ingest so a new upstream release fully REPLACES the
    previous one. This is why the edge tables must stay deletable: a class that
    lost a parent upstream has to lose it here too, and an insert-only merge can
    never express a removal. Scoped by source so an unrelated feed's edges survive.

    Class rows themselves are NOT deleted -- their UUIDs are immortal and are
    re-derived identically on the way back in.
    """
    for table in ("class_membership", "class_parent"):
        conn.execute(
            f"DELETE FROM drugref.{table} WHERE ingest_run IN "
            "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)",
            (source,))


def add_parent_edge(conn: psycopg.Connection, child_uuid: uuid.UUID,
                    parent_uuid: uuid.UUID, ingest_run_id: int) -> bool:
    """Add one subclass edge. Returns True if a new row was inserted.

    ON CONFLICT DO NOTHING keeps a file that repeats an edge harmless.
    """
    cur = conn.execute(
        "INSERT INTO drugref.class_parent (child_class_uuid, parent_class_uuid, ingest_run) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (child_uuid, parent_uuid, ingest_run_id))
    return cur.rowcount == 1


def add_membership(conn: psycopg.Connection, moiety_uuid: uuid.UUID,
                   class_uuid: uuid.UUID, relationship: str,
                   ingest_run_id: int) -> bool:
    """Link a moiety to a class on one axis. Returns True if newly inserted."""
    cur = conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (moiety_uuid, class_uuid, relationship, ingest_run_id))
    return cur.rowcount == 1


def resolve_moiety_by_rxcui(conn: psycopg.Connection, rxcui: str) -> uuid.UUID | None:
    """Find the moiety carrying this RxCUI, or None if we do not have it.

    This is the membership join key, and it needs no new bridge data: MED-RT states
    class membership against RxNorm ingredient concepts whose code IS the RxCUI,
    and slice 1 already attached an RXNORM_IN claim to every moiety.

    Superseded claims are excluded so a corrected-away RxCUI cannot resurrect a
    stale membership (the same rule chebi.py applies to InChIKey lookups). Returns
    the first match: an RxCUI identifies a single ingredient upstream, so a second
    hit would be an upstream data error rather than a case worth modelling.
    """
    row = conn.execute(
        "SELECT moiety_uuid FROM drugref.identity_claim "
        "WHERE scheme = 'RXNORM_IN' AND value = %s AND superseded_by IS NULL "
        "LIMIT 1", (rxcui,)).fetchone()
    return row[0] if row else None
