# src/drugref/ingest/spl_match.py
"""Deterministic entity recognition over SPL section 34073-7 prose.

PURE, per the architecture invariant -- no database and no I/O. The shipped
suppression vocabulary, and the corpus evidence it is derived from, live in
`spl_suppress` (split out under rule 4, issue #177).

**IT RECOGNISES ENTITIES AND ASSERTS NO RELATION.** drugref's standing invariant
is *ingest preserves evidence; curation creates clinical judgement*, and deciding
that a sentence means *contraindicated* rather than *monitor* is a clinical
reading of prose. This module answers only *which known moieties does this
section name, and exactly where*.

**THE RULE IS THE SHIPPED RESOLVER'S, NOT A MORE GENEROUS VARIANT** -- exact,
case-insensitive, contiguous, whole-token, longest-match-wins, `fold`-normalised.
`spl_checks.MEASURED_PAIR_FLOOR` rests on that rule, and a matcher that skipped
words would produce spans it cannot quote back to a reader, which the 25% quote
budget is computed over. The floor itself was measured with a vocabulary that
ALSO held 8,534 class entries; dropping them moved the drug x drug yield by +193
pairs, so it is a floor this matcher clears with room rather than a figure this
matcher produced -- see `KIND_CLASS`.

Three rules govern matching, and each exists to stop a specific way of inflating
the yield:

1. **Whole tokens only.** Substring matching over a 19,438-name registry
   manufactures yield out of ordinary English -- `iron` inside `environmental`.
2. **Contiguous only.** The matcher does not skip intervening words. The real
   tizanidine label reads *"strong cytochrome P450 1A2 (CYP1A2) inhibitors"* and
   the parenthetical defeats the phrase. That miss is REPORTED rather than
   papered over.
3. **Longest match wins, and matches never overlap.** Otherwise a name nested
   inside a longer one is counted twice and the pair yield double-counts.

**CLASSES ARE NOT MATCHED.** This slice is drug x drug only (owner's call,
2026-08-24): 32.3% of class occurrences name an EMPTY class, MED-RT's PK axis is
97.2% empty and is not a drug-class vocabulary (#155), and `Diuretics` (MeSH) and
`Diuretic [APC]` (MED-RT) fold to one string with no cross-source class identity.
The class half is its own slice, with its own measurement.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: Tokens are maximal runs of ASCII letters and digits. Everything else --
#: hyphens, parentheses, commas, whitespace -- is a separator. This is what makes
#: `P-gp` two tokens and `CYP1A2` one, and the vocabulary key is folded the same
#: way, so the two sides of every comparison agree by construction rather than by
#: coincidence.
_TOKEN = re.compile(r"[A-Za-z0-9]+")

#: The entry kinds. A `suppress` entry names a longer term that is not an entity
#: at all; see `Entry`.
KIND_MOIETY = "moiety"
KIND_SUPPRESS = "suppress"
#: A drug CLASS name. **The shipped ingest offers none**, and that exclusion is
#: measured rather than assumed: `tools/spl_class_vocabulary_delta.py` adds the
#: 8,534 class entries back and reproduces the design round's 20,554 pairs over
#: 26,721 wordings exactly, because longest-match-wins had class names consuming
#: 11,169 moiety spans. Deferring them RAISED the drug x drug yield by 193 pairs.
#: The kind is named here so that tool builds the same type the matcher does --
#: and so `Entry`'s check below can say a class entry carries no `moiety_uuid`
#: rather than treating the whole kind as malformed.
KIND_CLASS = "class"

#: Every kind `Entry` admits. Only `KIND_MOIETY` carries a `moiety_uuid`.
ENTRY_KINDS = (KIND_MOIETY, KIND_SUPPRESS, KIND_CLASS)


@dataclass(frozen=True, kw_only=True)
class Entry:
    """One name offered to the matcher.

    **KEYWORD-ONLY**, on `spl_evidence`'s stated rule: three same-typed strings
    in a row means `Entry("warfarin", "moiety", "warfarin")` -- kind and key
    transposed -- constructs cleanly and folds a vocabulary key of "warfarin"
    under a kind of "warfarin", which `moiety_occurrences` then skips silently.

    `kind` is one of `ENTRY_KINDS`. The shipped ingest offers `moiety` and
    `suppress`; `class` exists for the measurement tool that quantifies what
    excluding classes bought.

    A **suppress** entry names a longer term that is not an entity: `prothrombin
    time` is a lab test, `serotonin syndrome` is an adverse event, `lead to` is a
    verb. It carries no yield of its own and no `moiety_uuid`; it exists so that
    longest-match-wins CONSUMES the span and the short name inside it never
    fires.

    **That is strictly better than a stop-list, and the difference is a true
    positive.** A stop-list deletes the name everywhere, including where it
    really is the drug -- `lead` the element (Pb) is a real moiety and a real
    interaction participant through chelation. Suppression removes it only inside
    the phrase that misleads.

    `moiety_uuid` is carried on the entry rather than looked up later so the
    matcher's output is directly writable: a display name resolved to a UUID in a
    second pass is a second place for the resolution rule to live.
    """

    kind: str
    key: str
    display: str
    moiety_uuid: str | None = None

    def __post_init__(self) -> None:
        """`kind` and `moiety_uuid` are a MUTUAL-CONSISTENCY PAIR, not two fields.

        A `moiety` entry with no uuid matched, and then contributed no
        occurrence, no pair and no evidence -- `moiety_occurrences` skips
        `entry.moiety_uuid is None`, which ABSORBS the malformed entry rather
        than reporting it. The drug is recognised and then silently vanishes, and
        only a corpus-wide floor could ever notice. Same shape as `db/051`'s
        `spl_label_subject_complete`, which this slice already uses twice.
        """
        if self.kind not in ENTRY_KINDS:
            raise ValueError(
                f"entry kind {self.kind!r} is not one of {ENTRY_KINDS}")
        if (self.kind == KIND_MOIETY) != (self.moiety_uuid is not None):
            raise ValueError(
                f"entry {self.key!r} is a {self.kind!r} carrying "
                f"moiety_uuid={self.moiety_uuid!r}: a moiety entry names a "
                "moiety and a suppression entry does not, or the matcher "
                "recognises the name and then yields nothing for it")


@dataclass(frozen=True)
class Token:
    """One token, carrying its span in the original (unfolded) text."""

    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Vocabulary:
    """Folded names indexed by their token tuple.

    `max_tokens` is the longest key in the vocabulary, kept for callers that want
    the bound as a single number. **It is NOT what bounds the scan** -- the
    sentence here used to say it was, carried over unchanged from
    `tools/spl_entity_match.py`, where it is true. `_longest_at` reads only
    `max_tokens_by_first`, and nothing under `src/` reads `max_tokens` at all.

    `max_tokens_by_first` bounds it AGAIN, per starting token, and that is what
    makes a full-corpus run finish: one long registry name -- and drugref holds
    several of a dozen words -- would otherwise make every one of ~19 million
    token positions in the corpus try a dozen n-grams, almost all of them
    starting at ordinary English words that begin no drug name at all. A token
    absent from this map begins nothing and is skipped in one lookup.

    It is an OPTIMISATION AND NOTHING ELSE: the entries it can reach at any
    position are exactly the entries `max_tokens` alone could reach, because a
    key's first token is in the map with at least that key's own length.
    """

    by_key: Mapping[tuple[str, ...], tuple[Entry, ...]]
    max_tokens: int
    max_tokens_by_first: Mapping[str, int]

    def __post_init__(self) -> None:
        """Both maps are DERIVED from `by_key`, and stored beside it.

        `build_vocabulary` computes them correctly; nothing said so. A vocabulary
        built by hand -- a future test, a second builder -- with a short
        `max_tokens_by_first` UNDER-MATCHES SILENTLY, returning fewer matches
        with no error, and "matched nothing" is precisely the failure this
        slice's floors exist to catch at enormous expense. O(19,000) once,
        against a 19.3 GB scan.
        """
        for key, entries in self.by_key.items():
            # ⇒ A SUPPRESSION TERM FOLDING ONTO A NAME (#165). `moiety_occurrences`
            # would skip the suppress entry and still emit the name, flagged
            # `match_ambiguous` -- so the suppression silently stops working AND
            # the flag stops meaning db/051's "more than one registry entry".
            kinds = {entry.kind for entry in entries}
            if not entries or (KIND_SUPPRESS in kinds and len(kinds) > 1):
                raise ValueError(
                    f"vocabulary key {key!r} carries {sorted(kinds) or 'no'} "
                    "entries: a key needs at least one, and a suppression term "
                    "may not fold onto a name")
        if not self.by_key:
            return
        longest = max(len(key) for key in self.by_key)
        if self.max_tokens != longest:
            raise ValueError(
                f"max_tokens is {self.max_tokens} but the longest key holds "
                f"{longest} token(s)")
        for key in self.by_key:
            reach = self.max_tokens_by_first.get(key[0], 0)
            if reach < len(key):
                raise ValueError(
                    f"max_tokens_by_first[{key[0]!r}] is {reach}, so the "
                    f"{len(key)}-token key {key!r} can never be reached: the "
                    "map is an optimisation only while it reaches every entry "
                    "max_tokens alone would")


@dataclass(frozen=True)
class Match:
    """One recognised span, and every vocabulary entry that folds onto it."""

    entries: tuple[Entry, ...]
    char_start: int
    char_end: int
    token_start: int
    token_end: int

    def __post_init__(self) -> None:
        """A span is recognised BECAUSE an entry folds onto it, so it has one.

        Without this, `ambiguous` read an empty match as UNambiguous (#165).
        """
        if not self.entries:
            raise ValueError(
                f"span {self.char_start}:{self.char_end} carries no entries")

    @property
    def ambiguous(self) -> bool:
        """Whether this span folded onto more than one registry entry.

        `db/051`'s `spl_entity_occurrence.match_ambiguous`. It exists because
        **ambiguity is unresolved, never "pick the first"** -- FDA-CYP's rule.
        Measured: 24 folded keys carry more than one registry name, covering 55
        of 19,438 (0.28%), mostly stereoisomers whose punctuation suffix the fold
        strips (`carvone, (+)-`). The direction matters for DDI specifically --
        S- and R-warfarin take different CYP pathways.
        """
        return len(self.entries) > 1

    @property
    def entry(self) -> Entry:
        """The sole entry, for the common unambiguous case.

        RAISES when the span is ambiguous rather than silently picking the first.
        """
        if len(self.entries) != 1:
            raise ValueError(
                f"span {self.char_start}:{self.char_end} carries "
                f"{len(self.entries)} entries; read .entries")
        return self.entries[0]


@dataclass(frozen=True)
class Occurrence:
    """One `spl_entity_occurrence` row: a known moiety, named at a known place.

    A DERIVED FACT with an offset and no prose -- clear under either reading of
    rule 6, which is why it is stored for every match whether or not the quote
    budget could afford a window over it.
    """

    moiety_uuid: str
    display: str
    char_start: int
    char_end: int
    ambiguous: bool


def fold(text: str) -> str:
    """Lower-case, and reduce every non-alphanumeric run to a single space.

    Used for vocabulary keys and (via `tokenise`) for the text, so both sides of
    every comparison are normalised identically.
    """
    return " ".join(m.group(0).lower() for m in _TOKEN.finditer(text))


def tokenise(text: str) -> tuple[Token, ...]:
    """Split `text` into folded tokens that remember where they came from.

    The regex runs over the ORIGINAL string and the token text is lower-cased
    afterwards, so `char_start`/`char_end` stay valid indices into `text` --
    lower-casing first would be wrong for any character whose lower-case form has
    a different length.
    """
    return tuple(
        Token(text=m.group(0).lower(), char_start=m.start(), char_end=m.end())
        for m in _TOKEN.finditer(text))


def build_vocabulary(entries: Iterable[Entry]) -> Vocabulary:
    """Index entries by their folded token tuple.

    Entries sharing a folded key are GROUPED rather than de-duplicated, so a
    collision is countable downstream instead of being resolved here by insertion
    order.
    """
    by_key: dict[tuple[str, ...], list[Entry]] = {}
    by_first: dict[str, int] = {}
    longest = 0
    for entry in entries:
        key = tuple(fold(entry.key).split())
        if not key:
            raise ValueError(
                f"vocabulary entry {entry.display!r} folds to no tokens; "
                "a key of () would match at every position")
        by_key.setdefault(key, []).append(entry)
        longest = max(longest, len(key))
        by_first[key[0]] = max(by_first.get(key[0], 0), len(key))
    return Vocabulary(
        by_key={k: tuple(v) for k, v in by_key.items()},
        max_tokens=longest,
        max_tokens_by_first=by_first)


def find_matches(text: str, vocab: Vocabulary) -> tuple[Match, ...]:
    """Every non-overlapping, longest-first vocabulary hit in `text`.

    Returns one `Match` per OCCURRENCE, not per distinct name -- occurrences and
    distinct entities are different units, and the 5c.3 source evaluation was
    burned once by quoting one as the other.

    **In document order**, which the quote rule depends on: a licensing
    constraint whose result moved with the plan's row order would not be a
    constraint.
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
        # Skip PAST the whole matched span, which is what makes matches
        # non-overlapping -- and what lets a `suppress` entry consume the name
        # nested inside it.
        index = hit.token_end
    return tuple(matches)


def _longest_at(
    tokens: Sequence[Token], index: int, vocab: Vocabulary
) -> Match | None:
    """The longest vocabulary entry starting exactly at `tokens[index]`."""
    from_here = vocab.max_tokens_by_first.get(tokens[index].text, 0)
    if not from_here:
        return None
    longest_possible = min(from_here, len(tokens) - index)
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
            token_end=index + size)
    return None


def moiety_occurrences(matches: Iterable[Match]) -> tuple[Occurrence, ...]:
    """The writable rows: one per colliding registry entry, in document order.

    A suppressed span yields NOTHING -- it was recognised as not-an-entity, which
    is the point of it being in the vocabulary at all.

    An ambiguous span yields **one row per entry with the flag set**, never one
    row for a chosen winner. `db/051`'s comment states the rule: every colliding
    entry gets a row and nothing downstream may silently choose.
    """
    occurrences: list[Occurrence] = []
    for match in matches:
        for entry in match.entries:
            if entry.kind != KIND_MOIETY or entry.moiety_uuid is None:
                continue
            occurrences.append(Occurrence(
                moiety_uuid=entry.moiety_uuid,
                display=entry.display,
                char_start=match.char_start,
                char_end=match.char_end,
                ambiguous=match.ambiguous))
    return tuple(occurrences)
