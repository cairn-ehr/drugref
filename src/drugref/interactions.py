"""The ONLY module that writes the interaction tables.

It mirrors classes.py's single-writer role, and enforces the same discipline:
`class_contraindication` is a REBUILDABLE PROJECTION of MED-RT, not the
append-only signed overlay. So inserts dedupe (ON CONFLICT DO NOTHING) and
clear_source_contraindications() deliberately DELETEs, letting a newer MED-RT
release fully replace the previous one -- a contraindication retracted upstream
has to disappear here too, which an insert-only merge could never express.

Slice 5b (db/014) added three more tables to this module's remit, so it now
writes FOUR: `class_contraindication` above, plus `moiety_condition_contraindication`
(drug-condition, e.g. CI_with) and `moiety_contraindication` (drug-drug, CI_ChemClass's
moiety arm), plus the `ingest_unresolved_ci_object` worklist that records what an
ingest deliberately withheld. Same rebuildable-projection discipline throughout.
"""
import uuid
from collections.abc import Iterable

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


def unresolved_expansion_policy(conn: psycopg.Connection, source: str) -> list[str]:
    """The `source_code`s of `source`'s expansion decisions that resolve to no class.

    A read rather than a write, kept in this module because it reads
    class_expansion_policy, which is contraindication-expansion policy and so this
    module's business.

    WHY AN ORCHESTRATOR HAS TO ASK, AND NOT MERELY A VIEW EXIST. A deny that matches
    nothing looks exactly like a deny that is working: the pair set is silently wider
    and nothing fails. The condition only ever arises FROM AN INGEST -- upstream
    re-keys or withdraws a class the curator ruled on -- so the ingest that can cause
    it is the thing that has to report it. db/010 shipped the detector
    (expansion_policy_unresolved) with no consumer at all, which is precisely the
    failure mode it was written to catch.

    NOT an error: a re-keyed class is upstream's prerogative, and aborting an ingest
    over a stale curator note would be worse than the stale note. It is a worklist
    number, in the same class as the unmatched-ingredient counts.

    Scoped by source so a MED-RT run reports on MED-RT's decisions only -- a
    MeSH-keyed decision dangling because MeSH has not been ingested yet is not MED-RT's
    news to report.
    """
    return [row[0] for row in conn.execute(
        "SELECT source_code FROM drugref.expansion_policy_unresolved "
        "WHERE source = %s ORDER BY source_code", (source,)).fetchall()]


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


def clear_source_mesh_contraindications(conn: psycopg.Connection, source: str) -> None:
    """Drop every slice-5b contraindication contributed by `source`.

    Covers BOTH relations plus the unresolved-object worklist, because one ingest
    writes all three and a partial clear would leave last release's rows beside this
    one's. Same rebuildable-projection discipline as
    clear_source_contraindications: a contraindication retracted upstream has to
    disappear here too, and an insert-only merge could never express that.

    The worklist is cleared for the reason classes.clear_source_unmatched_ingredients
    gives: an object that starts resolving must LEAVE the list, or the worklist grows
    by its own length every ingest and never shrinks.
    """
    for table in ("moiety_condition_contraindication", "moiety_contraindication",
                  "ingest_unresolved_ci_object"):
        conn.execute(
            f"DELETE FROM drugref.{table} WHERE ingest_run IN "
            "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)",
            (source,))


def add_condition_contraindication(conn: psycopg.Connection,
                                   subject_moiety_uuid: uuid.UUID,
                                   object_condition_uuid: uuid.UUID,
                                   relationship: str, source: str,
                                   ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` is contraindicated in a patient who has
    `object_condition_uuid`, on axis `relationship` (CI_with).

    Returns True if a new row was inserted. The clinical direction is carried
    entirely by which side is which -- a condition is not a drug, so the two are not
    even the same kind of thing, but the column names still say so explicitly.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_condition_contraindication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_condition_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


def add_moiety_contraindication(conn: psycopg.Connection,
                                subject_moiety_uuid: uuid.UUID,
                                object_moiety_uuid: uuid.UUID, relationship: str,
                                source: str, ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` must not be co-administered with
    `object_moiety_uuid` (CI_ChemClass's moiety arm).

    DIRECTIONAL, and both directions may legitimately be stored: MED-RT states which
    drug the assertion is ABOUT, and it does not always assert the converse. A
    consumer wanting "is this pair contraindicated at all" must query both columns
    rather than assume symmetry.

    Returns True if a new row was inserted.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_moiety_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


def record_unresolved_ci_objects(
        conn: psycopg.Connection,
        rows: Iterable[tuple[str, str, str, str, str | None, str, int]],
        ingest_run_id: int) -> int:
    """Persist contraindication objects drugref did not ingest, and WHY.

    `rows` is an iterable of (source, relationship, object_source, object_code,
    object_name, object_kind, assertion_count). `object_name` is the only optional
    field -- MeSH's chemical tree always has one, but a future object_source might
    not, so the type says so rather than assuming.

    `object_kind` is CHEMICAL_CLASS or UNREGISTERED_SUBSTANCE (db/014), and passing
    it is MANDATORY because the two get different curator questions: a class is
    withheld pending a ruling on structural-tree expansion, while an unregistered
    substance is a coverage gap answered by registering the moiety. Reading "no
    moiety resolved" as "therefore a class" is the defect db/014's object_kind closes, so this
    function will not infer the kind on a caller's behalf.

    Not an error and not a drop: these are real upstream assertions drugref could
    not or would not ingest (see db/014 on the sulfonamide case). Persisting the
    IDENTITY rather than only a count is what lets gap_unresolved_ci_object be a
    query -- the exact lesson db/008 drew when the earlier ingest kept only the
    COUNT of unmatched ingredients and discarded the RxCUIs.

    Batched, like classes.add_unmatched_ingredients: nobody needs the per-row
    insert-vs-conflict answer, because the caller already holds the deduped set.
    Returns rows written.
    """
    batch = [(ingest_run_id, *row) for row in rows]
    if not batch:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO drugref.ingest_unresolved_ci_object "
            "(ingest_run, source, relationship, object_source, object_code, "
            " object_name, object_kind, assertion_count) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING", batch)
    return len(batch)
