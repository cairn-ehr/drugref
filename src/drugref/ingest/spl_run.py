# src/drugref/ingest/spl_run.py
"""Orchestrate one SPL ingest: read -> resolve -> scan -> clear -> write -> check.

The ONLY writer of drugref's SPL rows, per the architecture invariant: parsers
are pure, orchestrators own the transaction.

ORDER MATTERS, and here it matters more than in any sibling because the expensive
pass is 19.3 GB long:

  1. read openFDA and run the floor check;
  2. read the registry -- one statement, one snapshot;
  3. decide which labels the DailyMed pass has to look for;
  4. scan DailyMed once, and REFUSE if it dropped anything for a reading reason;
  5. only now open the run, clear this source's rows and write.

**EVERY REFUSAL IN STEPS 1-4 HAPPENS BEFORE THE RUN ROW EXISTS**, which is
stricter than `drugcentral_run`'s ordering and for the same reason it gives: a
refusal must leave the database exactly as it was, and an `ingest_run` with
`finished_at NULL` is not "exactly as it was". Here the scan takes tens of
minutes, so a run row opened before it would sit unfinished for the whole of a
pass that might yet refuse.

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

import psycopg

from drugref import provenance, spl_evidence
from drugref.ingest import (
    spl, spl_checks, spl_dailymed, spl_match, spl_quote, spl_subject,
)
from drugref.ingest.checksum import checksum
from drugref.ingest.spl_checks import (
    MEASURED_NOVEL_FLOOR, MEASURED_PAIR_FLOOR, SplSummary,
)

__all__ = ["MEASURED_NOVEL_FLOOR", "MEASURED_PAIR_FLOOR", "SplSummary",
           "build_vocabulary", "ingest_spl"]

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
    """The matcher's vocabulary: every registry display name, plus suppression.

    `names` is `display_name -> moiety_uuid`. The suppression vocabulary defaults
    to the SHIPPED one, so an ingest that forgot to pass it gets the measured
    behaviour rather than the naive one -- the baseline the 29,258-pair floor was
    measured against includes suppression, and a run without it measures a
    different corpus.

    A registry name folding to no tokens is DROPPED rather than refused: a key of
    `()` would match at every position, and one unfortunate display name is not a
    reason to refuse a whole release. It is counted by the caller through the
    difference between `len(names)` and the vocabulary's size.
    """
    terms = (spl_match.shipped_suppression_terms()
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


def _subject_rows(
    labels: Sequence[spl.LabelIdentity],
    recovered: Mapping[str, spl_dailymed.SubjectUniis],
    known_uniis: Mapping[str, str],
) -> tuple[list[spl_evidence.SubjectRow], dict[str, int]]:
    """One subject row per (label, moiety), plus one per label with no subject.

    Returns the rows and the per-route label tally. The tally counts LABELS, not
    rows: a combination product carries several subjects on one route, and
    reporting rows here would publish the combination rate as if it were a
    resolution rate.
    """
    rows: list[spl_evidence.SubjectRow] = []
    by_route: dict[str, int] = {}
    for label in labels:
        subject = spl_subject.resolve_subject(
            openfda_uniis=label.uniis,
            dailymed=recovered.get(label.set_id),
            known_uniis=known_uniis)
        by_route[subject.route] = by_route.get(subject.route, 0) + 1
        if not subject.moiety_uuids:
            rows.append(spl_evidence.SubjectRow(
                set_id=label.set_id, version=label.version, subject_ordinal=0,
                moiety_uuid=None, route=subject.route))
            continue
        for ordinal, moiety_uuid in enumerate(subject.moiety_uuids):
            rows.append(spl_evidence.SubjectRow(
                set_id=label.set_id, version=label.version,
                subject_ordinal=ordinal, moiety_uuid=moiety_uuid,
                route=subject.route))
    return rows, by_route


def _evidence_for_wording(
    text_key: str, text: str, vocab: spl_match.Vocabulary
) -> tuple[list[spl_evidence.OccurrenceRow], list[spl_evidence.QuoteRow]]:
    """The occurrence and quote rows one wording yields. Matched ONCE.

    The two are produced together because they rest on the same match pass, and
    running the matcher twice would be two chances for the offsets a quote is cut
    at to differ from the offsets an occurrence records.
    """
    matches = spl_match.find_matches(text, vocab)
    occurrences = spl_match.moiety_occurrences(matches)
    occurrence_rows = [
        spl_evidence.OccurrenceRow(
            text_key=text_key, char_start=occurrence.char_start,
            char_end=occurrence.char_end, moiety_uuid=occurrence.moiety_uuid,
            match_ambiguous=occurrence.ambiguous)
        for occurrence in occurrences]
    # `from_window` cuts the text itself rather than taking a pre-cut string
    # beside two offsets. The offsets and the cut then cannot disagree, and the
    # window is checked against the length of the text it was measured on --
    # which is the only place the raw-versus-normalised mistake is visible,
    # because Python slicing clamps an over-long window silently.
    quote_rows = [
        spl_evidence.QuoteRow.from_window(
            text_key=text_key, ordinal=ordinal, window=window, text=text)
        for ordinal, window, _quote_text in spl_quote.quotes_for(
            text,
            [(o.moiety_uuid, o.char_start, o.char_end) for o in occurrences])]
    return occurrence_rows, quote_rows


def _write_evidence(
    conn: psycopg.Connection, corpus: spl.Corpus, vocab: spl_match.Vocabulary, *,
    ingest_run_id: int, source: str,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int, int, int]:
    """Match every wording and write its evidence, in bounded chunks.

    Returns `(occurrences, wordings_with_a_moiety, quotes, quoted_chars)`.

    CHUNKED so peak memory does not scale with the corpus: 1.3 million occurrence
    rows held at once is hundreds of megabytes to say something that can be
    written 96,000 rows at a time.
    """
    keys = sorted(corpus.wordings)
    occurrences = with_moiety = quotes = quoted_chars = 0
    for start in range(0, len(keys), WORDING_CHUNK):
        chunk = keys[start:start + WORDING_CHUNK]
        occurrence_rows: list[spl_evidence.OccurrenceRow] = []
        quote_rows: list[spl_evidence.QuoteRow] = []
        for text_key in chunk:
            found, quoted = _evidence_for_wording(
                text_key, corpus.wordings[text_key], vocab)
            if found:
                with_moiety += 1
            occurrence_rows.extend(found)
            quote_rows.extend(quoted)
        occurrences += spl_evidence.write_occurrences(
            conn, occurrence_rows, ingest_run_id=ingest_run_id, source=source)
        quotes += spl_evidence.write_quotes(
            conn, quote_rows, ingest_run_id=ingest_run_id, source=source)
        quoted_chars += sum(row.char_end - row.char_start for row in quote_rows)
        if progress is not None:
            progress(min(start + WORDING_CHUNK, len(keys)), len(keys))
    return occurrences, with_moiety, quotes, quoted_chars


def ingest_spl(
    conn: psycopg.Connection, *,
    openfda_dir: str | pathlib.Path,
    dailymed_parts: Sequence[str | pathlib.Path],
    release: str,
    pair_floor: int | None = None,
    novel_floor: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> SplSummary:
    """Read both corpora and rebuild this source's projection.

    Owns TWO transactions on one connection, exactly as every other orchestrator
    here: `provenance.open_run` commits the run record in its own transaction
    before any writing begins, so a crash from there on leaves that row standing
    with `finished_at IS NULL` rather than no trace at all.

    `pair_floor` / `novel_floor`, when given, assert the measured floor with `>=`.
    They default to `None` here and to `MEASURED_PAIR_FLOOR` /
    `MEASURED_NOVEL_FLOOR` at the CLI, which is the production path -- so a real
    run checks by default and a partial-corpus run has to say so out loud.
    """
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

    # ---- 2. the registry, in ONE statement ----------------------------------
    # Read BEFORE the run is opened: it is a read, a single statement sees a
    # single snapshot at any isolation level, and everything that can refuse
    # below needs it. `substance_moiety` rows are immortal, so a uuid read here
    # is still a valid foreign-key target when the write happens an hour later.
    # NAMED fields, not a positional unpack: the two mappings are the same type,
    # and swapping them would build the matcher out of UNII codes and resolve
    # every subject against display names -- a failure only `check_floors` would
    # notice, twelve minutes later.
    registry = spl_evidence.load_registry(conn)
    names, known_uniis = registry.by_name, registry.by_unii
    if not names or not known_uniis:
        raise ValueError(
            f"spl: the registry holds {len(names)} moiety name(s) and "
            f"{len(known_uniis)} live UNII claim(s). This ingest resolves every "
            "subject through UNII and every occurrence through display_name, so "
            "against an empty registry it would publish nothing -- run `ingest "
            "unii` and `ingest chebi` first.")
    vocab = build_vocabulary(names)
    say(f"  registry: {len(names):,} moiety names, {len(known_uniis):,} UNIIs")
    # Reported rather than merely counted. A UNII two moieties claim resolves
    # first-wins, so every subject derived from it names ONE of them and the
    # other silently never appears -- `identity_claim` permits this by design, so
    # the operator has to be able to see how often it happened.
    if registry.name_collisions or registry.unii_collisions:
        say(f"  registry collisions (first-wins): "
            f"{registry.name_collisions:,} display name(s), "
            f"{registry.unii_collisions:,} UNII(s)")

    # ⇒ CLOSE THE SNAPSHOT BEFORE THE EXPENSIVE PASS.
    # `load_registry` is the first statement on a non-autocommit connection, so
    # it OPENED a transaction, and nothing below commits until `open_run` on the
    # far side of the DailyMed scan and the checksum. Measured, that is ~12.5
    # minutes of `idle in transaction` on a connection holding a snapshot: it
    # pins `xmin` database-wide, so autovacuum can reclaim nothing in ANY table
    # for the duration, and where `idle_in_transaction_session_timeout` is set
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
    scan = spl_dailymed.scan_release(
        [str(part) for part in parts], targets,
        progress=(lambda part: say(f"  {part}")) if progress else None)
    spl_checks.check_scan_dropped_nothing(scan)
    say(f"  read {scan.documents_read:,} documents, found {len(scan.found):,} "
        "of the labels looked for")

    # ONE digest over BOTH corpora, through the shared helper -- not a second
    # hashing idiom of this module's own. `checksum` already streams over several
    # files as one digest and says why that is the right unit: what a run consumed
    # is the TUPLE of files, so its provenance has to change if either corpus
    # does. Sorted by name, so the same two releases always hash the same however
    # the CLI happened to glob them.
    digest = checksum(*sorted([*partitions, *parts], key=lambda p: p.name))

    try:
        run_id = provenance.open_run(conn, source=spl.SOURCE,
                                     upstream_release=release,
                                     source_checksum=digest,
                                     writer=spl.WRITER)

        spl_evidence.clear_source_spl(conn, spl.SOURCE)

        wording_rows = [
            spl_evidence.WordingRow(
                text_key=text_key,
                char_length=len(corpus.wordings[text_key]),
                label_count=corpus.label_counts[text_key])
            for text_key in sorted(corpus.wordings)]
        stored_wordings = spl_evidence.write_wordings(
            conn, wording_rows, ingest_run_id=run_id, source=spl.SOURCE)

        stored_labels = spl_evidence.write_labels(
            conn,
            (spl_evidence.LabelRow(
                set_id=label.set_id, version=label.version,
                effective_time=label.effective_time,
                product_type=label.product_type, text_key=label.text_key)
             for label in corpus.labels),
            ingest_run_id=run_id, source=spl.SOURCE)

        subject_rows, by_route = _subject_rows(
            corpus.labels, scan.found, known_uniis)
        spl_evidence.write_label_subjects(
            conn, subject_rows, ingest_run_id=run_id, source=spl.SOURCE)

        say(f"matching {len(corpus.wordings):,} wordings")
        occurrences, with_moiety, quotes, quoted_chars = _write_evidence(
            conn, corpus, vocab, ingest_run_id=run_id, source=spl.SOURCE,
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
                   subjects=len(subject_rows), occurrences=occurrences)

        pairs, novel, self_pairs = spl_checks.read_pairs(conn, run_id)
        quotable = sum(spl_quote.quote_budget(row.char_length)
                       for row in wording_rows)

        summary = SplSummary(
            records_read=corpus.records,
            labels=len(corpus.labels),
            wordings=len(corpus.wordings),
            labels_by_route=by_route,
            dailymed_targets=len(targets),
            dailymed_documents_read=scan.documents_read,
            dailymed_found=len(scan.found),
            occurrences=occurrences,
            wordings_with_a_moiety=with_moiety,
            quotes=quotes,
            quoted_chars=quoted_chars,
            quotable_chars=quotable,
            self_pairs=self_pairs,
            pairs=pairs,
            novel_pairs=novel)

        spl_checks.check_floors(
            summary, pair_floor=pair_floor, novel_floor=novel_floor)

        # `register_from_gaps` IS DELIBERATELY NOT CALLED, unlike in every
        # sibling orchestrator. This run writes only its own five tables, and
        # `gap_unresolved_spl_subject` is not an `open_question` kind -- db/051
        # section 8 says why, at length. Calling it would re-derive eighteen
        # kinds this run cannot have changed, and would stamp SPL's run id as
        # `last_derived_ingest` on questions it had no part in deriving.

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
