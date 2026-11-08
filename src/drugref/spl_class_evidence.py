"""Class-mention evidence for SPL labels: the vocabulary, and one row per mention.

The class twin of the moiety pass. Pure except `load_class_entries` (one read-only
query) and `read_official_names` (one file read); the writer lives in `spl_evidence`
and the orchestrator owns the transaction.

Everything produced here is MACHINE-READ and NOT CONFIRMED: the advice level is a
phrase reader's guess about one sentence, never an assertion (see db/066).

This is its own module, not part of `spl_evidence`, because that file is at its
size budget and this logic is not about writing rows.
"""
from __future__ import annotations

import csv
import pathlib
import re
from dataclasses import dataclass
from typing import Collection, Iterable, NamedTuple, Sequence

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
    #: Class matches dropped because the class word qualifies a non-drug head (R19/P2).
    class_spans_dropped_on_a_non_drug_head: int = 0

    def class_drop_fields(self) -> dict[str, int]:
        """The two drop counts as `SplSummary` keyword arguments (same names)."""
        return {"class_spans_dropped_at_a_boundary":
                self.class_spans_dropped_at_a_boundary,
                "class_spans_dropped_on_a_non_drug_head":
                self.class_spans_dropped_on_a_non_drug_head}


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


#: The UNII_Names columns `official_names` reads; a file without them (UNII_Records
#: sits right beside it) is refused rather than read as "no names".
_NAMES_COLUMNS = ("Name", "TYPE", "UNII")
#: UNII_Names' TYPE for an OFFICIAL name (USAN, INN, BAN ...). Only this type: the
#: common-name type 'cn' would also drop the real class word 'sulfonamide'.
OFFICIAL_NAME_TYPE = "of"


def official_names(lines: Iterable[str], held_uniis: Collection[str]) -> set[str]:
    """Official names (TYPE 'of') of the substances whose UNII the registry holds.

    Why (R19/P1): 'Acetaminophen [EPC]' keyed as 'Acetaminophen' marks every
    mention of the DRUG paracetamol, but the registry names that moiety only
    'paracetamol', so R16(b)'s display-name check cannot see it. 'acetaminophen'
    is the substance's US official name, which UNII_Names carries. Pure: `lines`
    is the file's text, header first.
    """
    reader = csv.DictReader(lines, delimiter="\t")
    missing = [c for c in _NAMES_COLUMNS if c not in (reader.fieldnames or ())]
    if missing:
        raise ValueError(
            f"UNII names file lacks column(s) {missing} (has {reader.fieldnames}): "
            "is this UNII_Records rather than UNII_Names?")
    return {row["Name"] for row in reader
            if row["TYPE"] == OFFICIAL_NAME_TYPE and row["UNII"] in held_uniis}


def read_official_names(path: str | pathlib.Path,
                        held_uniis: Collection[str]) -> set[str]:
    """`official_names` over a UNII_Names_*.txt file (streamed, never held whole)."""
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return official_names(f, held_uniis)


class ClassKeyCounts(NamedTuple):
    """What became of the derived class keys, by NAME, for the run's summary."""

    kept: int
    shadowed_by_moieties: list[str]
    shadowed_by_official_names: list[str]
    #: True when no UNII names file was given, so R19/P1 never ran: said out loud
    #: in `__str__` because the ingest's progress line is the only place it shows.
    official_names_skipped: bool = False

    def summary_fields(self) -> dict[str, int]:
        """The two drop counts as `SplSummary` keyword arguments (same names)."""
        return {"class_keys_shadowed_by_moieties": len(self.shadowed_by_moieties),
                "class_keys_shadowed_by_official_names":
                len(self.shadowed_by_official_names)}

    def __str__(self) -> str:
        return (f"{self.kept:,} class match keys "
                f"({len(self.shadowed_by_moieties):,} dropped as moiety names: "
                f"{sorted(self.shadowed_by_moieties)[:12]}; "
                f"{len(self.shadowed_by_official_names):,} as UNII official names: "
                f"{sorted(self.shadowed_by_official_names)[:12]})"
                + (" -- WARNING: no UNII names file (--unii-names), so class keys "
                   "equal to a UNII official name were NOT dropped (R19/P1 skipped)"
                   if self.official_names_skipped else ""))


def drop_shadowed_class_keys(
    entries: Sequence[spl_match.Entry], *, moiety_names: Iterable[str],
    official_names: Iterable[str],
) -> tuple[list[spl_match.Entry], ClassKeyCounts]:
    """R16(b) then R19/P1, per KEY: a plural that is not itself a name survives.

    The two are counted apart because they fail differently: a moiety-name key is a
    mention the moiety pass already owns; an official-name key is a drug the moiety
    pass may not see at all (the registry holds no 'acetaminophen').
    """
    kept, by_moiety = split_shadowed_keys(entries, moiety_names)
    kept, by_official = split_shadowed_keys(kept, official_names)
    return kept, ClassKeyCounts(len(kept), by_moiety, by_official)


