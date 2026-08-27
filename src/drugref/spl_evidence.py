# src/drugref/spl_evidence.py
"""The writers for db/051's SPL evidence projection, and its per-source clear.

The SOLE writer of drugref's SPL rows. Every function here takes an already-built
row type and puts it in the database; nothing here reads a label, resolves a
name, or decides a route -- those are `ingest/spl*.py`'s pure jobs, and the
orchestrator (`ingest/spl_run.py`) owns the transaction.

**WHY `COPY` AND NOT `INSERT ... ON CONFLICT DO NOTHING`**, which every sibling
writer in this repo uses. Two reasons, and the second is the load-bearing one:

* **Volume.** One ingest writes ~1.3 million occurrence rows (26,721 wordings
  naming a moiety at 48.2 occurrences each). A per-row round trip there costs
  more than the rest of the ingest -- the same argument
  `questions.register_from_gaps` already makes for `executemany`.
* **A collision here is a DEFECT, not a repeat.** The sibling writers absorb a
  dump that repeats one assertion, because dumps do. This source does not:
  measured 2026-08-27 over all 262,032 openFDA records, `(set_id, version)` never
  repeats and neither does `set_id` alone, and the parser de-duplicates wordings
  and occurrences by construction. So a primary-key collision means the reader or
  the release changed shape, and the right response is to ABORT the whole run and
  keep the previous projection -- which is exactly what `COPY` does and what `ON
  CONFLICT DO NOTHING` would silently prevent.

**THE ROW TYPES ARE KEYWORD-ONLY**, and that is not style. Several of them are
runs of same-typed columns -- two offsets, a start and an end, a set_id and a
version -- and a positional call that transposed a pair would store cleanly and
produce a quote cut from the wrong characters, which nothing downstream could
detect. db/049's `add_drugcentral_assertion` states the same rule for the same
reason.
"""
from __future__ import annotations

import operator
from collections.abc import Iterable
from dataclasses import dataclass, fields

import psycopg

from drugref import db

#: WHAT THIS SOURCE OWNS, AND THE ORDER IT MUST BE CLEARED IN.
#:
#: **CHILDREN FIRST**: `db.clear_source_tables` deletes front to back and
#: preserves the order exactly, because a caller whose tables reference each other
#: must list children first or the foreign key refuses the delete.
#: `spl_wording_quote`, `spl_entity_occurrence` and `spl_label` all reference
#: `spl_wording`; `spl_label_subject` references `spl_label`.
#:
#: Registered in tests/test_source_clear_contract.py's EXPECTED_TABLES, which is
#: what turns a table silently dropped from this tuple into a failing test rather
#: than a rebuild that quietly stops covering it.
SPL_TABLES = (
    "spl_wording_quote",
    "spl_entity_occurrence",
    "spl_label_subject",
    "spl_label",
    "spl_wording",
)


@dataclass(frozen=True, kw_only=True)
class WordingRow:
    """One distinct section wording -- identified, never stored."""

    text_key: str
    char_length: int
    label_count: int


@dataclass(frozen=True, kw_only=True)
class LabelRow:
    """One section-carrying label's identity and citation."""

    set_id: str
    version: str
    effective_time: str | None
    product_type: str | None
    text_key: str


@dataclass(frozen=True, kw_only=True)
class SubjectRow:
    """One subject drug of one label, or the single row saying it has none."""

    set_id: str
    version: str
    subject_ordinal: int
    moiety_uuid: str | None
    route: str


@dataclass(frozen=True, kw_only=True)
class OccurrenceRow:
    """One recognised moiety span in one wording."""

    text_key: str
    char_start: int
    char_end: int
    moiety_uuid: str
    match_ambiguous: bool


@dataclass(frozen=True, kw_only=True)
class QuoteRow:
    """One bounded quoted window -- the only prose this slice stores."""

    text_key: str
    ordinal: int
    char_start: int
    char_end: int
    quote_text: str


