"""The ONLY module that writes the condition tables.

Mirrors classes.py exactly, and for the same reason: `condition` and
`condition_parent` are a REBUILDABLE PROJECTION of MeSH, not the append-only spine.
So clear_source_condition_edges() deliberately DELETEs -- a condition that lost a
parent upstream has to lose it here too, which an insert-only merge could never
express -- while condition IDENTITY survives untouched, because condition_uuid is a
pure function of (source, source_code).

Condition ROWS are never deleted by a rebuild, only their edges. The UUID is
immortal and externally citable; re-deriving it on the way back in is what makes the
whole projection safe to drop.
"""
import uuid

import psycopg

from drugref import db, ids
from drugref.ingest.mesh_concepts import MeshRecord


def upsert_condition(conn: psycopg.Connection, record: MeshRecord,
                     ingest_run_id: int, source: str) -> tuple[uuid.UUID, bool]:
    """Register a condition (or refresh its cached name, tree numbers and SCR class).

    Returns (condition_uuid, is_new), where is_new is True only the first time
    drugref ever saw this condition. The caller needs the distinction because
    conditions ACCUMULATE while edges are REBUILT, so "conditions in this release"
    and "conditions added by this run" are different numbers and a summary reporting
    only one of them would be ambiguous.

    Keyed on record_ui, NEVER on concept_ui: many concepts resolve to one record, so
    keying on the concept would split one condition into several rows.

    The UUID is derived, never looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the caches -- upstream renames records and re-files them
    in the tree -- and `scr_class` is one of them for exactly the reason `name` and
    `tree_numbers` are: it is an upstream value held here so a reader need not open
    supp2026 (db/019). NULL for every descriptor, which carries a different
    vocabulary. first_seen_ingest is deliberately left out of the SET list,
    because it records when drugref FIRST saw the condition. That is also what makes
    it the newness test: the row is new exactly when the value returned is this run's.
    """
    condition_uuid = ids.mint_condition_uuid(source, record.record_ui)
    # Store the SAME canonicalisation the UUID was minted from, so the stored source
    # and the identity key can never drift -- two spellings of one authority would
    # share a UUID yet be stored as two strings, and a per-source rebuild would then
    # miss half its own rows.
    stored_source = ids.canonical_source(source)
    first_seen = conn.execute(
        "INSERT INTO drugref.condition "
        "(condition_uuid, source, source_code, name, record_kind, tree_numbers, "
        " scr_class, first_seen_ingest) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (condition_uuid) DO UPDATE SET "
        "  name = EXCLUDED.name, record_kind = EXCLUDED.record_kind, "
        "  tree_numbers = EXCLUDED.tree_numbers, scr_class = EXCLUDED.scr_class "
        "RETURNING first_seen_ingest",
        (condition_uuid, stored_source, record.record_ui, record.name,
         record.record_kind, list(record.tree_numbers), record.scr_class,
         ingest_run_id)).fetchone()[0]
    return condition_uuid, first_seen == ingest_run_id


CONDITION_EDGE_TABLES = ("condition_parent",)


def clear_source_condition_edges(conn: psycopg.Connection, source: str) -> None:
    """Drop every condition DAG edge contributed by `source`.

    Called at the start of a re-ingest so a new upstream release fully REPLACES the
    previous one. Scoped by source so an unrelated feed's edges survive. Condition
    rows are NOT deleted -- their UUIDs are immortal and are re-derived identically
    on the way back in.
    """
    db.clear_source_tables(conn, CONDITION_EDGE_TABLES, source)


def add_condition_parent_edge(conn: psycopg.Connection, child_uuid: uuid.UUID,
                              parent_uuid: uuid.UUID, ingest_run_id: int) -> bool:
    """Add one condition DAG edge. Returns True if a new row was inserted.

    ON CONFLICT DO NOTHING keeps a release that states an edge twice harmless.
    """
    cur = conn.execute(
        "INSERT INTO drugref.condition_parent "
        "(child_condition_uuid, parent_condition_uuid, ingest_run) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (child_uuid, parent_uuid, ingest_run_id))
    return cur.rowcount == 1
