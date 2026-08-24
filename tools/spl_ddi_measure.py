"""Turn matched SPL sections into the figures the 5c.3 design round needs.

**Throwaway spike code.** Split out of ``tools/spl_ddi_spike.py`` under CLAUDE.md
rule 1 -- this is where every published number is computed, and nothing here
needs a database or a 1.7 GB download.

**Four units, and they are not interchangeable.** The 5c.3 source evaluation was
burned once already by quoting one as another, so they are named:

* **labels** -- one openFDA record carrying section 34073-7;
* **wordings** -- one distinct section text, after whitespace normalisation;
* **occurrences** -- one recognised entity span inside one wording;
* **pairs** -- one (subject moiety, object entity) couple, orientation-normalised
  and de-duplicated.

A rate quoted against the wrong denominator is the failure mode here, so
``Yield`` and ``PairCount`` each carry their own denominator rather than leaving
the caller to supply one.

**Self-matches are excluded from pairs, not from occurrences.** A label's section
routinely names its own drug ("the effect of WARFARIN is increased by..."), and
a drug does not interact with itself. Counting those spans as occurrences is
correct -- they were really recognised -- but letting them form pairs would
manufacture 1:1 self-interactions, which is the check DrugCentral's ingest
records as "0 self-pairs".
"""
from __future__ import annotations

import collections
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from tools.spl_entity_match import Match

#: FDA and label prose both band a class by the words 'strong', 'moderate' and
#: 'weak'. 'potent' is included because label prose uses it as a synonym for
#: strong; it is counted SEPARATELY in the report so the choice can be audited
#: rather than buried.
_BAND = re.compile(r"\b(strong|moderate|weak|potent)\b", re.IGNORECASE)

#: How far back from a class mention a band word still counts as qualifying it.
#: 40 characters covers 'strong cytochrome P450 1A2 inhibitors' and
#: 'moderate or weak CYP1A2 inhibitors' without reaching into a previous clause.
#: It is a heuristic and is reported as one -- the design round must decide the
#: real rule, and this measures only how OFTEN the question arises.
_BAND_WINDOW = 40


@dataclass(frozen=True)
class Yield:
    """How much of the corpus the matcher actually recognised.

    ``wordings`` is the denominator for every rate here -- deliberately not
    label count, which the de-duplication factor inflates by roughly an order of
    magnitude.
    """

    wordings: int
    with_any_entity: int
    with_moiety: int
    with_class: int
    moiety_occurrences: int
    class_occurrences: int
    distinct_moieties: int
    distinct_classes: int

    def __post_init__(self) -> None:
        for name in ("with_any_entity", "with_moiety", "with_class"):
            value = getattr(self, name)
            if value > self.wordings:
                raise ValueError(
                    f"{name}={value} exceeds {self.wordings} wordings"
                )
        if self.with_any_entity < max(self.with_moiety, self.with_class):
            raise ValueError(
                "with_any_entity must be at least as large as either kind"
            )


def summarise_yield(
    matches_by_wording: Mapping[str, Sequence[Match]],
) -> Yield:
    """Count what was recognised, per wording and per occurrence."""
    with_any = with_moiety = with_class = 0
    moiety_occurrences = class_occurrences = 0
    distinct_moieties: set[str] = set()
    distinct_classes: set[str] = set()

    for matches in matches_by_wording.values():
        kinds = set()
        for match in matches:
            for entry in match.entries:
                kinds.add(entry.kind)
                if entry.kind == "moiety":
                    moiety_occurrences += 1
                    distinct_moieties.add(entry.display)
                elif entry.kind == "class":
                    class_occurrences += 1
                    distinct_classes.add(entry.display)
        if kinds:
            with_any += 1
        if "moiety" in kinds:
            with_moiety += 1
        if "class" in kinds:
            with_class += 1

    return Yield(
        wordings=len(matches_by_wording),
        with_any_entity=with_any,
        with_moiety=with_moiety,
        with_class=with_class,
        moiety_occurrences=moiety_occurrences,
        class_occurrences=class_occurrences,
        distinct_moieties=len(distinct_moieties),
        distinct_classes=len(distinct_classes),
    )


@dataclass(frozen=True)
class PairCount:
    """Candidate pairs, and how many of them drugref already holds.

    ``held`` and ``novel`` partition ``distinct`` exactly. That is asserted
    rather than assumed: "91% new" was the figure that justified the DrugCentral
    slice, and a novelty rate whose parts do not add up is not evidence.
    """

    distinct: int
    held: int
    novel: int
    self_pairs_excluded: int

    def __post_init__(self) -> None:
        if self.held + self.novel != self.distinct:
            raise ValueError(
                f"held {self.held} + novel {self.novel} != "
                f"distinct {self.distinct}"
            )

    @property
    def novel_share(self) -> float:
        return self.novel / self.distinct if self.distinct else 0.0