def clear_source_spl(conn: psycopg.Connection, source: str) -> None:
    """Drop every SPL row contributed by `source`, children first.

    Same rebuildable-projection discipline as `clear_source_drugcentral`: a label
    withdrawn upstream, a wording revised, an occurrence that stops matching
    because the registry gained a longer name -- all have to be able to
    DISAPPEAR, which an insert-only merge could never express.

    It also clears the UNRESOLVED subject rows, for the reason
    `classes.clear_source_unmatched_ingredients` gives: a label whose subject
    starts resolving must LEAVE the recovery register, or the register grows by
    its own length on every ingest and never shrinks.
    """
    db.clear_source_tables(conn, SPL_TABLES, source)


#: Which table each row type is written to. THE ONLY PLACE THE TWO ARE PAIRED --
#: the COLUMN list is derived from the row type's own fields (see `_copy`), so a
#: column added to a row type and forgotten in a hand-written list is not a state
#: this module can reach. db/006's rule at the writer tier: one home per
#: vocabulary.
_TABLE_FOR_ROW = {
    "WordingRow": "spl_wording",
    "LabelRow": "spl_label",
    "SubjectRow": "spl_label_subject",
    "OccurrenceRow": "spl_entity_occurrence",
    "QuoteRow": "spl_wording_quote",
}


def _copy(conn: psycopg.Connection, row_type: type, rows: Iterable, *,
          ingest_run_id: int, source: str) -> int:
    """`COPY` rows into one drugref table, stamping run and source on each.

    **THE COLUMN LIST IS THE ROW TYPE'S OWN FIELD ORDER**, read from the
    dataclass rather than written out beside it. A hand-written list next to a
    dataclass is two orderings that can disagree, and the way they disagree here
    is silent: the columns are same-typed runs -- two offsets, a set_id and a
    version -- so a transposed pair COPYs cleanly and stores a quote cut from the
    wrong characters. Deriving it makes that unrepresentable.

    The run id and source are prepended HERE rather than carried on every row
    type, so a caller cannot write one row under a different run than its
    siblings -- which would survive every constraint and split one ingest's
    projection across two `ingest_run` values.

    Identifiers are interpolated because an identifier cannot be a bind
    parameter; the table comes from a module constant and the columns from the
    type, never from input -- which is `db.clear_source_tables`'s stated rule.
    """
    table = _TABLE_FOR_ROW[row_type.__name__]
    columns = [field.name for field in fields(row_type)]
    read = operator.attrgetter(*columns)
    written = 0
    statement = (f"COPY drugref.{table} (ingest_run, source, {', '.join(columns)}) "
                 "FROM STDIN")
    with conn.cursor() as cur, cur.copy(statement) as copy:
        for row in rows:
            copy.write_row((ingest_run_id, source, *read(row)))
            written += 1
    return written


def write_wordings(conn: psycopg.Connection, rows: Iterable[WordingRow], *,
                   ingest_run_id: int, source: str) -> int:
    """Write the wording register. Returns how many rows landed."""
    return _copy(conn, WordingRow, rows,
                 ingest_run_id=ingest_run_id, source=source)


def write_labels(conn: psycopg.Connection, rows: Iterable[LabelRow], *,
                 ingest_run_id: int, source: str) -> int:
    """Write one row per section-carrying label, resolved subject or not."""
    return _copy(conn, LabelRow, rows,
                 ingest_run_id=ingest_run_id, source=source)


def write_label_subjects(conn: psycopg.Connection, rows: Iterable[SubjectRow], *,
                         ingest_run_id: int, source: str) -> int:
    """Write the subject rows, including the ones recording that there is none."""
    return _copy(conn, SubjectRow, rows,
                 ingest_run_id=ingest_run_id, source=source)


def write_occurrences(conn: psycopg.Connection, rows: Iterable[OccurrenceRow], *,
                      ingest_run_id: int, source: str) -> int:
    """Write the derived facts: which known moiety is named where."""
    return _copy(conn, OccurrenceRow, rows,
                 ingest_run_id=ingest_run_id, source=source)


