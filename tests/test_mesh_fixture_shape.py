# tests/test_mesh_fixture_shape.py
"""Shape-pin for the committed slice-2b MeSH fixtures -- no parser, no DB.

The slice-2b MeSH parser is not built yet, so the three committed
`mesh_*_subset.xml` fixtures would otherwise ship with ZERO test coverage and
could silently drift (a hand-edit, or a regeneration against a revised MeSH
release) with nothing to catch it. This module reads the fixtures with the
stdlib and pins every fact the future parser will be TDD'd against, so any drift
in the fixtures fails here first.

Every value asserted below was extracted from the real MeSH 2026 release by
tests/fixtures/make_mesh_subset.py (see the design spec §5), never invented --
these assertions restate, in code, the acceptance cases spec §7 lists.
"""
import pathlib
import re
from xml.etree import ElementTree as ET

FIX = pathlib.Path(__file__).parent / "fixtures"
DESC = FIX / "mesh_desc_subset.xml"
SUPP = FIX / "mesh_supp_subset.xml"
PA = FIX / "mesh_pa_subset.xml"

# Registry-number typing, identical to spec §5.2 / the parser's future rule:
# UNII = 10 upper-alphanumerics; CAS = n-nn-n. A RelatedRegistryNumber may be
# annotated "<cas> (<name>)", so split off any parenthetical before matching.
UNII = re.compile(r"^[0-9A-Z]{10}$")
CAS = re.compile(r"^[0-9]{1,7}-[0-9]{2}-[0-9]$")


def _uniis(values):
    return {v for v in values if UNII.match(v)}


def _cas(values):
    return {v.split(" ", 1)[0] for v in values if CAS.match(v.split(" ", 1)[0])}


def _load_desc():
    out = {}
    for rec in ET.parse(DESC).getroot().findall("DescriptorRecord"):
        ui = rec.findtext("DescriptorUI")
        out[ui] = {
            "cls": rec.get("DescriptorClass"),
            "name": rec.findtext("DescriptorName/String"),
            "reg": [e.text for e in rec.iter("RegistryNumber") if e.text],
            "related": [e.text for e in rec.iter("RelatedRegistryNumber") if e.text],
            "trees": [e.text for e in rec.iter("TreeNumber") if e.text],
        }
    return out


def _load_supp():
    out = {}
    for rec in ET.parse(SUPP).getroot().findall("SupplementalRecord"):
        ui = rec.findtext("SupplementalRecordUI")
        out[ui] = {
            "cls": rec.get("SCRClass"),
            "name": rec.findtext("SupplementalRecordName/String"),
            "reg": [e.text for e in rec.iter("RegistryNumber") if e.text],
            "related": [e.text for e in rec.iter("RelatedRegistryNumber") if e.text],
        }
    return out


def _load_pa():
    """{PA-class descriptor UI -> {member RecordUI: member name}}."""
    out = {}
    for pa in ET.parse(PA).getroot().findall("PharmacologicalAction"):
        dui = pa.findtext("DescriptorReferredTo/DescriptorUI")
        out[dui] = {s.findtext("RecordUI"): s.findtext("RecordName/String")
                    for s in pa.iter("Substance")}
    return out


PA_CLASSES = {"D000700", "D000893", "D000894", "D012102", "D018501", "D018712"}


# ---- the class side of the axis --------------------------------------------


def test_pa_classes_are_exactly_the_six_descriptors():
    """The PA file's class side: six D-prefixed descriptors, no SCR ever a class."""
    classes = set(_load_pa())
    assert classes == PA_CLASSES
    assert all(c.startswith("D") for c in classes)


def test_pa_class_descriptors_carry_no_registry_number():
    """Abstract action classes have no identity key -- only members do (§5.2)."""
    desc = _load_desc()
    for c in PA_CLASSES:
        assert _uniis(desc[c]["reg"]) == set()
        assert _cas(desc[c]["reg"]) == set()


# ---- membership edges ------------------------------------------------------


def test_membership_edges_match_the_release():
    pa = _load_pa()
    assert set(pa["D000894"]) == {"C000002", "C007609", "D001241"}
    assert set(pa["D018712"]) == {"D000082", "C000002", "C007609", "D001241"}
    assert set(pa["D000700"]) == {"D008278", "C000002", "C007609", "D000082", "D001241"}
    assert set(pa["D012102"]) == {"D008278"}


# ---- the identity keys per member (the whole point of measuring first) -----


def test_positive_unii_join_member_carries_a_unii():
    """D000082 Acetaminophen -- UNII 362O9ITL9D, a slice-1 seed -> positive UNII join."""
    d = _load_desc()["D000082"]
    assert "362O9ITL9D" in _uniis(d["reg"])


