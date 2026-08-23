#!/usr/bin/env python3
r"""Re-measure DrugCentral's `ddi` table against drugref's registry (issue #101).

**Why this script exists.** Issue #101's figures were measured once, on
2026-08-13, with throwaway code, against a 1.4 GB dump that was then deleted.
PROJECT-NOTES § "Which of these figures can be RE-DERIVED" lists every one of them
under *"NOT re-derivable here at all -- treat as measured-once and re-measure
before acting"*, and states the remedy in as many words: **a future source
evaluation puts its measurement in ``tools/``**. This is that measurement.

**Run it**::

    RESULTS=docs/superpowers/specs
    uv run python -m tools.drugcentral_ddi_spike \
        --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz \
        --dsn "host=localhost port=5532 dbname=drugref_dc101 user=postgres" \
        --out "$RESULTS/2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md"

(The module docstring is a RAW string: a non-raw one silently ate the backslash
before a wrapped ``--out`` argument, so the command printed by ``print(__doc__)``
could not be copy-pasted -- argparse rejected it.)

The dump is **not** in the repo and **not** in `downloads/` by default (both are
gitignored); fetch it from ``https://unmtid-dbs.net/download/`` first. The DSN must
point at a database carrying the real releases -- a `drugref migrate` plus the
documented `ingest chain` -- because every resolution figure joins against the live
registry. See PROJECT-NOTES § "How to run / test".

**What it reports, and the one thing to read first.** The script measures endpoint
resolution TWICE: once by name alone (what issue #101 did) and once through the
structural cascade in `drugref.ingest.drugcentral_resolve`. Reporting both is the
point -- the difference between them is the finding, and a single number would
hide it.

**Where the parts live.** The COPY reader is `drugref.ingest.drugcentral_dump`, the
extract cache `tools.drugcentral_cache`, the arithmetic `tools.drugcentral_ddi_measure`
and the rendering `tools.drugcentral_ddi_report` -- all four pure and tested without
a dump or a database. This file owns the two things that are neither: the command
line and the only transaction.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import pathlib
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import psycopg

# THE RULE-6 DETERMINATION IS IMPORTED, NEVER RESTATED HERE. `BUNDLEABLE_REF_IDS`
# has exactly ONE home -- `drugref.ingest.drugcentral` -- and this script filters on
# that same object at `measure_dump` below and passes it into the report, so the
# verdict the measurement PRINTS and the set the ingest APPLIES cannot drift apart.
# Rule 6 is decided by the dump's own `reference` table, NOT by DrugCentral's CC
# BY-SA over the compilation: two of its three references are third-party compendia
# it has no power to relicense, and this script re-reads and re-prints all three so
# the determination is never inferred. This file used to define its own copy of the
# set, which is the SECOND time the same defect appeared in this tooling -- the
# re-measurement round's review found a hard-coded `ref_id == "2"` in the renderer,
# unconnected to the set that filtered the rows, which is why the design spec §2
# insists in bold on the one home.
from drugref.ingest.drugcentral import BUNDLEABLE_REF_IDS
from drugref.ingest.drugcentral_resolve import (
    ROUTE_DISPLAY_NAME,
    ROUTE_NOT_A_SUBSTANCE,
    Registry,
    Resolution,
    build_endpoint_index,
    first_wins,
    fold_key,
    fold_name,
    resolve_endpoint,
)
from tools.drugcentral_cache import (
    CacheManifest,
    cache_status,
    extract,
    load,
    read_manifest,
)
from tools.drugcentral_ddi_measure import (
    class_coverage,
    names_a_qt_population,
    measure,
    mentions_qt,
    name_provenance,
)
from tools.drugcentral_ddi_report import RegistryTotals, ReportContext, render_report

# The tables this measurement reads, and the columns kept from each. `ddi`,
# `ddi_risk` and `reference` are small enough to keep whole; the other three are
# projected because `structures.molfile` alone is most of their bulk. A projection
# the dump does not declare is refused by `drugcentral_cache.extract` rather than
# written blank -- an all-empty column and an absent one are indistinguishable
# downstream, and the blank-key guards in the cascade would turn the second into a
# silent, plausible "the structural route bought nothing".
WANTED_COLUMNS: dict[str, Sequence[str] | None] = {
    "ddi": None,
    "ddi_risk": None,
    "reference": None,
    "pharma_class": ("id", "struct_id", "type", "name", "class_code", "source"),
    "structures": ("id", "name", "cas_reg_no", "inchikey", "status"),
    "synonyms": ("syn_id", "id", "name", "preferred_name", "parent_id", "lname"),
}

# `drugcentral.dump.11012023.sql.gz` -> `11012023`.
_RELEASE_IN_FILENAME = re.compile(r"\.dump\.(?P<release>\d{8})\.")


def release_from_dump(dump: pathlib.Path) -> str | None:
    """Read the published release out of the dump's filename, or return ``None``.

    Derived rather than typed, because ``--release`` defaulted to ``"11012023"``
    whatever file ``--dump`` pointed at, so measuring a later dump would assert a
    2023 release beside a SHA-256 of completely different bytes.
    """
    match = _RELEASE_IN_FILENAME.search(dump.name)
    return match.group("release") if match else None


def sha256(path: pathlib.Path) -> str:
    """Hash the dump so a later run can prove it measured the same bytes.

    Recorded in the cache manifest as well as the report, and compared before a
    cached extract is trusted -- otherwise a warm cache plus a new ``--dump``
    prints the new digest above the old figures, which is worse than recording no
    digest at all.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RegistrySide:
    """Everything read from the drugref database, in one snapshot.

    Attributes:
        registry: the three moiety lookups the cascade joins against.
        class_sources: folded ``substance_class.class_name`` -> ``source``. Read
            so the report can say how much of the endpoint residue is a class
            drugref DOES hold, and under which authority -- the half of issue
            #101's claim that was wrong.
        held: the unordered moiety pairs `ddi_candidate_pair` already carries.
        candidate_rows: its row count, which is not its pair count.
        totals: sizes and collision counts, for the report's audit line.
    """

    registry: Registry
    class_sources: Mapping[str, str]
    held: set[tuple[str, str]]
    candidate_rows: int
    totals: RegistryTotals


