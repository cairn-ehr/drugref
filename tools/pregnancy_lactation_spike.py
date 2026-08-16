#!/usr/bin/env python3
"""Run the non-firing pregnancy/lactation source utility spike.

Full upstream payloads and sampled source text stay in ``--work-dir``. The checked-in
Markdown report contains aggregate measurements, identifiers, and links only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from typing import Any

from drugref import db
from drugref.ingest import lactmed
from drugref.ingest import regulatory_population as regulatory
from drugref.ingest.checksum import checksum
from tools.pregnancy_lactation_identity import (
    IdentityIndex,
    load_identity_index,
    resolve_lactmed,
    resolve_name,
)
from tools.pregnancy_lactation_report import render_report


LACTMED_URL = (
    "https://ftp.ncbi.nlm.nih.gov/pub/litarch/90/6c/"
    "lactmed_NBK501922.tar.gz")
CIMA_BASE = "https://cima.aemps.es/cima/rest"
BDPM_BASE = "https://base-donnees-publique.medicaments.gouv.fr"
BDPM_MOBILE = "https://m.base-donnees-publique.medicaments.gouv.fr"
USER_AGENT = "drugref-source-spike/0.1 (+https://drugref.org)"

LEGAL_URLS = {
    "lactmed_collection": "https://www.ncbi.nlm.nih.gov/books/NBK501922/",
    "ncbi_policy": "https://www.ncbi.nlm.nih.gov/home/about/policies/",
    "aemps_open_data": "https://sede.aemps.gob.es/datos-abiertos/",
    "cima_api": "https://www.aemps.gob.es/apps/cima/docs/CIMA_REST_API.pdf",
    "bdpm_download": f"{BDPM_BASE}/telechargement",
    "bdpm_licence": f"{BDPM_BASE}/docs/telechargement/licence_bdpm.pdf",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _url_name(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def fetch(url: str, cache: pathlib.Path, accept: str | None = None) -> bytes:
    """Fetch one small response with bounded retry and immutable local caching."""

    if cache.exists():
        return cache.read_bytes()
    cache.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read()
            cache.write_bytes(data)
            return data
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(attempt + 1)
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def download(url: str, destination: pathlib.Path) -> None:
    """Stream one large source file; a partial response never becomes the cache."""

    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        with partial.open("wb") as out:
            shutil.copyfileobj(response, out, length=1 << 20)
    partial.replace(destination)


def medrt_baseline(conn) -> tuple[dict[str, Any], dict[str, set[str]]]:
    release = conn.execute(
        "SELECT upstream_release, source_checksum FROM drugref.ingest_run "
        "WHERE source = 'MED-RT' AND finished_at IS NOT NULL "
        "ORDER BY finished_at DESC LIMIT 1").fetchone()
    rows = conn.execute(
        "SELECT c.source_code, c.name, count(*), "
        "count(DISTINCT m.subject_moiety_uuid) "
        "FROM drugref.moiety_condition_contraindication m "
        "JOIN drugref.condition c ON c.condition_uuid = m.object_condition_uuid "
        "WHERE c.source = 'MeSH' AND "
        "(lower(c.name) LIKE '%pregnan%' OR lower(c.name) LIKE '%lactation%') "
        "GROUP BY c.source_code, c.name "
        "ORDER BY count(*) DESC, c.source_code").fetchall()
    sets: dict[str, set[str]] = {}
    for code in ("D011247", "D007774"):
        found = conn.execute(
            "SELECT m.subject_moiety_uuid "
            "FROM drugref.moiety_condition_contraindication m "
            "JOIN drugref.condition c ON c.condition_uuid = m.object_condition_uuid "
            "WHERE c.source = 'MeSH' AND c.source_code = %s", (code,)).fetchall()
        sets[code] = {str(row[0]) for row in found}
    return ({
        "release": release[0] if release else None,
        "checksum": release[1] if release else None,
        "conditions": [dict(code=code, name=name, rules=rules, moieties=moieties)
                       for code, name, rules, moieties in rows],
    }, sets)


def analyze_lactmed(path: pathlib.Path, identity: IdentityIndex,
                    medrt_sets: dict[str, set[str]]) -> dict[str, Any]:
    archived_records = sorted(
        lactmed.iter_archive(path),
        key=lambda archived: (
            archived.record.record_id,
            archived.record.title,
            archived.member_name,
        ),
    )
    records = [
        archived.record for archived in archived_records
        if lactmed.is_evidence_record(archived.record)
    ]
    auxiliary = [
        archived for archived in archived_records
        if not lactmed.is_evidence_record(archived.record)
    ]
    statuses: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    section_counts: Counter[str] = Counter()
    resolved: set[str] = set()
    signals: Counter[str] = Counter()
    for record in records:
        status, moieties = resolve_lactmed(record, identity)
        statuses[status] += 1
        resolved.update(moieties)
        if len(examples[status]) < 8:
            examples[status].append(f"{record.record_id} — {record.title}")
        kinds = {section.evidence_kind for section in record.sections}
        section_counts.update(kinds)
        text = " ".join(section.text for section in record.sections).casefold()
        for name, pattern in {
            "relative_infant_dose": r"relative infant dose|\brid\b",
            "postpartum_timing": r"postpartum|after delivery",
            "prematurity": r"prematur|preterm",
            "conditional_or_conflicting": r"conflict|however|although|until further",
            "explicit_no_information": r"information was not found",
        }.items():
            if re.search(pattern, text):
                signals[name] += 1
    ids = [record.record_id for record in records]
    return {
        "archive_bytes": path.stat().st_size,
        "archive_sha256": checksum(path),
        "nxml_members": len(archived_records),
        "records": len(records),
        "auxiliary_members": len(auxiliary),
        "auxiliary_examples": [
            archived.member_name for archived in auxiliary[:12]
        ],
        "duplicate_record_ids": len(ids) - len(set(ids)),
        "missing_revision": sum(record.revised is None for record in records),
        "publishers": dict(Counter(record.publisher for record in records)),
        "rights_variants": len({record.rights for record in records}),
        "disclaimer_variants": len({record.disclaimer for record in records}),
        "records_with_cas": sum(bool(record.cas_numbers) for record in records),
        "records_with_unii": sum(bool(record.uniis) for record in records),
        "resolution": dict(statuses),
        "resolution_examples": dict(examples),
        "section_coverage": dict(section_counts),
        "signals": dict(signals),
        "distinct_resolved_moieties": len(resolved),
        "pregnancy_overlap": len(resolved & medrt_sets["D011247"]),
        "lactation_overlap": len(resolved & medrt_sets["D007774"]),
        "outside_lactation_floor": len(resolved - medrt_sets["D007774"]),
    }


def _parallel_fetch(tasks: list[tuple[str, pathlib.Path]], workers: int,
                    accept: str | None = None) -> dict[str, bytes]:
    results: dict[str, bytes] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(fetch, url, path, accept): url for url, path in tasks}
        for future in concurrent.futures.as_completed(pending):
            url = pending[future]
            results[url] = future.result()
    return results


def analyze_cima(work: pathlib.Path, identity: IdentityIndex,
                 sample_size: int, workers: int) -> dict[str, Any]:
    cache = work / "cima"
    first_url = f"{CIMA_BASE}/medicamentos?pagina=1&autorizados=1"
    first = fetch(first_url, cache / "pages/0001.json")
    first_page = regulatory.parse_cima_page(first)
    page_count = math.ceil(first_page.total_rows / first_page.page_size)
    tasks = [(f"{CIMA_BASE}/medicamentos?pagina={page}&autorizados=1",
              cache / f"pages/{page:04d}.json")
             for page in range(2, page_count + 1)]
    raw = {first_url: first} | _parallel_fetch(tasks, workers)
    pages = [regulatory.parse_cima_page(
        raw[f"{CIMA_BASE}/medicamentos?pagina={page}&autorizados=1"])
        for page in range(1, page_count + 1)]
    products = [product for page in pages for product in page.products]
    products = list({product.registration: product for product in products}.values())
    products.sort(key=lambda product: (product.name, product.registration))
    sample = regulatory.even_sample(products, sample_size)

    tasks = []
    for product in sample:
        key = urllib.parse.quote(product.registration)
        tasks.extend([
            (f"{CIMA_BASE}/medicamento?nregistro={key}",
             cache / f"products/{key}.json"),
            (f"{CIMA_BASE}/docSegmentado/contenido/1?nregistro={key}&seccion=4.3",
             cache / f"sections/{key}-4.3.json"),
            (f"{CIMA_BASE}/docSegmentado/contenido/1?nregistro={key}&seccion=4.6",
             cache / f"sections/{key}-4.6.json"),
        ])
    sample_raw = _parallel_fetch(tasks, workers, "application/json")

    ingredient_status: Counter[str] = Counter()
    section_counts: Counter[str] = Counter()
    signals: Counter[str] = Counter()
    section_hashes: list[str] = []
    section_error_responses = 0
    sample_rows = []
    for listing in sample:
        key = urllib.parse.quote(listing.registration)
        product_url = f"{CIMA_BASE}/medicamento?nregistro={key}"
        product = regulatory.parse_cima_product(_json_bytes(sample_raw[product_url]))
        for ingredient in product.ingredients:
            status, unused = resolve_name(ingredient.name, identity)
            ingredient_status[status] += 1
        sections = []
        for code in ("4.3", "4.6"):
            url = (f"{CIMA_BASE}/docSegmentado/contenido/1?nregistro={key}"
                   f"&seccion={code}")
            if sample_raw[url]:
                parsed_json = _json_bytes(sample_raw[url])
                if isinstance(parsed_json, dict) and "error" in parsed_json:
                    section_error_responses += 1
                sections.extend(regulatory.parse_cima_sections(sample_raw[url]))
        codes = {section.code for section in sections}
        section_counts.update({"4.3": any(code.startswith("4.3") for code in codes),
                               "4.6": any(code.startswith("4.6") for code in codes)})
        text = " ".join(section.text for section in sections).casefold()
        if text:
            section_hashes.append(_sha(text.encode()))
        for name, pattern in {
            "pregnancy": r"embarazo|gestaci[oó]n|embarazada",
            "trimester": r"trimestre|semana \d+",
            "lactation": r"lactancia|leche materna",
            "dose_or_route": r"dosis|v[ií]a|administr",
            "uncertainty": r"no (?:hay|existen) datos|datos limitados|desconoce",
        }.items():
            if re.search(pattern, text):
                signals[name] += 1
        sample_rows.append({
            "registration": listing.registration,
            "name": listing.name,
            "ingredients": len(product.ingredients),
            "sections": sorted(codes),
        })

    changes_since = (dt.date.today() - dt.timedelta(days=30)).strftime("%d/%m/%Y")
    encoded_date = urllib.parse.quote(changes_since)
    changes_url = f"{CIMA_BASE}/registroCambios?fecha={encoded_date}"
    changes = fetch(changes_url, cache / "changes-30d.json")
    change_data = _json_bytes(changes)
    change_rows = (change_data.get("resultados", [])
                   if isinstance(change_data, dict) else change_data)
    return {
        "reported_products": first_page.total_rows,
        "pages": page_count,
        "unique_products": len(products),
        "segmented_smpc_products": sum(p.has_segmented_smpc for p in products),
        "unique_vtm_ids": len({p.vtm_id for p in products if p.vtm_id}),
        "combination_products": sum(p.is_combination for p in products),
        "listing_sha256": _sha(b"".join(raw[url] for url in sorted(raw))),
        "sample_size": len(sample),
        "sample_section_coverage": dict(section_counts),
        "sample_section_error_responses": section_error_responses,
        "sample_ingredient_resolution": dict(ingredient_status),
        "sample_signals": dict(signals),
        "duplicate_sample_sections": len(section_hashes) - len(set(section_hashes)),
        "changes_since": changes_since,
        "changes_reported": (
            change_data.get("totalFilas", len(change_rows))
            if isinstance(change_data, dict) else len(change_rows)
        ),
        "changes_page_rows": len(change_rows),
        "review_sample": regulatory.even_sample(sample_rows, 12),
    }


def _json_bytes(value: bytes) -> Any:
    return json.loads(value.decode("utf-8"))


def analyze_bdpm(work: pathlib.Path, identity: IdentityIndex,
                 sample_size: int, workers: int,
                 specialty_path: pathlib.Path | None,
                 composition_path: pathlib.Path | None) -> dict[str, Any]:
    cache = work / "bdpm"
    specialty_path = specialty_path or cache / "CIS_bdpm.txt"
    composition_path = composition_path or cache / "CIS_COMPO_bdpm.txt"
    if not specialty_path.exists():
        download(f"{BDPM_BASE}/download/file/CIS_bdpm.txt", specialty_path)
    if not composition_path.exists():
        download(f"{BDPM_BASE}/download/file/CIS_COMPO_bdpm.txt", composition_path)
    specialties = list(regulatory.iter_bdpm_specialties(specialty_path))
    compositions = list(regulatory.iter_bdpm_compositions(composition_path))
    by_cis: dict[str, list[regulatory.BdpmComposition]] = defaultdict(list)
    for row in compositions:
        by_cis[row.cis].append(row)
    unique_names = sorted({row.name for row in compositions})
    identity_counts = Counter(resolve_name(name, identity)[0] for name in unique_names)
    eligible = sorted((
        row for row in specialties
        if row.cis in by_cis and "active" in row.status.lower()
    ), key=lambda row: (row.name, row.cis))
    sample = regulatory.even_sample(eligible, sample_size)
    tasks = [(f"{BDPM_MOBILE}/rcp-{row.cis}-1",
              cache / f"rcp/{row.cis}.html") for row in sample]

    failures: list[str] = []
    responses: dict[str, bytes] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {executor.submit(fetch, url, path): (url, path)
                   for url, path in tasks}
        for future in concurrent.futures.as_completed(pending):
            url, unused = pending[future]
            try:
                responses[url] = future.result()
            except RuntimeError:
                failures.append(url)

    section_counts: Counter[str] = Counter()
    signals: Counter[str] = Counter()
    section_hashes: list[str] = []
    revision_counts: Counter[str] = Counter()
    review = []
    for specialty in sample:
        url = f"{BDPM_MOBILE}/rcp-{specialty.cis}-1"
        if url not in responses:
            continue
        rcp = regulatory.parse_bdpm_rcp(responses[url])
        section_counts["4.3"] += bool(rcp.section_4_3)
        section_counts["4.6"] += bool(rcp.section_4_6)
        revision_counts["present"] += bool(rcp.revised)
        text = rcp.section_4_6.casefold()
        if text:
            section_hashes.append(_sha(text.encode()))
        for name, pattern in {
            "pregnancy": r"grossesse|enceinte|gestation",
            "trimester": r"trimestre|semaine \d+",
            "lactation": r"allaitement|lait maternel",
            "dose_or_route": r"dose|voie|administr",
            "uncertainty": r"absence de donn[eé]es|donn[eé]es limit[eé]es|inconnu",
        }.items():
            if re.search(pattern, text):
                signals[name] += 1
        review.append({
            "cis": specialty.cis,
            "name": specialty.name,
            "ingredients": len(by_cis[specialty.cis]),
            "section_4_3": bool(rcp.section_4_3),
            "section_4_6": bool(rcp.section_4_6),
            "revised": rcp.revised,
        })
    return {
        "specialties": len(specialties),
        "composition_rows": len(compositions),
        "specialties_with_composition": len(by_cis),
        "distinct_ingredient_names": len(unique_names),
        "ingredient_name_resolution": dict(identity_counts),
        "specialty_sha256": checksum(specialty_path),
        "composition_sha256": checksum(composition_path),
        "sample_size": len(sample),
        "sample_fetch_failures": len(failures),
        "sample_section_coverage": dict(section_counts),
        "sample_revision_coverage": dict(revision_counts),
        "sample_signals": dict(signals),
        "duplicate_sample_sections": len(section_hashes) - len(set(section_hashes)),
        "review_sample": regulatory.even_sample(review, 12),
    }


def fetch_legal(work: pathlib.Path) -> dict[str, Any]:
    results = {}
    for name, url in LEGAL_URLS.items():
        data = fetch(url, work / "legal" / f"{name}-{_url_name(url)}")
        results[name] = {"url": url, "bytes": len(data), "sha256": _sha(data)}
        if name == "lactmed_collection":
            results[name]["toc_items"] = data.count(b'class="toc-item"')
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--work-dir", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--lactmed-archive", type=pathlib.Path)
    parser.add_argument("--bdpm-specialties", type=pathlib.Path)
    parser.add_argument("--bdpm-compositions", type=pathlib.Path)
    parser.add_argument("--cima-sample", type=int, default=120)
    parser.add_argument("--bdpm-sample", type=int, default=80)
    parser.add_argument("--workers", type=int, default=6)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    archive = args.lactmed_archive or args.work_dir / "lactmed.tar.gz"
    download(LACTMED_URL, archive)
    with db.connect(args.dsn) as conn:
        identity = load_identity_index(conn)
        medrt, medrt_sets = medrt_baseline(conn)
        database = conn.info.dbname
    result = {
        "run_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "database": database,
        "medrt": medrt,
        "legal": fetch_legal(args.work_dir),
        "lactmed": analyze_lactmed(archive, identity, medrt_sets),
        "cima": analyze_cima(
            args.work_dir, identity, args.cima_sample, args.workers),
        "bdpm": analyze_bdpm(
            args.work_dir, identity, args.bdpm_sample, args.workers,
            args.bdpm_specialties, args.bdpm_compositions),
    }
    args.work_dir.joinpath("result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    command = (
        "uv run python -m tools.pregnancy_lactation_spike \\\n"
        "  --dsn 'host=localhost port=5532 dbname=drugref_db038 user=postgres' \\\n"
        "  --work-dir /private/tmp/drugref-population-spike \\\n"
        f"  --report {args.report}")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(result, command), encoding="utf-8")
    print(json.dumps({
        "report": str(args.report),
        "lactmed_records": result["lactmed"]["records"],
        "cima_products": result["cima"]["unique_products"],
        "bdpm_specialties": result["bdpm"]["specialties"],
    }, indent=2))


if __name__ == "__main__":
    main()
