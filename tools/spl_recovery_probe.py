"""Runner for the subject-recovery measurement (slice 5c.3 design round).

**Throwaway spike code.** It answers a question; it is not the ingest.

Six stages, deliberately separated so the expensive pass happens once and its
result can be re-interpreted without repeating it:

``reach``
    The CHEAP BOUND, from the openFDA cache alone. How many distinct wordings
    are reachable only through labels with no ``openfda.unii``? Those are the
    only ones recovery can add anything to; the rest are other manufacturers
    reprinting a wording drugref can already reach.

``scan``
    The expensive pass: 17.6 GB of DailyMed's nested zips, read once, keeping
    only the labels ``reach`` identified as worth looking for. Writes
    ``recovered.jsonl``, and tallies every document it DROPPED, because a drop
    with no counter reappears as "absent from DailyMed" three stages later.

``resolve``
    Join what the scan found against drugref's own UNII bridge and report what
    it bought -- counted in WORDINGS rescued, never in labels found.

``elements``
    Pull ``spl_product_data_elements`` for every unkeyed label, so the route-3
    name heuristic can be measured rather than dismissed.

``quotes``
    Measure what each stored-prose window rule would actually store, which is
    what the 25%-of-section schema CHECK rests on.

``yield``
    Re-run the round's own pair rule with the recovered subjects folded in, so
    the gain is a DELTA against the published baseline rather than a second
    number computed a slightly different way.

Usage::

    CACHE=/tmp/spl
    uv run python -m tools.spl_recovery_probe reach --cache $CACHE
    uv run python -m tools.spl_recovery_probe scan  --cache $CACHE \\
        --parts downloads/DAILYMED/dm_spl_release_human_rx_part*.zip \\
        --out $CACHE/recovered.jsonl
    uv run python -m tools.spl_recovery_probe resolve --cache $CACHE \\
        --recovered $CACHE/recovered.jsonl --dsn "$DSN"
    uv run python -m tools.spl_recovery_probe elements \\
        --downloads downloads/OPENFDA --cache $CACHE
    uv run python -m tools.spl_recovery_probe yield --cache $CACHE \\
        --recovered $CACHE/recovered.jsonl --dsn "$DSN" \\
        --suppress-terms tools/spl_suppress_terms.txt

**The suppression list is not optional for a comparable baseline.** Without
``--suppress-terms`` the baseline is the round's "all names" variant (21,201
pairs), not the published one (20,554), and the delta below would be measured
against a different number than the one it is reported against.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections.abc import Iterator

from tools.spl_dailymed_crosscheck import iter_release_labels
from tools.spl_subject_read import (
    SubjectUniis,
    dedupe_by_set_id,
    extract_subject_uniis,
    subject_uniis,
)
from tools.spl_subject_recovery import (
    augment_rows,
    classify_wordings,
    orphan_label_targets,
    split_wordings_by_reachability,
    summarise_recovery,
)

#: Pulled from the raw bytes before any tree is built. Building a tree for every
#: document in the release to discover that most are not wanted costs far more
#: than one regex: the scan reads ~39,700 section-bearing labels to find the
#: 26,401 it wants, and the scan is the expensive stage.
#:
#: **Both quote styles and optional whitespace are accepted.** ``root='x'`` is
#: legal XML, and a regex that missed it would drop a targeted label BEFORE it
#: was ever parsed -- landing it in "absent from DailyMed", which is the figure
#: the design spec turns into a commitment. ``extract_subject_uniis`` re-reads
#: the set_id from the tree and the two are asserted to agree, so this filter can
#: only ever be a cheap pre-pass, never the authority.
_SET_ID = re.compile(rb"<(?:\w+:)?setId[^>]*\sroot\s*=\s*[\"']([^\"']+)[\"']")


def _cache_rows(cache: pathlib.Path) -> Iterator[dict]:
    """The spike cache's per-label rows."""
    with (cache / "sections.jsonl").open() as handle:
        for line in handle:
            yield json.loads(line)


