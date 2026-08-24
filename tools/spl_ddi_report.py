"""Print every figure the 5c.3 measurement round publishes.

**Throwaway spike code.** Split out of ``tools/spl_ddi_spike.py`` under CLAUDE.md
rule 4, and named after ``tools/drugcentral_ddi_report.py`` because it does the
same job for this round: the computation lives in ``spl_ddi_measure``, and this
module only decides what gets shown and against which denominator.
"""
from __future__ import annotations

import collections
import json
import pathlib
from collections.abc import Mapping

from tools.spl_ddi_measure import (
    band_for,
    count_pairs,
    frequency_profile,
    summarise_yield,
    tally_bands,
)
from tools.spl_entity_match import find_matches
from tools.spl_registry import Registry, load_registry, load_suppress_terms


def _load_texts(cache: pathlib.Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    with (cache / "texts.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            texts[row["text_key"]] = row["text"]
    return texts


def _load_sections(cache: pathlib.Path) -> list[dict]:
    with (cache / "sections.jsonl").open() as handle:
        return [json.loads(line) for line in handle]


def measure(
    cache: pathlib.Path,
    dsn: str,
    words_path: pathlib.Path | None = None,
    suppress_path: pathlib.Path | None = None,
) -> None:
    """Stage 2: match the cached corpus and print every figure."""
    common_words = None
    if words_path is not None:
        common_words = frozenset(
            line.strip().lower()
            for line in words_path.read_text(errors="ignore").splitlines()
            if line.strip()
        )
    suppress_terms: tuple[str, ...] = ()
    if suppress_path is not None:
        suppress_terms = load_suppress_terms(suppress_path)
    print("loading drugref vocabularies ...", flush=True)
    registry = load_registry(
        dsn, common_words=common_words, suppress_terms=suppress_terms
    )
    if registry.suppress_terms:
        print(
            f"  SUPPRESSING {len(registry.suppress_terms)} measured non-entity "
            f"terms, e.g. {', '.join(registry.suppress_terms[:4])}"
        )
    if registry.excluded_common_words:
        print(
            f"  EXCLUDED {len(registry.excluded_common_words)} single-token moiety "
            f"names that are also ordinary English, e.g. "
            f"{', '.join(registry.excluded_common_words[:6])}"
        )
    print(
        f"  {len(registry.moiety_uuid_by_name):,} moiety names, "
        f"{registry.class_count:,} classes "
        f"({len(registry.vocabulary.by_key):,} distinct folded keys), "
        f"{len(registry.unii_to_moiety):,} UNII claims"
    )
    print(
        f"  held pairs: exact {len(registry.held_exact):,}, "
        f"class-expansion {len(registry.held_candidate):,}"
    )

    texts = _load_texts(cache)
    sections = _load_sections(cache)
    print(f"\nmatching {len(texts):,} distinct wordings ...", flush=True)

    matches_by_wording = {
        key: find_matches(text, registry.vocabulary) for key, text in texts.items()
    }

    result = summarise_yield(matches_by_wording)
    print("\n=== ENTITY YIELD (denominator: distinct wordings) ===")
    print(f"  wordings                     {result.wordings:>9,}")
    print(f"  name >=1 known entity        {result.with_any_entity:>9,} "
          f"({result.with_any_entity / result.wordings:.1%})")
    print(f"  name >=1 known MOIETY        {result.with_moiety:>9,} "
          f"({result.with_moiety / result.wordings:.1%})")
    print(f"  name >=1 known CLASS         {result.with_class:>9,} "
          f"({result.with_class / result.wordings:.1%})")
    print(f"  moiety occurrences           {result.moiety_occurrences:>9,}")
    print(f"  class occurrences            {result.class_occurrences:>9,}")
    print(f"  distinct moieties named      {result.distinct_moieties:>9,}")
    print(f"  distinct classes named       {result.distinct_classes:>9,}")

    _report_class_usability(matches_by_wording, registry)
    _report_bands(texts, matches_by_wording, registry)
    _report_pairs(sections, texts, matches_by_wording, registry)
    _report_profiles(matches_by_wording)


def _report_class_usability(
    matches_by_wording: Mapping[str, tuple], registry: Registry
) -> None:
    """How much of the class yield could actually serve as an endpoint.

    A class with **no members** cannot be one end of an interaction rule however
    often a label names it -- expanding it reaches nobody. Counting those
    separately turns "93.2% of wordings name a known class" from an encouraging
    number into an honest one, and the gap between the two is the real cost of
    class-grain extraction.
    """
    usable = unusable = 0
    by_source: collections.Counter[str] = collections.Counter()
    unusable_by_source: collections.Counter[str] = collections.Counter()
    for matches in matches_by_wording.values():
        for match in matches:
            for entry in match.entries:
                if entry.kind != "class":
                    continue
                by_source[entry.source] += 1
                if registry.class_members.get(entry.display, 0) > 0:
                    usable += 1
                else:
                    unusable += 1
                    unusable_by_source[entry.source] += 1

    total = usable + unusable
    print("\n=== CLASS YIELD, BY WHETHER THE CLASS HAS ANY MEMBERS ===")
    print(f"  class occurrences            {total:>9,}")
    print(f"  class HAS members (usable)   {usable:>9,} "
          f"({usable / total:.1%})" if total else "  (none)")
    print(f"  class is EMPTY (unusable)    {unusable:>9,} "
          f"({unusable / total:.1%})" if total else "")
    print("  by publishing axis (occurrences, of which empty):")
    for source, count in by_source.most_common():
        print(f"    {source:<18} {count:>9,}  empty {unusable_by_source[source]:>9,}")


#: The axes issue #102 is actually about. FDA-CYP's 65 classes are all PK by
#: construction; MED-RT's PK axis is 59 of its 3,634. Every other class in the
#: registry -- therapeutic, chemical, mechanism -- is counted separately, because
#: a band rate computed over 'Antacids [MoA]' answers a different question.
_PK_AXES = frozenset({"FDA-CYP", "MED-RT-PK"})


def _report_bands(
    texts: Mapping[str, str],
    matches_by_wording: Mapping[str, tuple],
    registry: Registry,
) -> None:
    """How often a class mention carries a potency band, and whether we can use it."""
    overall = tally_bands(texts, matches_by_wording)  # type: ignore[arg-type]
    pk_only = tally_bands(texts, matches_by_wording, sources=_PK_AXES)  # type: ignore[arg-type]

    print("\n=== POTENCY BANDS (issue #102) ===")
    for label, tally in (("every class axis", overall), ("PK axes only", pk_only)):
        print(f"  {label}:")
        print(f"    class occurrences          {tally.class_occurrences:>9,}")
        print(f"    qualified by a band word   {tally.banded:>9,} "
              f"({tally.banded_share:.1%})")
        for band, count in sorted(tally.by_band.items(), key=lambda kv: -kv[1]):
            print(f"      {band:<24} {count:>9,}")

    # The figure that decides #102: when a label bands a class, does drugref
    # hold a class at that band, and does it contain anybody? An empty class is
    # not a usable answer -- 'CYP1A2 strong inhibitor [FDA-CYP]' has 0 members.
    banded_pk: collections.Counter[str] = collections.Counter()
    for key, matches in matches_by_wording.items():
        text = texts[key]
        for match in matches:
            for entry in match.entries:
                if entry.kind != "class" or entry.source not in _PK_AXES:
                    continue
                if band_for(text, match.char_start) is not None:
                    banded_pk[entry.display] += 1

    print("\n  PK classes named WITH a band, and their membership in drugref:")
    if not banded_pk:
        print("    (none)")
    for name, count in banded_pk.most_common(20):
        members = registry.class_members.get(name, 0)
        flag = "  <-- EMPTY" if members == 0 else ""
        print(f"    {count:>7,} mentions  {members:>4} members  {name}{flag}")
    total_empty = sum(
        1 for name in banded_pk if registry.class_members.get(name, 0) == 0
    )
    print(
        f"    {total_empty} of {len(banded_pk)} banded PK classes "
        "are EMPTY in drugref"
    )


def _report_pairs(
    sections: list[dict],
    texts: Mapping[str, str],
    matches_by_wording: Mapping[str, tuple],
    registry: Registry,
) -> None:
    """Form candidate drug-drug pairs and compare them with what drugref holds."""
    candidate: set[tuple[str, str]] = set()
    self_pairs = 0
    resolved_subject_labels = 0
    unresolved_subject_labels = 0

    for row in sections:
        subjects = {
            registry.unii_to_moiety[u]
            for u in row["uniis"]
            if u in registry.unii_to_moiety
        }
        if not subjects:
            unresolved_subject_labels += 1
            continue
        resolved_subject_labels += 1
        for match in matches_by_wording.get(row["text_key"], ()):  # type: ignore[arg-type]
            for entry in match.entries:
                if entry.kind != "moiety":
                    continue
                other = registry.moiety_uuid_by_name.get(entry.display)
                if other is None:
                    continue
                for subject in subjects:
                    if subject == other:
                        self_pairs += 1
                        continue
                    candidate.add(
                        (subject, other) if subject < other else (other, subject)
                    )

    held = registry.held_exact | registry.held_candidate
    counted = count_pairs(candidate, held, self_pairs_excluded=self_pairs)
    vs_exact = count_pairs(candidate, registry.held_exact, self_pairs_excluded=0)

    print("\n=== CANDIDATE DRUG-DRUG PAIRS ===")
    print(f"  labels with a resolved subject   {resolved_subject_labels:>9,}")
    print(f"  labels with NO resolvable subject {unresolved_subject_labels:>9,}")
    print(f"  self-pairs excluded              {counted.self_pairs_excluded:>9,}")
    print(f"  distinct candidate pairs         {counted.distinct:>9,}")
    print(f"  already held (exact OR class)    {counted.held:>9,}")
    print(f"  NOVEL                            {counted.novel:>9,} "
          f"({counted.novel_share:.1%})")
    print(f"  novel vs exact_ddi_pair alone    {vs_exact.novel:>9,} "
          f"({vs_exact.novel_share:.1%})")


def _report_profiles(matches_by_wording: Mapping[str, tuple]) -> None:
    """Print the most frequent matches so false positives are visible."""
    for kind in ("moiety", "class"):
        profile = frequency_profile(matches_by_wording, kind)  # type: ignore[arg-type]
        print(f"\n=== TOP 25 {kind.upper()} MATCHES (eyeball for false positives) ===")
        for name, count in profile.most_common(25):
            print(f"  {count:>8,}  {name}")


