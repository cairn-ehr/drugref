"""The ONLY module that writes the interaction tables.

It mirrors classes.py's single-writer role, and enforces the same discipline:
`class_contraindication` is a REBUILDABLE PROJECTION of MED-RT, not the
append-only signed overlay. So inserts dedupe (ON CONFLICT DO NOTHING) and
clear_source_contraindications() deliberately DELETEs, letting a newer MED-RT
release fully replace the previous one -- a contraindication retracted upstream
has to disappear here too, which an insert-only merge could never express.
"""
import uuid

import psycopg


def clear_source_contraindications(conn: psycopg.Connection, source: str) -> None:
    """Drop every contraindication contributed by `source`.

    Called at the start of a re-ingest so a new upstream release REPLACES the
    previous one. Scoped by the run's source (as classes.clear_source_edges is), so
    an unrelated feed's rows survive; run before any of this run's rows are written,
    it only ever removes the prior release's.
    """
    conn.execute(
        "DELETE FROM drugref.class_contraindication WHERE ingest_run IN "
        "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)",
        (source,))


def add_contraindication(conn: psycopg.Connection, subject_moiety_uuid: uuid.UUID,
                         object_class_uuid: uuid.UUID, relationship: str,
                         source: str, ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` is contraindicated with a co-administered
    drug of `object_class_uuid`, on axis `relationship` (CI_MoA / CI_PE).

    Returns True if a new row was inserted. ON CONFLICT DO NOTHING keeps a file that
    repeats the same assertion harmless.
    """
    cur = conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run_id))
    return cur.rowcount == 1
