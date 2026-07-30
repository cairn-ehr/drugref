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


# ---- the descriptor-wins tie-break (issue #42) ------------------------------
#
# MEASURED AGAINST THE REAL 2026 RELEASE, because the fixture rule (#27) is
# "extract, never invent" and the first question is always whether there is
# anything to extract: desc2026 defines 61,794 ConceptUIs and supp2026 402,107,
# and **exactly 0 appear in both**. MeSH partitions concept ids across the two
# files, so the release cannot exercise this branch at all -- which is precisely
# why it needed a test and had none.
#
# So the guarantee is pinned on controlled input, as the issue directs, and the
# measurement is recorded rather than the absence being read as "untestable".
# The branch stays, and stays load-bearing: it is a guard against a release whose
# partition changes, and the cost of losing it is silent and permanent -- an SCR
# would mint a DIFFERENT immortal condition_uuid for the same clinical concept
# and, bearing no tree numbers, drop it out of the DAG with its whole descendant
# expansion. Nothing would fail; the row count would barely move.


def _both_files_defining(tmp_path, concept_ui):
    """One ConceptUI defined in BOTH a descriptor record and an SCR.

    The record CONTENTS mirror the real files' shape (a descriptor bears tree
    numbers, an SCR bears none); what is synthetic is only that one concept id
    appears twice, which the 2026 release never does.
    """
    desc = tmp_path / "desc.xml"
    desc.write_text(
        '<?xml version="1.0"?>\n<DescriptorRecordSet><DescriptorRecord>'
        '<DescriptorUI>D000001</DescriptorUI>'
        '<DescriptorName><String>A Descriptor</String></DescriptorName>'
        '<TreeNumberList><TreeNumber>C10.228</TreeNumber></TreeNumberList>'
        f'<ConceptList><Concept PreferredConceptYN="Y">'
        f'<ConceptUI>{concept_ui}</ConceptUI></Concept></ConceptList>'
        '</DescriptorRecord></DescriptorRecordSet>\n', encoding="utf-8")
    supp = tmp_path / "supp.xml"
    supp.write_text(
        '<?xml version="1.0"?>\n<SupplementalRecordSet><SupplementalRecord>'
        '<SupplementalRecordUI>C000001</SupplementalRecordUI>'
        '<SupplementalRecordName><String>An SCR</String></SupplementalRecordName>'
        f'<ConceptList><Concept PreferredConceptYN="Y">'
        f'<ConceptUI>{concept_ui}</ConceptUI></Concept></ConceptList>'
        '</SupplementalRecord></SupplementalRecordSet>\n', encoding="utf-8")
    return desc, supp


def test_a_descriptor_wins_over_an_scr_defining_the_same_concept(tmp_path):
    """The tie-break the docstring promises, on the only input that can show it.

    Reversing the read order would pass every other test in this module: the
    concept still resolves, to a record that still exists, with a name that still
    reads correctly. Only the identity would differ -- and identity is immortal.
    """
    desc, supp = _both_files_defining(tmp_path, "M0000001")
    got = mesh_concepts.resolve_concepts(desc, supp, {"M0000001"})

    assert got["M0000001"].record_ui == "D000001"
    assert got["M0000001"].record_kind == mesh_concepts.DESCRIPTOR
    assert got["M0000001"].tree_numbers == ("C10.228",)   # the SCR carries none


def test_the_scr_is_still_reachable_when_no_descriptor_defines_the_concept(tmp_path):
    """The other half: 'descriptors win' must not become 'SCRs are unreachable'.
    86 of the release's stragglers resolve only in supp2026, and losing them takes
    resolution from 99.88% back to 96.4%."""
    desc, supp = _both_files_defining(tmp_path, "M0000001")
    empty_desc = tmp_path / "empty_desc.xml"
    empty_desc.write_text('<?xml version="1.0"?>\n<DescriptorRecordSet/>\n',
                          encoding="utf-8")

    got = mesh_concepts.resolve_concepts(empty_desc, supp, {"M0000001"})
    assert got["M0000001"].record_ui == "C000001"
    assert got["M0000001"].record_kind == mesh_concepts.SCR


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


