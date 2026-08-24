"""Runner for the slice 5c.3 SPL/DailyMed measurement spike.

**Throwaway spike code.** It answers questions; it is not the ingest.

Two stages, deliberately separated so the expensive pass happens once:

``extract``
    Walk openFDA's 14 bulk partitions (~1.7 GB zipped, 262,032 records), keep
    only the labels carrying section 34073-7, and write two caches:

    * ``sections.jsonl`` -- one row per LABEL, without the prose, carrying the
      identity fields and the text's de-duplication key;
    * ``texts.jsonl`` -- one row per DISTINCT wording.

    Splitting them is not tidiness. The corpus is dominated by generic labels
    repeating one manufacturer's words (a single UNII appears on up to 498
    labels), so storing the prose per-label would multiply it needlessly, and --
    more importantly -- it keeps the two units the evaluation once conflated
    physically apart: labels are counted in one file, wordings in the other.

``measure``
    Load the caches plus drugref's own vocabularies from a database, run the
    matcher, and print every figure the design round needs.

Both stages are re-runnable and neither writes to the drugref schema.

Usage::

    uv run python -m tools.spl_ddi_spike extract --downloads downloads/OPENFDA \\
        --out /tmp/spl
    uv run python -m tools.spl_ddi_spike measure --cache /tmp/spl \\
        --dsn "host=localhost port=5532 dbname=drugref_spl user=postgres"
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from tools.spl_ddi_measure import (
    band_for,
    count_pairs,
    frequency_profile,
    summarise_yield,
    tally_bands,
)
from tools.spl_entity_match import (
    Entry,
    Vocabulary,
    build_vocabulary,
    find_matches,
    fold,
    name_variants,
)
from tools.spl_label_extract import Census, LabelSection, extract_section


def iter_partition_records(path: pathlib.Path) -> Iterator[dict]:
    """Yield every record from one openFDA ``*.json.zip`` partition.

    Each partition is a single JSON document holding 20,000 records under
    ``results``. At ~633 MB uncompressed that is comfortably loadable one
    partition at a time, so this deliberately does NOT pull in a streaming JSON
    parser -- one fewer dependency to licence-check for a throwaway probe.
    """
    with zipfile.ZipFile(path) as archive:
        (member,) = archive.namelist()
        with archive.open(member) as handle:
            document = json.load(handle)
    yield from document.get("results", [])


def extract(downloads: pathlib.Path, out: pathlib.Path) -> Census:
    """Stage 1: build the two caches and return the corpus census."""
    out.mkdir(parents=True, exist_ok=True)
    partitions = sorted(downloads.glob("drug-label-*.json.zip"))
    if not partitions:
        raise SystemExit(f"no openFDA partitions under {downloads}")

    records = 0
    by_product_type: collections.Counter[str | None] = collections.Counter()
    with_unii = 0
    seen_texts: set[str] = set()

    sections_path = out / "sections.jsonl"
    texts_path = out / "texts.jsonl"
    with sections_path.open("w") as sections_out, texts_path.open("w") as texts_out:
        for partition in partitions:
            print(f"  reading {partition.name} ...", flush=True)
            for record in iter_partition_records(partition):
                records += 1
                section = extract_section(record)
                if section is None:
                    continue
                by_product_type[section.product_type] += 1
                if section.uniis:
                    with_unii += 1
                key = section.text_key
                sections_out.write(json.dumps(_section_row(section, key)) + "\n")
                if key not in seen_texts:
                    seen_texts.add(key)
                    texts_out.write(
                        json.dumps({"text_key": key, "text": section.text}) + "\n"
                    )

    return Census(
        records=records,
        with_section=sum(by_product_type.values()),
        by_product_type=dict(by_product_type),
        with_unii=with_unii,
        distinct_text_keys=len(seen_texts),
    )


def _section_row(section: LabelSection, key: str) -> dict:
    """The per-label cache row -- identity and provenance, never the prose."""
    return {
        "set_id": section.set_id,
        "version": section.version,
        "effective_time": section.effective_time,
        "product_type": section.product_type,
        "uniis": list(section.uniis),
        "text_key": key,
    }


def _report_census(census: Census) -> None:
    print("\n=== CORPUS CENSUS (openFDA drug/label bulk export) ===")
    print(f"  records read                 {census.records:>9,}")
    print(f"  carry section 34073-7        {census.with_section:>9,}")
    print(f"  do NOT carry it              {census.without_section:>9,}")
    print("  by product type:")
    for product_type, count in sorted(
        census.by_product_type.items(), key=lambda kv: -kv[1]
    ):
        print(f"    {str(product_type):<34} {count:>9,}")
    print(f"  carry >=1 UNII               {census.with_unii:>9,}")
    print(f"  DISTINCT wordings            {census.distinct_text_keys:>9,}")
    factor = census.with_section / census.distinct_text_keys
    print(f"  de-duplication factor        {factor:>9.2f} labels per wording")


@dataclass(frozen=True)
class Registry:
    """drugref's own vocabularies and holdings, loaded once for the measurement."""

    vocabulary: Vocabulary
    moiety_uuid_by_name: Mapping[str, str]
    unii_to_moiety: Mapping[str, str]
    held_exact: set[tuple[str, str]]
    held_candidate: set[tuple[str, str]]
    class_members: Mapping[str, int]
    class_count: int
    excluded_common_words: tuple[str, ...] = ()


