#!/usr/bin/env python3
"""Regenerate tests/fixtures/medrt_subset.xml from a real MED-RT release.

WHY THIS EXISTS: the slice-2a parser depends on subtle facts about MED-RT's
shape that documentation alone got wrong -- most importantly that a 'Parent Of'
association runs from the PARENT to the CHILD, and that the ubiquitous [HC]
concepts are alphabetical navigation bins ("A [Preparations]") rather than real
classifications. A hand-written fixture can only ever encode whatever the author
believed, so it would happily "pass" against an inverted DAG. Extracting the
fixture from the genuine release removes that whole class of error.

USAGE:
    python tests/fixtures/make_medrt_subset.py \
        /path/to/Core_MEDRT_<version>_XML.xml > tests/fixtures/medrt_subset.xml

The full release is ~45 MB and is NOT committed (see .gitignore); download it
from NCI EVS (https://evs.nci.nih.gov/ftp1/MED-RT/) when you need to regenerate.

WHAT IT SELECTS: four real drug ingredients chosen to exercise every acceptance
case -- three that our tests/fixtures/unii_subset.tsv registry carries and one it
deliberately does not -- plus every MED-RT class they reference, those classes'
ancestors, and a deliberate sprinkling of out-of-scope material (an HC bin, a
SNOMED endpoint, a MeSH has_SC, an overlay may_treat) that the parser must drop.
"""
import re
import sys
from xml.sax.saxutils import escape

# RxCUIs chosen for coverage. The first three appear in unii_subset.tsv; 5640
# (ibuprofen) deliberately does not, so it exercises the skipped-and-counted path.
INGREDIENTS = {
    "161": "paracetamol / has_MoA, has_PE, has_PK, has_TC; no EPC; sits under an HC bin",
    "17767": "amlodipine / carries TWO EPC parents plus a MeSH has_SC",
    "6853": "magnesium sulfate / HC bin only, must end up unclassified",
    "5640": "ibuprofen / NOT in our registry, so membership must be skipped and counted",
}

# Concept types we ingest as classes (HC and EXT are deliberately absent).
INGESTED_CTY = {"MoA", "PE", "TC", "PK", "EPC", "APC"}


def field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return m.group(1).strip() if m else ""


def main(path: str) -> None:
    data = open(path, encoding="utf-8").read()

    concepts = {}                                    # code -> (name, cty)
    for block in re.findall(r"<concept>(.*?)</concept>", data, re.S):
        if field(block, "namespace") != "MED-RT":
            continue
        props = dict(re.findall(
            r"<property>\s*<namespace>.*?</namespace>\s*<name>(.*?)</name>\s*<value>(.*?)</value>",
            block, re.S))
        cty = props.get("CTY", "").strip()
        if cty:
            concepts[field(block, "code")] = (field(block, "name"), cty)

    assocs = [
        {"name": field(b, "name"),
         "fns": field(b, "from_namespace"), "fnm": field(b, "from_name"), "fc": field(b, "from_code"),
         "tns": field(b, "to_namespace"), "tnm": field(b, "to_name"), "tc": field(b, "to_code")}
        for b in re.findall(r"<association>(.*?)</association>", data, re.S)
    ]

    # 1. Every association that touches one of our four ingredients.
    keep = [a for a in assocs
            if (a["fns"] == "RxNorm" and a["fc"] in INGREDIENTS)
            or (a["tns"] == "RxNorm" and a["tc"] in INGREDIENTS)]
    # Trim the noisiest overlay relations: keep just a couple as proof they're dropped.
    trimmed, seen_overlay = [], {}
    for a in keep:
        if a["name"] in ("may_treat", "may_prevent", "CI_with", "Synonym Of"):
            seen_overlay[a["name"]] = seen_overlay.get(a["name"], 0) + 1
            if seen_overlay[a["name"]] > 1:
                continue
        trimmed.append(a)
    keep = trimmed

    # 2. Every MED-RT class those associations reference, plus ancestors (2 levels),
    #    so the fixture contains a genuine multi-level, multi-parent DAG.
    wanted = {a["fc"] for a in keep if a["fns"] == "MED-RT"}
    wanted |= {a["tc"] for a in keep if a["tns"] == "MED-RT"}
    for _ in range(2):
        parents = {a["fc"] for a in assocs
                   if a["name"] == "Parent Of" and a["tns"] == "MED-RT"
                   and a["fns"] == "MED-RT" and a["tc"] in wanted}
        wanted |= parents

    # 3. Hierarchy edges among the selected classes (this is the class DAG).
    for a in assocs:
        if (a["name"] == "Parent Of" and a["fns"] == "MED-RT" and a["tns"] == "MED-RT"
                and a["fc"] in wanted and a["tc"] in wanted and a not in keep):
            keep.append(a)

    # 4. One SNOMED-endpoint edge, as proof the parser refuses to traverse it.
    for a in assocs:
        if a["name"] == "Parent Of" and a["fns"] == "SNOMED CT" and a["tc"] in wanted:
            keep.append(a)
            break

    emit_concepts = sorted(c for c in wanted if c in concepts)

    out = ['<?xml version="1.0" encoding="UTF-8" ?>',
           "<!-- EXTRACTED FROM A REAL MED-RT RELEASE by make_medrt_subset.py. Do not hand-edit.",
           "     Regenerate with:  python tests/fixtures/make_medrt_subset.py <Core_MEDRT_*.xml>",
           "     Ingredients covered:"]
    for rx, why in INGREDIENTS.items():
        out.append(f"       RxCUI {rx}: {why}")
    out += ["-->", "<terminology>",
            "\t<namespace>", "\t\t<name>MED-RT</name>",
            "\t\t<version>2026.07.06</version>", "\t</namespace>"]

    for code in emit_concepts:
        name, cty = concepts[code]
        out += ["\t<concept>", "\t\t<namespace>MED-RT</namespace>",
                f"\t\t<name>{escape(name)}</name>", f"\t\t<code>{code}</code>",
                "\t\t<status>A</status>",
                "\t\t<property>", "\t\t\t<namespace>MED-RT</namespace>",
                "\t\t\t<name>CTY</name>", f"\t\t\t<value>{cty}</value>", "\t\t</property>",
                "\t\t<property>", "\t\t\t<namespace>MED-RT</namespace>",
                "\t\t\t<name>NUI</name>", f"\t\t\t<value>{code}</value>", "\t\t</property>",
                "\t</concept>"]

    for a in keep:
        out += ["\t<association>", "\t\t<namespace>MED-RT</namespace>",
                f"\t\t<name>{escape(a['name'])}</name>",
                f"\t\t<from_namespace>{escape(a['fns'])}</from_namespace>",
                f"\t\t<from_name>{escape(a['fnm'])}</from_name>",
                f"\t\t<from_code>{escape(a['fc'])}</from_code>",
                f"\t\t<to_namespace>{escape(a['tns'])}</to_namespace>",
                f"\t\t<to_name>{escape(a['tnm'])}</to_name>",
                f"\t\t<to_code>{escape(a['tc'])}</to_code>",
                "\t</association>"]

    out.append("</terminology>")
    print("\n".join(out))

    counts = {}
    for a in keep:
        counts[a["name"]] = counts.get(a["name"], 0) + 1
    print(f"\n<!-- concepts={len(emit_concepts)} associations={len(keep)} {counts} -->",
          file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
