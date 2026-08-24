"""Runner for the subject-recovery measurement (slice 5c.3 design round).

**Throwaway spike code.** It answers a question; it is not the ingest.

Three stages, deliberately separated so the expensive pass happens once and its
result can be re-interpreted without repeating it:

``reach``
    The CHEAP BOUND, from the openFDA cache alone. How many distinct wordings
    are reachable only through labels with no ``openfda.unii``? Those are the
    only ones recovery can add anything to; the rest are other manufacturers
    reprinting a wording drugref can already reach.

``scan``
    The expensive pass: 17.6 GB of DailyMed's nested zips, read once, keeping
    only the labels ``reach`` identified as worth looking for. Writes
    ``recovered.jsonl``.

``resolve``
    Join what the scan found against drugref's own UNII bridge and report what
    it bought -- counted in WORDINGS rescued, never in labels found.

``yield``
    Re-run the round's own pair rule with the recovered subjects folded in, so
    the gain is a DELTA against the published baseline rather than a second
    number computed a slightly different way.

Usage::

    uv run python -m tools.spl_recovery_probe reach --cache CACHE
    uv run python -m tools.spl_recovery_probe scan  --cache CACHE \\
        --parts downloads/DAILYMED/*.zip --out CACHE/recovered.jsonl
    uv run python -m tools.spl_recovery_probe resolve --cache CACHE \\
        --recovered CACHE/recovered.jsonl --dsn "$DRUGREF_DSN"
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections.abc import Iterator

from tools.spl_dailymed_crosscheck import iter_release_labels
from tools.spl_subject_recovery import (
    SubjectUniis,
    augment_rows,
    classify_wordings,
    extract_subject_uniis,
    orphan_label_targets,
    split_wordings_by_reachability,
    summarise_recovery,
)

#: Pulled from the raw bytes before any tree is built. Parsing 50,000 XML
#: documents to discover that 24,000 of them are not wanted costs far more than
#: one regex, and the scan is the expensive stage.
_SET_ID = re.compile(rb'<setId[^>]*\sroot="([^"]+)"')


def _cache_rows(cache: pathlib.Path) -> Iterator[dict]:
    """The spike cache's per-label rows."""
    with (cache / "sections.jsonl").open() as handle:
        for line in handle:
            yield json.loads(line)


def report_reach(cache: pathlib.Path) -> None:
    """Stage 1: print the cheap bound."""
    reach = classify_wordings(_cache_rows(cache))
    print("\n=== WHAT RECOVERY COULD POSSIBLY ADD (openFDA cache only) ===")
    print(f"  labels                            {reach.labels:>9,}")
    print(f"    keyed by openfda.unii           {reach.keyed_labels:>9,}")
    print(f"    unkeyed, wording already keyed  {reach.redundant_unkeyed_labels:>9,}")
    print(f"    unkeyed, ORPHAN wording         {reach.recoverable_unkeyed_labels:>9,}")
    print(f"  distinct wordings                 {reach.distinct_wordings:>9,}")
    print(f"    reachable via a keyed label     {reach.keyed_wordings:>9,}")
    print(f"    ORPHAN -- unkeyed labels only   {reach.orphan_wordings:>9,}"
          f"   ({reach.orphan_share:.1%})")


def scan(cache: pathlib.Path, parts: list[str], out: pathlib.Path) -> None:
    """Stage 2: read DailyMed once, keeping only the targeted labels."""
    targets = orphan_label_targets(_cache_rows(cache))
    print(f"looking for {len(targets):,} labels across {len(parts)} release parts")

    written = 0
    scanned = 0
    with out.open("w") as handle:
        for part in parts:
            print(f"  scanning {part} ...", flush=True)
            for document_id, xml_bytes in iter_release_labels(part):
                scanned += 1
                match = _SET_ID.search(xml_bytes)
                if match is None or match.group(1).decode() not in targets:
                    continue
                recovered = extract_subject_uniis(xml_bytes)
                if recovered is None:
                    continue
                handle.write(
                    json.dumps(
                        {
                            "set_id": recovered.set_id,
                            "document_id": document_id,
                            "moiety_uniis": list(recovered.moiety_uniis),
                            "substance_uniis": list(recovered.substance_uniis),
                        }
                    )
                    + "\n"
                )
                written += 1
    print(f"\n  DailyMed labels read              {scanned:>9,}")
    print(f"  targeted labels found             {written:>9,}")