def test_ancestor_trees_is_the_same_rule_read_backwards():
    """THE EQUIVALENCE THE CLOSURE SCAN RELIES ON.

    descriptors_under matches by probing a prefix SET with each tree number's own
    ancestors, because testing every prefix against every tree number cost 4.49s per
    scan against release-shaped data where this costs 0.03s. That is only a safe
    substitution while the two agree EXACTLY, so they are pinned against each other
    here rather than assumed -- including the two cases a naive startswith gets
    wrong: a shared text prefix that is not a tree ancestor, and a node's own number.
    """
    trees = ["C10", "C10.228", "C10.228.140", "C10.228.140.49", "C10.228.140.490",
             "C10.228.140.490.100", "D02", "G08.686"]
    for t in trees:
        for p in trees:
            assert mesh_concepts.is_descendant_tree(t, p) == \
                (p in mesh_concepts.ancestor_trees(t)), f"{t} vs {p}"
    # A top-level number has no ancestors, so it can never be anyone's descendant.
    assert mesh_concepts.ancestor_trees("C10") == []
    assert mesh_concepts.ancestor_trees("C10.228.140") == ["C10", "C10.228"]


def test_the_closure_scan_respects_segment_boundaries():
    """The equivalence above, exercised through descriptors_under itself.

    'C10.228.140.49' is not a real MeSH node, and nothing may be found beneath it --
    least of all the records under the textually-similar 'C10.228.140.490'.
    """
    assert mesh_concepts.descriptors_under(DESC, frozenset({"C10.228.140.49"})) == []


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


def test_a_supplementary_record_carries_its_scr_class(tmp_path):
    """MeSH's SCRClass is what tells a rare disease (3) from a chemical (1) among
    records that bear NO tree numbers, and therefore no DAG position at all. It is the
    only thing that lets gap_condition_without_indication publish 'Short QT Syndrome'
    while excluding 'aliskiren'."""
    supp = tmp_path / "supp.xml"
    supp.write_text(
        '<?xml version="1.0"?><SupplementalRecordSet>'
        '<SupplementalRecord SCRClass="3">'
        '<SupplementalRecordUI>C536914</SupplementalRecordUI>'
        '<SupplementalRecordName><String>Thyroid cancer, medullary</String>'
        '</SupplementalRecordName>'
        '<ConceptList><Concept PreferredConceptYN="Y">'
        '<ConceptUI>M0999001</ConceptUI></Concept></ConceptList>'
        '</SupplementalRecord></SupplementalRecordSet>', encoding="utf-8")
    empty = tmp_path / "desc.xml"
    empty.write_text('<?xml version="1.0"?><DescriptorRecordSet/>', encoding="utf-8")

    got = mesh_concepts.resolve_concepts(empty, supp, {"M0999001"})["M0999001"]
    assert got.record_kind == mesh_concepts.SCR
    assert got.scr_class == "3"


def test_a_descriptor_carries_no_scr_class(tmp_path):
    """DescriptorRecord publishes DescriptorClass, a different vocabulary. Reading it
    into this field would make descriptors indistinguishable from SCR chemicals."""
    desc = tmp_path / "desc.xml"
    desc.write_text(
        '<?xml version="1.0"?><DescriptorRecordSet>'
        '<DescriptorRecord DescriptorClass="1">'
        '<DescriptorUI>D004827</DescriptorUI>'
        '<DescriptorName><String>Epilepsy</String></DescriptorName>'
        '<ConceptList><Concept PreferredConceptYN="Y">'
        '<ConceptUI>M0007720</ConceptUI></Concept></ConceptList>'
        '</DescriptorRecord></DescriptorRecordSet>', encoding="utf-8")
    empty = tmp_path / "supp.xml"
    empty.write_text('<?xml version="1.0"?><SupplementalRecordSet/>', encoding="utf-8")

    got = mesh_concepts.resolve_concepts(desc, empty, {"M0007720"})["M0007720"]
    assert got.record_kind == mesh_concepts.DESCRIPTOR
    assert got.scr_class is None


def _concept_uis(path, tag):
    """Every ConceptUI in a fixture file -- test scaffolding, not production code."""
    from xml.etree import ElementTree as ET
    root = ET.parse(path).getroot()
    return [c.text for r in root.iter(tag)
            for c in r.iter("ConceptUI") if c.text]