def load_registry(dsn: str) -> RegistrySide:
    """Load the drugref side of the join in one REPEATABLE READ transaction.

    Every statement in a READ COMMITTED transaction gets its own snapshot, so the
    registry totals, the three lookups and `ddi_candidate_pair` could each come
    from a different state of the database. For a measurement claiming
    reproducibility that is the wrong isolation level.

    Every read is ORDERED. See `drugref.ingest.drugcentral_resolve.first_wins` for
    why that is not cosmetic.
    """
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.cursor() as cur:
            cur.execute("SELECT display_name, moiety_uuid::text "
                        "FROM drugref.substance_moiety "
                        "ORDER BY display_name, moiety_uuid")
            display_name, duplicate_display_names = first_wins(
                cur.fetchall(), fold_name)

            cur.execute(
                "SELECT value, moiety_uuid::text FROM drugref.identity_claim "
                "WHERE scheme = %s AND superseded_by IS NULL "
                "ORDER BY value, moiety_uuid", ("INCHIKEY",))
            inchikey, duplicate_inchikeys = first_wins(cur.fetchall(), fold_key)

            cur.execute(
                "SELECT value, moiety_uuid::text FROM drugref.identity_claim "
                "WHERE scheme = %s AND superseded_by IS NULL "
                "ORDER BY value, moiety_uuid", ("CAS",))
            cas, duplicate_cas = first_wins(cur.fetchall(), fold_key)

            cur.execute("SELECT lower(class_name), source "
                        "FROM drugref.substance_class "
                        "ORDER BY class_name, source")
            class_sources = {name: source for name, source in cur.fetchall()}

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

    # `Registry` folds its own keys, so the SQL above does not -- the case rule
    # used to live in both places, which is the shape this repo keeps losing to.
    registry = Registry(display_name=display_name, inchikey=inchikey, cas=cas)
    totals = RegistryTotals(
        moieties=int(moieties),
        classes=int(classes),
        migration=migration,
        display_names=len(registry.display_name),
        inchikeys=len(registry.inchikey),
        cas=len(registry.cas),
        duplicate_display_names=duplicate_display_names,
        duplicate_inchikeys=duplicate_inchikeys,
        duplicate_cas=duplicate_cas,
    )
    return RegistrySide(registry, class_sources, held, candidate_rows, totals)


def name_only_resolver(registry: Registry) -> Callable[[str], Resolution]:
    """Issue #101's original resolver: ``display_name`` and nothing else.

    Kept so the report can state both figures side by side. A comparison against a
    remembered number is not a comparison.
    """
    def resolve(name: str) -> Resolution:
        hit = registry.display_name.get(fold_name(name))
        if hit is None:
            return Resolution(None, ROUTE_NOT_A_SUBSTANCE)
        return Resolution(hit, ROUTE_DISPLAY_NAME)

    return resolve


def _ensure_cache(args: argparse.Namespace, dump_sha256: str) -> CacheManifest:
    """Extract unless the cache can be PROVED to match this dump.

    Returns the manifest either way, so the report's provenance block and its
    table counts come from the same record as the TSVs beside it.
    """
    usable, reason = cache_status(args.work_dir, dump_sha256, WANTED_COLUMNS)
    if args.refresh or not usable:
        if not args.refresh:
            print(f"re-extracting: {reason}", file=sys.stderr)
        print(f"extracting {args.dump} -> {args.work_dir} ...", file=sys.stderr)
        with gzip.open(args.dump, "rt", encoding="utf-8") as stream:
            manifest = extract(
                stream,
                args.work_dir,
                wanted_columns=WANTED_COLUMNS,
                dump_path=str(args.dump),
                dump_bytes=args.dump.stat().st_size,
                dump_sha256=dump_sha256,
            )
        return manifest

    print(f"using cached extract in {args.work_dir}", file=sys.stderr)
    manifest = read_manifest(args.work_dir)
    assert manifest is not None                 # cache_status proved it readable
    return manifest


