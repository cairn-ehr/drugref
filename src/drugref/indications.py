"""The ONLY module that writes the indication tables.

Mirrors interactions.py's single-writer role and enforces the same discipline:
`moiety_condition_indication` and `moiety_induced_condition` are REBUILDABLE
PROJECTIONS of MED-RT, not the append-only signed overlay. So inserts dedupe
(ON CONFLICT DO NOTHING) and clear_source_indications() deliberately DELETEs -- an
indication withdrawn upstream has to disappear here too, which an insert-only merge
could never express.

WHY THIS IS NOT IN interactions.py. That module answers "what must not be given";
this one answers "what is this drug for". They share a shape and nothing else, and
interactions.py is already the writer of four tables. Keeping them apart is also what
makes the two-table split of db/019 legible at the call site.
"""
import uuid

import psycopg

from drugref import db

# The predicate moiety_induced_condition holds. Named here rather than spelled at the
# call site so the writer supplies it and a caller CANNOT file an induces row through
# the indication path (or the reverse) by passing a string.
INDUCES = "induces"

# Both relations one 5b.2 ingest writes. Restated independently in
# tests/test_source_clear_contract.py, so dropping one fails loudly instead of leaving
# a projection that grows a little on every ingest (#43).
INDICATION_TABLES = ("moiety_condition_indication", "moiety_induced_condition")


def clear_source_indications(conn: psycopg.Connection, source: str) -> None:
    """Drop every indication and induced-state row contributed by `source`.

    Covers BOTH tables, because one ingest writes both and a partial clear would leave
    the last release's rows beside this one's. Scoped by source so an unrelated feed's
    rows survive.

    No `reason` narrowing here, unlike classes.clear_source_unmatched_ingredients:
    these tables have exactly ONE writer, which is the state #39 restored for
    ingest_unmatched_ingredient rather than the exception it made for it.
    """
    db.clear_source_tables(conn, INDICATION_TABLES, source)


def add_condition_indication(conn: psycopg.Connection, subject_moiety_uuid: uuid.UUID,
                             object_condition_uuid: uuid.UUID, relationship: str,
                             source: str, ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` is used for `object_condition_uuid`, on
    `relationship` (may_treat / may_prevent / may_diagnose).

    Returns True if a new row was inserted. ON CONFLICT DO NOTHING keeps a release that
    states one assertion through two MeSH concepts harmless -- 19 assertions in the
    2026.07.06 release collapse exactly that way, which is why the caller's count comes
    from this return value and not from the assertion list's length.

    `relationship` is a foreign key into condition_indication_axis, so a predicate
    nobody has declared a generalisation policy for cannot reach the table.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_condition_indication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_condition_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


def add_induced_condition(conn: psycopg.Connection, subject_moiety_uuid: uuid.UUID,
                          object_condition_uuid: uuid.UUID, source: str,
                          ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` CAUSES `object_condition_uuid`.

    Takes no `relationship` argument on purpose: the table holds one predicate, and
    supplying it here means a caller cannot file a may_treat row in the induced-state
    table by passing the wrong string. The database CHECK is the second line of that
    defence, not the first.

    Returns True if a new row was inserted.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_induced_condition "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_condition_uuid, INDUCES, source, ingest_run_id))
    return cur.rowcount == 1
