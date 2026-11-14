# src/drugref/ingest/spl_run.py
"""Orchestrate one SPL ingest: read -> resolve -> scan -> clear -> write -> check.

The ONLY writer of drugref's SPL rows, per the architecture invariant: parsers
are pure, orchestrators own the transaction.

ORDER MATTERS, and here it matters more than in any sibling because the expensive
pass is 19.3 GB long:

  1. read openFDA and run the floor check;
  2. read the registry and the class names -- one transaction, rolled back before
     the scan;
  3. decide which labels the DailyMed pass has to look for;
  4. scan DailyMed once, and REFUSE if it dropped anything for a reading reason;
  5. only now open the run, clear this source's rows and write.

**EVERY REFUSAL IN STEPS 1-4 HAPPENS BEFORE THE RUN ROW EXISTS**, which is
stricter than `drugcentral_run`'s ordering and for the same reason it gives: a
refusal must leave the database exactly as it was, and an `ingest_run` with
`finished_at NULL` is not "exactly as it was". Here the scan takes minutes (2 min
09 s over 19.3 GB since issue 160), so a run row opened before it would sit
unfinished for the whole of a pass that might yet refuse.

**THE CHECKS IN STEP 5 ARE DELIBERATELY AFTER IT**, and the claim above used to
be written without that qualification, which made it false: `reconcile`,
`check_floors` and the deferred quote-budget trigger all read rows that must
already have been written, so they cannot precede the run. A refusal there rolls
the projection back and leaves an `ingest_run` row with `finished_at NULL` --
the record that a run was ATTEMPTED and refused, which `ingest_run_incomplete`
reports and `test_a_refused_floor_rolls_the_WHOLE_run_back` asserts by name.

THE DATA DEPENDENCY IS REAL: the subject bridge is `identity_claim` UNIIs and the
matcher is `substance_moiety.display_name`, so `unii` and `chebi` must have run.
Ingest this against an empty registry and every label resolves to nothing,
quietly -- which is what the floor checks at the end are for.

WHAT THIS MODULE REFUSES TO DO:

* It matches NO name and resolves NO subject itself. The matcher is
  `spl_match.find_matches`, the subject rule is `spl_subject.resolve_subject`,
  and the window rule is `spl_quote.budgeted_windows`. A rule duplicated here
  would be a second place for it to drift out of -- which is exactly how the
  design round published a delta whose two arms used different subject rules.
* It grades nothing and extracts no relation. That two drugs are named together
  in an interactions section is the whole claim.
* It does not retry or fall back to a partial write. Any exception once the run
  is open rolls back the WHOLE run and re-raises.
"""
from __future__ import annotations

import logging
import pathlib
from collections.abc import Callable, Mapping, Sequence
from importlib import resources

import psycopg

from drugref import (
    provenance, questions, registry_read, spl_census, spl_class_evidence,
    spl_evidence)
from drugref.ingest import (
    spl, spl_checks, spl_match, spl_official_names, spl_quote, spl_release,
    spl_rows, spl_suppress, spl_subject,
)
from drugref.ingest.checksum import checksum
from drugref.ingest.spl_checks import (
    MEASURED_CLASS_FLOOR, MEASURED_NOVEL_FLOOR, MEASURED_PAIR_FLOOR, SplSummary,
)

__all__ = ["MEASURED_CLASS_FLOOR", "MEASURED_NOVEL_FLOOR", "MEASURED_PAIR_FLOOR",
           "SplSummary", "build_vocabulary", "checksummed_inputs", "ingest_spl"]

log = logging.getLogger(__name__)

#: How many wordings are matched, quoted and written per `COPY`. It bounds peak
#: memory: the corpus averages ~48 moiety occurrences per wording, so a chunk of
#: 2,000 holds ~96,000 rows rather than the ~1.3 million a whole-corpus list
#: would. Purely an internal batching choice -- no figure depends on it, which is
#: why the average is stated to an order of magnitude and not to a decimal.
#: `test_the_corpus_is_written_IDENTICALLY_when_it_spans_several_chunks` drives
#: a corpus spanning several chunks and asserts the counts do not move; before
#: it existed, widening the stride past the chunk size silently dropped one
#: wording per chunk with the whole suite green.
WORDING_CHUNK = 2_000


