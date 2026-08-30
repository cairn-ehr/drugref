# src/drugref/spl_evidence.py
"""The writers for db/051's SPL evidence projection, and its per-source clear.

The SOLE writer of drugref's SPL rows. Every function here takes an already-built
row type and puts it in the database; nothing here reads a label, resolves a
name, or decides a route -- those are `ingest/spl*.py`'s pure jobs, and the
orchestrator (`ingest/spl_run.py`) owns the transaction.

**WHY `COPY` AND NOT `INSERT ... ON CONFLICT DO NOTHING`**, which every sibling
writer in this repo uses. Two reasons, and the second is the load-bearing one:

* **Volume.** One ingest writes ~1.3 million occurrence rows (26,760 wordings
  naming a moiety at ~48 occurrences each). A per-row round trip there costs
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
from typing import TYPE_CHECKING

import psycopg

from drugref import db

if TYPE_CHECKING:                       # pragma: no cover
    # Import-time-free: this module is the WRITER and `spl_quote` is a pure
    # parser, so the dependency may exist for a reader and a type checker but
    # never at runtime. A module-level import would make that direction a matter
    # of luck rather than of statement.
    from drugref.ingest import spl_quote

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

    def __post_init__(self) -> None:
        """The SAME rule `spl_subject.Subject` already validates, twelve lines
        upstream, and `db/051`'s `spl_label_subject_complete` CHECK enforces.

        `_subject_rows` reads a validated `Subject` and writes this, so the
        invariant was checked and then thrown away between the two. Restating it
        here is not duplication -- it is the difference between failing on the
        row that is wrong and failing at COMMIT after 68,550 labels, with no
        `set_id` in the error.
        """
        # Imported here rather than at module scope: `spl_subject` is a pure
        # parser and this module is the writer, so the writer depends on the
        # vocabulary and never the other way round. A module-level import would
        # make that direction a matter of luck.
        from drugref.ingest.spl_subject import RESOLVING_ROUTES, SUBJECT_ROUTES

        if self.route not in SUBJECT_ROUTES:
            raise ValueError(
                f"route {self.route!r} is not one of {SUBJECT_ROUTES}; "
                "db/051's spl_label_subject_route CHECK would refuse it")
        if (self.route in RESOLVING_ROUTES) != (self.moiety_uuid is not None):
            raise ValueError(
                f"subject row for {self.set_id} v{self.version} takes route "
                f"{self.route!r} with moiety_uuid={self.moiety_uuid!r}: a "
                "resolving route names a moiety and a non-resolving one does "
                "not, which is db/051's spl_label_subject_complete")


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
    """One bounded quoted window -- the only prose this slice stores.

    **BUILD THESE WITH `from_window`, not by hand.** The three row-local rules --
    the span is a span, the text is the cut those offsets name, and the cut does
    not run past the wording -- are all properties of one window against one
    string, both of which the caller has in hand. Leaving them to `db/051` meant
    a transposition surfaced as a CHECK violation partway through a 2,000-wording
    `COPY`, tens of minutes into the run, naming no `text_key`.
    """

    text_key: str
    ordinal: int
    char_start: int
    char_end: int
    quote_text: str

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.char_start < 0:
            raise ValueError(
                f"quote row {self.text_key}#{self.ordinal} has a negative "
                f"ordinal or offset")
        if self.char_end <= self.char_start:
            raise ValueError(
                f"quote {self.char_start}:{self.char_end} for {self.text_key} "
                "is not a span -- db/051's spl_wording_quote_span")
        if len(self.quote_text) != self.char_end - self.char_start:
            raise ValueError(
                f"quote {self.char_start}:{self.char_end} for {self.text_key} "
                f"names {self.char_end - self.char_start} characters but "
                f"carries {len(self.quote_text)} -- db/051's "
                "spl_wording_quote_length, and the usual cause is cutting the "
                "RAW text while offsetting the NORMALISED one")

    @classmethod
    def from_window(cls, *, text_key: str, ordinal: int,
                    window: "spl_quote.Window", text: str) -> "QuoteRow":
        """Cut `window` out of the text it was measured against.

        The cut happens HERE so `length(quote_text) = char_end - char_start` and
        `char_end <= char_length` hold BY CONSTRUCTION rather than at COMMIT.

        The explicit range check is not redundant with `__post_init__`: Python
        slicing CLAMPS silently, so a window running past the end of `text`
        yields a short string, and the two would then agree with each other
        about the wrong characters. This is the one mistake the schema's offsets
        are most exposed to, so it is refused where the text is still in scope.
        """
        if window.char_end > len(text):
            raise ValueError(
                f"window {window.char_start}:{window.char_end} runs past the "
                f"{len(text)}-character wording {text_key}: the offsets and the "
                "text describe different strings (raw versus normalised)")
        return cls(text_key=text_key, ordinal=ordinal,
                   char_start=window.char_start, char_end=window.char_end,
                   quote_text=text[window.char_start:window.char_end])


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
    # `attrgetter("a")` returns the VALUE; `attrgetter("a", "b")` returns a
    # tuple. With one column, `(run, source, *read(row))` would splat a string
    # into its characters and COPY one column per letter. Unreachable today --
    # every row type has at least three fields -- so this is refused rather than
    # handled, because the day someone adds a single-field row type is the day
    # that becomes a silent corruption.
    if len(columns) < 2:
        raise ValueError(
            f"{row_type.__name__} declares {len(columns)} column(s); _copy's "
            "attrgetter returns a bare value rather than a tuple below two, "
            "which would COPY one column per character")
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


@dataclass(frozen=True, kw_only=True)
class Registry:
    """The two lookups this ingest resolves through, AND WHAT THEY DISCARDED.

    **NAMED, not a bare 2-tuple, because the two halves are the same type.**
    `names, known_uniis = load_registry(conn)` type-checks just as well the wrong
    way round, and a transposition would build the matcher out of UNII codes and
    resolve every subject against display names -- caught only by `check_floors`
    at the very end of a full 12.5-minute ingest, if at all.

    **A DATACLASS AND NOT A `NamedTuple`, WHICH IS NOT A STYLE CHOICE.** This was
    a `NamedTuple` for one round, and a `NamedTuple` is still unpackable: the two
    committed tools that read the registry kept `names, uniis =
    load_registry(conn)` and began raising `ValueError: too many values to
    unpack` on their first line of real work -- silently, because neither tool
    has a test. A type that cannot be destructured at all fails at EVERY call
    site the moment the shape changes, instead of only at the ones whose arity
    happens to stop matching.

    **The collision counts exist because `load_registry` used to say they were
    "the caller's to report" and no caller reported them.** `identity_claim` is
    unique on (moiety_uuid, scheme, value) and deliberately NOT across moieties,
    so two moieties may legitimately claim one UNII; first-wins then attaches
    every subject derived from it to whichever moiety sorted first. Deterministic,
    and reproducibly wrong is still wrong -- so the number of discarded entries
    is carried out where the summary can print it.
    """

    by_name: dict[str, str]
    by_unii: dict[str, str]
    #: display names claimed by more than one moiety, and UNIIs likewise. Each
    #: counts DISCARDED entries, not colliding keys: three moieties on one UNII
    #: is two discards.
    name_collisions: int
    unii_collisions: int


def load_registry(conn: psycopg.Connection) -> Registry:
    """`Registry(display_name -> moiety_uuid, UNII -> moiety_uuid, collisions)`.

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

    **First-wins on a collision, and the collision is COUNTED here.** Both
    mappings are built in one pass in sorted order, so which entry wins is a
    property of the data rather than of the plan -- and the count of what lost
    rides out on `Registry` so the orchestrator can report it. It used to say the
    collision was "the caller's to report"; the sole caller reported only the
    post-de-duplication sizes, so the number was unobservable anywhere.
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
    name_collisions = unii_collisions = 0
    for lookup, key, moiety_uuid in rows:
        if lookup == "display_name":
            if key in by_name:
                name_collisions += 1
            else:
                by_name[key] = moiety_uuid
        elif key in by_unii:
            unii_collisions += 1
        else:
            by_unii[key] = moiety_uuid
    return Registry(by_name=by_name, by_unii=by_unii,
                    name_collisions=name_collisions,
                    unii_collisions=unii_collisions)
