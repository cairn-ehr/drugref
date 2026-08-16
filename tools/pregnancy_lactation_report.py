"""Render the aggregate pregnancy/lactation spike report."""
from __future__ import annotations

import json
from typing import Any


def _table(rows: list[list[Any]], headers: list[str]) -> str:
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for unused in headers) + " |",
    ]
    output.extend(
        "| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _bdpm_section_labels(row: dict[str, Any]) -> str:
    labels = [
        code for code, present in (
            ("4.3", row["section_4_3"]),
            ("4.6", row["section_4_6"]),
        )
        if present
    ]
    return ", ".join(labels) or "—"


def render_report(result: dict[str, Any], command: str) -> str:
    medrt = result["medrt"]
    lact = result["lactmed"]
    cima = result["cima"]
    bdpm = result["bdpm"]
    medrt_rows = [
        [row["name"], row["code"], row["rules"], row["moieties"]]
        for row in medrt["conditions"]
    ]
    legal_rows = [
        [name, item["bytes"], f"`{item['sha256']}`", item["url"]]
        for name, item in result["legal"].items()
    ]
    review_rows = [
        ["CIMA", row["registration"], row["name"], row["ingredients"],
         ", ".join(row["sections"]) or "—"]
        for row in cima["review_sample"]
    ]
    review_rows.extend([
        ["BDPM", row["cis"], row["name"], row["ingredients"],
         _bdpm_section_labels(row)]
        for row in bdpm["review_sample"]
    ])
    lines = [
        "# Pregnancy and lactation source spike — measured results",
        "",
        f"**Run:** {result['run_at']} · **Database:** `{result['database']}` ·",
        "**Status:** computational source spike complete; clinician review pending.",
        "",
        "Command:",
        "",
        "```bash",
        command,
        "```",
        "",
        "Full source payloads and sampled narrative remain outside the repository.",
        "This report contains aggregate measurements, identifiers and links only.",
        "No clinical assertion, schema row or alert was created.",
        "",
        "## Decision",
        "",
        "| source | decision | measured reason |",
        "| --- | --- | --- |",
        ("| MED-RT | **Keep as candidate floor** | Existing direct categorical "
         "patient-state assertions |"),
        (f"| LactMed | **Design next** | {lact['nxml_members']:,} XML members "
         f"parsed; {lact['records']:,} evidence records and "
         f"{lact['distinct_resolved_moieties']:,} moieties resolved |"),
        (f"| AEMPS CIMA | **Design next** | {cima['unique_products']:,} "
         "authorised products; segmented SmPCs and change API usable |"),
        ("| ANSM BDPM | **Design next (non-firing)** | Official "
         f"bulk plus RCP; 4.6 in {bdpm['sample_section_coverage'].get('4.6', 0)}"
         f"/{bdpm['sample_size']} sampled products |"),
        "",
        "All new sources remain **non-firing evidence candidates**. `Design next`",
        "does not approve normalization or writing to `curated_condition`.",
        "",
        "## MED-RT floor",
        "",
        f"Release `{medrt['release']}`, checksum `{medrt['checksum']}`.",
        "",
        _table(medrt_rows, ["condition", "MeSH", "direct rules", "moieties"]),
        "",
        "## LactMed",
        "",
        (f"- Archive: {lact['archive_bytes']:,} bytes; SHA-256 "
         f"`{lact['archive_sha256']}`."),
        (f"- XML members: {lact['nxml_members']:,}; evidence records: "
         f"**{lact['records']:,}**; auxiliary/support members: "
         f"{lact['auxiliary_members']}."),
        (f"- Current HTML table-of-contents items: "
         f"{result['legal']['lactmed_collection'].get('toc_items', 'unmeasured')}; "
         "the upstream archive/HTML discrepancy is retained."),
        (f"- Evidence-record duplicate IDs "
         f"{lact['duplicate_record_ids']}; missing revision "
         f"{lact['missing_revision']}."),
        (f"- Rights variants: {lact['rights_variants']}; disclaimer variants: "
         f"{lact['disclaimer_variants']}."),
        (f"- Records with UNII: {lact['records_with_unii']:,}; with CAS: "
         f"{lact['records_with_cas']:,}."),
        f"- Identity buckets: `{_dump(lact['resolution'])}`.",
        f"- Section coverage: `{_dump(lact['section_coverage'])}`.",
        f"- Discovery signals: `{_dump(lact['signals'])}`.",
        (f"- MED-RT overlap — pregnancy {lact['pregnancy_overlap']}; lactation "
         f"{lact['lactation_overlap']}."),
        ("- Resolved moieties outside the direct MED-RT lactation floor: "
         f"**{lact['outside_lactation_floor']:,}**."),
        "",
        "Only `.nxml` members were parsed. Linked works were not downloaded.",
        "",
        "## AEMPS CIMA",
        "",
        (f"- API total {cima['reported_products']:,}; reconciled unique products "
         f"**{cima['unique_products']:,}** over {cima['pages']} pages."),
        (f"- Segmented SmPC metadata: "
         f"{cima['segmented_smpc_products']:,} products."),
        (f"- Distinct VTM IDs {cima['unique_vtm_ids']:,}; combinations "
         f"{cima['combination_products']:,}."),
        f"- Listing SHA-256: `{cima['listing_sha256']}`.",
        f"- Stratified product/section sample: {cima['sample_size']}.",
        f"- Section coverage: `{_dump(cima['sample_section_coverage'])}`.",
        (f"- No-sections API responses: "
         f"{cima['sample_section_error_responses']}."),
        ("- Ingredient name-resolution buckets: `"
         f"{_dump(cima['sample_ingredient_resolution'])}`."),
        f"- Discovery signals: `{_dump(cima['sample_signals'])}`.",
        (f"- Duplicate normalized sampled sections: "
         f"{cima['duplicate_sample_sections']}."),
        (f"- Changes since {cima['changes_since']}: "
         f"{cima['changes_reported']:,} reported; "
         f"{cima['changes_page_rows']:,} returned on the cached first page."),
        "",
        "CIMA names remain identity candidates. ATC and SNOMED were not read.",
        "",
        "## ANSM BDPM",
        "",
        (f"- Specialties {bdpm['specialties']:,}; composition rows "
         f"{bdpm['composition_rows']:,}; with composition "
         f"{bdpm['specialties_with_composition']:,}."),
        f"- Distinct ingredient names: {bdpm['distinct_ingredient_names']:,}.",
        ("- Ingredient name-resolution buckets: `"
         f"{_dump(bdpm['ingredient_name_resolution'])}`."),
        (f"- Bulk SHA-256: specialties `{bdpm['specialty_sha256']}`; "
         f"composition `{bdpm['composition_sha256']}`."),
        (f"- RCP sample {bdpm['sample_size']}; fetch failures "
         f"{bdpm['sample_fetch_failures']}."),
        f"- Section coverage: `{_dump(bdpm['sample_section_coverage'])}`.",
        f"- Revision coverage: `{_dump(bdpm['sample_revision_coverage'])}`.",
        f"- Discovery signals: `{_dump(bdpm['sample_signals'])}`.",
        (f"- Duplicate normalized sampled 4.6 sections: "
         f"{bdpm['duplicate_sample_sections']}."),
        "",
        "Official RCP responses are server-rendered with stable 4.3/4.6 anchors.",
        "Production retrieval still needs rate limits and change tests.",
        "",
        "## Clinician-readable review worklist",
        "",
        "The deterministic sample was chosen before inspecting clinical meaning.",
        "Cached inputs contain exact sections; this table contains identifiers only.",
        "",
        _table(review_rows,
               ["source", "record", "product", "ingredients", "sections"]),
        "",
        "**Pending human step:** a clinician must verify extraction boundaries,",
        "product scope, and whether normalization would be unsafe.",
        "",
        "## Licence evidence snapshots",
        "",
        _table(legal_rows, ["evidence", "bytes", "SHA-256", "live URL"]),
        "",
        "Hashes prove what this spike reviewed, not permanent licence approval.",
        "Production still requires signed rule-6 deeds and `NOTICE` entries.",
        "",
        "## Implementation boundary confirmed",
        "",
        "The sources have unlike grains: active-substance review, Spanish product",
        "section, and French product section. A production design must not collapse",
        "them into one moiety recommendation. The next round should design a",
        "rebuildable non-firing projection plus an open-question bridge.",
        "Clinician-signed promotion remains separate.",
        "",
    ]
    return "\n".join(lines)