def report_reach(cache: pathlib.Path) -> None:
    """Stage 1: print the cheap bound."""
    rows = list(_cache_rows(cache))
    reach = classify_wordings(rows)
    targets = orphan_label_targets(rows)
    if len(targets) != reach.recoverable_unkeyed_labels:
        raise ValueError(
            f"{len(targets)} scan targets but {reach.recoverable_unkeyed_labels} "
            "recoverable labels: one counts distinct set_ids and the other "
            "counts rows, and they must describe the same population"
        )
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
    """Stage 2: read DailyMed once, keeping only the targeted labels.

    **Every drop is counted.** ``labels_missing_from_dailymed`` is a set
    difference over what this stage wrote, so a document silently skipped here
    is republished downstream as a fact about the RELEASE -- "absent from
    DailyMed" -- when it is a fact about the reading. Three counters keep the two
    apart, and ``parse_failed`` is expected to be zero.
    """
    targets = orphan_label_targets(_cache_rows(cache))
    print(f"looking for {len(targets):,} labels across {len(parts)} release parts")

    written_set_ids: set[str] = set()
    rows_written = scanned = no_set_id_bytes = parse_failed = 0
    no_set_id_tree = disagreed = 0
    with out.open("w") as handle:
        for part in parts:
            print(f"  scanning {part} ...", flush=True)
            for document_id, xml_bytes in iter_release_labels(part):
                scanned += 1
                match = _SET_ID.search(xml_bytes)
                if match is None:
                    no_set_id_bytes += 1
                    continue
                pre_filter_set_id = match.group(1).decode()
                if pre_filter_set_id not in targets:
                    continue
                try:
                    recovered = extract_subject_uniis(xml_bytes)
                except Exception:  # noqa: BLE001 -- counted, then re-raised below
                    parse_failed += 1
                    continue
                if recovered is None:
                    # Either unparseable or carrying no setId in the tree. Both
                    # are readings, not release facts, so neither may fall into
                    # the absent bucket unremarked.
                    no_set_id_tree += 1
                    continue
                if recovered.set_id != pre_filter_set_id:
                    # The byte pre-filter matched a DIFFERENT setId than the
                    # document's own -- an SPL <relatedDocument> names the label
                    # it replaces. Writing the tree's value under a target
                    # selected by the regex would attach a subject to the wrong
                    # wording.
                    disagreed += 1
                    continue
                handle.write(
                    json.dumps(
                        {
                            "set_id": recovered.set_id,
                            "document_id": document_id,
                            "version": recovered.version,
                            "moiety_uniis": list(recovered.moiety_uniis),
                            "substance_uniis": list(recovered.substance_uniis),
                        }
                    )
                    + "\n"
                )
                rows_written += 1
                written_set_ids.add(recovered.set_id)
    print(f"\n  DailyMed documents read           {scanned:>9,}")
    print(f"  rows written (documents)          {rows_written:>9,}")
    print(f"  ⇒ TARGETED LABELS FOUND (set_ids) {len(written_set_ids):>9,}")
    print(f"  dropped: no setId in the bytes    {no_set_id_bytes:>9,}")
    print(f"  dropped: unreadable / no setId    {no_set_id_tree:>9,}")
    print(f"  dropped: pre-filter disagreed     {disagreed:>9,}")
    print(f"  dropped: parse failed             {parse_failed:>9,}")
    if parse_failed or disagreed:
        raise SystemExit(
            "documents were dropped for a READING reason; they would be "
            "republished as 'absent from DailyMed'. Fix before quoting."
        )


