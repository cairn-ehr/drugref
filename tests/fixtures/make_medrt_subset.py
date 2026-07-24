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

WHAT IT REDACTS, AND WHY THAT IS A LICENCE RULE AND NOT TIDINESS: those
out-of-scope edges name their far endpoint, and for the SNOMED one that endpoint
is a SNOMED CT concept id and its fully specified name. The parser refuses such
an edge, so nothing unlicensed reaches the database -- but a fixture is a file in
an AGPL-licensed repository, so committing the term verbatim would redistribute
it regardless of what the parser does, and would falsify the claim NOTICE makes.
Every endpoint outside REDISTRIBUTABLE_NAMESPACES therefore has its term and code
replaced before the fixture is written. The edges stay, and stay exactly as
discriminating: the parser rejects them on their NAMESPACE, which is preserved.
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

# The only namespaces whose terms this repository may redistribute: MED-RT (VA,
# public domain) and RxNorm (NLM, public domain), both attributed in NOTICE. An
# endpoint in any other namespace -- SNOMED CT, which we are not licensed to
# redistribute at all, and MeSH, which is not attributed until slice 2b -- is
# emitted with its term and code redacted. See the module docstring.
REDISTRIBUTABLE_NAMESPACES = {"MED-RT", "RxNorm"}
REDACTED = "REDACTED"


def field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return m.group(1).strip() if m else ""


def endpoint(namespace: str, name: str, code: str) -> tuple[str, str]:
    """Return the (name, code) to emit for one association endpoint.

    Unchanged for namespaces we may redistribute; redacted for every other, so
    the fixture carries the SHAPE of an out-of-scope edge without its content.
    """
    if namespace in REDISTRIBUTABLE_NAMESPACES:
        return name, code
    return REDACTED, REDACTED


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
    # CI_MoA / CI_PE are deliberately NOT trimmed -- they are slice-5a's drug-drug
    # contraindications, extracted into class_contraindication, and the fixture must
    # keep exercising them (amlodipine's real CI_PE -> N0000178477 is the edge the
    # release provides for these ingredients). CI_with stays trimmed: it is MeSH-keyed
    # (slice 5b) and its endpoint is redacted here anyway.
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
           "     Association endpoints outside MED-RT/RxNorm carry REDACTED in place of their",
           "     term and code: this repository may not redistribute SNOMED CT content, and",
           "     MeSH is not attributed until slice 2b. The namespace is kept, which is what",
           "     the parser rejects the edge on, so the fixture loses no discriminating power.",
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
        from_name, from_code = endpoint(a["fns"], a["fnm"], a["fc"])
        to_name, to_code = endpoint(a["tns"], a["tnm"], a["tc"])
        out += ["\t<association>", "\t\t<namespace>MED-RT</namespace>",
                f"\t\t<name>{escape(a['name'])}</name>",
                f"\t\t<from_namespace>{escape(a['fns'])}</from_namespace>",
                f"\t\t<from_name>{escape(from_name)}</from_name>",
                f"\t\t<from_code>{escape(from_code)}</from_code>",
                f"\t\t<to_namespace>{escape(a['tns'])}</to_namespace>",
                f"\t\t<to_name>{escape(to_name)}</to_name>",
                f"\t\t<to_code>{escape(to_code)}</to_code>",
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
