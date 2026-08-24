"""Deterministic entity recognition over SPL section 34073-7 prose.

**Throwaway spike code for the slice 5c.3 measurement round.** It exists to
produce figures, not to ship: nothing imports it from ``src/drugref/``, and the
design round is free to throw all of it away.

The extraction rule this module implements was chosen deliberately over the
alternatives (a trained classifier, an LLM, cue-phrase relation extraction):
**recognise entities, assert no relation.** drugref's standing invariant is
*ingest preserves evidence; curation creates clinical judgement*, and deciding
that a sentence means "contraindicated" rather than "monitor" is a clinical
reading of prose. So this module answers only *which known things does this
section name, and exactly where*.

Three rules govern matching, and each one exists to stop a specific way of
inflating the yield figure:

1. **Whole tokens only.** Substring matching over a 19,438-name registry
   manufactures yield out of ordinary English -- ``iron`` inside
   ``environmental``. Names match token sequences, never character spans.
2. **Contiguous only.** The matcher does not skip intervening words. The real
   tizanidine label reads *"strong cytochrome P450 1A2 (CYP1A2) inhibitors"*,
   and the parenthetical defeats the class phrase. That miss is REPORTED rather
   than papered over, because a matcher that skips words produces spans it
   cannot quote back to a reader.
3. **Longest match wins, and matches never overlap.** Otherwise a name nested
   inside a longer one is counted twice and the pair yield double-counts.

Offsets index the ORIGINAL text, not a folded copy, so a consumer can cut the
quoted span back out of the source.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: Tokens are maximal runs of ASCII letters and digits. Everything else --
#: hyphens, parentheses, commas, whitespace -- is a separator. This is what
#: makes 'P-gp' two tokens and 'CYP1A2' one, and both behaviours are pinned by
#: tests: the vocabulary key is folded the same way, so the two agree by
#: construction rather than by coincidence.
_TOKEN = re.compile(r"[A-Za-z0-9]+")


#: MED-RT and drugref tag a class with its axis -- 'Cytochrome P450 1A2
#: Inhibitors [MoA]', 'CYP1A2 strong inhibitor [FDA-CYP]'. **That tag never
#: appears on a drug label**, so matching the stored name verbatim would score a
#: near-zero class yield and the round would conclude labels do not name classes.
#: They do; drugref just spells them differently.
_AXIS_TAG = re.compile(r"\s*\[[A-Za-z0-9-]+\]\s*$")


def strip_axis_tag(class_name: str) -> str:
    """Drop the trailing '[MoA]' / '[EPC]' / '[FDA-CYP]' axis tag."""
    return _AXIS_TAG.sub("", class_name)


def name_variants(name: str) -> tuple[str, ...]:
    """The spellings of one class name that a label might plausibly use.

    Deliberately SMALL and mechanical: the stored name, plus a singular/plural
    swap on the final word. Nothing here rewrites word order, expands
    'Cytochrome P450' to 'CYP', or invents synonyms -- those are real
    differences between drugref's vocabulary and label prose, and the point of
    the measurement is to SIZE that gap, not to paper over it with normalisation
    nobody agreed to.

    Returned in a stable order with duplicates removed, so a caller registering
    every variant does not double-count one name.
    """
    base = strip_axis_tag(name).strip()
    variants = [base]
    if base.endswith("s"):
        variants.append(base[:-1])
    else:
        variants.append(base + "s")
    seen: dict[str, None] = {}
    for variant in variants:
        if variant:
            seen.setdefault(variant, None)
    return tuple(seen)


@dataclass(frozen=True)
class Entry:
    """One name offered to the matcher.

    The kind is kept as data rather than as separate vocabularies because a
    single folded string can be BOTH a moiety and a class (see ``Match``), and a
    design that could not represent that would decide a grain question by
    accident.
    """

    #: 'moiety', 'class', or 'suppress'. A **suppress** entry names a longer
    #: term that is not an entity at all -- 'prothrombin time' is a lab test,
    #: 'serotonin syndrome' is a syndrome, 'lead to' is a verb. It carries no
    #: yield of its own; it exists so that longest-match-wins CONSUMES the span
    #: and the short name inside it never fires. That is the principled fix for
    #: these false positives, and it is strictly better than a stop-list: a
    #: stop-list deletes the name everywhere, including where it is genuinely
    #: the drug, whereas this suppresses it only in the phrase that misleads.
    kind: str
    key: str
    display: str
    #: For a class, the authority that published it ('MED-RT', 'MeSH',
    #: 'FDA-CYP'). Carried so band figures can be reported PER SOURCE: issue
    #: #102 is a question about the PK classes specifically, and a rate computed
    #: over every class in the registry answers a different question.
    source: str = ""


@dataclass(frozen=True)
class Token:
    """One token, carrying its span in the original (unfolded) text."""

    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Vocabulary:
    """Folded names indexed by their token tuple.

    ``max_tokens`` bounds the n-gram window at match time, so the scan cost is
    O(tokens x max_tokens) rather than O(tokens^2).
    """

    by_key: Mapping[tuple[str, ...], tuple[Entry, ...]]
    max_tokens: int


@dataclass(frozen=True)
class Match:
    """One recognised span, and every vocabulary entry that folds onto it."""

    entries: tuple[Entry, ...]
    char_start: int
    char_end: int
    token_start: int
    token_end: int

    @property
    def entry(self) -> Entry:
        """The sole entry, for the common unambiguous case.

        Raises when the span is ambiguous rather than silently picking the first
        -- the same rule FDA-CYP's name resolution follows ("ambiguity is
        unresolved, never 'pick the first'").
        """
        if len(self.entries) != 1:
            raise ValueError(
                f"span {self.char_start}:{self.char_end} carries "
                f"{len(self.entries)} entries; read .entries"
            )
        return self.entries[0]


def fold(text: str) -> str:
    """Lower-case, and reduce every non-alphanumeric run to a single space.

    Used for vocabulary keys and (via ``tokenise``) for the text, so the two
    sides of every comparison are normalised identically.
    """
    return " ".join(m.group(0).lower() for m in _TOKEN.finditer(text))


def tokenise(text: str) -> tuple[Token, ...]:
    """Split ``text`` into folded tokens that remember where they came from.

    The regex runs over the ORIGINAL string and the token text is lower-cased
    afterwards, so ``char_start``/``char_end`` stay valid indices into ``text``
    -- lower-casing first would be wrong for any character whose lower-case form
    has a different length.
    """
    return tuple(
        Token(text=m.group(0).lower(), char_start=m.start(), char_end=m.end())
        for m in _TOKEN.finditer(text)
    )


def build_vocabulary(entries: Iterable[Entry]) -> Vocabulary:
    """Index entries by their folded token tuple.

    Entries sharing a folded key are grouped rather than de-duplicated, so a
    collision between a moiety and a class is COUNTABLE downstream instead of
    being resolved here by insertion order.
    """
    by_key: dict[tuple[str, ...], list[Entry]] = {}
    longest = 0
    for entry in entries:
        key = tuple(fold(entry.key).split())
        if not key:
            raise ValueError(
                f"vocabulary entry {entry.display!r} folds to no tokens; "
                "a key of () would match at every position"
            )
        by_key.setdefault(key, []).append(entry)
        longest = max(longest, len(key))
    return Vocabulary(
        by_key={k: tuple(v) for k, v in by_key.items()}, max_tokens=longest
    )


def find_matches(text: str, vocab: Vocabulary) -> tuple[Match, ...]:
    """Every non-overlapping, longest-first vocabulary hit in ``text``.

    Returns one ``Match`` per OCCURRENCE, not per distinct name -- occurrences
    and distinct entities are different units, and the 5c.3 source evaluation
    was already burned once by quoting one as the other.
    """
    tokens = tokenise(text)
    matches: list[Match] = []
    index = 0
    while index < len(tokens):
        hit = _longest_at(tokens, index, vocab)
        if hit is None:
            index += 1
            continue
        matches.append(hit)
        index = hit.token_end
    return tuple(matches)


def _longest_at(
    tokens: Sequence[Token], index: int, vocab: Vocabulary
) -> Match | None:
    """The longest vocabulary entry starting exactly at ``tokens[index]``."""
    longest_possible = min(vocab.max_tokens, len(tokens) - index)
    for size in range(longest_possible, 0, -1):
        key = tuple(t.text for t in tokens[index:index + size])
        entries = vocab.by_key.get(key)
        if entries is None:
            continue
        return Match(
            entries=entries,
            char_start=tokens[index].char_start,
            char_end=tokens[index + size - 1].char_end,
            token_start=index,
            token_end=index + size,
        )
    return None