def write_quotes(conn: psycopg.Connection, rows: Iterable[QuoteRow], *,
                 ingest_run_id: int, source: str) -> int:
    """Write the bounded quoted windows.

    THE BUDGET IS NOT CHECKED HERE, and that is deliberate: `db/051`'s deferred
    constraint trigger re-computes it per wording at COMMIT, so a writer that
    forgot to apply the rule -- or a future one that never knew about it -- is
    refused by the database rather than trusted. A licensing determination
    enforced only by the code that happens to write it is a determination one
    refactor away from being a convention.
    """
    return _copy(conn, QuoteRow, rows,
                 ingest_run_id=ingest_run_id, source=source)


def analyze_source_tables(conn: psycopg.Connection) -> None:
    """`ANALYZE` this source's five tables. **NOT optional, and not a tidy-up.**

    ⇒ MEASURED, NOT REASONED: without it, the orchestrator's own read-backs do
    not finish. On the real releases the self-pair count -- a three-way join of
    spl_label_subject, spl_label and 1.3 million spl_entity_occurrence rows --
    ran for **25 minutes at 100% CPU and was still going** when it was cancelled.
    With statistics it is seconds.

    THE CAUSE, and it is a property of bulk loading rather than of these queries:
    every table here is written by `COPY` inside the same transaction that then
    queries it, so at planning time `pg_class.reltuples` still says the tables are
    empty. The planner therefore costs a nested loop over what it believes are a
    handful of rows, and picks one over 1.3 million.

    IT IS ALSO WHY THE FIRST DIAGNOSIS WAS WRONG. The obvious suspect was the
    foreign-key checks on the child `COPY` -- freshly loaded parent, no stats, RI
    seq scans. That was measured and REFUTED: 20,000 child rows against an
    unanalyzed 68,550-row parent insert in 175 ms, because PostgreSQL's RI
    triggers use a plan pinned to the parent's primary key rather than a
    re-planned query. The cost was never in the write; it was in the read-back
    that follows it.

    Runs INSIDE the transaction, which PostgreSQL permits, so the statistics
    describe the projection this run is about to publish and are rolled back with
    it if the run is refused.
    """
    # The table list has ONE home -- SPL_TABLES -- and this reads it rather than
    # restating it, so a table added to the source cannot be left unanalyzed and
    # quietly reintroduce the stall.
    tables = ", ".join(f"drugref.{table}" for table in SPL_TABLES)
    conn.execute(f"ANALYZE {tables}")


def load_registry(conn: psycopg.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """`(display_name -> moiety_uuid, UNII -> moiety_uuid)`, in ONE statement.

    ONE STATEMENT, NOT TWO, for the reason `drugcentral_run.load_registry`
    records: a single statement always sees a single snapshot at any isolation
    level, so this reads consistently without the transaction having to be
    REPEATABLE READ -- and raising isolation for the whole run made a concurrent
    write to any question row abort the entire ingest with SerializationFailure.

    EVERY READ IS ORDERED, and that is not cosmetic. `identity_claim` is unique on
    (moiety_uuid, scheme, value) and deliberately NOT across moieties, so two
    moieties may legitimately claim one UNII. An unordered read would let the same
    release resolve differently on two runs.

    LIVE CLAIMS ONLY (`superseded_by IS NULL`): a corrected-away identifier must
    not resurrect a resolution.

    **First-wins on a collision, and the collision is the caller's to report.**
    Both mappings are built in one pass in sorted order, so which entry wins is a
    property of the data rather than of the plan.
    """
    rows = conn.execute(
        "  SELECT 'display_name' AS lookup, display_name AS key, "
        "         moiety_uuid::text AS moiety_uuid "
        "    FROM drugref.substance_moiety "
        "   UNION ALL "
        "  SELECT 'UNII', value, moiety_uuid::text "
        "    FROM drugref.identity_claim "
        "   WHERE scheme = 'UNII' AND superseded_by IS NULL "
        "   ORDER BY lookup, key, moiety_uuid").fetchall()

    by_name: dict[str, str] = {}
    by_unii: dict[str, str] = {}
    for lookup, key, moiety_uuid in rows:
        target = by_name if lookup == "display_name" else by_unii
        target.setdefault(key, moiety_uuid)
    return by_name, by_unii
