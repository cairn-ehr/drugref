# tests/test_medrt_parser.py
"""Parser unit tests -- no database.

Every expected value here was measured against the real MED-RT release the
fixture was extracted from (see tests/fixtures/make_medrt_subset.py), not
invented. That matters: the two facts most likely to be got wrong -- the
direction of 'Parent Of', and that [HC] concepts are alphabetical navigation
bins rather than classifications -- are invisible in a hand-written fixture,
because a hand-written fixture just encodes whatever the author assumed.
"""
import pathlib
import re

from drugref.ingest import medrt

FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"


def parsed():
    return medrt.parse(FIX)


def _write(tmp_path, concepts: str, associations: str = "") -> pathlib.Path:
    """Write a minimal MED-RT file for the shape-variation tests below.

    The acceptance tests all run against the real extracted fixture; this exists
    only for shapes the current release does not contain (a retired concept, a
    concept whose code differs from its NUI), which cannot be extracted from it.
    """
    path = tmp_path / "medrt.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        "<terminology>\n"
        "\t<namespace><name>MED-RT</name><version>test</version></namespace>\n"
        f"{concepts}{associations}"
        "</terminology>\n", encoding="utf-8")
    return path


def _concept(code: str, nui: str, name: str, cty: str = "MoA", status: str = "A") -> str:
    return (f"\t<concept><namespace>MED-RT</namespace><name>{name}</name>"
            f"<code>{code}</code><status>{status}</status>"
            f"<property><namespace>MED-RT</namespace><name>CTY</name>"
            f"<value>{cty}</value></property>"
            + (f"<property><namespace>MED-RT</namespace><name>NUI</name>"
               f"<value>{nui}</value></property>" if nui else "")
            + "</concept>\n")


def _assoc(name: str, fns: str, fc: str, tns: str, tc: str) -> str:
    return (f"\t<association><namespace>MED-RT</namespace><name>{name}</name>"
            f"<from_namespace>{fns}</from_namespace><from_name>x</from_name>"
            f"<from_code>{fc}</from_code>"
            f"<to_namespace>{tns}</to_namespace><to_name>y</to_name>"
            f"<to_code>{tc}</to_code></association>\n")


# ---- concepts --------------------------------------------------------------


def test_ingests_exactly_the_six_classification_concept_types():
    types = {c.concept_type for c in parsed().classes}
    assert types == {"MoA", "PE", "TC", "PK", "EPC", "APC"}


def test_ingests_every_class_in_the_fixture():
    assert len(parsed().classes) == 75


def test_excludes_hc_navigation_bins():
    """[HC] concepts are the 26 alphabetical bins ('A [Preparations]'). Ingesting
    them would attach a meaningless 'class' to almost every drug."""
    names = {c.name for c in parsed().classes}
    assert not [n for n in names if "[Preparations]" in n]
    assert "Pharmaceutical and Biological Preparations" not in names


def test_class_records_carry_nui_and_name():
    by_nui = {c.nui: c for c in parsed().classes}
    amlodipine_epc = by_nui["N0000175421"]
    assert amlodipine_epc.name == "Dihydropyridine Calcium Channel Blocker [EPC]"
    assert amlodipine_epc.concept_type == "EPC"
    # Code as published, kept separately from the identity key -- equal here, but
    # see test_edges_resolve_when_code_and_nui_differ for why they are two fields.
    assert amlodipine_epc.code == "N0000175421"


def test_the_real_release_holds_no_inactive_or_unidentified_concepts():
    """The counters exist for a release shape we have not seen yet; against the
    real one they must read zero, or something has changed upstream."""
    result = parsed()
    assert (result.inactive_concepts, result.unidentified_concepts) == (0, 0)


def test_a_retired_concept_is_refused_and_counted(tmp_path):
    """substance_class never deletes, so a concept upstream has stopped asserting
    must not get in -- and must be reported, not dropped in silence."""
    path = _write(tmp_path, _concept("N0000000001", "N0000000001", "Live [MoA]")
                  + _concept("N0000000002", "N0000000002", "Dead [MoA]", status="R"))
    result = medrt.parse(path)
    assert [c.nui for c in result.classes] == ["N0000000001"]
    assert result.inactive_concepts == 1


