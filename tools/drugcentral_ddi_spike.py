#!/usr/bin/env python3
"""Re-measure DrugCentral's `ddi` table against drugref's registry (issue #101).

**Why this script exists.** Issue #101's figures were measured once, on
2026-08-13, with throwaway code, against a 1.4 GB dump that was then deleted.
PROJECT-NOTES § "Which of these figures can be RE-DERIVED" lists every one of them
under *"NOT re-derivable here at all -- treat as measured-once and re-measure
before acting"*, and states the remedy in as many words: **a future source
evaluation puts its measurement in ``tools/``**. This is that measurement.

**Run it**::

    uv run python -m tools.drugcentral_ddi_spike \\
        --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz \\
        --dsn "host=localhost port=5532 dbname=drugref_dc101 user=postgres" \\
        --out docs/superpowers/specs/\
              2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md

The dump is **not** in the repo and **not** in `downloads/` by default (both are
gitignored); fetch it from ``https://unmtid-dbs.net/download/`` first. The DSN must
point at a database carrying the real releases -- a `drugref migrate` plus the
documented `ingest chain` -- because every resolution figure joins against the live
registry. Building one takes ~132 s; see PROJECT-NOTES § "How to run / test".

**What it reports, and the one thing to read first.** The script measures endpoint
resolution TWICE: once by name alone (what issue #101 did) and once through the
structural cascade in `tools.drugcentral_resolve`. Reporting both is the point --
the difference between them is the finding, and a single number would hide it.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import pathlib
import sys
from collections.abc import Mapping, Sequence

import psycopg

from tools.drugcentral_dump import iter_copy_rows
from tools.drugcentral_resolve import (
    ROUTE_UNRESOLVED,
    Registry,
    build_endpoint_index,
    resolve_endpoint,
    unordered_pair,
)

# The tables this measurement reads, and the columns kept from each. `ddi`,
# `ddi_risk` and `reference` are small enough to keep whole; the other three are
# projected because `structures.molfile` alone is most of their bulk.
WANTED_COLUMNS: dict[str, Sequence[str] | None] = {
    "ddi": None,
    "ddi_risk": None,
    "reference": None,
    "pharma_class": ("id", "struct_id", "type", "name", "class_code", "source"),
    "structures": ("id", "name", "cas_reg_no", "inchikey", "status"),
    "synonyms": ("syn_id", "id", "name", "preferred_name", "parent_id", "lname"),
}

# `ddi.ddi_ref_id` values whose rows drugref may bundle. Rule 6 is decided by the
# `reference` table, NOT by DrugCentral's own CC BY-SA over the compilation: two of
# its three references are third-party compendia it has no power to relicense. The
# script re-reads and re-prints all three so the determination is never inferred.
BUNDLEABLE_REF_IDS = frozenset({"2"})


def _mentions_qt(row: Mapping[str, str]) -> bool:
    """True if a `ddi` row mentions QT prolongation anywhere (issue 93).

    Endpoints AND description, because the two class-named QT populations appear
    as endpoints while the third row mentions QT only in its prose.
    """
    blob = f'{row["drug_class1"]} {row["drug_class2"]} {row["description"]}'.lower()
    return "qt" in blob or "torsade" in blob


def sha256(path: pathlib.Path) -> str:
    """Hash the dump so a later run can prove it measured the same bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract(dump: pathlib.Path, work_dir: pathlib.Path) -> dict[str, int]:
    """Stream the dump once, writing one TSV per wanted table. Returns row counts.

    One pass over ~5 GB of decompressed SQL, ~14 s. The caches make every later
    phase cheap, which is what lets this script be re-run while a design is being
    argued about rather than once at the end.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    writers: dict[str, csv.DictWriter] = {}
    handles: dict[str, object] = {}
    counts: collections.Counter[str] = collections.Counter()

    with gzip.open(dump, "rt", encoding="utf-8") as stream:
        for table, row in iter_copy_rows(stream, set(WANTED_COLUMNS)):
            if table not in writers:
                columns = WANTED_COLUMNS[table] or list(row)
                path = work_dir / f"{table}.tsv"
                handle = path.open("w", newline="", encoding="utf-8")
                handles[table] = handle
                writers[table] = csv.DictWriter(
                    handle, fieldnames=list(columns), delimiter="\t",
                    extrasaction="ignore")
                writers[table].writeheader()
            writers[table].writerow(
                {k: ("" if v is None else v) for k, v in row.items()})
            counts[table] += 1

    for handle in handles.values():
        handle.close()          # type: ignore[attr-defined]
    return dict(counts)


def load(work_dir: pathlib.Path, table: str) -> list[dict[str, str]]:
    """Read one cached TSV back."""
    with (work_dir / f"{table}.tsv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_registry(
    dsn: str,
) -> tuple[Registry, set[tuple[str, str]], int, dict[str, int]]:
    """Load the drugref side: three lookups, the held pairs, and some totals.

    ``setdefault`` on every map because a duplicate key must not silently pick the
    last row read; the totals report whether any duplicates existed at all.
    """
    display_name: dict[str, str] = {}
    inchikey: dict[str, str] = {}
    cas: dict[str, str] = {}

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT lower(display_name), moiety_uuid::text "
                    "FROM drugref.substance_moiety")
        for name, uuid in cur.fetchall():
            display_name.setdefault(name, uuid)

        cur.execute(
            "SELECT upper(value), moiety_uuid::text FROM drugref.identity_claim "
            "WHERE scheme = %s AND superseded_by IS NULL", ("INCHIKEY",))
        for value, uuid in cur.fetchall():
            inchikey.setdefault(value, uuid)

        cur.execute(
            "SELECT upper(value), moiety_uuid::text FROM drugref.identity_claim "
            "WHERE scheme = %s AND superseded_by IS NULL", ("CAS",))
        for value, uuid in cur.fetchall():
            cas.setdefault(value, uuid)

        cur.execute("""
            SELECT DISTINCT
                   least(subject_moiety, partner_moiety)::text,
                   greatest(subject_moiety, partner_moiety)::text
              FROM drugref.ddi_candidate_pair
        """)
        held = {(a, b) for a, b in cur.fetchall()}

        cur.execute("SELECT count(*) FROM drugref.ddi_candidate_pair")
        candidate_rows = int(cur.fetchone()[0])

        cur.execute("""
            SELECT (SELECT count(*) FROM drugref.substance_moiety),
                   (SELECT count(*) FROM drugref.substance_class),
                   (SELECT max(filename) FROM drugref.schema_migration)
        """)
        moieties, classes, migration = cur.fetchone()

    totals = {"moieties": int(moieties), "classes": int(classes),
              "migration": migration}
    return Registry(display_name, inchikey, cas), held, candidate_rows, totals


def name_only(name: str, registry: Registry) -> tuple[str | None, str]:
    """Issue #101's original resolver: ``display_name`` and nothing else.

    Kept so the report can state both figures side by side. A comparison against a
    remembered number is not a comparison.
    """
    hit = registry.display_name.get(name.strip().lower())
    return (hit, "display_name") if hit is not None else (None, ROUTE_UNRESOLVED)


def measure(
    rows: Sequence[Mapping[str, str]],
    resolve: object,
    held: set[tuple[str, str]],
) -> dict[str, object]:
    """Resolve every endpoint in *rows* and count rows, pairs and overlap.

    Three units, deliberately kept apart and all reported: **rows** (one `ddi`
    record), **pairs** (a resolved, orientation-normalised moiety pair) and
    **distinct pairs**. PROJECT-NOTES records that these were being quoted
    interchangeably in the original evaluation.
    """
    names = sorted({r["drug_class1"] for r in rows} | {r["drug_class2"] for r in rows})
    resolved: dict[str, str | None] = {}
    routes: collections.Counter[str] = collections.Counter()
    for name in names:
        uuid, route = resolve(name)            # type: ignore[operator]
        resolved[name] = uuid
        routes[route] += 1

    pairs: set[tuple[str, str]] = set()
    unresolvable_rows = 0
    self_pair_rows = 0
    for row in rows:
        left, right = resolved[row["drug_class1"]], resolved[row["drug_class2"]]
        if left is None or right is None:
            unresolvable_rows += 1
            continue
        pair = unordered_pair(left, right)
        if pair is None:
            self_pair_rows += 1
            continue
        pairs.add(pair)

    overlap = pairs & held
    return {
        "rows": len(rows),
        "names": len(names),
        "routes": dict(routes),
        "names_resolved": sum(1 for v in resolved.values() if v is not None),
        "unresolved_names": sorted(n for n in names if resolved[n] is None),
        "unresolvable_rows": unresolvable_rows,
        "self_pair_rows": self_pair_rows,
        "pairs": len(pairs),
        "held": len(overlap),
        "new": len(pairs) - len(overlap),
    }


def render(context: dict[str, object]) -> str:
    """Render the measurement as Markdown. Pure: every figure arrives in *context*."""
    from tools.drugcentral_ddi_report import render_report

    return render_report(context)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", type=pathlib.Path, required=True,
                        help="drugcentral.dump.<date>.sql.gz")
    parser.add_argument("--dsn", required=True,
                        help="a drugref database carrying the real releases")
    parser.add_argument("--work-dir", type=pathlib.Path,
                        default=pathlib.Path("downloads/DRUGCENTRAL/extracted"),
                        help="where the extracted TSV caches live (gitignored)")
    parser.add_argument("--out", type=pathlib.Path, required=True,
                        help="Markdown results file to write")
    parser.add_argument("--refresh", action="store_true",
                        help="re-extract even if the TSV caches already exist")
    parser.add_argument("--release", default="11012023",
                        help="the dump's published date, recorded in the report")
    args = parser.parse_args(argv)

    if not args.dump.exists():
        parser.error(f"dump not found: {args.dump}")

    cached = (args.work_dir / "ddi.tsv").exists()
    if args.refresh or not cached:
        print(f"extracting {args.dump} -> {args.work_dir} ...", file=sys.stderr)
        table_counts = extract(args.dump, args.work_dir)
    else:
        print(f"using cached extract in {args.work_dir}", file=sys.stderr)
        table_counts = {t: len(load(args.work_dir, t)) for t in WANTED_COLUMNS}

    ddi = load(args.work_dir, "ddi")
    references = {r["id"]: r for r in load(args.work_dir, "reference")}
    bundleable = [r for r in ddi if r["ddi_ref_id"] in BUNDLEABLE_REF_IDS]

    index = build_endpoint_index(load(args.work_dir, "structures"),
                                 load(args.work_dir, "synonyms"))
    registry, held, candidate_rows, totals = load_registry(args.dsn)

    def cascade(name: str) -> tuple[str | None, str]:
        return resolve_endpoint(name, index, registry)

    context: dict[str, object] = {
        "generated": dt.date.today().isoformat(),
        "dump": str(args.dump),
        "dump_bytes": args.dump.stat().st_size,
        "dump_sha256": sha256(args.dump),
        "release": args.release,
        "table_counts": table_counts,
        "references": references,
        "ref_distribution": dict(collections.Counter(r["ddi_ref_id"] for r in ddi)),
        "risk_vocabulary": load(args.work_dir, "ddi_risk"),
        "risk_whole": dict(collections.Counter(r["ddi_risk"] for r in ddi)),
        "risk_bundleable": dict(collections.Counter(r["ddi_risk"] for r in bundleable)),
        "registry_totals": totals,
        "candidate_rows": candidate_rows,
        "candidate_pairs": len(held),
        "whole_name_only": measure(ddi, lambda n: name_only(n, registry), held),
        "whole_cascade": measure(ddi, cascade, held),
        "bundleable_name_only": measure(
            bundleable, lambda n: name_only(n, registry), held),
        "bundleable_cascade": measure(bundleable, cascade, held),
        "qt_rows": [r for r in ddi if _mentions_qt(r)],
        "pharma_class_qt": sum(1 for r in load(args.work_dir, "pharma_class")
                               if "qt" in (r["name"] or "").lower()),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(context), encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":                       # pragma: no cover - CLI entry point
    raise SystemExit(main())