def build_vocabulary(
    names: Mapping[str, str], suppression_terms: Sequence[str] | None = None
) -> spl_match.Vocabulary:
    """The matcher's vocabulary: every name in `names`, plus suppression.

    `names` is `match key -> moiety_uuid` (display names plus the official names
    `spl_official_names` adopted, #213). Suppression defaults to the SHIPPED
    list, so an ingest that forgot to pass it gets the measured behaviour rather
    than the naive one -- the pair floor's baseline includes suppression, and a
    run without it measures a different corpus.

    A registry name folding to no tokens is DROPPED rather than refused: a key of
    `()` would match at every position, and one unfortunate display name is not a
    reason to refuse a whole release. Uncounted here (`spl_official_names`
    counts its own as `unfoldable`).
    """
    terms = (spl_suppress.shipped_suppression_terms()
             if suppression_terms is None else tuple(suppression_terms))
    entries = [spl_match.Entry(kind=spl_match.KIND_SUPPRESS, key=term,
                               display=term, moiety_uuid=None)
               for term in terms]
    for display_name, moiety_uuid in names.items():
        if not spl_match.fold(display_name):
            continue
        entries.append(spl_match.Entry(kind=spl_match.KIND_MOIETY,
                                       key=display_name, display=display_name,
                                       moiety_uuid=moiety_uuid))
    return spl_match.build_vocabulary(entries)


def checksummed_inputs(
    partitions: Sequence[pathlib.Path], parts: Sequence[pathlib.Path],
    unii_names: str | pathlib.Path | None,
) -> list[pathlib.Path | resources.abc.Traversable]:
    """Every file whose bytes can move this run's pairs, in `checksum` order (#228).

    What a run consumed is the TUPLE of files, so its `source_checksum` must change
    if any one of them does: both corpora, the UNII_Names release (adopted as match
    keys since #213, and R19/P1's class-key drops), and the two SHIPPED lists the
    run always reads (suppression terms, official-name exclusions). Without the
    last three, two runs with one checksum could publish different pairs. Files
    sorted by name, so the same releases hash the same however the CLI globbed
    them; the two data files last, in a fixed order.
    """
    files = [*partitions, *parts, *([pathlib.Path(unii_names)] if unii_names else [])]
    data = resources.files("drugref.data")
    return [*sorted(files, key=lambda p: p.name),
            data / spl_suppress.SUPPRESSION_DATA,
            data / spl_official_names.EXCLUSIONS_DATA]


def _write_evidence(
    conn: psycopg.Connection, corpus: spl.Corpus, vocab: spl_match.Vocabulary,
    class_vocab: spl_match.Vocabulary, *,
    ingest_run_id: int, source: str,
    progress: Callable[[int, int], None] | None = None,
) -> spl_class_evidence.EvidenceCounts:
    """Match every wording and write its evidence, in bounded chunks.

    The class pass is SEPARATE from the moiety pass (own vocabulary, own table), so
    the moiety figures cannot move. REQUIRED: no caller can skip it silently.

    CHUNKED so peak memory does not scale with the corpus (1.3M rows is 100s of MB).
    """
    keys = sorted(corpus.wordings)
    occurrences = with_moiety = quotes = quoted_chars = 0
    class_total = with_class = inside = boundary_drops = head_drops = 0
    for start in range(0, len(keys), WORDING_CHUNK):
        chunk = keys[start:start + WORDING_CHUNK]
        occurrence_rows: list[spl_evidence.OccurrenceRow] = []
        quote_rows: list[spl_evidence.QuoteRow] = []
        class_rows: list[spl_class_evidence.ClassOccurrenceRow] = []
        for text_key in chunk:
            found, quoted = spl_rows.evidence_for_wording(
                text_key, corpus.wordings[text_key], vocab)
            if found:
                with_moiety += 1
            cp = spl_class_evidence.class_pass_for_wording(
                text_key, corpus.wordings[text_key], class_vocab,
                [(o.char_start, o.char_end) for o in found])
            with_class += bool(cp.rows)
            inside += cp.moiety_spans_inside
            boundary_drops += cp.boundary_drops
            head_drops += cp.head_drops
            class_rows.extend(cp.rows)
            occurrence_rows.extend(found)
            quote_rows.extend(quoted)
        occurrences += spl_evidence.write_occurrences(
            conn, occurrence_rows, ingest_run_id=ingest_run_id, source=source)
        class_total += spl_evidence.write_class_occurrences(
            conn, class_rows, ingest_run_id=ingest_run_id, source=source)
        quotes += spl_evidence.write_quotes(
            conn, quote_rows, ingest_run_id=ingest_run_id, source=source)
        quoted_chars += sum(row.char_end - row.char_start for row in quote_rows)
        if progress is not None:
            progress(min(start + WORDING_CHUNK, len(keys)), len(keys))
    return spl_class_evidence.EvidenceCounts(
        occurrences=occurrences, wordings_with_a_moiety=with_moiety, quotes=quotes,
        quoted_chars=quoted_chars, class_occurrences=class_total,
        wordings_with_a_class=with_class, moiety_spans_inside_class_spans=inside,
        class_spans_dropped_at_a_boundary=boundary_drops,
        class_spans_dropped_on_a_non_drug_head=head_drops)