def test_a_concept_with_no_identifier_at_all_is_refused_and_counted(tmp_path):
    """Minting from an empty key would collapse every such concept onto ONE
    class_uuid, so they would silently overwrite each other's names."""
    path = _write(tmp_path, _concept("", "", "Anonymous [MoA]")
                  + _concept("", "", "Also Anonymous [MoA]"))
    result = medrt.parse(path)
    assert result.classes == []
    assert result.unidentified_concepts == 2


def test_either_identifier_alone_is_enough(tmp_path):
    """A concept with only a code is still identifiable; the code stands in as the
    NUI (and vice versa). Only carrying neither is fatal."""
    path = _write(tmp_path, _concept("N0000000003", "", "Code Only [MoA]"))
    only = medrt.parse(path).classes
    assert [(c.nui, c.code) for c in only] == [("N0000000003", "N0000000003")]


def test_edges_resolve_when_code_and_nui_differ(tmp_path):
    """Associations reference endpoints by CODE while identity is the NUI. They
    are equal in the 2026.07.06 release, so matching endpoint codes against NUIs
    works by luck today; were upstream to let them diverge, every edge would fail
    to match and the DAG would come back EMPTY with no error and no count."""
    path = _write(
        tmp_path,
        _concept("C-PARENT", "N0000000004", "Parent [MoA]")
        + _concept("C-CHILD", "N0000000005", "Child [MoA]"),
        _assoc("Parent Of", "MED-RT", "C-PARENT", "MED-RT", "C-CHILD"))
    # Edges are emitted in terms of NUIs, because that is what class_uuid derives
    # from -- but they are FOUND by code.
    assert medrt.parse(path).parents == [
        medrt.ParentEdge(child_nui="N0000000005", parent_nui="N0000000004")]


def test_epc_membership_also_resolves_by_code(tmp_path):
    """The same code-vs-NUI rule on the hierarchical EPC membership path."""
    path = _write(
        tmp_path,
        _concept("C-EPC", "N0000000006", "Some Class [EPC]", cty="EPC"),
        _assoc("Parent Of", "MED-RT", "C-EPC", "RxNorm", "161"))
    assert medrt.parse(path).memberships == [
        medrt.MembershipAssertion("161", "N0000000006", "has_EPC")]


# ---- the subclass DAG ------------------------------------------------------


def test_parent_edges_run_parent_to_child_not_the_reverse():
    """MED-RT 'Parent Of' points FROM the parent TO the child. Getting this
    backwards silently inverts the whole hierarchy, so it is pinned with a real
    pair: the broad 'Calcium Channel Agent [APC]' sits ABOVE the specific
    'Dihydropyridine Calcium Channel Blocker [EPC]', never below it."""
    edges = parsed().parents
    assert medrt.ParentEdge(child_nui="N0000175421", parent_nui="N0000193892") in edges
    assert medrt.ParentEdge(child_nui="N0000193892", parent_nui="N0000175421") not in edges


def test_builds_the_expected_number_of_dag_edges():
    assert len(parsed().parents) == 59


def test_a_class_can_have_two_parents():
    """A DAG, not a tree. Real multi-parent node from the release: 'Calcium Channel
    Agent [APC]' hangs off both 'Ion Channel or Pump Agent' and 'Cardiovascular
    Agent' -- a genuine two-axis classification a tree could not express."""
    parents = {e.parent_nui for e in parsed().parents if e.child_nui == "N0000193892"}
    assert parents == {"N0000193904", "N0000193893"}


def test_drops_hierarchy_into_unlicensed_or_uningested_endpoints():
    """SNOMED CT endpoints must never be traversed (licence), and HC bins are not
    classes, so neither may appear anywhere in the DAG."""
    nuis = {c.nui for c in parsed().classes}
    for edge in parsed().parents:
        assert edge.child_nui in nuis and edge.parent_nui in nuis
    # The check above is only meaningful while the fixture actually CONTAINS an
    # edge reaching in from an unlicensed namespace. Pin that it still does.
    assert "<from_namespace>SNOMED CT</from_namespace>" in FIX.read_text(encoding="utf-8")


# Namespaces whose terms this repository may redistribute, restated here rather than
# imported from make_medrt_subset.py ON PURPOSE: this test is the INDEPENDENT check on
# the generator, so widening the generator's set must make it fail, not follow along.
REDISTRIBUTABLE_NAMESPACES = {"MED-RT", "RxNorm", "MeSH"}


