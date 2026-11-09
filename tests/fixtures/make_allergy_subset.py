#!/usr/bin/env python3
"""Regenerate the allergy-group MeSH fixtures from a real MeSH 2026 release.

EXTRACTED, NOT WRITTEN: every registry number, tree number, name, class and
HeadingMappedTo UI is copied from the genuine release (same reasoning as
make_mesh_subset.py, whose helpers this reuses). The ONLY synthetic thing is the
`<ConceptUI>` wrapper id (M+UI), which no parser reads.

WHAT IT KEEPS -- one small set that exercises every membership rule of the
penicillin allergy group (spec 2026-11-08 section 4.2):
  * the root (D010406) and its descendants (floxacillin, amoxicillin,
    penicillin G, ampicillin);
  * excluded descriptors (sulbactam, tazobactam, penicillanic acid);
  * descriptors OUTSIDE the tree (perindopril, cephalexin, procaine) and one
    in-tree-by-name-only decoy (D000077725), kept so placement_keys has work to do;
  * SCRs: temocillin (maps into the tree), sulbactam pivoxyl (maps ONLY to the
    excluded sulbactam), bevonium (maps outside the tree).

USAGE:  python tests/fixtures/make_allergy_subset.py <downloads_dir> [<out_dir>]
Do not hand-edit the output files.
"""
import pathlib
import sys
from xml.sax.saxutils import escape

import make_mesh_subset
from make_mesh_subset import _iter, _release_file, _texts, write_desc

KEEP_DESC = {"D010406", "D005436", "D000658", "D013407", "D000078142", "D010397",
             "D000077725", "D020913", "D002511", "D002506", "D011343",
             # Penicillin G and Ampicillin: the fold components of Task 4's
             # composition cases (penicillin g procaine, clemizole penicillin,
             # ampicillin sodium).
             "D010400", "D000667",
             # Amoxicillin-Potassium Clavulanate: in the Penicillins subtree, excluded by
             # the allow-list so a mixture key can never pull clavulanate in.
             "D019980"}
KEEP_SCR = {"C031367", "C035975", "C000002"}

_HEADER = ("<!-- EXTRACTED FROM A REAL MeSH 2026 RELEASE by make_allergy_subset.py. "
           "Do not hand-edit.\n"
           "     Regenerate: python tests/fixtures/make_allergy_subset.py DOWNLOADS_DIR\n"
           "     Registry/tree numbers, names, classes and HeadingMappedTo UIs are copied "
           "from the release;\n"
           "     only the ConceptUI wrapper id is synthetic (M+UI, never read).\n"
           "     MeSH is attributable (see NOTICE); nothing is redacted. -->")


# write_desc stamps make_mesh_subset's header; swap in ours so the file names its real generator.
make_mesh_subset._HEADER = _HEADER


def extract(dl: pathlib.Path):
    descriptors = {}
    for rec in _iter(_release_file(dl, "desc2026"), "DescriptorRecord"):
        ui = rec.findtext("DescriptorUI")
        if ui in KEEP_DESC:
            descriptors[ui] = {
                "name": rec.findtext("DescriptorName/String") or "",
                "cls": rec.get("DescriptorClass", "1"),
                "reg": _texts(rec, "RegistryNumber"),
                "related": _texts(rec, "RelatedRegistryNumber"),
                "trees": _texts(rec, "TreeNumber"),
            }
    scrs = {}
    for rec in _iter(_release_file(dl, "supp2026"), "SupplementalRecord"):
        ui = rec.findtext("SupplementalRecordUI")
        if ui in KEEP_SCR:
            scrs[ui] = {
                "name": rec.findtext("SupplementalRecordName/String") or "",
                "cls": rec.get("SCRClass", "1"),
                "reg": _texts(rec, "RegistryNumber"),
                # Copied verbatim, leading "*" included (the parser strips it).
                "mapped": _texts(rec, "DescriptorUI"),
            }
    missing = (KEEP_DESC - set(descriptors)) | (KEEP_SCR - set(scrs))
    if missing:
        raise SystemExit(f"make_allergy_subset.py: not in release: {sorted(missing)}")
    return descriptors, scrs


def write_supp(scrs, out: pathlib.Path):
    lines = ['<?xml version="1.0"?>', _HEADER, '<SupplementalRecordSet LanguageCode="eng">']
    for ui in sorted(scrs):
        s = scrs[ui]
        lines += [f' <SupplementalRecord SCRClass="{escape(s["cls"])}">',
                  f'  <SupplementalRecordUI>{ui}</SupplementalRecordUI>',
                  '  <SupplementalRecordName>', f'   <String>{escape(s["name"])}</String>',
                  '  </SupplementalRecordName>',
                  '  <HeadingMappedToList>']
        for m in s["mapped"]:
            lines += ['   <HeadingMappedTo>', '    <DescriptorReferredTo>',
                      f'     <DescriptorUI>{escape(m)}</DescriptorUI>',
                      '    </DescriptorReferredTo>', '   </HeadingMappedTo>']
        lines += ['  </HeadingMappedToList>',
                  '  <ConceptList>', '   <Concept PreferredConceptYN="Y">',
                  f'    <ConceptUI>M{ui}</ConceptUI>',
                  '    <RegistryNumberList>']
        lines += [f'     <RegistryNumber>{escape(r)}</RegistryNumber>' for r in s["reg"]]
        lines += ['    </RegistryNumberList>', '   </Concept>', '  </ConceptList>',
                  ' </SupplementalRecord>']
    lines.append('</SupplementalRecordSet>')
    out.write_text("\n".join(lines) + "\n")


def main(dl: str, out_dir: str | None) -> None:
    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(__file__).parent
    descriptors, scrs = extract(pathlib.Path(dl))
    write_desc(descriptors, out / "allergy_desc_subset.xml")
    write_supp(scrs, out / "allergy_supp_subset.xml")
    print(f"descriptors={len(descriptors)} SCRs={len(scrs)}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
