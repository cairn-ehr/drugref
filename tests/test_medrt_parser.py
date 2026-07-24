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

from drugref.ingest import medrt

FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"


def parsed():
    return medrt.parse(FIX)


# ---- concepts --------------------------------------------------------------


def test_ingests_exactly_the_six_classification_concept_types():
    types = {c.concept_type for c in parsed().classes}
    assert types == {"MoA", "PE", "TC", "PK", "EPC", "APC"}


def test_ingests_every_class_in_the_fixture():
    assert len(parsed().classes) == 49


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
    assert len(parsed().parents) == 39


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
    assert "6853" not in counts     # magnesium sulfate: only an HC bin, so unclassified


def test_indication_and_contraindication_are_not_membership():
    """may_treat / may_prevent / CI_* are curated-overlay data for a later slice."""
    relationships = {m.relationship for m in parsed().memberships}
    assert relationships <= {"has_MoA", "has_PE", "has_TC", "has_PK", "has_EPC"}


def test_has_sc_into_mesh_is_dropped():
    """has_SC targets MeSH, which belongs to slice 2b and is not ours to bundle here."""
    assert all(m.relationship != "has_SC" for m in parsed().memberships)
    assert all(not m.class_nui.startswith("M") for m in parsed().memberships)


def test_every_membership_points_at_an_ingested_class():
    nuis = {c.nui for c in parsed().classes}
    assert all(m.class_nui in nuis for m in parsed().memberships)