def test_the_fixture_redacts_snomed_but_keeps_mesh():
    """A licence rule about the REPOSITORY, not the database -- and it has two halves.

    The parser refusing a SNOMED edge keeps unlicensed content out of the DB, but the
    fixture is a committed file in an AGPL-licensed repo: a SNOMED term sitting in it
    is redistributed no matter what the parser does, and would contradict NOTICE.
    SNOMED CT is not redistributable under drugref's licence at all, so its endpoints
    must stay redacted, permanently and without exception.

    MeSH is the other half, and the reason this is not simply "redact everything
    foreign": MeSH was licence-cleared in slice 2b (NLM terms -- attribution, no
    endorsement, version currency; no NonCommercial, no NoDerivatives), this repo
    already commits mesh_*_subset.xml beside this fixture, and slice 5b's CI_with /
    CI_ChemClass are keyed by MeSH ConceptUI -- a redacted object code would make
    those two predicates untestable. So MeSH endpoints must come through intact.

    Asserting only one half would let a regeneration break the other silently.
    """
    text = FIX.read_text(encoding="utf-8")
    endpoints = re.findall(
        r"<(from|to)_namespace>([^<]+)</\1_namespace>\s*"
        r"<\1_name>([^<]*)</\1_name>\s*<\1_code>([^<]*)</\1_code>", text)
    namespaces = {namespace for _side, namespace, _name, _code in endpoints}

    # Half one: SNOMED CT (and anything else unlicensed) is present but redacted.
    assert "SNOMED CT" in namespaces, "fixture no longer exercises an unlicensed endpoint"
    for _side, namespace, name, code in endpoints:
        if namespace not in REDISTRIBUTABLE_NAMESPACES:
            assert (name, code) == ("REDACTED", "REDACTED"), \
                f"unredacted {namespace} content in the committed fixture: {name!r} / {code!r}"

    # Half two: MeSH is present and NOT redacted, carrying real ConceptUIs.
    mesh = [(name, code) for _side, namespace, name, code in endpoints if namespace == "MeSH"]
    assert mesh, "fixture no longer exercises a MeSH endpoint"
    for name, code in mesh:
        assert (name, code) != ("REDACTED", "REDACTED"), \
            "MeSH endpoints are licence-cleared and must survive extraction intact"
        assert re.fullmatch(r"M\d+", code), f"not a MeSH ConceptUI: {code!r}"


# ---- membership ------------------------------------------------------------


def test_membership_carries_rxcui_class_and_axis():
    memberships = parsed().memberships
    assert medrt.MembershipAssertion("161", "N0000000108", "has_MoA") in memberships
    assert medrt.MembershipAssertion("17767", "N0000178310", "has_TC") in memberships


def test_epc_membership_is_derived_from_the_hierarchy():
    """MED-RT has NO has_EPC association; an ingredient's EPC is expressed as a
    'Parent Of' from the EPC class down to the drug. Amlodipine has two."""
    epc = {m.class_nui for m in parsed().memberships
           if m.rxcui == "17767" and m.relationship == "has_EPC"}
    assert epc == {"N0000175421", "N0000175566"}


def test_membership_counts_per_ingredient():
    counts = {}
    for m in parsed().memberships:
        counts[m.rxcui] = counts.get(m.rxcui, 0) + 1
    assert counts["161"] == 8       # 1 MoA + 4 PE + 2 PK + 1 TC, no EPC
    assert counts["17767"] == 9     # 3 MoA + 2 PE + 2 TC + 2 EPC
    assert counts["5640"] == 9      # ibuprofen: parsed here, unmatched at ingest time
    assert counts["272"] == 5       # activated charcoal: 1 MoA + 3 PE + 1 TC, no EPC
    assert counts["321988"] == 4    # escitalopram: 1 MoA + 1 PE + 1 TC + 1 EPC
    assert "6853" not in counts     # methoxamine: only an HC bin, so unclassified


def test_indication_and_contraindication_are_not_membership():
    """may_treat / may_prevent / CI_* are curated-overlay data for a later slice."""
    relationships = {m.relationship for m in parsed().memberships}
    assert relationships <= {"has_MoA", "has_PE", "has_TC", "has_PK", "has_EPC"}


