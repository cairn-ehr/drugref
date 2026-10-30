"""The ONLY module that writes the open-question registry.

It mirrors classes.py's and interactions.py's single-writer role, and enforces the
split db/007 is built around:

  * `open_question` is DERIVED. register_from_gaps() re-derives it from the gap
    views at the end of every ingest, upserting on the deterministic question_uuid,
    and retires rows whose gap has closed. Nothing a curator owns lives on it.
  * `question_state` / `question_source_check` / `question_evidence` are CURATED.
    They are append-only, keyed off that same immortal UUID, and no rebuild touches
    them -- which is the whole reason state is not a column on open_question.

"Retires" rather than "deletes" because the two halves meet at a cascade: a closed
gap with no curator work is deleted, one that has any is kept with `is_current`
false. See register_from_gaps.

The registry is auto-registering by design (a known gap IS a question; requiring a
promotion step means real gaps sit unregistered because nobody did the paperwork),
so the noise control is `withdrawn`, not a manual allow-list.
"""
import uuid

import psycopg

from drugref import ids, overlay
from drugref.gap_sources_interactions import INTERACTION_GAP_SOURCES
from drugref.gap_sources_registry import REGISTRY_GAP_SOURCES

# Each gap_kind, the view that derives it, and how a row of that view becomes a
# question. Keeping the three together is what stops a view being added without a
# gap_key format -- and gap_key is an INPUT to question_uuid, so an ad-hoc format
# chosen at the call site would mint questions nothing can reconcile later.
#
# `key_sql` must produce the frozen SCHEME:value form; `text_sql` produces the
# literature-searchable statement, which names its subject rather than referring to
# it by UUID so the text is usable as a search expression on its own.
#
# The specs themselves live in two vocabulary modules (issue 177, rule 4), split at
# slice 5c. Merged IN THIS ORDER, which is the order register_from_gaps walks;
# test_question_ids pins the merged list and that the halves share no kind (a dict
# merge would silently keep only the later of two equal keys).
_GAP_SOURCES = {**REGISTRY_GAP_SOURCES, **INTERACTION_GAP_SOURCES}


def register_from_gaps(conn: psycopg.Connection, ingest_run_id: int) -> dict[str, int]:
    """Re-derive `open_question` from the gap views. Returns rows live per gap_kind.

    Call this at the END of an ingest, after every projection the gap views read has
    been rebuilt and before the commit. Called earlier it reads a half-demolished
    registry -- the orchestrators clear this source's edges, memberships and
    contraindications before re-inserting them -- and would close, then reopen, every
    question those tables feed.

    Idempotent by construction: question_uuid is a pure function of (gap_kind,
    gap_key), so re-running mints the same UUIDs and the upsert refreshes the text
    and `last_derived_ingest` rather than inserting duplicates. `first_derived_ingest`
    is never overwritten -- it is write-once provenance answering "when did drugref
    first notice this".

    A CLOSED GAP LEAVES, BUT NEVER TAKES CURATOR WORK WITH IT. The register tracks
    reality, so a question whose gap has closed is deleted -- one that only ever
    grows is the stale generated document these views exist to replace. But every
    curated table cascades from open_question, and those tables are APPEND-ONLY with
    a trigger that refuses DELETE. So an unconditional delete here does not quietly
    lose a curator's work: the cascade hits forbid_question_state_rewrite (or the
    evidence one), the trigger RAISES, and the whole ingest transaction aborts. The
    first design shipped that, and it was unreachable only while no question had
    ever been withdrawn or cited -- it would have failed on the first ingest after a
    curator touched a gap that later closed.

    So a question carrying any curated row is RETAINED with `is_current` false instead:
    invisible on the worklist, still citable by the external tool that already holds the
    UUID, and restored to current under that same UUID if the gap reopens. Only
    untouched questions are deleted, and those have nothing to cascade to. The guard now
    covers EIGHT tables, not three: db/029 (slice 5c.1) added curated_interaction and
    curated_condition to question_state's, question_source_check's and
    question_evidence's original three, because curating a pair is exactly what CLOSES
    its gap -- the very row that answers a question is what would otherwise make the
    next ingest try to delete it. db/032 (slice 5c.2) added curated_class_interaction,
    the class grain's own overlay, for exactly the same reason. db/045 adds reviewer
    annotations and citation-only evidence references; research history must also keep
    a closed question addressable.
    """
    counts: dict[str, int] = {}
    for gap_kind, spec in _GAP_SOURCES.items():
        # The view computes the gap_key and the question text; Python mints the UUID.
        # Deliberately NOT minted in SQL: uuid5 in Postgres would mean a second
        # implementation of a derivation that is frozen forever and that external
        # tools hold references to, and two implementations of one frozen rule is
        # the "two lists in two places" footgun db/006 was written to remove. One
        # implementation, in ids.mint_question_uuid, is the whole point.
        gaps = conn.execute(
            f"SELECT {spec['key_sql']}, {spec['text_sql']} FROM drugref.{spec['view']}"
        ).fetchall()

        live_keys = [gap_key for gap_key, _ in gaps]
        if gaps:
            # executemany, not a Python loop of execute(): gap_unclassified_moiety
            # returns one row per moiety carrying no has_PE membership, which on a
            # full registry is thousands. A per-row round trip there costs more than
            # the rest of the ingest.
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO drugref.open_question "
                    "(question_uuid, gap_kind, gap_key, "
                    "question_text, first_derived_ingest, last_derived_ingest) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (question_uuid) DO UPDATE "
                    "   SET question_text       = EXCLUDED.question_text, "
                    "       last_derived_ingest = EXCLUDED.last_derived_ingest, "
                    # A reopened gap becomes current again under the same UUID.
                    "       is_current          = true",
                    [(ids.mint_question_uuid(gap_kind, gap_key), gap_kind, gap_key,
                      question_text, ingest_run_id, ingest_run_id)
                     for gap_key, question_text in gaps])

        # Whatever this kind derived last time and does not derive now has closed.
        # Drop the ones nobody has touched; keep -- and mark stale -- the rest.
        conn.execute(
            "DELETE FROM drugref.open_question q "
            "WHERE q.gap_kind = %s AND NOT (q.gap_key = ANY(%s)) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_state x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_source_check x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_evidence x "
            "                WHERE x.question_uuid = q.question_uuid) "
            # db/045. A working note or reference is not a ruling, but it is still
            # authored history that must keep its immortal question addressable when
            # a later ingest closes the derived gap.
            "AND NOT EXISTS (SELECT 1 FROM drugref.reviewer_annotation x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.reviewer_evidence_reference x "
            "                WHERE x.question_uuid = q.question_uuid) "
            # db/029. Curating a pair is exactly what CLOSES its gap, so without these
            # two the very first curated row would make the next ingest delete its
            # question, cascade into an append-only table, RAISE, and abort the whole
            # transaction. The guard -- not the cascade -- is what keeps curator work.
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_interaction x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_condition x "
            "                WHERE x.question_uuid = q.question_uuid) "
            # db/032 (slice 5c.2), the class grain. Added with the same hazard shape
            # as the two above -- ON DELETE CASCADE plus an append-only trigger that
            # refuses DELETE -- and so needs the same guard. WHENEVER A TABLE GAINS A
            # question_uuid FK, IT BELONGS IN THIS LIST: the cascade is what makes the
            # omission an aborted ingest rather than a silent data loss, which means
            # it is discovered by every source's ingest failing at once, long after
            # the curated row that caused it was written.
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_class_interaction x "
            "                WHERE x.question_uuid = q.question_uuid)",
            (gap_kind, live_keys))
        conn.execute(
            "UPDATE drugref.open_question SET is_current = false "
            "WHERE gap_kind = %s AND NOT (gap_key = ANY(%s)) AND is_current",
            (gap_kind, live_keys))
        counts[gap_kind] = len(live_keys)

    return counts


