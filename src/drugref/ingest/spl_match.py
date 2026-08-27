# src/drugref/ingest/spl_match.py
"""Deterministic entity recognition over SPL section 34073-7 prose.

PURE, per the architecture invariant -- no database, no I/O beyond reading the
shipped suppression vocabulary out of the package.

**IT RECOGNISES ENTITIES AND ASSERTS NO RELATION.** drugref's standing invariant
is *ingest preserves evidence; curation creates clinical judgement*, and deciding
that a sentence means *contraindicated* rather than *monitor* is a clinical
reading of prose. This module answers only *which known moieties does this
section name, and exactly where*.

**THE RULE IS THE SHIPPED RESOLVER'S, NOT A MORE GENEROUS VARIANT** -- exact,
case-insensitive, contiguous, whole-token, longest-match-wins, `fold`-normalised.
The measured 29,258-pair floor rests on that rule, and a matcher that skipped
words would produce spans it cannot quote back to a reader, which the 25% quote
budget is computed over.

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
from importlib import resources

#: Tokens are maximal runs of ASCII letters and digits. Everything else --
#: hyphens, parentheses, commas, whitespace -- is a separator. This is what makes
#: `P-gp` two tokens and `CYP1A2` one, and the vocabulary key is folded the same
#: way, so the two sides of every comparison agree by construction rather than by
#: coincidence.
_TOKEN = re.compile(r"[A-Za-z0-9]+")

#: The shipped negative vocabulary, as a package data file.
SUPPRESSION_DATA = "spl_suppression_terms.txt"

#: The two entry kinds. A `suppress` entry names a longer term that is not an
#: entity at all; see `Entry`.
KIND_MOIETY = "moiety"
KIND_SUPPRESS = "suppress"


@dataclass(frozen=True)
class Entry:
    """One name offered to the matcher.

    `kind` is `moiety` or `suppress`.

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