def _known_uniis(dsn: str) -> set[str]:
    """drugref's own UNII bridge -- the same one ``moiety_uuid`` is minted from."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM drugref.identity_claim "
            "WHERE scheme = 'UNII' AND superseded_by IS NULL"
        )
        return {value for (value,) in cur}


def resolve(cache: pathlib.Path, recovered_path: pathlib.Path, dsn: str) -> None:
    """Stage 3: what the scan actually bought."""
    targets = orphan_label_targets(_cache_rows(cache))
    known = _known_uniis(dsn)
    print(f"drugref holds {len(known):,} live UNII claims")

    rows = []
    with recovered_path.open() as handle:
        for line in handle:
            row = json.loads(line)
            rows.append(
                SubjectUniis(
                    set_id=row["set_id"],
                    moiety_uniis=tuple(row["moiety_uniis"]),
                    substance_uniis=tuple(row["substance_uniis"]),
                )
            )

    summary = summarise_recovery(rows, targets, known)
    print("\n=== WHAT THE DAILYMED SCAN RECOVERED ===")
    print(f"  orphan wordings targeted          {summary.wordings_targeted:>9,}")
    print(f"  labels targeted                   {summary.labels_targeted:>9,}")
    print(f"    found in DailyMed               {summary.labels_found:>9,}")
    print(f"    ABSENT from DailyMed            "
          f"{summary.labels_missing_from_dailymed:>9,}")
    print(f"    found but carrying no UNII      {summary.labels_without_any_unii:>9,}")
    print(f"    resolved against drugref        {summary.labels_resolved:>9,}")
    print(f"      on the active MOIETY          {summary.resolved_on_moiety:>9,}")
    print(f"      on the SALT only (issue #67)  "
          f"{summary.resolved_on_substance_only:>9,}")
    print(f"  ⇒ ORPHAN WORDINGS RESCUED         {summary.wordings_rescued:>9,}"
          f"   ({summary.rescue_share:.1%} of targeted)")


def extract_elements(downloads: pathlib.Path, cache: pathlib.Path) -> None:
    """Stage 5: pull ``spl_product_data_elements`` for every UNKEYED label.

    openFDA carries this field on 99.4% of the labels whose ``openfda`` block is
    empty. It is a flattened, uppercase, UNDELIMITED string holding the product
    name, the active ingredients, the active moieties and the excipients all
    run together -- so it is a candidate recovery route that needs no second
    corpus at all, and one whose precision has to be MEASURED rather than
    hoped for. Reading it as "the drugs this label is about" would also read
    `LACTOSE MONOHYDRATE` and `MAGNESIUM STEARATE`, which are real registry
    moieties and are not what any label is about.
    """
    from tools.spl_ddi_spike import iter_partition_records
    from tools.spl_label_extract import extract_section

    written = 0
    with (cache / "elements.jsonl").open("w") as handle:
        for partition in sorted(downloads.glob("drug-label-*.json.zip")):
            print(f"  reading {partition.name} ...", flush=True)
            for record in iter_partition_records(partition):
                if extract_section(record) is None:
                    continue
                if (record.get("openfda") or {}).get("unii"):
                    continue
                parts = record.get("spl_product_data_elements") or []
                if isinstance(parts, str):
                    parts = [parts]
                text = " ".join(p for p in parts if p)
                handle.write(
                    json.dumps(
                        {"set_id": record.get("set_id") or record.get("id") or "",
                         "elements": text}
                    ) + "\n"
                )
                written += 1
    print(f"\n  unkeyed labels written            {written:>9,}")


def _load_recovered(path: pathlib.Path) -> dict[str, SubjectUniis]:
    """The scan's output, keyed by ``set_id``."""
    recovered: dict[str, SubjectUniis] = {}
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            recovered[row["set_id"]] = SubjectUniis(
                set_id=row["set_id"],
                moiety_uniis=tuple(row["moiety_uniis"]),
                substance_uniis=tuple(row["substance_uniis"]),
            )
    return recovered


def _pairs_from(rows, matches_by_wording, registry) -> set[tuple[str, str]]:
    """Form orientation-normalised candidate pairs, exactly as the round did.

    This repeats ``tools.spl_ddi_report._report_pairs``'s rule rather than
    importing it, because that function prints instead of returning -- and a
    delta measured with a DIFFERENT rule than the baseline is not a delta.
    Self-pairs are excluded here for the same reason they were there: a label
    routinely names its own drug.
    """
    candidate: set[tuple[str, str]] = set()
    for row in rows:
        subjects = {
            registry.unii_to_moiety[u]
            for u in row["uniis"]
            if u in registry.unii_to_moiety
        }
        if not subjects:
            continue
        for match in matches_by_wording.get(row["text_key"], ()):
            for entry in match.entries:
                if entry.kind != "moiety":
                    continue
                other = registry.moiety_uuid_by_name.get(entry.display)
                if other is None:
                    continue
                for subject in subjects:
                    if subject == other:
                        continue
                    candidate.add(
                        (subject, other) if subject < other else (other, subject)
                    )
    return candidate


