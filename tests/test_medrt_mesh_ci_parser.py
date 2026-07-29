# tests/test_medrt_mesh_ci_parser.py
"""Parser unit tests for MED-RT's MESH-KEYED contraindications (slice 5b).

Split out of tests/test_medrt_parser.py, which had grown past CLAUDE.md rule 4's
~500-line budget. The split mirrors the parser's own separation: `medrt.py` keeps
the MeSH-keyed predicates in their own `MESH_CI_RELATIONSHIPS` set precisely because
they are a different KIND of assertion -- their object is a bare MeSH ConceptUI that
the MED-RT file alone cannot name -- so their tests belong in their own module too.

The controlled-input helpers are IMPORTED from test_medrt_parser rather than copied:
they build a MED-RT file, and there is one way to do that here, not two. (The repo
already reaches across test modules this way -- see test_ingest_run.py.)
"""
import re

from drugref.ingest import medrt
from tests.test_medrt_parser import assoc, concept, parsed, write_medrt

# ---- MeSH-keyed contraindications (slice 5b) -------------------------------
#
# CI_with ("contraindicated in a patient WITH <condition>") and CI_ChemClass ("do
# not co-administer with <this chemical>") were skipped through slices 2a and 5a
# for one reason only: their object is a bare MeSH ConceptUI, and this parser reads
# the MED-RT file alone, so it cannot say what that code names. It still cannot --
# ingest/mesh_concepts.py resolves the code against the MeSH release, and the
# orchestrator joins the two. What changes here is that the assertion is HANDED ON
# rather than discarded, raw code and all.


def test_mesh_keyed_contraindications_are_parsed():
    """The headline: CI_with and CI_ChemClass now come out of the parser."""
    parsed_fixture = parsed()
    assert parsed_fixture.mesh_contraindications, "no MeSH-keyed contraindications parsed"
    predicates = {a.relationship for a in parsed_fixture.mesh_contraindications}
    assert predicates <= {"CI_with", "CI_ChemClass"}
    for assertion in parsed_fixture.mesh_contraindications:
        assert assertion.rxcui and assertion.mesh_code
        # A MeSH ConceptUI, not a DescriptorUI: 'M' + digits. Worth pinning because
        # MED-RT publishes both shapes elsewhere and mesh_concepts.py looks the code
        # up as a ConceptUI -- a DescriptorUI would resolve to nothing, silently.
        assert re.fullmatch(r"M\d+", assertion.mesh_code), assertion


def test_the_fixture_exercises_both_mesh_keyed_predicates():
    """Without this, the test above passes on a fixture carrying only CI_with, and
    CI_ChemClass -- the half of slice 5b whose object is usually a SPECIFIC DRUG
    rather than a condition -- would go completely unexercised."""
    predicates = {a.relationship for a in parsed().mesh_contraindications}
    assert predicates == {"CI_with", "CI_ChemClass"}


def test_a_mesh_keyed_contraindication_names_the_drug_as_subject(tmp_path):
    """Direction, pinned on controlled input exactly as CI_MoA's is: the subject is
    the drug the statement is ABOUT (an RxCUI), the object is the MeSH concept it is
    contraindicated with or in. Reversing it inverts the clinical meaning."""
    path = write_medrt(
        tmp_path,
        concept("C-MOA", "N0000000301", "Some Mechanism [MoA]", cty="MoA"),
        assoc("CI_with", "RxNorm", "161", "MeSH", "M0012644")
        + assoc("CI_ChemClass", "RxNorm", "272", "MeSH", "M0000711"))
    assert medrt.parse(path).mesh_contraindications == [
        medrt.MeshObjectAssertion(rxcui="161", mesh_code="M0012644",
                                  relationship="CI_with"),
        medrt.MeshObjectAssertion(rxcui="272", mesh_code="M0000711",
                                  relationship="CI_ChemClass"),
    ]


def test_a_ci_object_outside_mesh_is_refused_and_counted(tmp_path):
    """Two CI_with assertions in the 2026.07.06 release point at a MED-RT EXT concept
    ('Current Non-smoker') instead of MeSH, and EXT is deliberately not an ingested
    concept type -- so that object can never be resolved. It is refused, but COUNTED,
    the same posture inactive_concepts takes: a number an operator can act on beats a
    row that vanishes. Asserted on controlled input because the fixture's own
    ingredients happen to carry only MeSH-keyed ones."""
    path = write_medrt(
        tmp_path,
        concept("C-MOA", "N0000000302", "Some Mechanism [MoA]", cty="MoA"),
        assoc("CI_with", "RxNorm", "1", "MED-RT", "N0000191637")
        + assoc("CI_with", "RxNorm", "1", "MeSH", "M0001885"))
    result = medrt.parse(path)
    assert result.mesh_contraindications == [
        medrt.MeshObjectAssertion(rxcui="1", mesh_code="M0001885",
                                  relationship="CI_with")]
    assert result.non_mesh_ci_objects == 1


def test_the_real_fixture_carries_no_non_mesh_ci_object():
    """Every CI_with/CI_ChemClass the fixture's ingredients carry is MeSH-keyed in
    this release, so the refusal counter must read zero here. If it ever fires,
    upstream started keying one of them somewhere else and we need to know."""
    assert parsed().non_mesh_ci_objects == 0


def test_mesh_ci_predicates_left_the_skipped_list():
    """skipped_predicates is the release-to-release change detector. A predicate we
    now INGEST must leave it, or the detector stops meaning anything."""
    skipped = parsed().skipped_predicates
    assert "CI_with" not in skipped
    assert "CI_ChemClass" not in skipped


def test_class_level_ci_is_unaffected():
    """CI_MoA/CI_PE must be untouched by this change -- slice 5a's class_contraindication
    rows are load-bearing for ddi_candidate_pair. A MeSH-keyed assertion must not leak
    into the class-level list, nor a class-level one into the MeSH-keyed list."""
    result = parsed()
    assert {c.relationship for c in result.contraindications} <= {"CI_MoA", "CI_PE"}
    assert {c.relationship for c in result.mesh_contraindications} <= {
        "CI_with", "CI_ChemClass"}