@dataclass(frozen=True)
class Token:
    """One token, carrying its span in the original (unfolded) text."""

    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Vocabulary:
    """Folded names indexed by their token tuple.

    `max_tokens` bounds the n-gram window at match time, so the scan costs
    O(tokens x max_tokens) rather than O(tokens^2).

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


@dataclass(frozen=True)
class Match:
    """One recognised span, and every vocabulary entry that folds onto it."""

    entries: tuple[Entry, ...]
    char_start: int
    char_end: int
    token_start: int
    token_end: int

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


def parse_suppression_terms(text: str) -> tuple[str, ...]:
    """Read a suppression vocabulary, ignoring comments and blank lines.

    The file's comments carry each term's MEASURED distribution -- the share of
    that name's occurrences followed by the suppressing word -- because every
    line has to be justified by a measurement rather than by intuition. The
    round's first pass asserted causes it had not checked (*"lead is a verb"*)
    and got one backwards: `alcohol` was called a false positive when 13,530 of
    its occurrences are ethanol as a genuine interactant and only 0.2% are
    excipient-qualified.
    """
    return tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#"))


@dataclass(frozen=True)
class NextWordProfile:
    """What follows one single-token registry name, across a corpus.

    The evidence a suppression decision is made on, and NOT the decision. See
    `suppression_candidates` for why the two are kept apart.
    """

    name: str
    occurrences: int
    following: Mapping[str, int]

    @property
    def dominant(self) -> tuple[str, int] | None:
        """The most frequent following word and its count, or None."""
        if not self.following:
            return None
        # Sorted by count then alphabetically, so a tie resolves the same way on
        # two runs over one corpus -- the same reproducibility rule the registry
        # lookups follow.
        word, count = max(self.following.items(), key=lambda kv: (kv[1], kv[0]))
        return word, count

    @property
    def dominance(self) -> float:
        """The dominant word's share of ALL occurrences of this name.

        Of ALL of them, not of the followed ones: an occurrence at the end of a
        section has no next word, and that is evidence AGAINST the bigram rather
        than missing data.
        """
        top = self.dominant
        return top[1] / self.occurrences if top and self.occurrences else 0.0


@dataclass(frozen=True)
class SuppressionCandidate:
    """One bigram a human should rule on, with the measurement to rule on it.

    IT CARRIES NO VERDICT, deliberately -- see `suppression_candidates`.
    """

    term: str
    occurrences: int
    share: float
    evidence: str


def next_word_profiles(
    texts: Iterable[str], vocab: Vocabulary, *, min_occurrences: int = 100
) -> tuple[NextWordProfile, ...]:
    """Tally what follows each SINGLE-TOKEN moiety name across a corpus.

    Single-token only: a multi-token name IS already the longer phrase, and
    suppressing a phrase inside another phrase is not what the negative
    vocabulary is for.

    `min_occurrences` keeps the output to names the corpus actually says
    something about -- a distribution over three occurrences is not a
    measurement, and the shipped terms rest on 9,160 to 19,804.

    Returned ranked by occurrences, so the names carrying the most evidence read
    first.
    """
    following: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for text in texts:
        tokens = tokenise(text)
        for match in find_matches(text, vocab):
            if match.token_end - match.token_start != 1:
                continue
            if not any(entry.kind == KIND_MOIETY for entry in match.entries):
                continue
            name = tokens[match.token_start].text
            totals[name] = totals.get(name, 0) + 1
            if match.token_end < len(tokens):
                next_word = tokens[match.token_end].text
                counts = following.setdefault(name, {})
                counts[next_word] = counts.get(next_word, 0) + 1

    profiles = [
        NextWordProfile(name=name, occurrences=count,
                        following=dict(following.get(name, {})))
        for name, count in totals.items() if count >= min_occurrences]
    return tuple(sorted(profiles, key=lambda p: (-p.occurrences, p.name)))


def suppression_candidates(
    profiles: Iterable[NextWordProfile], *,
    min_dominance: float = 0.5,
    already: Iterable[str] = (),
) -> tuple[SuppressionCandidate, ...]:
    """Bigrams worth a human's attention, RANKED. **It decides nothing.**

    ⇒ **AND THE RESTRAINT IS THE FINDING, not a hedge.** `lead` is followed by
    `to` in 9,157 of its 9,160 occurrences and the bigram is a verb. `warfarin`
    is followed by `sodium` on many of its occurrences and the bigram is still
    the drug. **The distributions have the same shape**; what separates them is
    whether the longer phrase names an entity, which is a reading and not a
    statistic.

    This project has already been burned in both directions on exactly this: the
    mining round asserted *"lead is a verb"* without checking it, and in the same
    pass called `alcohol` a false positive when 13,530 of its occurrences are
    ethanol as a genuine interactant and only 0.2% are excipient-qualified. So
    the output is a candidate list carrying the measurement, and a term reaches
    `data/spl_suppression_terms.txt` only with its distribution written beside it.

    `already` is the shipped vocabulary, so a second run does not re-propose what
    somebody has already ruled on.
    """
    seen = {term.strip().lower() for term in already}
    candidates = []
    for profile in profiles:
        top = profile.dominant
        if top is None or profile.dominance < min_dominance:
            continue
        term = f"{profile.name} {top[0]}"
        if term in seen:
            continue
        others = sorted(profile.following.items(),
                        key=lambda kv: (-kv[1], kv[0]))[1:4]
        tail = ", ".join(f"{word} {count:,}" for word, count in others)
        candidates.append(SuppressionCandidate(
            term=term,
            occurrences=profile.occurrences,
            share=profile.dominance,
            evidence=(f"{profile.name}: {profile.occurrences:,} occurrences, "
                      f"{profile.dominance:.1%} followed by {top[0]!r}"
                      + (f"; then {tail}" if tail else ""))))
    return tuple(sorted(candidates, key=lambda c: (-c.occurrences, c.term)))


def shipped_suppression_terms() -> tuple[str, ...]:
    """The negative vocabulary that ships with drugref. SEED DATA, not a rule.

    A file rather than a table, because it is the matcher's input and not a
    projection: `db/051` builds five tables and this is none of them. Revising it
    is a code change with a measurement attached, which is exactly the review it
    should get.
    """
    text = resources.files("drugref.data").joinpath(SUPPRESSION_DATA).read_text()
    return parse_suppression_terms(text)