def report_yield(
    cache: pathlib.Path,
    recovered_path: pathlib.Path,
    dsn: str,
    suppress_path: pathlib.Path | None,
) -> None:
    """Stage 4: what recovery buys in PAIRS, and whether the material is alike.

    Two questions the wording count alone cannot answer:

    * **Is the orphan half comparable material?** If those wordings named fewer
      known drugs, 56% of wordings would be nowhere near 56% of the yield.
    * **What does recovery actually add?** Measured as a delta against the
      round's own published baseline, using the round's own suppression list --
      the only variant whose exclusions were each measured.
    """
    from tools.spl_ddi_measure import count_pairs, summarise_yield
    from tools.spl_entity_match import find_matches
    from tools.spl_registry import load_registry, load_suppress_terms

    suppress_terms: tuple[str, ...] = ()
    if suppress_path is not None:
        suppress_terms = load_suppress_terms(suppress_path)
    print("loading drugref vocabularies ...", flush=True)
    registry = load_registry(dsn, suppress_terms=suppress_terms)

    texts = {}
    with (cache / "texts.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            texts[row["text_key"]] = row["text"]

    rows = list(_cache_rows(cache))
    keyed_keys, orphan_keys = split_wordings_by_reachability(rows)

    print(f"matching {len(texts):,} distinct wordings ...", flush=True)
    matches = {
        key: find_matches(text, registry.vocabulary)
        for key, text in texts.items()
    }

    print("\n=== IS THE ORPHAN HALF COMPARABLE MATERIAL? ===")
    for name, keys in (("reachable (keyed)", keyed_keys), ("ORPHAN", orphan_keys)):
        subset = {k: matches[k] for k in keys if k in matches}
        result = summarise_yield(subset)
        print(f"  {name:<20} wordings {result.wordings:>7,}  "
              f"name a moiety {result.with_moiety / result.wordings:>6.1%}  "
              f"moiety occurrences/wording "
              f"{result.moiety_occurrences / result.wordings:>5.1f}  "
              f"distinct moieties {result.distinct_moieties:>5,}")

    recovered = _load_recovered(recovered_path)
    baseline = _pairs_from(rows, matches, registry)
    augmented = _pairs_from(augment_rows(rows, recovered), matches, registry)
    added = augmented - baseline
    held = registry.held_exact | registry.held_candidate

    base_counted = count_pairs(baseline, held, self_pairs_excluded=0)
    aug_counted = count_pairs(augmented, held, self_pairs_excluded=0)
    add_counted = count_pairs(added, held, self_pairs_excluded=0)

    print("\n=== WHAT RECOVERY BUYS, IN PAIRS ===")
    print(f"  baseline distinct pairs           {base_counted.distinct:>9,}"
          f"   novel {base_counted.novel:>7,} ({base_counted.novel_share:.1%})")
    print(f"  with recovered subjects           {aug_counted.distinct:>9,}"
          f"   novel {aug_counted.novel:>7,} ({aug_counted.novel_share:.1%})")
    print(f"  ⇒ ADDED by recovery               {len(added):>9,}"
          f"   novel {add_counted.novel:>7,} ({add_counted.novel_share:.1%})")
    if base_counted.distinct:
        print(f"    growth over the baseline        "
              f"{len(added) / base_counted.distinct:>8.1%}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)

    reach_parser = sub.add_parser("reach", help="the cheap bound")
    reach_parser.add_argument("--cache", type=pathlib.Path, required=True)

    scan_parser = sub.add_parser("scan", help="read DailyMed once")
    scan_parser.add_argument("--cache", type=pathlib.Path, required=True)
    scan_parser.add_argument("--parts", nargs="+", required=True)
    scan_parser.add_argument("--out", type=pathlib.Path, required=True)

    resolve_parser = sub.add_parser("resolve", help="what it bought")
    resolve_parser.add_argument("--cache", type=pathlib.Path, required=True)
    resolve_parser.add_argument("--recovered", type=pathlib.Path, required=True)
    resolve_parser.add_argument("--dsn", required=True)

    yield_parser = sub.add_parser("yield", help="what it buys in pairs")
    yield_parser.add_argument("--cache", type=pathlib.Path, required=True)
    yield_parser.add_argument("--recovered", type=pathlib.Path, required=True)
    yield_parser.add_argument("--dsn", required=True)
    yield_parser.add_argument("--suppress-terms", type=pathlib.Path, default=None)

    elements_parser = sub.add_parser(
        "elements", help="pull spl_product_data_elements for unkeyed labels"
    )
    elements_parser.add_argument("--downloads", type=pathlib.Path, required=True)
    elements_parser.add_argument("--cache", type=pathlib.Path, required=True)

    args = parser.parse_args(argv)
    if args.stage == "reach":
        report_reach(args.cache)
    elif args.stage == "scan":
        scan(args.cache, args.parts, args.out)
    elif args.stage == "resolve":
        resolve(args.cache, args.recovered, args.dsn)
    elif args.stage == "yield":
        report_yield(args.cache, args.recovered, args.dsn, args.suppress_terms)
    elif args.stage == "elements":
        extract_elements(args.downloads, args.cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
