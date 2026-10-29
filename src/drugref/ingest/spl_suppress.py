# src/drugref/ingest/spl_suppress.py
"""The SPL matcher's negative vocabulary: reading it, and the evidence behind it.

Split out of `spl_match` under CLAUDE.md rule 4 (issue #177) by a verbatim move:
that module RECOGNISES entities, and this one holds the suppression terms the
recogniser is fed (`shipped_suppression_terms`) plus the corpus measurement a
human rules on before a term is added (`next_word_profiles`,
`suppression_candidates`, driven by `tools/spl_suppress_derive.py`). PURE apart
from reading the shipped data file out of the package.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources

from drugref.ingest.spl_match import KIND_MOIETY, Vocabulary, find_matches, tokenise

#: The shipped negative vocabulary, as a package data file.
SUPPRESSION_DATA = "spl_suppression_terms.txt"


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

    def __post_init__(self) -> None:
        """Every following word follows an occurrence, so the counts are bounded
        by `occurrences` -- or `dominance` could exceed 1.0 (#165)."""
        followed = sum(self.following.values())
        if followed > self.occurrences:
            raise ValueError(
                f"{self.name!r} is followed {followed} time(s) but occurs "
                f"only {self.occurrences}")

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
