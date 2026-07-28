"""Extract a MeSH desc/supp subset covering slice 5b's contraindication objects.

Run:
    uv run python tests/fixtures/make_mesh_ci_subset.py \
        downloads/mesh/desc2026.gz downloads/mesh/supp2026.gz tests/fixtures/

WHY A SEPARATE FIXTURE FROM slice 2b's mesh_desc_subset.xml: 2b's fixture is scoped
to the PHARMACOLOGICAL ACTION axis, and 5b's objects are diseases, physiological
states and procedures -- disjoint records. Extending 2b's file would grow it for a
purpose its own tests do not share; a separate file keeps each fixture legible.

EXTRACTED FROM THE REAL RELEASE, NEVER HAND-WRITTEN. This is the standing rule since
issue #27, where the last hand-written fixture concealed a wrong column name that
would have shipped an entirely unlabelled registry. A fixture invented by hand can
only ever confirm what its author already believed.
"""
import gzip
import pathlib
import sys
from xml.etree import ElementTree as ET

# Records the 5b tests need, chosen to exercise every branch of the resolver:
#   D004827 Epilepsy               -- the worked example; has descendants
#   D004829 Epilepsy, Generalized  -- a DESCENDANT of Epilepsy (closure test)
#   D011247 Pregnancy              -- the G-branch case the table is named for
#   D001026 Coronary Artery Bypass -- an E-branch procedure
#   D010868 Pimozide               -- a CI_ChemClass object that IS a substance
#     (NOTE: the plan this was drawn from named D010860 for Pimozide; the real
#     2026 release has D010860 = "Pigments, Biological" and D010868 = Pimozide.
#     Verified directly against the release rather than trusted from the plan --
#     exactly the class of error this extraction rule exists to prevent.)
#   D013449 Sulfonamides           -- the class-arm object (must NOT be ingested)
WANT_DESC = ["D004827", "D004829", "D011247", "D001026", "D010868", "D013449"]
#   C536778 -- an SCR, to exercise the supplementary-record fallback
WANT_SUPP = ["C536778"]


def extract(path, tag, ui_tag, wanted, out_path, root_tag):
    """Copy whole records verbatim -- never a reconstruction, so the fixture cannot
    disagree with upstream about element names or nesting."""
    kept = []
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as fh:
        for _event, el in ET.iterparse(fh, events=("end",)):
            if el.tag != tag:
                continue
            if (el.findtext(ui_tag) or "") in wanted:
                kept.append(ET.tostring(el, encoding="unicode"))
            el.clear()
    out_path.write_text(
        f"<{root_tag}>\n" + "\n".join(kept) + f"\n</{root_tag}>\n", encoding="utf-8")
    print(f"{out_path}: {len(kept)}/{len(wanted)} records")


if __name__ == "__main__":
    desc, supp, outdir = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
    extract(desc, "DescriptorRecord", "DescriptorUI", set(WANT_DESC),
            outdir / "mesh_ci_desc_subset.xml", "DescriptorRecordSet")
    extract(supp, "SupplementalRecord", "SupplementalRecordUI", set(WANT_SUPP),
            outdir / "mesh_ci_supp_subset.xml", "SupplementalRecordSet")