def test_has_sc_into_mesh_is_dropped():
    """has_SC ('has chemical structure') targets MeSH, and is an indications-era
    predicate drugref does not ingest on any axis.

    Slice 5b made MeSH endpoints readable for CI_with / CI_ChemClass, which is
    exactly why this needs pinning now: "the object is MeSH" stopped being a reason
    to drop an edge, so has_SC has to be dropped on its NAME instead. Asserted
    against the fixture's own has_SC count, so it cannot pass by the fixture quietly
    losing the edges it is meant to exercise.
    """
    text = FIX.read_text(encoding="utf-8")
    assert text.count("<name>has_SC</name>") == 2, "fixture no longer exercises has_SC"
    assert all(m.relationship != "has_SC" for m in parsed().memberships)
    # ...and it must not have leaked into the MeSH-keyed contraindications either.
    assert all(a.relationship != "has_SC" for a in parsed().mesh_contraindications)
    assert "has_SC" in parsed().skipped_predicates


def test_every_membership_points_at_an_ingested_class():
    nuis = {c.nui for c in parsed().classes}
    assert all(m.class_nui in nuis for m in parsed().memberships)


# ---- contraindications (slice 5a) ------------------------------------------
# ---- upstream vocabulary drift ---------------------------------------------
#
# The parser silently ignores any concept type or association name it does not
# recognise. That is correct behaviour (HC bins and may_treat are deliberately out
# of scope) but it is also exactly how an upstream RENAME would look: a predicate
# drugref does ingest, quietly matching nothing, forever. Reporting the distinct
# names seen-and-skipped turns that into something a release-to-release diff shows.


def test_skipped_concept_types_are_reported(tmp_path):
    path = _write(
        tmp_path,
        _concept("C-HC", "N-HC-1", "A [Preparations]", cty="HC")
        + _concept("C-EXT", "N-EXT-1", "Some Chemical", cty="EXT")
        + _concept("C-MOA", "N-MOA-9", "Real Mechanism [MoA]", cty="MoA"))
    assert medrt.parse(path).skipped_concept_types == ("EXT", "HC")


def test_skipped_association_names_are_reported(tmp_path):
    path = _write(
        tmp_path,
        _concept("C-MOA", "N-MOA-9", "Real Mechanism [MoA]", cty="MoA"),
        _assoc("may_treat", "RxNorm", "161", "MeSH", "M0001")
        + _assoc("has_SC", "MED-RT", "C-MOA", "MeSH", "M0002")
        + _assoc("has_MoA", "RxNorm", "161", "MED-RT", "C-MOA"))
    result = medrt.parse(path)
    assert result.skipped_predicates == ("has_SC", "may_treat")
    assert len(result.memberships) == 1      # the recognised one still lands


# ---- ambiguous published codes ---------------------------------------------
#
# Associations reference their endpoints by published <code>, while a class's
# identity is its NUI, so the parser resolves code -> NUI through a lookup built
# from the concepts. That lookup is only sound while codes are UNIQUE. Nothing
# upstream guarantees it and no constraint enforces it, so a release in which two
# concepts publish one code must not be resolved by "whichever came last".


def test_an_edge_through_a_code_claimed_by_two_concepts_is_refused(tmp_path):
    # Two DIFFERENT classes on DIFFERENT axes publishing one code. Resolving
    # last-write-wins would attach this has_MoA membership to the PE class --
    # a mechanism-of-action fact filed as a physiological effect, silently.
    path = _write(
        tmp_path,
        _concept("SHARED", "N-MOA-1", "Real Mechanism [MoA]", cty="MoA")
        + _concept("SHARED", "N-PE-1", "Unrelated Effect [PE]", cty="PE"),
        _assoc("has_MoA", "RxNorm", "161", "MED-RT", "SHARED"))
    result = medrt.parse(path)
    assert result.memberships == []
    assert result.ambiguous_codes == 1


def test_an_unambiguous_code_still_resolves_when_another_code_is_ambiguous(tmp_path):
    # The refusal is scoped to the offending code, not the whole release.
    path = _write(
        tmp_path,
        _concept("SHARED", "N-MOA-1", "Real Mechanism [MoA]", cty="MoA")
        + _concept("SHARED", "N-PE-1", "Unrelated Effect [PE]", cty="PE")
        + _concept("CLEAN", "N-MOA-2", "Clean Mechanism [MoA]", cty="MoA"),
        _assoc("has_MoA", "RxNorm", "161", "MED-RT", "SHARED")
        + _assoc("has_MoA", "RxNorm", "161", "MED-RT", "CLEAN"))
    result = medrt.parse(path)
    assert result.memberships == [
        medrt.MembershipAssertion(rxcui="161", class_nui="N-MOA-2",
                                  relationship="has_MoA")]
    assert result.ambiguous_codes == 1


