# src/drugref/ingest/spl_checks.py
"""The summary type and the four guards that stand between an SPL ingest and a
silently empty projection.

SPLIT OUT OF `spl_run.py` under CLAUDE.md rule 4: that module had crossed the
~500-line guideline, and this is the half with one job -- deciding whether what
the ingest produced may be reported as success.

**WHY THE GUARDS ARE A MODULE AND NOT A HANDFUL OF `if`s AT THE END OF THE RUN.**
db/050's finding was that every guard in a slice passed VACUOUSLY. Every
reconciliation an orchestrator can do in Python proves it is self-consistent and
none proves it published anything: the all-zeros run satisfies all of them --
`stored (0) == written (0)`, buckets summing to zero -- while the per-source
clear has already deleted the previous release's rows. So the checks that matter
are the ones comparing two quantities derived by DIFFERENT routes, and putting
them where they can be read together, and tested without an ingest, is how they
stay that way.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping

import psycopg

from drugref.ingest import spl_dailymed, spl_subject

#: THE MEASURED PAIR FLOOR, on the 2026-08-22 openFDA and 2026-08-21 DailyMed
#: releases. **A FLOOR, NOT A TARGET, and the check asserts `>=`.**
#:
#: An ingest reproducing MORE is not failing: the measurement scanned only
#: orphan-wording labels, so the 14,455 unkeyed labels sharing a keyed label's
#: wording contributed no subject and therefore no pairs, and 200 labels carrying
#: a UNII drugref does not hold were filed as keyed by the probe's classifiers.
#: This ingest scans both populations, and prints the actual figure so the
#: difference is VISIBLE rather than absorbed.
MEASURED_PAIR_FLOOR = 29_258
MEASURED_NOVEL_FLOOR = 25_960


@dataclasses.dataclass(frozen=True, kw_only=True)
class SplSummary:
    """What one ingest did, in buckets that RECONCILE.

    BE CLEAR ABOUT WHAT EACH CHECK CAN AND CANNOT CATCH -- `drugcentral_run`'s
    `DrugCentralSummary` states the same distinction for its own, and this round
    exists partly because a previous one published three figures that only ever
    agreed with themselves.

    Satisfied BY CONSTRUCTION at the one call site, so they are a contract for a
    future caller rather than a guard that can fire today: the route buckets sum
    to `labels`, because every label is assigned exactly one route by a total
    function.

    The checks that CAN fail are the ones comparing two quantities computed by
    DIFFERENT routes -- `wordings_with_a_moiety <= wordings`, `quoted_chars`
    against the budget, and above all the read-backs in `ingest_spl`, which
    compare what Python thinks it wrote against what the database actually holds.
    """

    records_read: int
    labels: int
    wordings: int
    labels_by_route: Mapping[str, int]
    dailymed_targets: int
    dailymed_documents_read: int
    dailymed_found: int
    occurrences: int
    wordings_with_a_moiety: int
    quotes: int
    quoted_chars: int
    quotable_chars: int
    self_pairs: int
    pairs: int
    novel_pairs: int

    def __post_init__(self) -> None:
        tallied = sum(self.labels_by_route.values())
        if tallied != self.labels:
            raise ValueError(
                f"the route buckets sum to {tallied}, but {self.labels} labels "
                "were read -- every label takes exactly one route")
        for route in self.labels_by_route:
            if route not in spl_subject.SUBJECT_ROUTES:
                raise ValueError(
                    f"route {route!r} is not in the vocabulary db/051's CHECK "
                    f"admits: {spl_subject.SUBJECT_ROUTES}")
        if self.wordings_with_a_moiety > self.wordings:
            raise ValueError(
                f"{self.wordings_with_a_moiety} wordings name a moiety but only "
                f"{self.wordings} were read")
        if self.dailymed_found > self.dailymed_targets:
            raise ValueError(
                f"the scan found {self.dailymed_found} of "
                f"{self.dailymed_targets} targeted labels: it cannot find more "
                "than it looked for, so the two describe different populations")
        # NOT tautological, and it is the licensing determination in one line:
        # `quoted_chars` is summed over the windows actually written, and
        # `quotable_chars` over each wording's independently-computed budget.
        if self.quoted_chars > self.quotable_chars:
            raise ValueError(
                f"{self.quoted_chars} characters quoted against a budget of "
                f"{self.quotable_chars} -- issue 154's determination is a "
                "bounded window, and this ingest exceeded it")

    @property
    def resolved_labels(self) -> int:
        return sum(self.labels_by_route.get(route, 0)
                   for route in spl_subject.RESOLVING_ROUTES)

    @property
    def quoted_share(self) -> float:
        """Stored characters as a share of what the budget would have allowed."""
        return self.quoted_chars / self.quotable_chars if self.quotable_chars else 0.0

    def __str__(self) -> str:
        routes = ", ".join(
            f"{route} {self.labels_by_route.get(route, 0):,}"
            for route in spl_subject.SUBJECT_ROUTES)
        return (
            f"{self.labels:,} labels of {self.records_read:,} records carrying "
            f"{self.wordings:,} wordings -> {self.pairs:,} pairs "
            f"({self.novel_pairs:,} novel); subjects: {routes}; "
            f"{self.occurrences:,} occurrences over "
            f"{self.wordings_with_a_moiety:,} wordings; {self.quotes:,} quoted "
            f"windows using {self.quoted_chars:,} of {self.quotable_chars:,} "
            f"budgeted characters ({self.quoted_share:.1%}); "
            f"{self.self_pairs:,} self-pairs excluded")


def check_scan_dropped_nothing(scan: spl_dailymed.ScanResult) -> None:
    """Refuse a scan that lost documents for a READING reason.

    A document dropped here is republished by `spl_label_subject` as
    `absent_from_dailymed` -- a fact about this code sold as a fact about the
    release, and the design spec turns that route's population into a
    commitment. Measured on the 2026-08-21 Human Rx release: all three counters
    are ZERO, which is what lets *"the limit is the release, not the reading"*
    be a measurement rather than an inference.

    Raised BEFORE the run is opened, so a release this reader cannot handle
    leaves the previous projection standing.
    """
    if scan.total_dropped:
        raise ValueError(
            f"SPL: the DailyMed scan dropped {scan.total_dropped} document(s) "
            f"for a reading reason ({scan.dropped_no_set_id_bytes} with no "
            f"setId in the bytes, {scan.dropped_unreadable} unreadable, "
            f"{scan.dropped_prefilter_disagreed} where the byte pre-filter "
            "named a different setId than the document). They would be "
            "republished as 'absent from DailyMed', which is a fact about this "
            "reader rather than about the release. Fix the reader before "
            "quoting any recovery figure.")


def reconcile(conn: psycopg.Connection, run_id: int, *, wordings: int,
               labels: int, subjects: int, occurrences: int) -> None:
    """Compare what Python wrote against what the database holds, and abort if not.

    Scoped `WHERE ingest_run = %s` rather than counting whole tables, so a
    concurrent run's rows could never mask or manufacture a discrepancy in this
    one.

    THE LAST CHECK IS THE ONE NO CONSTRAINT CAN EXPRESS: an occurrence's offsets
    live in one table and the wording's length in another, and a row-local CHECK
    cannot see across them. A span reaching past the end of its wording is a
    stored fact nobody can cut back out of the source -- the same defect the
    quote trigger refuses for windows, at the grain a trigger over 1.3 million
    rows would be too expensive to police.
    """
    expected = {"spl_wording": wordings, "spl_label": labels,
                "spl_label_subject": subjects,
                "spl_entity_occurrence": occurrences}
    for table, written in expected.items():
        (stored,) = conn.execute(
            f"SELECT count(*) FROM drugref.{table} WHERE ingest_run = %s",
            (run_id,)).fetchone()
        if stored != written:
            raise ValueError(
                f"spl: {stored:,} row(s) stored in {table} for run {run_id}, "
                f"but {written:,} were written -- the projection does not hold "
                "what the summary would report")

    (past_end,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_entity_occurrence o "
        "  JOIN drugref.spl_wording w "
        "    ON w.ingest_run = o.ingest_run AND w.source = o.source "
        "   AND w.text_key = o.text_key "
        " WHERE o.ingest_run = %s AND o.char_end > w.char_length",
        (run_id,)).fetchone()
    if past_end:
        raise ValueError(
            f"spl: {past_end:,} occurrence(s) end past their wording's length. "
            "The offsets and the stored char_length describe different strings "
            "-- the usual cause is matching the RAW section text while measuring "
            "the NORMALISED one.")


def read_pairs(conn: psycopg.Connection, run_id: int) -> tuple[int, int, int]:
    """`(pairs, novel_pairs, self_pairs)`, all read from the database.

    `novel` is measured against BOTH held pair relations -- `exact_ddi_pair`
    (MED-RT's moiety arm plus DrugCentral) and `ddi_candidate_pair` (the expanded
    class rules) -- which is the same pair of populations the mining measurement
    computed its 88.7% against. Quoting a novelty rate against one of them would
    be a different number wearing the same name.

    `self_pairs` counts what the view excludes: a label naming its own drug is a
    CORRECT reading of the source rather than a malformed row, so it is dropped
    in the read path and counted here -- db/049's rule, so the number cannot
    become nonzero unnoticed.
    """
    (pairs,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_ddi_pair").fetchone()
    (novel,) = conn.execute(
        "SELECT count(*) FROM ("
        "  SELECT moiety_lo, moiety_hi FROM drugref.spl_ddi_pair "
        "  EXCEPT SELECT moiety_lo, moiety_hi FROM drugref.exact_ddi_pair "
        "  EXCEPT SELECT least(subject_moiety, partner_moiety), "
        "                greatest(subject_moiety, partner_moiety) "
        "           FROM drugref.ddi_candidate_pair) novel").fetchone()
    (self_pairs,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_label_subject s "
        "  JOIN drugref.spl_label l "
        "    ON l.ingest_run = s.ingest_run AND l.source = s.source "
        "   AND l.set_id = s.set_id AND l.version = s.version "
        "  JOIN drugref.spl_entity_occurrence o "
        "    ON o.ingest_run = l.ingest_run AND o.source = l.source "
        "   AND o.text_key = l.text_key "
        " WHERE s.ingest_run = %s AND s.moiety_uuid = o.moiety_uuid",
        (run_id,)).fetchone()
    return int(pairs), int(novel), int(self_pairs)


def check_floors(summary: SplSummary, *, pair_floor: int | None,
                  novel_floor: int | None) -> None:
    """Refuse a run that published nothing, or less than the measured floor.

    db/050's pattern. **Every reconciliation above proves self-consistency and
    none proves the ingest published anything**: the all-zeros run satisfies all
    of them -- `stored (0) == written (0)` four times over -- while
    `clear_source_spl` has already deleted the previous release's rows.

    The four structural floors below are unconditional because none of them can
    legitimately be zero on any corpus that carried a section at all. The two
    MEASURED floors are optional and assert `>=`, because the figures are floors
    rather than targets and a partial corpus has to be able to say so.
    """
    for label, value in (("labels", summary.labels),
                         ("wordings", summary.wordings),
                         ("resolved subjects", summary.resolved_labels),
                         ("entity occurrences", summary.occurrences),
                         ("candidate pairs", summary.pairs)):
        if value == 0:
            raise ValueError(
                f"spl: this ingest published 0 {label}. Every reconciliation "
                "passed, because an all-zeros run is perfectly self-consistent "
                "-- and the previous projection has already been cleared. "
                "Refusing to report success over an empty read.")
    if pair_floor is not None and summary.pairs < pair_floor:
        raise ValueError(
            f"spl: {summary.pairs:,} candidate pairs, below the measured floor "
            f"of {pair_floor:,}. The floor is a FLOOR -- more is fine, less "
            "means the matcher, the subject rule or the corpus changed.")
    if novel_floor is not None and summary.novel_pairs < novel_floor:
        raise ValueError(
            f"spl: {summary.novel_pairs:,} novel pairs, below the measured "
            f"floor of {novel_floor:,}.")