def test_cas_fallback_member_carries_cas_but_no_unii():
    """D008278 Magnesium Sulfate -- no UNII in MeSH, CAS 7487-88-9 -> CAS fallback join.
    This is the case that makes the bridge two-key rather than UNII-only."""
    d = _load_desc()["D008278"]
    assert _uniis(d["reg"]) == set()
    assert "7487-88-9" in _cas(d["reg"])


def test_aspirin_carries_unii_in_registry_and_cas_in_related():
    """The fact issue #11 got wrong: a Descriptor DOES carry a UNII in
    <RegistryNumber>, with the CAS displaced to <RelatedRegistryNumber>."""
    d = _load_desc()["D001241"]
    assert "R16CO5Y76E" in _uniis(d["reg"])          # UNII in RegistryNumber
    assert "R16CO5Y76E" not in _uniis(d["related"])  # not in Related
    assert "50-78-2" in _cas(d["related"])           # CAS in RelatedRegistryNumber


def test_a_member_can_carry_multiple_uniis():
    """SCR C000002 bevonium exposes TWO UNIIs across its concepts. Key extraction
    is set-valued, so the bridge must try every UNII, not 'the' UNII (spec §5.2)."""
    s = _load_supp()["C000002"]
    assert {"34B0471E08", "UWC15E373Z"} <= _uniis(s["reg"])


def test_no_key_member_carries_neither_unii_nor_cas():
    """SCR C007609 aspirin/meprobamate combination -- RegistryNumber '0' only.
    Structurally unjoinable: it must be counted, never silently dropped (§5.3)."""
    s = _load_supp()["C007609"]
    assert _uniis(s["reg"]) == set()
    assert _cas(s["reg"]) == set()


# ---- the class DAG derived from tree-number nesting ------------------------


def _dag_parents():
    """Child->parents edges by the spec §5.4 rule: a PA class' tree number whose
    immediate parent tree number (drop the trailing .NNN) belongs to another PA
    class yields an edge; a parent tree number that is not a PA class is dropped."""
    desc = _load_desc()
    tree_to_class = {t: ui for ui in PA_CLASSES for t in desc[ui]["trees"]}
    parents = {ui: set() for ui in PA_CLASSES}
    for ui in PA_CLASSES:
        for t in desc[ui]["trees"]:
            parent_tree = t.rsplit(".", 1)[0] if "." in t else None
            owner = tree_to_class.get(parent_tree)
            if owner and owner != ui:
                parents[ui].add(owner)
    return parents


def test_dag_has_a_multi_parent_node():
    """D000894 (NSAIDs) nests under THREE kept PA classes -- a real DAG node a tree
    could not express. Getting tree nesting wrong silently mis-shapes the hierarchy."""
    assert _dag_parents()["D000894"] == {"D000893", "D018501", "D018712"}


def test_dag_has_a_two_level_chain():
    """D000700 -> D018712 -> D000894: the fixture proves nesting past one level."""
    assert _dag_parents()["D018712"] == {"D000700"}


def test_dag_roots_have_no_in_subset_parent():
    """A tree-number parent that is not a kept PA class drops the edge (§5.4), so
    these four attach nowhere within the subset -- they are roots, not orphans."""
    parents = _dag_parents()
    for root in ("D000700", "D000893", "D018501", "D012102"):
        assert parents[root] == set(), f"{root} should be a root in the subset"


# ---- provenance / faithfulness guards --------------------------------------


def test_record_classes_are_copied_from_the_release():
    """DescriptorClass / SCRClass are copied, not hardcoded (generator fix). These
    records are all class-1; a regeneration that finds otherwise fails here."""
    assert all(d["cls"] == "1" for d in _load_desc().values())
    assert all(s["cls"] == "1" for s in _load_supp().values())


def test_member_names_are_the_real_mesh_names():
    """Member names in the PA file are copied from the release, not the generator's
    curated labels -- pin them so a curated-label drift can't sneak in."""
    pa = _load_pa()
    assert pa["D000700"]["D008278"] == "Magnesium Sulfate"
    assert pa["D000700"]["D000082"] == "Acetaminophen"
    assert pa["D000894"]["C000002"] == "bevonium"
    assert pa["D000894"]["C007609"] == "aspirin, meprobamate drug combination"


def test_fixtures_are_single_source_mesh_only():
    """MeSH is attributable so nothing is redacted, but the files must stay MeSH-only
    (no SNOMED or other unlicensed namespace), matching NOTICE and spec §1."""
    for f in (DESC, SUPP, PA):
        text = f.read_text(encoding="utf-8")
        assert "SNOMED" not in text
        assert "EXTRACTED FROM A REAL MeSH 2026 RELEASE" in text