def class_vocabulary_for_run(
    conn: psycopg.Connection, moiety_names: Iterable[str],
    held_uniis: Collection[str], unii_names: str | pathlib.Path | None,
) -> tuple[spl_match.Vocabulary, ClassKeyCounts]:
    """The class vocabulary an ingest run matches with, and what was dropped.

    One call, so `spl_run` (at its size budget) stays a thin hook. `unii_names`
    None skips P1 (the CLI requires the file; the count then says 0 and the
    returned counts carry `official_names_skipped`, whose `str` warns).
    """
    names = read_official_names(unii_names, held_uniis) if unii_names else set()
    kept, counts = drop_shadowed_class_keys(
        load_class_entries(conn), moiety_names=moiety_names, official_names=names)
    skipped = not unii_names
    return class_vocabulary(kept), counts._replace(official_names_skipped=skipped)


def load_class_entries(conn: psycopg.Connection) -> list[spl_match.Entry]:
    """One class entry per derived match key, for every class labels may name.

    MED-RT's PK and APC axes, and the named `EXCLUDED_CLASSES`, are skipped
    (`is_matchable`); FDA-CYP, also 'PK', is kept.
    `display` is the stored `class_name`, so the vocabulary says what it matched.
    """
    entries = []
    for class_uuid, source, class_name, concept_type in conn.execute(
            "SELECT class_uuid, source, class_name, concept_type "
            "FROM drugref.substance_class ORDER BY class_uuid"):
        if not is_matchable(source, concept_type, class_name):
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


#: R19/P2: the class word is an ADJECTIVE when the next word (past at most one short
#: parenthetical, '(drying)') is one of these heads -- 'the anticoagulant effect of
#: warfarin' is about warfarin, 'insulin secretagogue' is another class. Measured on
#: run 11: 0 genuine mentions among 70 hand-checked drops. 'activity'/'properties'
#: are deliberately absent ('drugs with anticholinergic activity' is a real group,
#: and dropping it would under-warn), and so is a bare 'dose' ('the PPI dose should
#: not exceed ...' is advice ABOUT the class); only 'dose(s) of' is a descriptor.
_NON_DRUG_HEAD = re.compile(
    r"\s*(?:\([^()]{1,40}\)\s*)?"
    r"(?:effects?|actions?|side[\s-]+effects?|secretagogues?|doses?\s+of)\b",
    re.IGNORECASE)


def followed_by_non_drug_head(text: str, char_end: int) -> bool:
    """True when the class span ending at `char_end` qualifies a non-drug head."""
    return _NON_DRUG_HEAD.match(text, char_end) is not None


def class_rows_for_wording(text_key: str, text: str,
                           vocab: spl_match.Vocabulary) -> list[ClassOccurrenceRow]:
    """Match classes in one wording, then read the advice around each mention."""
    return _rows_and_drops(text_key, text, vocab)[0]


def _rows_and_drops(text_key: str, text: str, vocab: spl_match.Vocabulary,
                    ) -> tuple[list[ClassOccurrenceRow], int, int]:
    """Rows, then the matches dropped at a sentence boundary and on a non-drug head.

    A match whose span crosses a boundary is a false mention (the tokeniser ignores
    punctuation); it is dropped and COUNTED -- the design's sentence-splitter signal.
    A match on a non-drug head (R19/P2) is an adjective, not a mention: also counted.
    """
    rows = []
    boundary = heads = 0
    for occ in spl_match.class_occurrences(spl_match.find_matches(text, vocab)):
        if spl_advice.span_crosses_sentence_boundary(
                text, occ.char_start, occ.char_end):
            boundary += 1
            continue
        if followed_by_non_drug_head(text, occ.char_end):
            heads += 1
            continue
        reading = spl_advice.read_advice(text, occ.char_start, occ.char_end)
        rows.append(ClassOccurrenceRow(
            text_key=text_key, char_start=occ.char_start, char_end=occ.char_end,
            class_uuid=occ.class_uuid, match_ambiguous=occ.ambiguous,
            match_form=occ.form, advice_level=reading.level,
            advice_mixed=reading.mixed, advice_rule=reading.rule_id,
            advice_scope=reading.scope, scope_start=reading.scope_start,
            scope_end=reading.scope_end))
    return rows, boundary, heads


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
) -> tuple[list[ClassOccurrenceRow], int, int, int]:
    """`(class rows, moiety spans inside a class span, boundary drops, head drops)`.

    Kept here so `spl_run` (at its size budget) makes ONE call per wording.
    `moiety_spans` are that wording's moiety occurrences from the moiety pass.
    """
    rows, boundary, heads = _rows_and_drops(text_key, text, vocab)
    inside = moiety_spans_inside_class_spans(
        moiety_spans, [(r.char_start, r.char_end) for r in rows])
    return rows, inside, boundary, heads
