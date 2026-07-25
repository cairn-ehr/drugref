"""The ONLY module that writes the open-question registry.

It mirrors classes.py's and interactions.py's single-writer role, and enforces the
split db/007 is built around:

  * `open_question` is DERIVED. register_from_gaps() re-derives it from the gap
    views on every ingest, upserting on the deterministic question_uuid, and deletes
    rows whose gap has closed. Nothing a curator owns lives on it.
  * `question_state` / `question_source_check` / `question_evidence` are CURATED.
    They are append-only, keyed off that same immortal UUID, and no rebuild touches
    them -- which is the whole reason state is not a column on open_question.

The registry is auto-registering by design (a known gap IS a question; requiring a
promotion step means real gaps sit unregistered because nobody did the paperwork),
so the noise control is `withdrawn`, not a manual allow-list.
"""
import uuid

import psycopg

from drugref import ids

# Each gap_kind, the view that derives it, and how a row of that view becomes a
# question. Keeping the three together is what stops a view being added without a
# gap_key format -- and gap_key is an INPUT to question_uuid, so an ad-hoc format
# chosen at the call site would mint questions nothing can reconcile later.
#
# `key_sql` must produce the frozen SCHEME:value form; `text_sql` produces the
# literature-searchable statement, which names its subject rather than referring to
# it by UUID so the text is usable as a search expression on its own.
_GAP_SOURCES = {
    "unclassified_moiety": {
        "view": "gap_unclassified_moiety",
        "key_sql": "'MOIETY:' || moiety_uuid",
        "text_sql": (
            "'Which physiologic effects does ' || display_name || "
            "' produce? No has_PE membership is recorded, so it cannot participate "
            "in any effect-accumulation model.'"),
    },
    "unpopulated_contraindication": {
        "view": "gap_unpopulated_contraindication",
        "key_sql": "'CLASS:' || class_uuid",
        "text_sql": (
            "'Which drugs belong to ' || class_name || '? It carries ' || "
            "ci_rule_count || ' contraindication rule(s) but no drug is filed under "
            "it anywhere in its subtree, so those rules can never yield a pair.'"),
    },
    "unmatched_ingredient": {
        "view": "gap_unmatched_ingredient",
        "key_sql": "'RXNORM_IN:' || rxcui",
        "text_sql": (
            "'Does RxCUI ' || rxcui || COALESCE(' (' || name || ')', '') || "
            "' have an active moiety drugref should carry? It is classified "
            "upstream but no moiety in the registry claims it.'"),
    },
}


def register_from_gaps(conn: psycopg.Connection, ingest_run_id: int) -> dict[str, int]:
    """Re-derive `open_question` from the gap views. Returns rows live per gap_kind.

    Idempotent by construction: question_uuid is a pure function of (gap_kind,
    gap_key), so re-running mints the same UUIDs and the upsert refreshes the text
    and `last_derived_ingest` rather than inserting duplicates. `first_derived_ingest`
    is never overwritten -- it is write-once provenance answering "when did drugref
    first notice this".

    Questions whose gap has closed are DELETED. The register tracks reality; one
    that only ever grows is the stale generated document these views exist to
    replace. Deleting is safe precisely because nothing a curator owns lives here --
    though a curator's `question_state` rows do cascade with it, which is correct:
    state about a gap that no longer exists is not worth resurrecting, and if the
    gap reopens the question is re-derived under the very same UUID.
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
        for gap_key, question_text in gaps:
            conn.execute(
                "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
                "question_text, first_derived_ingest, last_derived_ingest) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (question_uuid) DO UPDATE "
                "   SET question_text       = EXCLUDED.question_text, "
                "       last_derived_ingest = EXCLUDED.last_derived_ingest",
                (ids.mint_question_uuid(gap_kind, gap_key), gap_kind, gap_key,
                 question_text, ingest_run_id, ingest_run_id))

        # Whatever this kind derived last time and does not derive now has closed.
        conn.execute(
            "DELETE FROM drugref.open_question "
            "WHERE gap_kind = %s AND NOT (gap_key = ANY(%s))",
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
        "WHERE question_uuid = %s AND superseded_by IS NULL", (question_uuid,)).fetchone()
    return row[0] if row else "open"


def set_state(conn: psycopg.Connection, question_uuid: uuid.UUID, state: str,
              rationale: str, ingest_run_id: int, source: str = "DRUGREF") -> int:
    """Move a question to `state`, superseding whatever it said before.

    Insert-then-point, in that order, because superseded_by must reference a row that
    already exists. Both rows are briefly live, which is exactly why db/007 makes
    single-live a DEFERRED constraint rather than a unique index -- an immediate
    check would reject the only sequence that can express a correction.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.question_state "
        "(question_uuid, state, rationale, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING question_state_id",
        (question_uuid, state, rationale, source, ingest_run_id)).fetchone()[0]
    conn.execute(
        "UPDATE drugref.question_state SET superseded_by = %s "
        "WHERE question_uuid = %s AND superseded_by IS NULL AND question_state_id <> %s",
        (new_id, question_uuid, new_id))
    return new_id


def record_source_check(conn: psycopg.Connection, question_uuid: uuid.UUID, source: str,
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
