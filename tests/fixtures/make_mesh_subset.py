#!/usr/bin/env python3
"""Regenerate the slice-2b MeSH test fixtures from a real MeSH release.

WHY THIS EXISTS (same reason as make_medrt_subset.py): slice 2b depends on subtle
facts about MeSH's shape that the documentation research in issue #11 got wrong --
most importantly that a MeSH **Descriptor** carries its substance's **UNII** in the
`<RegistryNumber>` field (issue #11 believed Descriptors held only CAS; the real
2026 release shows aspirin D001241 with UNII R16CO5Y76E), and that the PA class
**hierarchy** is encoded in **tree-number nesting**, not an explicit parent link.
A hand-written fixture can only encode whatever the author believed, so it would
"pass" against those wrong assumptions. Extracting from the genuine release, with
every VALUE (registry numbers, tree numbers, PA memberships, names, and the
DescriptorClass/SCRClass of each record) copied from the file, removes that whole
class of error -- no identity key, name, relationship, or record class is invented.
The ONLY synthetic thing is the `<ConceptUI>` wrapper id (emitted as `M`+UI): the
parser never reads it (it reaches the RegistryNumbers nested under a Concept), so
its exact value is not a fact under test -- everything that IS under test is copied.
The record structure is otherwise minimised to the elements the parser walks.

USAGE:
    python tests/fixtures/make_mesh_subset.py <downloads_dir> [<out_dir>]

    <downloads_dir> must hold the three real release files, EXACTLY AS NLM SERVES
    THEM (NOT committed -- see .gitignore): pa2026.xml plus the gzipped
    desc2026.gz / supp2026.gz, from
    https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/. No gunzip step
    -- reading the compressed files is the whole point of issue #40; a plain
    `.xml` beside them is also accepted. <out_dir> defaults to the directory this
    script lives in.

WHAT IT SELECTS -- an aspirin-centred cluster chosen so ONE small connected subset
exercises every slice-2b acceptance case (see the two curated dicts below). The PA
classes were chosen because D000894 (Non-Steroidal Anti-Inflammatory Agents) is a
genuine MULTI-PARENT node in the tree-number DAG, and three of the members are the
substances the slice-1 seed already carries (so the membership join has a real
moiety to hit -- by UNII for paracetamol, and by CAS for magnesium sulfate, which
carries no UNII in MeSH).

LICENCE: unlike make_medrt_subset.py, this fixture does NOT redact anything. MeSH
is attributable (NLM terms: "Courtesy of the U.S. National Library of Medicine"),
and these three files are single-source -- no SNOMED CT or other unlicensed
namespace appears in them at all. The `NOTICE` MeSH entry must be in place before
the fixture is committed.
"""
import gzip
import pathlib
import sys
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

# --- Curated PA CLASSES to keep (the class side of the axis). --------------
# D000894 is the payload: it is a real multi-parent DAG node, and its three
# tree-number parents (D000893 / D018712 / D018501) are the other members here,
# so the fixture proves both a multi-parent child AND parents that are roots
# within the subset (their own tree parents are not kept -> no edge = dropped).
# D000700 and D012102 give the seed substances (paracetamol / magnesium sulfate)
# a class to belong to, and D000700->D018712 adds a second hierarchy level.
KEEP_PA_CLASSES = {
    "D000894": "Anti-Inflammatory Agents, Non-Steroidal (MULTI-PARENT DAG node; aspirin's class)",
    "D000893": "Anti-Inflammatory Agents (a tree parent of D000894; a root in the subset)",
    "D018712": "Analgesics, Non-Narcotic (tree parent of D000894; child of D000700)",
    "D018501": "Antirheumatic Agents (a tree parent of D000894; a root in the subset)",
    "D000700": "Analgesics (parent of D018712; membership class for paracetamol & magnesium sulfate)",
    "D012102": "Reproductive Control Agents (membership class for magnesium sulfate; a root)",
}