def current_state(conn: psycopg.Connection, question_uuid: uuid.UUID) -> str:
    """The question's live state, defaulting to 'open' when no row has been written.

    Absence meaning `open` is what makes auto-registration affordable: thousands of
    questions can be registered without a single state row, and only a deliberate
    curator action ever writes one.
    """
    row = conn.execute(
        "SELECT state FROM drugref.question_state "
        "WHERE question_uuid = %s AND superseded_by IS NULL",
        (question_uuid,)).fetchone()
    return row[0] if row else "open"


def set_state(conn: psycopg.Connection, question_uuid: uuid.UUID, state: str,
              rationale: str, ingest_run_id: int, source: str = "DRUGREF") -> int:
    """Move a question to `state`, superseding whatever it said before.

    Insert-then-point, in that order, via overlay.supersede -- see overlay.py for why
    the order is forced and why single-live is a DEFERRED trigger rather than a unique
    index. db/007 met that problem here first; db/020 generalised the answer.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.question_state "
        "(question_uuid, state, rationale, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING question_state_id",
        (question_uuid, state, rationale, source, ingest_run_id)).fetchone()[0]
    overlay.supersede(conn, "question_state", "question_state_id", new_id,
                      ("question_uuid",), (question_uuid,))
    return new_id


def record_source_check(
        conn: psycopg.Connection, question_uuid: uuid.UUID, source: str,
        source_version: str, outcome: str, note: str | None = None) -> bool:
    """Record that `source` was consulted at `source_version`, with `outcome`.

    Never an overwrite: a re-check against a NEWER version is a new row, which is
    what makes "has this been looked at since the January labels?" answerable. A
    re-check at the same version is a no-op rather than an error, so a re-run of a
    sweep is harmless.

    Recording `not_covered` does NOT close the question -- it is a watermark, and the
    only terminal state is `withdrawn`.
    """
    cur = conn.execute(
        "INSERT INTO drugref.question_source_check "
        "(question_uuid, source, source_version, outcome, note) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (question_uuid, source, source_version, outcome, note))
    return cur.rowcount == 1


def add_evidence(conn: psycopg.Connection, question_uuid: uuid.UUID,
                 reference_scheme: str, reference_value: str, verdict: str,
                 ingest_run_id: int, confidence: str | None = None,
                 source: str = "DRUGREF") -> int:
    """Attach a finding to a question. Append-only; supersede rather than edit.

    Whether the reference actually supports the verdict is a judgement this schema
    RECORDS and does not make.
    """
    return conn.execute(
        "INSERT INTO drugref.question_evidence "
        "(question_uuid, reference_scheme, reference_value, verdict, confidence, "
        " source, ingest_run) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING question_evidence_id",
        (question_uuid, reference_scheme, reference_value, verdict, confidence,
         source, ingest_run_id)).fetchone()[0]