def ingest_spl(
    conn: psycopg.Connection, *,
    openfda_dir: str | pathlib.Path,
    dailymed_parts: Sequence[str | pathlib.Path],
    release: str,
    pair_floor: int | None = None,
    novel_floor: int | None = None,
    class_floor: int | None = None,
    progress: Callable[[str], None] | None = None,
    unii_names: str | pathlib.Path | None = None,
) -> SplSummary:
    """Read both corpora and rebuild this source's projection.

    Owns TWO transactions on one connection, exactly as every other orchestrator
    here: `provenance.open_run` commits the run record in its own transaction
    before any writing begins, so a crash from there on leaves that row standing
    with `finished_at IS NULL` rather than no trace at all.

    `pair_floor` / `novel_floor` / `class_floor`, when given, assert the measured
    floor with `>=`: None here, `MEASURED_*_FLOOR` at the CLI (the production
    path), so a real run checks by default. `unii_names` (UNII_Names_*.txt,
    required by the CLI) drives #213 adoption and R19/P1; None skips both, warned.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    # AUTOCOMMIT VOIDS EVERY GUARANTEE BELOW, AND POSTGRES ONLY WHISPERS ABOUT IT.
    # Under autocommit each statement is its own transaction, so `conn.rollback()`
    # rolls back nothing and a failure between the clear and `finish_run` leaves
    # the projection cleared and half-rewritten. It would also break the quote
    # budget outright: the trigger is DEFERRED to commit, and under autocommit
    # every row commits alone, so it would fire against a wording holding one
    # window and pass every time.
    if conn.autocommit:
        raise ValueError(
            "spl: this ingest owns its transactions and must not be handed an "
            "autocommit connection -- the per-source clear and the rows that "
            "replace it have to commit together or not at all, and the deferred "
            "quote-budget trigger only means anything at a real commit.")

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    openfda_dir = pathlib.Path(openfda_dir)
    partitions = sorted(openfda_dir.glob("drug-label-*.json.zip"))
    if not partitions:
        raise ValueError(
            f"spl: no openFDA partitions (drug-label-*.json.zip) under "
            f"{openfda_dir} -- nothing to read, and refusing to clear the "
            "existing projection to find that out.")
    parts = [pathlib.Path(part) for part in dailymed_parts]

    # ---- 1. read openFDA, and refuse a corpus carrying no sections -----------
    say(f"reading {len(partitions)} openFDA partition(s)")
    corpus = spl.read_corpus(partitions)
    spl.check_something_was_read(corpus.labels, records=corpus.records)
    say(f"  {len(corpus.labels):,} labels of {corpus.records:,} records, "
        f"{len(corpus.wordings):,} distinct wordings")
    # #163's counts, said BEFORE any floor can refuse: a refused run rolls its
    # db/057 census back, so this line is all a refused, drifted release leaves.
    say(f"  {corpus.field_absent:,} absent, {corpus.field_blank:,} blank, "
        f"{corpus.labels_keyed_on_document_id:,} keyed on document id")

    # ---- 2. the registry (and class names), read before the run opens --------
    # Read BEFORE the run is opened: it is a read, a single statement sees a
    # single snapshot at any isolation level, and everything that can refuse
    # below needs it. `substance_moiety` rows are immortal, so a uuid read here
    # is still a valid foreign-key target when the write happens an hour later.
    # NAMED fields, not a positional unpack: the two mappings are the same type,
    # and swapping them would build the matcher out of UNII codes and resolve
    # every subject against display names -- a failure only `check_floors` would
    # notice, at the very end of the run.
    registry = registry_read.load_registry(conn)
    names, known_uniis = registry.by_name, registry.by_unii
    if not names or not known_uniis:
        raise ValueError(
            f"spl: the registry holds {len(names)} moiety name(s) and "
            f"{len(known_uniis)} live UNII claim(s). This ingest resolves every "
            "subject through UNII and every occurrence through display_name, so "
            "against an empty registry it would publish nothing -- run `ingest "
            "unii` and `ingest chebi` first.")
    say(f"  registry: {len(names):,} moiety names, {len(known_uniis):,} UNIIs")
    # UNII_Names read ONCE: the drug pass adopts its names (#213), R19/P1 drops them.
    official = (spl_class_evidence.read_official_name_rows(unii_names, known_uniis)
                if unii_names else None)
    adopted, adoption = spl_official_names.for_run(registry, official)
    vocab = build_vocabulary(adopted)
    say(f"  registry: {adoption}")
    # Classes: a 2nd statement (READ COMMITTED: maybe a later snapshot; nothing
    # writes either mid-ingest). Zero is legitimate; the CLI's class floor refuses.
    class_vocab, class_keys = spl_class_evidence.class_vocabulary_for_run(
        conn, names, None if official is None else {n for n, _ in official})
    say(f"  registry: {class_keys}")
    # Reported, not merely counted: a UNII two moieties claim resolves first-wins,
    # so the other silently never appears (`identity_claim` permits this by design).
    if registry.name_collisions or registry.unii_collisions:
        say(f"  registry collisions (first-wins): "
            f"{registry.name_collisions:,} display name(s), "
            f"{registry.unii_collisions:,} UNII(s)")

    # ⇒ CLOSE THE SNAPSHOT BEFORE THE EXPENSIVE PASS.
    # `load_registry` is the first statement on a non-autocommit connection, so
    # it OPENED a transaction, and nothing below commits until `open_run` on the
    # far side of the DailyMed scan and the checksum. Measured 2026-09-01, that
    # window is ~50 s of `idle in transaction` -- the scan plus the 19.3 GB
    # checksum, not the whole run, and not the 262,032-record openFDA read, which
    # happens above `load_registry`. (`open_run` commits, so the window is finite
    # anyway; this rollback makes it ~0 rather than ~50 s.) A snapshot held even
    # that long pins `xmin` database-wide, so autovacuum can reclaim nothing in
    # ANY table for the duration, and where `idle_in_transaction_session_timeout` is set
    # the backend is killed at the END of the most expensive step in the ingest.
    # Rolling back is safe and costs nothing -- the registry is already
    # materialised in Python above (`fetchall`), and `substance_moiety` rows are
    # immortal, which is the same fact the comment above leans on. Rollback
    # rather than commit because nothing was written and a read needs no commit.
    conn.rollback()

    # ---- 3 & 4. the expensive pass, and its refusal --------------------------
    targets = spl_subject.dailymed_targets(
        ({"set_id": label.set_id, "uniis": label.uniis,
          "text_key": label.text_key} for label in corpus.labels),
        known_uniis=known_uniis)
    say(f"scanning {len(parts)} DailyMed part(s) for {len(targets):,} labels")
    scan = spl_release.scan_release(
        [str(part) for part in parts], targets,
        progress=(lambda part: say(f"  {part}")) if progress else None)
    spl_checks.check_scan_dropped_nothing(scan)
    say(f"  read {scan.documents_read:,} documents, found {len(scan.found):,} "
        "of the labels looked for")
    reported = spl_release.describe_reported_skips(scan)
    if reported:
        say(f"  {reported}")

    # ONE digest over every input, through the shared helper (see
    # `checksummed_inputs` for what counts as an input and why).
    digest = checksum(*checksummed_inputs(partitions, parts, unii_names))

    try:
        run_id = provenance.open_run(conn, source=spl.SOURCE,
                                     upstream_release=release,
                                     source_checksum=digest,
                                     writer=spl.WRITER, clock=clock)

        spl_evidence.clear_source_spl(conn, spl.SOURCE)

        wording_rows = [
            spl_evidence.WordingRow(
                text_key=text_key,
                char_length=len(corpus.wordings[text_key]),
                label_count=corpus.label_counts[text_key])
            for text_key in sorted(corpus.wordings)]
        stored_wordings = spl_evidence.write_wordings(
            conn, wording_rows, ingest_run_id=run_id, source=spl.SOURCE)
        # ⇒ ANALYSE A PARENT BEFORE LOADING ANYTHING THAT REFERENCES IT (issue
        # 160). Each foreign-key check below is planned the FIRST time it fires,
        # so a parent with no statistics at that moment pins a bad plan and the
        # whole load pays it -- and the ANALYZE at the end of this function
        # arrives after the COPY has already been paid for, not in time to save
        # it. Measured on the real releases: 493,539 ms for the subject COPY
        # without these two calls, 1,352 ms with them, bought by 112 ms of
        # ANALYZE. `analyze_loaded_table` carries the mechanism.
        spl_evidence.analyze_loaded_table(conn, "spl_wording")

        stored_labels = spl_evidence.write_labels(
            conn,
            (spl_evidence.LabelRow(
                set_id=label.set_id, version=label.version,
                effective_time=label.effective_time,
                product_type=label.product_type, text_key=label.text_key)
             for label in corpus.labels),
            ingest_run_id=run_id, source=spl.SOURCE)
        spl_evidence.analyze_loaded_table(conn, "spl_label")

        subject_rows, by_route = spl_rows.subject_rows(
            corpus.labels, scan.found, known_uniis)
        spl_evidence.write_label_subjects(
            conn, subject_rows, ingest_run_id=run_id, source=spl.SOURCE)

        say(f"matching {len(corpus.wordings):,} wordings")
        counts = _write_evidence(
            conn, corpus, vocab, class_vocab, ingest_run_id=run_id, source=spl.SOURCE,
            progress=(lambda done, total: say(f"  {done:,}/{total:,}"))
            if progress else None)

        # EVERY READ-BACK BELOW QUERIES A TABLE THIS TRANSACTION JUST BULK-LOADED,
        # so the planner would otherwise cost them as if the tables were empty.
        # Measured: without this the self-pair count ran 25 minutes at 100% CPU
        # and had not finished. See `analyze_source_tables` for the measurement,
        # and for the diagnosis it replaced.
        say("analysing the projection so the read-backs can be planned")
        spl_evidence.analyze_source_tables(conn)

        # ---- RECONCILE AGAINST WHAT ACTUALLY LANDED, INSIDE THE TRANSACTION --
        # Every identity on SplSummary is computed in Python from Python
        # counters, so they can only prove this module is self-consistent. These
        # are the numbers read back out of the database, and they are the ones
        # that can contradict it. Raising here rolls the whole run back, so the
        # database keeps the previous projection rather than a miscounted one.
        spl_checks.reconcile(conn, run_id,
                   wordings=stored_wordings, labels=stored_labels,
                   subjects=len(subject_rows),
                   occurrences=counts.occurrences,
                   class_occurrences=counts.class_occurrences)

        pairs, novel, self_pairs = spl_checks.read_pairs(conn, run_id)
        quotable = sum(spl_quote.quote_budget(row.char_length)
                       for row in wording_rows)

        summary = SplSummary(
            records_read=corpus.records,
            labels=len(corpus.labels),
            wordings=len(corpus.wordings),
            records_field_absent=corpus.field_absent,
            records_field_blank=corpus.field_blank,
            labels_keyed_on_document_id=corpus.labels_keyed_on_document_id,
            labels_by_route=by_route,
            dailymed_targets=len(targets),
            dailymed_documents_read=scan.documents_read,
            dailymed_found=len(scan.found),
            dailymed_reported_skips=reported,
            occurrences=counts.occurrences,
            wordings_with_a_moiety=counts.wordings_with_a_moiety,
            quotes=counts.quotes,
            quoted_chars=counts.quoted_chars,
            quotable_chars=quotable,
            self_pairs=self_pairs,
            pairs=pairs,
            novel_pairs=novel,
            class_occurrences=counts.class_occurrences,
            wordings_with_a_class=counts.wordings_with_a_class,
            moiety_spans_inside_class_spans=(
                counts.moiety_spans_inside_class_spans),
            **counts.class_drop_fields(), **class_keys.summary_fields(),
            **adoption.summary_fields())

        spl_checks.check_floors(
            summary, pair_floor=pair_floor, novel_floor=novel_floor,
            class_floor=class_floor)
        # #186: the counts that must never refuse (#163's, then #215's class
        # drops and #229's adoption counts), kept per run so the next release
        # has this one to compare against (db/057, db/072). Inside the
        # transaction, so a run that rolls back leaves no census behind.
        spl_census.write_run_census(conn, ingest_run_id=run_id, summary=summary)

        # This run now feeds a gap kind (unconfirmed_label_class_statement), so it
        # registers questions. Like fda_cyp_run it re-derives EVERY kind and stamps
        # last_derived_ingest with this run ("when last seen"); that is the repo
        # convention. gap_unresolved_spl_subject stays deliberately NOT a question
        # kind (db/051 section 8). Inside the transaction: a rollback leaves none.
        questions.register_from_gaps(conn, run_id)

        provenance.finish_run(conn, run_id)
        # THE DEFERRED QUOTE-BUDGET TRIGGER FIRES HERE, at this commit, over
        # every wording written above. A budget violation raises out of
        # `commit()` and lands in the `except` below.
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("SPL ingest failed for release %s; rolled back", release)
        raise

    log.info("spl: %s", summary)
    return summary