# --- Curated MEMBER substances to keep (the moiety side). ------------------
# Each maps to the slice-2b case it exercises. The keys are copied from the real
# records at extraction time, never asserted here.
KEEP_MEMBERS = {
    "D000082": "Acetaminophen -- Descriptor, UNII 362O9ITL9D IS a slice-1 seed -> POSITIVE UNII join",
    "D008278": "Magnesium Sulfate -- Descriptor, NO UNII, CAS 7487-88-9 IS a seed -> POSITIVE CAS-fallback join",
    "D001241": "Aspirin -- Descriptor, UNII-in-RegistryNumber + CAS-in-Related; not seeded -> key-not-in-registry",
    "C000002": "bevonium -- SCR carrying a UNII (not seeded) -> exercises SCR RegistryNumber extraction",
    "C007609": "aspirin, meprobamate drug combination -- SCR with NEITHER key -> no-key, counted never dropped",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# The names one release file can arrive under. NLM serves pa2026 plain but desc2026
# and supp2026 gzipped, and the gzipped ones are named "<stem>.gz" -- NOT
# "<stem>.xml.gz". The third form is here for an operator who renamed them.
_RELEASE_SUFFIXES = (".xml", ".gz", ".xml.gz")


def _release_file(dl: pathlib.Path, stem: str) -> pathlib.Path:
    """Locate one release file in the downloads directory, whatever NLM named it.

    This script used to hardcode "<stem>.xml", so the regeneration command drugref
    documents found NOTHING when pointed at a directory holding a genuine release
    (#40). Raising rather than returning None is deliberate and matches
    make_mesh_ci_subset.py's posture: a fixture written from an unread file would
    silently delete test cases, which is worse than not regenerating at all.
    """
    for suffix in _RELEASE_SUFFIXES:
        candidate = dl / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    raise SystemExit(
        f"make_mesh_subset.py: no {stem} release file in {dl} -- looked for "
        + ", ".join(f"{stem}{s}" for s in _RELEASE_SUFFIXES))


def _open(path: pathlib.Path):
    """Open a release file, transparently handling the .gz NLM publishes.

    Deliberately a copy of drugref.ingest.mesh.open_release_file rather than an
    import: every fixture extractor in this directory is stdlib-only and runnable
    without drugref installed, and one of them (make_medrt_subset.py) is checked by
    a test that must stay INDEPENDENT of the code it feeds. Two lines is the price
    of that independence; the production copies were collapsed to one (#40).
    """
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def _iter(path: pathlib.Path, record_tag: str):
    """Stream top-level records, detaching each from the root to bound memory
    (supp2026 is 750 MB uncompressed)."""
    with _open(path) as fh:
        context = ET.iterparse(fh, events=("start", "end"))
        _, root = next(context)
        for event, elem in context:
            if event == "end" and _local(elem.tag) == record_tag:
                yield elem
                elem.clear()
                root.clear()


def _texts(record, tag: str) -> list[str]:
    """Every distinct text of `tag` under a record, order-preserved.

    De-duplicated because a real record repeats the placeholder "0" (no registry
    number) on each of its several concepts; keeping one is faithful to the shape
    (the parser aggregates unique numbers across concepts anyway) while dropping
    the dozen redundant "0"s that would otherwise bloat the fixture. Every genuine
    key survives -- only exact repeats are collapsed."""
    seen, out = set(), []
    for e in record.iter():
        if _local(e.tag) == tag and e.text and e.text.strip():
            v = e.text.strip()
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _members(pa_block) -> list[tuple[str, str]]:
    """Each Substance under a PA block as (record_ui, record_name), both copied
    from the release -- the member name is taken from the file, never invented,
    so the fixture cannot drift from the real RecordName on regeneration."""
    out = []
    for sub in pa_block.iter():
        if _local(sub.tag) != "Substance":
            continue
        ui = next((e.text for e in sub.iter()
                   if _local(e.tag) == "RecordUI" and e.text), None)
        name = next((e.text for e in sub.iter()
                     if _local(e.tag) == "String" and e.text), "")
        if ui:
            out.append((ui, name))
    return out


# ----- extraction --------------------------------------------------------
def extract(dl: pathlib.Path):
    pa_path = _release_file(dl, "pa2026")
    supp_path = _release_file(dl, "supp2026")
    desc_path = _release_file(dl, "desc2026")

    # 1. PA membership: keep each kept class' block, substances trimmed to kept
    #    members. Member UI *and* name are copied from the file (never invented).
    pa_blocks = []          # (descriptor_ui, name, [(member_ui, member_name),...])
    for pa in _iter(pa_path, "PharmacologicalAction"):
        dui = pa.findtext(".//DescriptorReferredTo/DescriptorUI")
        if dui not in KEEP_PA_CLASSES:
            continue
        name = pa.findtext(".//DescriptorReferredTo/DescriptorName/String") or ""
        members = [(ui, nm) for ui, nm in _members(pa) if ui in KEEP_MEMBERS]
        pa_blocks.append((dui, name, members))

    # 2. Descriptor records (kept classes + descriptor-type members): copy the
    #    REAL registry numbers, tree numbers and DescriptorClass -- the shape
    #    facts under test (DescriptorClass is a real filtering axis, not scaffolding).
    want_desc = set(KEEP_PA_CLASSES) | {m for m in KEEP_MEMBERS if m.startswith("D")}
    descriptors = {}        # ui -> dict(name, cls, reg, related, trees)
    for rec in _iter(desc_path, "DescriptorRecord"):
        ui = rec.findtext("DescriptorUI")
        if ui not in want_desc:
            continue
        descriptors[ui] = {
            "name": rec.findtext("DescriptorName/String") or "",
            "cls": rec.get("DescriptorClass", "1"),
            "reg": _texts(rec, "RegistryNumber"),
            "related": _texts(rec, "RelatedRegistryNumber"),
            "trees": _texts(rec, "TreeNumber"),
        }

    # 3. Supplemental records (SCR-type members): copy their real registry numbers
    #    and SCRClass (1=chemical / 2=protocol / 3=disease -- a real axis §5.1).
    want_supp = {m for m in KEEP_MEMBERS if m.startswith("C")}
    scrs = {}               # ui -> dict(name, cls, reg, related)
    for rec in _iter(supp_path, "SupplementalRecord"):
        ui = rec.findtext("SupplementalRecordUI")
        if ui not in want_supp:
            continue
        scrs[ui] = {
            "name": rec.findtext("SupplementalRecordName/String") or "",
            "cls": rec.get("SCRClass", "1"),
            "reg": _texts(rec, "RegistryNumber"),
            "related": _texts(rec, "RelatedRegistryNumber"),
        }
    return pa_blocks, descriptors, scrs


# ----- emission ----------------------------------------------------------
def _reg_block(reg: list[str], related: list[str], indent: str) -> list[str]:
    """A minimal <RegistryNumberList>/<RelatedRegistryNumberList> pair, holding the
    REAL numbers. Emitting them under their own tags is what preserves the shape
    fact under test: the UNII really did come from a <RegistryNumber> slot."""
    out = []
    if reg:
        out.append(f"{indent}<RegistryNumberList>")
        out += [f"{indent} <RegistryNumber>{escape(r)}</RegistryNumber>" for r in reg]
        out.append(f"{indent}</RegistryNumberList>")
    if related:
        out.append(f"{indent}<RelatedRegistryNumberList>")
        out += [f"{indent} <RelatedRegistryNumber>{escape(r)}</RelatedRegistryNumber>"
                for r in related]
        out.append(f"{indent}</RelatedRegistryNumberList>")
    return out


# NB: an XML comment may not contain a double hyphen, so this text uses none.
_HEADER = "<!-- EXTRACTED FROM A REAL MeSH 2026 RELEASE by make_mesh_subset.py. Do not hand-edit.\n" \
          "     Regenerate: python tests/fixtures/make_mesh_subset.py DOWNLOADS_DIR\n" \
          "     Identity keys, registry/tree numbers, names and record classes are copied from the\n" \
          "     release, never invented; only the ConceptUI wrapper id is synthetic (M+UI, never read).\n" \
          "     MeSH is attributable (see NOTICE); nothing is redacted; these files are single source. -->"


def write_desc(descriptors, out: pathlib.Path):
    lines = ['<?xml version="1.0"?>', _HEADER, '<DescriptorRecordSet LanguageCode="eng">']
    for ui in sorted(descriptors):
        d = descriptors[ui]
        lines += [f' <DescriptorRecord DescriptorClass="{escape(d["cls"])}">',
                  f'  <DescriptorUI>{ui}</DescriptorUI>',
                  '  <DescriptorName>', f'   <String>{escape(d["name"])}</String>',
                  '  </DescriptorName>']
        if d["trees"]:
            lines.append('  <TreeNumberList>')
            lines += [f'   <TreeNumber>{escape(t)}</TreeNumber>' for t in d["trees"]]
            lines.append('  </TreeNumberList>')
        # ConceptUI is synthetic (M+UI): the parser reaches the RegistryNumbers
        # under a Concept, never the ConceptUI itself, so its value is not tested.
        lines += ['  <ConceptList>', '   <Concept PreferredConceptYN="Y">',
                  f'    <ConceptUI>M{ui}</ConceptUI>',
                  '    <ConceptName>', f'     <String>{escape(d["name"])}</String>',
                  '    </ConceptName>']
        lines += _reg_block(d["reg"], d["related"], "    ")
        lines += ['   </Concept>', '  </ConceptList>', ' </DescriptorRecord>']
    lines.append('</DescriptorRecordSet>')
    out.write_text("\n".join(lines) + "\n")


def write_supp(scrs, out: pathlib.Path):
    lines = ['<?xml version="1.0"?>', _HEADER, '<SupplementalRecordSet LanguageCode="eng">']
    for ui in sorted(scrs):
        s = scrs[ui]
        lines += [f' <SupplementalRecord SCRClass="{escape(s["cls"])}">',
                  f'  <SupplementalRecordUI>{ui}</SupplementalRecordUI>',
                  '  <SupplementalRecordName>', f'   <String>{escape(s["name"])}</String>',
                  '  </SupplementalRecordName>',
                  '  <ConceptList>', '   <Concept PreferredConceptYN="Y">',
                  f'    <ConceptUI>M{ui}</ConceptUI>',
                  '    <ConceptName>', f'     <String>{escape(s["name"])}</String>',
                  '    </ConceptName>']
        lines += _reg_block(s["reg"], s["related"], "    ")
        lines += ['   </Concept>', '  </ConceptList>', ' </SupplementalRecord>']
    lines.append('</SupplementalRecordSet>')
    out.write_text("\n".join(lines) + "\n")


def write_pa(pa_blocks, out: pathlib.Path):
    lines = ['<?xml version="1.0"?>', _HEADER, '<PharmacologicalActionSet>']
    for dui, name, members in sorted(pa_blocks):
        lines += [' <PharmacologicalAction>', '  <DescriptorReferredTo>',
                  f'   <DescriptorUI>{dui}</DescriptorUI>',
                  '   <DescriptorName>', f'    <String>{escape(name)}</String>',
                  '   </DescriptorName>', '  </DescriptorReferredTo>',
                  '  <PharmacologicalActionSubstanceList>']
        for m, mname in members:
            lines += ['   <Substance>', f'    <RecordUI>{m}</RecordUI>',
                      '    <RecordName>', f'     <String>{escape(mname)}</String>',
                      '    </RecordName>', '   </Substance>']
        lines += ['  </PharmacologicalActionSubstanceList>', ' </PharmacologicalAction>']
    lines.append('</PharmacologicalActionSet>')
    out.write_text("\n".join(lines) + "\n")


def main(dl: str, out_dir: str | None) -> None:
    dl_path = pathlib.Path(dl)
    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(__file__).parent
    pa_blocks, descriptors, scrs = extract(dl_path)

    write_desc(descriptors, out / "mesh_desc_subset.xml")
    write_supp(scrs, out / "mesh_supp_subset.xml")
    write_pa(pa_blocks, out / "mesh_pa_subset.xml")

    # Self-report to stderr (like make_medrt_subset.py) so a regeneration surfaces
    # any drift in what the release contains for these curated records.
    print(f"<!-- PA blocks={len(pa_blocks)} descriptors={len(descriptors)} SCRs={len(scrs)}",
          file=sys.stderr)
    for ui in sorted(descriptors):
        d = descriptors[ui]
        print(f"     {ui} {d['name']!r} reg={d['reg']} related={d['related']} "
              f"trees={len(d['trees'])}", file=sys.stderr)
    for ui in sorted(scrs):
        s = scrs[ui]
        print(f"     {ui} {s['name']!r} reg={s['reg']} related={s['related']}", file=sys.stderr)
    print("-->", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