# CI_MoA / CI_PE are "contraindicated MoA / physiological-effect of a
# CO-ADMINISTERED ingredient" -- drug-drug interaction rules. Subject is the drug
# the statement is about (from_code, an RxCUI); object is the co-administered
# drug's MED-RT class (to_code). Getting that direction backwards inverts the
# clinical meaning, so it is pinned on controlled input here and on real data in
# the fixture tests.


def test_ci_moa_is_emitted_with_the_drug_as_subject_and_the_class_as_object(tmp_path):
    path = _write(
        tmp_path,
        _concept("C-MOA", "N0000000201", "Some Mechanism [MoA]", cty="MoA"),
        _assoc("CI_MoA", "RxNorm", "12345", "MED-RT", "C-MOA"))
    assert medrt.parse(path).contraindications == [
        medrt.ContraindicationAssertion(rxcui="12345", class_nui="N0000000201",
                                        relationship="CI_MoA")]


def test_ci_pe_is_emitted_on_the_pe_axis(tmp_path):
    path = _write(
        tmp_path,
        _concept("C-PE", "N0000000202", "Some Effect [PE]", cty="PE"),
        _assoc("CI_PE", "RxNorm", "678", "MED-RT", "C-PE"))
    assert medrt.parse(path).contraindications == [
        medrt.ContraindicationAssertion(rxcui="678", class_nui="N0000000202",
                                        relationship="CI_PE")]


def test_a_contraindication_to_an_uningested_class_is_dropped(tmp_path):
    """Endpoint scoping, exactly as for the DAG and membership: the object must be a
    class we ingested, or the edge cannot be resolved to a class_uuid at all."""
    path = _write(
        tmp_path,
        _concept("C-MOA", "N0000000203", "Some Mechanism [MoA]", cty="MoA"),
        # object code C-GONE names no ingested concept
        _assoc("CI_MoA", "RxNorm", "999", "MED-RT", "C-GONE"))
    assert medrt.parse(path).contraindications == []


def test_contraindications_and_membership_do_not_leak_into_each_other(tmp_path):
    """A CI_MoA is not membership; a has_MoA is not a contraindication; and the
    MeSH-keyed CI_with (slice 5b) is neither, here."""
    path = _write(
        tmp_path,
        _concept("C-MOA", "N0000000204", "Some Mechanism [MoA]", cty="MoA"),
        _assoc("CI_MoA", "RxNorm", "1", "MED-RT", "C-MOA")
        + _assoc("has_MoA", "RxNorm", "1", "MED-RT", "C-MOA")
        + _assoc("CI_with", "RxNorm", "1", "MeSH", "M0001111"))
    result = medrt.parse(path)
    assert [c.relationship for c in result.contraindications] == ["CI_MoA"]
    assert [m.relationship for m in result.memberships] == ["has_MoA"]


def test_the_real_fixture_contraindications_all_point_at_ingested_classes():
    """Whatever CI_MoA/CI_PE the fixture carries, each resolves to a real class."""
    nuis = {c.nui for c in parsed().classes}
    assert all(c.class_nui in nuis for c in parsed().contraindications)


def test_the_fixture_exercises_a_real_contraindication_edge():
    """The generator keeps CI_MoA/CI_PE because it does not trim them; a regeneration
    that started trimming them would silently empty slice-5a's axis and leave the
    test above passing vacuously. Pin the real edge the release provides for our
    ingredients: amlodipine's CI_PE (17767 -> N0000178477 [PE])."""
    assert "<name>CI_PE</name>" in FIX.read_text(encoding="utf-8"), \
        "fixture no longer exercises a contraindication edge"
    assert medrt.ContraindicationAssertion("17767", "N0000178477", "CI_PE") \
        in parsed().contraindications


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
    path = _write(
        tmp_path,
        _concept("C-MOA", "N0000000301", "Some Mechanism [MoA]", cty="MoA"),
        _assoc("CI_with", "RxNorm", "161", "MeSH", "M0012644")
        + _assoc("CI_ChemClass", "RxNorm", "272", "MeSH", "M0000711"))
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
    path = _write(
        tmp_path,
        _concept("C-MOA", "N0000000302", "Some Mechanism [MoA]", cty="MoA"),
        _assoc("CI_with", "RxNorm", "1", "MED-RT", "N0000191637")
        + _assoc("CI_with", "RxNorm", "1", "MeSH", "M0001885"))
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
