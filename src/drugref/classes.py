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
                 ingest_run_id: int) -> tuple[uuid.UUID, bool]:
    """Register a class (or refresh its cached name).

    Returns (class_uuid, is_new), where is_new is True only the first time drugref
    ever saw this class. The caller needs that distinction because classes
    ACCUMULATE while edges are rebuilt, so "classes in this release" and "classes
    added by this run" are genuinely different numbers and a summary that reported
    only one of them would be ambiguous.

    The UUID is derived, never looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the name, type and code caches -- upstream does rename
    classes -- while first_seen_ingest is deliberately left out of the SET list,
    because it records when drugref FIRST saw the class, not when it was last
    confirmed. That is also what makes it the newness test: the row is new to us
    exactly when the value that came back is this run's id.
    """
    class_uuid = ids.mint_class_uuid(concept.nui)
    first_seen = conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (class_uuid) DO UPDATE SET "
        "  class_name = EXCLUDED.class_name, concept_type = EXCLUDED.concept_type, "
        "  medrt_code = EXCLUDED.medrt_code "
        "RETURNING first_seen_ingest",
        (class_uuid, concept.nui, concept.code, concept.name,
         concept.concept_type, ingest_run_id)).fetchone()[0]
    return class_uuid, first_seen == ingest_run_id


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


def moieties_by_rxcui(conn: psycopg.Connection) -> dict[str, list[uuid.UUID]]:
    """Build the RxCUI -> moieties index the membership join runs on.

    This is the join key, and it needs no new bridge data: MED-RT states class
    membership against RxNorm ingredient concepts whose code IS the RxCUI, and
    slice 1 already attached an RXNORM_IN claim to every moiety.

    Superseded claims are excluded so a corrected-away RxCUI cannot resurrect a
    stale membership (the same rule chebi.py applies to InChIKey lookups).

    EVERY claimant is kept, not the first. identity_claim is unique on
    (moiety_uuid, scheme, value), so nothing stops two moieties from claiming one
    RxCUI, and slice 1 takes the value straight from the UNII feed's RXCUI column
    without checking it is unique across moieties. Picking one arbitrarily would
    both drop a real membership and make the ingest non-reproducible, since an
    unordered single-row read may answer differently run to run. chebi.py resolved
    the identical question the identical way for InChIKey; the two lookups deserve
    the same rule. The ordering makes the retained order deterministic too.

    Read WHOLE rather than queried per assertion: MED-RT asserts ~27,500
    memberships over ~6,000 distinct ingredients, so a per-assertion lookup re-asks
    an already-answered question four times in five. The index is bounded by the
    moiety registry -- one entry per moiety carrying an RxCUI -- so it grows with
    the registry, not with MED-RT; if that ever outgrows memory it is the same
    conversation as the whole-file parse, tracked in the production-ingest
    follow-up rather than solved differently here.
    """
    index: dict[str, list[uuid.UUID]] = {}
    for value, moiety_uuid in conn.execute(
            "SELECT value, moiety_uuid FROM drugref.identity_claim "
            "WHERE scheme = 'RXNORM_IN' AND superseded_by IS NULL "
            "ORDER BY value, moiety_uuid").fetchall():
        index.setdefault(value, []).append(moiety_uuid)
    return index
