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
from collections.abc import Iterator

from tools.spl_ddi_report import measure
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
