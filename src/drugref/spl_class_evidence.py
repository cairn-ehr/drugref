"""Class-mention evidence for SPL labels: the vocabulary, and one row per mention.

The class twin of the moiety pass. Pure except `load_class_entries` (one read-only
query); the writer lives in `spl_evidence` and the orchestrator owns the transaction.

Everything produced here is MACHINE-READ and NOT CONFIRMED: the advice level is a
phrase reader's guess about one sentence, never an assertion (see db/066).

This is its own module, not part of `spl_evidence`, because that file is at its
size budget and this logic is not about writing rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple, Sequence

import psycopg

from drugref.ingest import spl_advice, spl_match
from drugref.ingest.spl_class_vocab import class_match_keys, is_matchable


@dataclass(frozen=True, kw_only=True)
class ClassOccurrenceRow:
    """One `spl_class_occurrence` row, less the run and source the writer stamps.

    **FIELD ORDER IS db/066's COLUMN ORDER** after `(ingest_run, source)`: the
    writer derives its COPY column list from these field names, so a renamed or
    reordered field is a column mismatch, not a silent transposition.
    """

    text_key: str
    char_start: int
    char_end: int
    class_uuid: str
    match_ambiguous: bool
    match_form: str
    advice_level: str
    advice_mixed: bool
    advice_rule: str | None
    advice_scope: str
    scope_start: int
    scope_end: int


class EvidenceCounts(NamedTuple):
    """What `_write_evidence` wrote, by NAME (seven ints by position invite swaps)."""

    occurrences: int
    wordings_with_a_moiety: int
    quotes: int
    quoted_chars: int
    class_occurrences: int
    wordings_with_a_class: int
    moiety_spans_inside_class_spans: int
    #: Class matches dropped because the span held a sentence boundary (R17).
    class_spans_dropped_at_a_boundary: int = 0


def split_shadowed_keys(
    entries: Sequence[spl_match.Entry], moiety_names: Iterable[str],
) -> tuple[list[spl_match.Entry], list[str]]:
    """Drop class keys that fold equal to a registry moiety display name.

    'Calcium [EPC]' keyed as 'Calcium' marks every mention of the DRUG calcium,
    which the moiety pass already owns (R16). Returns `(kept, dropped key texts)`
    so the caller can report what it removed.
    """
    names = {spl_match.fold(n) for n in moiety_names}
    kept, dropped = [], []
    for entry in entries:
        if spl_match.fold(entry.key) in names:
            dropped.append(entry.key)
        else:
            kept.append(entry)
    return kept, dropped


def load_class_entries(conn: psycopg.Connection) -> list[spl_match.Entry]:
    """One class entry per derived match key, for every class labels may name.

    MED-RT's PK and APC axes are skipped (`is_matchable`); FDA-CYP, also 'PK', is kept.
    `display` is the stored `class_name`, so the vocabulary says what it matched.
    """
    entries = []
    for class_uuid, source, class_name, concept_type in conn.execute(
            "SELECT class_uuid, source, class_name, concept_type "
            "FROM drugref.substance_class ORDER BY class_uuid"):
        if not is_matchable(source, concept_type):
            continue
        for key, form in class_match_keys(class_name, source):
            entries.append(spl_match.Entry(
                kind=spl_match.KIND_CLASS, key=key, display=class_name,
                class_uuid=str(class_uuid), form=form))
    return entries


def class_vocabulary(entries: Sequence[spl_match.Entry]) -> spl_match.Vocabulary:
    """Class entries only, with NO suppression terms.

    The shipped suppression list was derived for moieties ('lead to' is a verb);
    applying it here would silently eat class phrases it was never measured on.
    """
    return spl_match.build_vocabulary(
        e for e in entries if e.kind == spl_match.KIND_CLASS)


def class_rows_for_wording(text_key: str, text: str,
                           vocab: spl_match.Vocabulary) -> list[ClassOccurrenceRow]:
    """Match classes in one wording, then read the advice around each mention."""
    return _rows_and_drops(text_key, text, vocab)[0]


def _rows_and_drops(text_key: str, text: str, vocab: spl_match.Vocabulary,
                    ) -> tuple[list[ClassOccurrenceRow], int]:
    """Rows, plus how many matches were dropped for holding a sentence boundary.

    A match whose span crosses a boundary is a false mention (the tokeniser ignores
    punctuation); it is dropped and COUNTED -- the design's sentence-splitter signal.
    """
    rows = []
    dropped = 0
    for occ in spl_match.class_occurrences(spl_match.find_matches(text, vocab)):
        if spl_advice.span_crosses_sentence_boundary(
                text, occ.char_start, occ.char_end):
            dropped += 1
            continue
        reading = spl_advice.read_advice(text, occ.char_start, occ.char_end)
        rows.append(ClassOccurrenceRow(
            text_key=text_key, char_start=occ.char_start, char_end=occ.char_end,
            class_uuid=occ.class_uuid, match_ambiguous=occ.ambiguous,
            match_form=occ.form, advice_level=reading.level,
            advice_mixed=reading.mixed, advice_rule=reading.rule_id,
            advice_scope=reading.scope, scope_start=reading.scope_start,
            scope_end=reading.scope_end))
    return rows, dropped


def moiety_spans_inside_class_spans(moiety_spans: Iterable[tuple[int, int]],
                                    class_spans: Iterable[tuple[int, int]]) -> int:
    """How many moiety spans of ONE wording lie wholly inside some class span.

    Measures the overlap the separate class pass exists to keep out of the moiety
    pass ("rifampin" inside "rifampin inducers"). Spans are `(char_start,
    char_end)` of the same wording; a moiety counts once however many class spans
    cover it. Equal, nested and shared-start spans count; a PARTIAL overlap and a
    span merely touching a class span's edge do not. The quadratic scan is fine:
    a wording holds a handful of class spans.
    """
    classes = list(class_spans)
    return sum(1 for start, end in moiety_spans
               if any(c_start <= start and end <= c_end for c_start, c_end in classes))


def class_pass_for_wording(
    text_key: str, text: str, vocab: spl_match.Vocabulary,
    moiety_spans: Sequence[tuple[int, int]],
) -> tuple[list[ClassOccurrenceRow], int, int]:
    """`(class rows, moiety spans inside a class span, boundary drops)` for a wording.

    Kept here so `spl_run` (at its size budget) makes ONE call per wording.
    `moiety_spans` are that wording's moiety occurrences from the moiety pass.
    """
    rows, dropped = _rows_and_drops(text_key, text, vocab)
    inside = moiety_spans_inside_class_spans(
        moiety_spans, [(r.char_start, r.char_end) for r in rows])
    return rows, inside, dropped