def load_registry(
    dsn: str, *, common_words: frozenset[str] | None = None
) -> Registry:
    """Read the registry, the class vocabulary and the pairs drugref holds.

    Moiety names follow FDA-CYP's precedent exactly -- ``display_name``, exact
    and case-insensitive -- so this measurement's resolution behaviour is the
    same one the shipped code already uses, rather than a more generous variant
    that would flatter the yield.

    ``common_words`` drops SINGLE-TOKEN moiety names that are also ordinary
    English -- 'lead', 'iron', 'alcohol'. It exists so the pair count can be
    reported as a RANGE between two reproducible endpoints rather than as one
    number resting on somebody's judgement about which names are real. Both ends
    are wrong in a known direction: keeping every name over-counts (a label
    saying 'lead to hypotension' scores the metal), and dropping all 463 of them
    under-counts (amphetamine and adenosine are perfectly good drugs). The truth
    is between, and saying so is more honest than picking one.
    """
    import psycopg

    entries: list[Entry] = []
    moiety_uuid_by_name: dict[str, str] = {}
    unii_to_moiety: dict[str, str] = {}
    held_exact: set[tuple[str, str]] = set()
    held_candidate: set[tuple[str, str]] = set()
    class_members: dict[str, int] = {}
    excluded_names: list[str] = []

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moiety_uuid, display_name FROM drugref.substance_moiety"
            )
            for moiety_uuid, display_name in cur:
                moiety_uuid_by_name[display_name] = str(moiety_uuid)
                if common_words is not None:
                    folded = fold(display_name)
                    if " " not in folded and folded in common_words:
                        excluded_names.append(display_name)
                        continue
                entries.append(
                    Entry(kind="moiety", key=display_name, display=display_name)
                )

            # Member counts come along because issue #102's real question is not
            # "does drugref have a class for this band" but "does that class
            # contain anybody" -- 'CYP1A2 strong inhibitor [FDA-CYP]' exists and
            # is EMPTY, and a design that checked only for existence would not
            # notice.
            cur.execute(
                "SELECT c.class_uuid, c.class_name, c.source, c.concept_type, "
                "       count(m.moiety_uuid) AS members "
                "  FROM drugref.substance_class c "
                "  LEFT JOIN drugref.class_membership m USING (class_uuid) "
                " GROUP BY c.class_uuid, c.class_name, c.source, c.concept_type"
            )
            classes = cur.fetchall()
            for _class_uuid, class_name, source, concept_type, members in classes:
                # MED-RT's PK axis is broken out from the rest of MED-RT because
                # that axis -- not the therapeutic or chemical ones -- is what
                # issue #102 is about.
                axis = f"{source}-PK" if source == "MED-RT" and concept_type == "PK" \
                    else source
                class_members[class_name] = members
                for variant in name_variants(class_name):
                    entries.append(
                        Entry(
                            kind="class", key=variant,
                            display=class_name, source=axis,
                        )
                    )

            cur.execute(
                "SELECT value, moiety_uuid FROM drugref.identity_claim "
                "WHERE scheme = 'UNII' AND superseded_by IS NULL"
            )
            for value, moiety_uuid in cur:
                unii_to_moiety[value] = str(moiety_uuid)

            cur.execute("SELECT moiety_lo, moiety_hi FROM drugref.exact_ddi_pair")
            for lo, hi in cur:
                held_exact.add((str(lo), str(hi)))

            cur.execute(
                "SELECT subject_moiety, partner_moiety FROM drugref.ddi_candidate_pair"
            )
            for subject, partner in cur:
                a, b = str(subject), str(partner)
                held_candidate.add((a, b) if a < b else (b, a))

    return Registry(
        vocabulary=build_vocabulary(entries),
        moiety_uuid_by_name=moiety_uuid_by_name,
        unii_to_moiety=unii_to_moiety,
        held_exact=held_exact,
        held_candidate=held_candidate,
        class_members=class_members,
        class_count=len(classes),
        excluded_common_words=tuple(sorted(excluded_names)),
    )


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
    cache: pathlib.Path, dsn: str, words_path: pathlib.Path | None = None
) -> None:
    """Stage 2: match the cached corpus and print every figure."""
    common_words = None
    if words_path is not None:
        common_words = frozenset(
            line.strip().lower()
            for line in words_path.read_text(errors="ignore").splitlines()
            if line.strip()
        )
    print("loading drugref vocabularies ...", flush=True)
    registry = load_registry(dsn, common_words=common_words)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)

    extract_parser = sub.add_parser("extract", help="build the caches")
    extract_parser.add_argument("--downloads", type=pathlib.Path, required=True)
    extract_parser.add_argument("--out", type=pathlib.Path, required=True)

    measure_parser = sub.add_parser("measure", help="print the figures")
    measure_parser.add_argument("--cache", type=pathlib.Path, required=True)
    measure_parser.add_argument("--dsn", required=True)
    measure_parser.add_argument(
        "--exclude-common-words", type=pathlib.Path, default=None,
        help="word list; single-token moiety names appearing in it are dropped, "
             "giving the LOW end of the candidate-pair range",
    )

    args = parser.parse_args(argv)
    if args.stage == "measure":
        measure(args.cache, args.dsn, args.exclude_common_words)
        return 0
    if args.stage == "extract":
        census = extract(args.downloads, args.out)
        _report_census(census)
        (args.out / "census.json").write_text(
            json.dumps(
                {
                    "records": census.records,
                    "with_section": census.with_section,
                    "by_product_type": {
                        str(k): v for k, v in census.by_product_type.items()
                    },
                    "with_unii": census.with_unii,
                    "distinct_text_keys": census.distinct_text_keys,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