def _known_uniis(dsn: str) -> set[str]:
    """drugref's own UNII bridge -- the same one ``moiety_uuid`` is minted from."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM drugref.identity_claim "
            "WHERE scheme = 'UNII' AND superseded_by IS NULL"
        )
        known = {value for (value,) in cur}
    if not known:
        raise ValueError(
            "drugref holds no live UNII claims: every resolution below would "
            "report a confident zero"
        )
    return known


def _load_recovered(path: pathlib.Path) -> dict[str, SubjectUniis]:
    """The scan's output, one row per label, newest version winning.

    Uses the same :func:`dedupe_by_set_id` the resolve stage uses. The two used
    to disagree -- first-seen here, last-seen there -- so the resolve table and
    the pair delta described different readings of the same 44 labels.
    """
    rows = []
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            rows.append(
                SubjectUniis(
                    set_id=row["set_id"],
                    moiety_uniis=tuple(row["moiety_uniis"]),
                    substance_uniis=tuple(row["substance_uniis"]),
                    version=row.get("version"),
                )
            )
    return dedupe_by_set_id(rows)


def resolve(cache: pathlib.Path, recovered_path: pathlib.Path, dsn: str) -> None:
    """Stage 3: what the scan actually bought."""
    targets = orphan_label_targets(_cache_rows(cache))
    known = _known_uniis(dsn)
    print(f"drugref holds {len(known):,} live UNII claims")

    summary = summarise_recovery(
        _load_recovered(recovered_path).values(), targets, known
    )
    print("\n=== WHAT THE DAILYMED SCAN RECOVERED ===")
    print(f"  orphan wordings targeted          {summary.wordings_targeted:>9,}")
    print(f"  labels targeted                   {summary.labels_targeted:>9,}")
    print(f"    found in DailyMed               {summary.labels_found:>9,}")
    print(f"    ABSENT from DailyMed            "
          f"{summary.labels_missing_from_dailymed:>9,}")
    print(f"    found but carrying no UNII      {summary.labels_without_any_unii:>9,}")
    print(f"    found, UNII drugref lacks       "
          f"{summary.labels_found_but_unresolvable:>9,}")
    print(f"    resolved against drugref        {summary.labels_resolved:>9,}")
    print(f"      on the active MOIETY          {summary.resolved_on_moiety:>9,}")
    print(f"      on the SALT only (issue #67)  "
          f"{summary.resolved_on_substance_only:>9,}")
    print(f"  ⇒ ORPHAN WORDINGS RESCUED         {summary.wordings_rescued:>9,}"
          f"   ({summary.rescue_share:.1%} of targeted)")


def extract_elements(downloads: pathlib.Path, cache: pathlib.Path) -> None:
    """Stage 4: pull ``spl_product_data_elements`` for every UNKEYED label.

    openFDA carries this field on 99.5% of the labels whose ``openfda`` block is
    empty (40,633 of 40,856). It is a flattened, uppercase, UNDELIMITED string
    holding the product name, the active ingredients, the active moieties and
    the excipients all run together -- so it is a candidate recovery route that
    needs no second corpus at all, and one whose precision has to be MEASURED
    rather than hoped for. Reading it as "the drugs this label is about" would
    also read `LACTOSE MONOHYDRATE` and `MAGNESIUM STEARATE`, which are real
    registry moieties and are not what any label is about.

    The populated and empty counts are printed separately: writing an empty
    ``elements`` row and counting it identically is what made the 99.5% figure
    impossible to derive from this stage's own output.
    """
    from tools.spl_ddi_spike import iter_partition_records
    from tools.spl_label_extract import extract_section

    populated = empty = 0
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
                set_id = record.get("set_id") or record.get("id") or ""
                if not set_id:
                    raise ValueError(
                        "an unkeyed openFDA record carries neither set_id nor "
                        "id; keying it on '' would collapse every such record"
                    )
                handle.write(
                    json.dumps({"set_id": set_id, "elements": text}) + "\n"
                )
                if text:
                    populated += 1
                else:
                    empty += 1
    total = populated + empty
    print(f"\n  unkeyed labels written            {total:>9,}")
    print(f"    field POPULATED                 {populated:>9,}"
          f"   ({populated / total:.1%})" if total else "")
    print(f"    field empty                     {empty:>9,}")


def report_yield(
    cache: pathlib.Path,
    recovered_path: pathlib.Path,
    dsn: str,
    suppress_path: pathlib.Path | None,
) -> None:
    """Stage 5: what recovery buys in PAIRS, and whether the material is alike.

    Two questions the wording count alone cannot answer:

    * **Is the orphan half comparable material?** If those wordings named fewer
      known drugs, 56% of wordings would be nowhere near 56% of the yield.
    * **What does recovery actually add?** Measured as a delta against the
      round's own published baseline, using the round's own suppression list --
      the only variant whose exclusions were each measured.

    Both arms use ``spl_ddi_measure.form_candidate_pairs``, which is also what
    ``spl_ddi_report`` prints the baseline from. The probe used to re-implement
    that rule by hand, and a delta measured with a re-implementation is only a
    delta while the copy stays faithful.
    """
    from tools.spl_ddi_measure import count_pairs, form_candidate_pairs, summarise_yield
    from tools.spl_entity_match import find_matches
    from tools.spl_registry import load_registry, load_suppress_terms

    suppress_terms: tuple[str, ...] = ()
    if suppress_path is not None:
        suppress_terms = load_suppress_terms(suppress_path)
    else:
        print("WARNING: no --suppress-terms; this is the 'all names' variant, "
              "NOT the published baseline")
    print("loading drugref vocabularies ...", flush=True)
    registry = load_registry(dsn, suppress_terms=suppress_terms)
    known = _known_uniis(dsn)

    texts = {}
    with (cache / "texts.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            texts[row["text_key"]] = row["text"]

    rows = list(_cache_rows(cache))
    keyed_keys, orphan_keys = split_wordings_by_reachability(rows)
    missing = (keyed_keys | orphan_keys) - texts.keys()
    if missing:
        raise ValueError(
            f"{len(missing):,} wordings are in sections.jsonl but not in "
            "texts.jsonl: the two cache files describe different corpora, and "
            "every rate below would be quoted against a short denominator"
        )

    print(f"matching {len(texts):,} distinct wordings ...", flush=True)
    matches = {
        key: find_matches(text, registry.vocabulary)
        for key, text in texts.items()
    }

    print("\n=== IS THE ORPHAN HALF COMPARABLE MATERIAL? ===")
    for name, keys in (("reachable (keyed)", keyed_keys), ("ORPHAN", orphan_keys)):
        result = summarise_yield({k: matches[k] for k in keys})
        print(f"  {name:<20} wordings {result.wordings:>7,}  "
              f"name a moiety {result.with_moiety / result.wordings:>6.1%}  "
              f"moiety occurrences/wording "
              f"{result.moiety_occurrences / result.wordings:>5.1f}  "
              f"distinct moieties {result.distinct_moieties:>5,}")

    recovered = _load_recovered(recovered_path)
    pair_args = {
        "unii_to_moiety": registry.unii_to_moiety,
        "moiety_uuid_by_name": registry.moiety_uuid_by_name,
    }
    base = form_candidate_pairs(rows, matches, **pair_args)
    aug = form_candidate_pairs(
        augment_rows(rows, recovered, known_uniis=known), matches, **pair_args
    )
    added = aug.pairs - base.pairs
    held = registry.held_exact | registry.held_candidate

    base_counted = count_pairs(base.pairs, held, self_pairs_excluded=base.self_pairs)
    aug_counted = count_pairs(aug.pairs, held, self_pairs_excluded=aug.self_pairs)
    add_counted = count_pairs(added, held, self_pairs_excluded=0)

    print("\n=== WHAT RECOVERY BUYS, IN PAIRS ===")
    print(f"  wordings with a resolved subject, baseline  "
          f"{_wordings_with_subject(rows, {}, known):>9,}")
    print(f"  wordings with a resolved subject, recovered "
          f"{_wordings_with_subject(rows, recovered, known):>9,}")
    print(f"  labels with a resolved subject, baseline    "
          f"{base.resolved_subject_labels:>9,}")
    print(f"  labels with NO resolvable subject, baseline "
          f"{base.unresolved_subject_labels:>9,}")
    print(f"  baseline distinct pairs           {base_counted.distinct:>9,}"
          f"   novel {base_counted.novel:>7,} ({base_counted.novel_share:.1%})")
    print(f"  with recovered subjects           {aug_counted.distinct:>9,}"
          f"   novel {aug_counted.novel:>7,} ({aug_counted.novel_share:.1%})")
    print(f"  ⇒ ADDED by recovery               {len(added):>9,}"
          f"   novel {add_counted.novel:>7,} ({add_counted.novel_share:.1%})")
    if base_counted.distinct:
        print(f"    growth over the baseline        "
              f"{len(added) / base_counted.distinct:>8.1%}")


def _wordings_with_subject(
    rows: list[dict],
    recovered: dict[str, SubjectUniis],
    known: set[str],
) -> int:
    """Distinct wordings carried by at least one label whose subject RESOLVES.

    Spelled out because the round published this column with a looser meaning --
    a wording counted if a carrying label held any UNII at all, resolvable or
    not -- while the rescued-wording figure beside it required resolution. Two
    definitions of "has a subject" in adjacent tables is how 12,061 + 4,671
    came to be reported as 16,754.
    """
    with_subject: set[str] = set()
    for row in rows:
        uniis = row["uniis"]
        if not uniis:
            found = recovered.get(row["set_id"])
            uniis = list(subject_uniis(found, known)) if found else []
        if any(unii in known for unii in uniis):
            with_subject.add(row["text_key"])
    return len(with_subject)



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

    quotes_parser = sub.add_parser("quotes", help="what each window rule stores")
    quotes_parser.add_argument("--cache", type=pathlib.Path, required=True)
    quotes_parser.add_argument("--dsn", required=True)
    quotes_parser.add_argument("--suppress-terms", type=pathlib.Path, default=None)

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
    elif args.stage == "quotes":
        from tools.spl_quote_report import report_quotes

        report_quotes(args.cache, args.dsn, args.suppress_terms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
