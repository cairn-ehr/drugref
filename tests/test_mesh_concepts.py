"""The M-code resolver (slice 5b): MED-RT's MeSH to_code is a MeSH ConceptUI."""
import pathlib

from drugref.ingest import mesh_concepts

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DESC = FIXTURES / "mesh_ci_desc_subset.xml"
SUPP = FIXTURES / "mesh_ci_supp_subset.xml"

# The preferred ConceptUI of D004827 (Epilepsy), read from the real release.
EPILEPSY_CONCEPT = "M0007564"


def test_resolves_a_concept_to_its_descriptor():
    """THE CENTRAL FACT of this slice: MED-RT points at a MeSH ConceptUI, and the
    record that owns it is the condition. Established from the release, not the
    docs -- the documentation route (the NDF-RT crosswalk) resolves 85% and yields
    only a name."""
    got = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    rec = got[EPILEPSY_CONCEPT]
    assert rec.record_ui == "D004827"
    assert rec.record_kind == "DESCRIPTOR"
    assert rec.name == "Epilepsy"
    assert rec.is_preferred_concept is True
    assert any(t.startswith("C10.") for t in rec.tree_numbers)


def test_unwanted_concepts_are_not_returned():
    """The resolver is scoped: streaming the full release must retain only what was
    asked for, or peak memory grows with MeSH rather than with the query."""
    got = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    assert set(got) == {EPILEPSY_CONCEPT}


def test_unresolvable_code_is_absent_not_invented():
    """A withdrawn M-code must be MISSING from the result so the caller can count
    it, not silently mapped to something plausible."""
    assert mesh_concepts.resolve_concepts(DESC, SUPP, {"M9999999"}) == {}


def test_resolves_a_supplementary_record():
    """86 of the release's stragglers live in supp2026, so the SCR fallback is
    load-bearing: without it resolution stops at 96.4% instead of 99.88%."""
    concepts = _concept_uis(SUPP, "SupplementalRecord")
    got = mesh_concepts.resolve_concepts(DESC, SUPP, set(concepts))
    assert got, "the SCR fixture yielded no concepts"
    rec = next(iter(got.values()))
    assert rec.record_kind == "SCR"
    assert rec.record_ui.startswith("C")
    assert rec.tree_numbers == ()          # SCRs carry no tree numbers


def test_descendants_are_found_under_a_tree_prefix():
    """The closure test. A rule names Epilepsy; the patient is coded Epilepsy,
    Generalized. Without this the read path expands into an empty registry and the
    whole feature is inert while appearing to work."""
    epilepsy = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    prefixes = frozenset(epilepsy[EPILEPSY_CONCEPT].tree_numbers)
    found = {r.record_ui for r in mesh_concepts.descriptors_under(DESC, prefixes)}
    assert "D004829" in found          # Epilepsy, Generalized -- strictly below
    assert "D004827" not in found      # the root itself is NOT its own descendant
    assert "D011247" not in found      # Pregnancy is in another branch entirely


def test_a_prefix_is_not_matched_by_string_prefix_alone():
    """'C10.228.140.49' must not match 'C10.228.140.490'. Segment-aware matching,
    or a tree number would adopt unrelated siblings as children."""
    assert mesh_concepts.is_descendant_tree("C10.228.140.490.100", "C10.228.140.490")
    assert not mesh_concepts.is_descendant_tree("C10.228.140.490", "C10.228.140.49")
    assert not mesh_concepts.is_descendant_tree("C10.228.140.490", "C10.228.140.490")


def test_parent_edges_come_from_tree_nesting():
    """Mirrors mesh._build_dag: only the IMMEDIATE tree-parent, and only when that
    parent is itself in the ingested set."""
    records = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    parent = records[EPILEPSY_CONCEPT]
    children = mesh_concepts.descriptors_under(DESC, frozenset(parent.tree_numbers))
    edges = mesh_concepts.parent_edges([parent, *children])
    assert mesh_concepts.ConditionParentEdge("D004829", "D004827") in edges


def test_parent_edges_never_self_parent():
    records = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    for edge in mesh_concepts.parent_edges(list(records.values())):
        assert edge.child_code != edge.parent_code


def _concept_uis(path, tag):
    """Every ConceptUI in a fixture file -- test scaffolding, not production code."""
    from xml.etree import ElementTree as ET
    root = ET.parse(path).getroot()
    return [c.text for r in root.iter(tag)
            for c in r.iter("ConceptUI") if c.text]