def moiety_pairs(
    subject: str, matches: Iterable[Match]
) -> set[tuple[str, str]]:
    """Orientation-normalised (subject, object) moiety pairs from one wording.

    ``subject`` and the matched displays are opaque keys -- the caller decides
    whether they are UUIDs or names. Self-pairs are dropped here and counted by
    the caller.
    """
    pairs: set[tuple[str, str]] = set()
    for match in matches:
        for entry in match.entries:
            if entry.kind != "moiety":
                continue
            other = entry.display
            if other == subject:
                continue
            pairs.add((subject, other) if subject < other else (other, subject))
    return pairs


def count_pairs(
    candidate: set[tuple[str, str]],
    held: set[tuple[str, str]],
    *,
    self_pairs_excluded: int,
) -> PairCount:
    """Partition candidate pairs into those drugref holds and those it does not."""
    overlap = candidate & held
    return PairCount(
        distinct=len(candidate),
        held=len(overlap),
        novel=len(candidate) - len(overlap),
        self_pairs_excluded=self_pairs_excluded,
    )


@dataclass(frozen=True)
class BandTally:
    """How often a class mention is qualified by a potency word.

    This measures **how often the question arises**, not what the answer should
    be. Issue #102 asks what the schema does with a band; the design round
    cannot weigh that without knowing whether it affects a handful of labels or
    most of them.
    """

    class_occurrences: int
    banded: int
    by_band: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.banded > self.class_occurrences:
            raise ValueError("more banded mentions than class mentions")
        if sum(self.by_band.values()) != self.banded:
            raise ValueError("per-band tally does not sum to the banded total")

    @property
    def banded_share(self) -> float:
        return self.banded / self.class_occurrences if self.class_occurrences else 0.0


def band_for(text: str, char_start: int, *, window: int = _BAND_WINDOW) -> str | None:
    """The potency word immediately preceding a mention, if any.

    Returns the LAST band word in the window -- 'moderate or weak CYP1A2
    inhibitors' is qualified by 'weak' at its nearest edge, and taking the first
    would report 'moderate' for a phrase that names both.
    """
    start = max(0, char_start - window)
    found = _BAND.findall(text[start:char_start])
    return found[-1].lower() if found else None


def tally_bands(
    texts: Mapping[str, str],
    matches_by_wording: Mapping[str, Sequence[Match]],
    *,
    sources: frozenset[str] | None = None,
) -> BandTally:
    """Count banded vs unbanded class mentions across every wording.

    Counts class **entries**, the same unit ``summarise_yield`` reports, so the
    two figures can be divided by one another. Counting *matches* instead would
    under-count every span where a moiety and a class share one folded name, and
    the resulting rate would silently use a different denominator than the one
    it is printed beside.

    ``sources`` restricts the tally to classes published by named authorities.
    Issue #102 asks what the schema does with a potency band, and that is a
    question about the PK classes -- a rate computed over 'Antacids [MoA]' and
    every other class in the registry answers something else entirely.
    """
    occurrences = 0
    by_band: collections.Counter[str] = collections.Counter()
    for key, matches in matches_by_wording.items():
        text = texts[key]
        for match in matches:
            for entry in match.entries:
                if entry.kind != "class":
                    continue
                if sources is not None and entry.source not in sources:
                    continue
                occurrences += 1
                band = band_for(text, match.char_start)
                if band is not None:
                    by_band[band] += 1
    return BandTally(
        class_occurrences=occurrences,
        banded=sum(by_band.values()),
        by_band=dict(by_band),
    )


def frequency_profile(
    matches_by_wording: Mapping[str, Sequence[Match]], kind: str
) -> collections.Counter[str]:
    """Occurrences per distinct display name, for eyeballing false positives.

    Exact whole-token matching over a 19,438-name registry still admits real
    words -- 'iron', 'gold', 'tin', 'lead'. Rather than encode a stop-list here
    (a judgement this spike has no mandate to make), the profile is printed so a
    human can see which names dominate and the design round can decide.
    """
    counter: collections.Counter[str] = collections.Counter()
    for matches in matches_by_wording.values():
        for match in matches:
            for entry in match.entries:
                if entry.kind == kind:
                    counter[entry.display] += 1
    return counter