def build_context(args: argparse.Namespace, release: str) -> ReportContext:
    """Do the measuring. Every figure the report prints is decided here."""
    dump_sha256 = sha256(args.dump)
    manifest = _ensure_cache(args, dump_sha256)

    ddi = load(args.work_dir, "ddi")
    if not ddi:
        raise SystemExit("the dump's `ddi` table is empty: nothing to measure")
    structures = load(args.work_dir, "structures")
    synonyms = load(args.work_dir, "synonyms")
    references = {r["id"]: r for r in load(args.work_dir, "reference")}
    bundleable = [r for r in ddi if r["ddi_ref_id"] in BUNDLEABLE_REF_IDS]

    index = build_endpoint_index(structures, synonyms)
    side = load_registry(args.dsn)

    def cascade(name: str) -> Resolution:
        return resolve_endpoint(name, index, side.registry)

    by_name = name_only_resolver(side.registry)
    # Provenance is measured over the BUNDLEABLE endpoints, because that is the
    # denominator the synonym-bridge claim is about: 924 NDF-RT endpoint names,
    # not the 970 of the whole table. Class coverage below is whole-table, which
    # is the denominator issue #101's "970 names" claim used. Two different
    # questions, two different denominators, both named where they are printed.
    endpoints = ([r["drug_class1"] for r in bundleable]
                 + [r["drug_class2"] for r in bundleable])
    pharma_class = load(args.work_dir, "pharma_class")

    return ReportContext(
        generated=dt.date.today().isoformat(),
        dump=str(args.dump),
        dump_bytes=args.dump.stat().st_size,
        dump_sha256=dump_sha256,
        release=release,
        dump_lines=manifest.dump_lines,
        decompressed_bytes=manifest.decompressed_bytes,
        table_counts=dict(manifest.counts),
        references=references,
        ref_distribution=dict(collections.Counter(r["ddi_ref_id"] for r in ddi)),
        bundleable_ref_ids=BUNDLEABLE_REF_IDS,
        risk_vocabulary=load(args.work_dir, "ddi_risk"),
        risk_whole=dict(collections.Counter(r["ddi_risk"] for r in ddi)),
        risk_bundleable=dict(
            collections.Counter(r["ddi_risk"] for r in bundleable)),
        registry_totals=side.totals,
        candidate_rows=side.candidate_rows,
        candidate_pairs=len(side.held),
        whole_name_only=measure(ddi, by_name, side.held),
        whole_cascade=measure(ddi, cascade, side.held),
        bundleable_name_only=measure(bundleable, by_name, side.held),
        bundleable_cascade=measure(bundleable, cascade, side.held),
        whole_class_coverage=class_coverage(ddi, cascade, side.class_sources),
        whole_class_coverage_name_only=class_coverage(
            ddi, by_name, side.class_sources),
        name_provenance=name_provenance(endpoints, structures, synonyms),
        qt_rows=[r for r in ddi if mentions_qt(r)],
        pharma_class_rows=len(pharma_class),
        pharma_class_named=sum(1 for r in pharma_class if (r["name"] or "").strip()),
        # The same whole-token rule `mentions_qt` uses, so the report's two QT
        # figures are counted the same way rather than one by substring.
        pharma_class_qt=sum(
            1 for r in pharma_class if names_a_qt_population(r["name"] or "")),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", type=pathlib.Path, required=True,
                        help="drugcentral.dump.<MMDDYYYY>.sql.gz")
    parser.add_argument("--dsn", required=True,
                        help="a drugref database carrying the real releases")
    parser.add_argument("--work-dir", type=pathlib.Path,
                        default=pathlib.Path("downloads/DRUGCENTRAL/extracted"),
                        help="where the extracted TSV caches live (gitignored)")
    parser.add_argument("--out", type=pathlib.Path, required=True,
                        help="Markdown results file to write")
    parser.add_argument("--refresh", action="store_true",
                        help="re-extract even if the cache matches this dump")
    parser.add_argument("--release", default=None,
                        help="override the release read from the dump's filename")
    args = parser.parse_args(argv)

    if not args.dump.exists():
        parser.error(f"dump not found: {args.dump}")

    derived = release_from_dump(args.dump)
    release = args.release or derived
    if release is None:
        parser.error(
            f"cannot read a release date out of {args.dump.name!r}; pass --release")
    if args.release and derived and args.release != derived:
        parser.error(
            f"--release {args.release} disagrees with the dump's filename "
            f"({derived}); the report would attribute these figures to the wrong "
            "release")

    context = build_context(args, release)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_report(context), encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":                       # pragma: no cover - CLI entry point
    raise SystemExit(main())
