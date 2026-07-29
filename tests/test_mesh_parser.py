# tests/test_mesh_parser.py
"""Parser unit tests for the slice-2b MeSH PA axis -- no database.

Every expected value here was measured against the real MeSH 2026 release the
three fixtures were extracted from (tests/fixtures/make_mesh_subset.py, spec §5),
never invented. The facts most likely to be got wrong are pinned deliberately:

* a MeSH **Descriptor** carries its substance's **UNII** in <RegistryNumber>
  (issue #11 believed Descriptors held only CAS -- aspirin D001241 shows a UNII);
* the PA class **hierarchy** is encoded in **tree-number nesting**, not a parent
  link, and is a genuine multi-parent DAG;
* the bridge keys come from **RegistryNumber only** -- a CAS in
  <RelatedRegistryNumber> is NOT a membership key in this slice (design tension B).
"""
import gzip
import pathlib

from drugref.ingest import mesh

FIX = pathlib.Path(__file__).parent / "fixtures"
PA = FIX / "mesh_pa_subset.xml"
DESC = FIX / "mesh_desc_subset.xml"
SUPP = FIX / "mesh_supp_subset.xml"

# The six PA classes the fixture carries (all D-prefixed descriptors, spec §5.1).
PA_CLASSES = {"D000700", "D000893", "D000894", "D012102", "D018501", "D018712"}


def parsed():
    return mesh.parse(pa_path=PA, desc_path=DESC, supp_path=SUPP)


# ---- reading the files NLM actually publishes (issue #40) -------------------


def _gzipped(tmp_path: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """The same committed fixture, gzipped -- the shape NLM ships desc/supp in.

    Compressed here rather than committed twice: a second copy of a fixture is a
    second thing that can drift, and what is under test is the READER, not the
    bytes (which are byte-identical to the file beside it by construction).
    """
    out = tmp_path / (path.name + ".gz")
    out.write_bytes(gzip.compress(path.read_bytes()))
    return out


def test_parses_the_gzipped_files_the_nlm_publishes(tmp_path):
    """NLM ships desc2026/supp2026 as `.gz`, ~750 MB each once expanded.

    Slice 5b's reader handled that transparently and slice 2b's did not, so half
    the MeSH ingest needed a manual gunzip and half did not -- an asymmetry
    invisible from either call site (#40). One reader now serves both, and this
    pins that a gzipped release parses to EXACTLY what the plain files do rather
    than merely "not crashing".
    """
    from_gz = mesh.parse(pa_path=_gzipped(tmp_path, PA),
                         desc_path=_gzipped(tmp_path, DESC),
                         supp_path=_gzipped(tmp_path, SUPP))
    assert from_gz == parsed()


def test_one_reader_serves_both_mesh_parsers():
    """The hoist itself, pinned: `mesh_concepts` must READ through this module's
    reader, not carry its own copy. Two copies is how the asymmetry above arose,
    and a second copy would pass every behavioural test above on the day it was
    written -- so the sharing is asserted directly.
    """
    from drugref.ingest import mesh_concepts
    assert mesh_concepts.iter_records is mesh.iter_records


# ---- registry-number classification (the pure key rule, spec §5.2) ---------


def test_unii_is_ten_upper_alphanumerics():
    unii, cas = mesh.registry_keys(["R16CO5Y76E"])
    assert unii == {"R16CO5Y76E"}
    assert cas == set()


def test_cas_is_the_n_nn_n_shape():
    unii, cas = mesh.registry_keys(["7487-88-9"])
    assert cas == {"7487-88-9"}
    assert unii == set()


def test_placeholder_zero_and_ec_numbers_are_neither():
    """'0' means 'no registry number'; 'EC 3.4.21.4' is an enzyme number, not a
    moiety identity key drugref holds. Both classify as neither (spec §5.2/§10)."""
    unii, cas = mesh.registry_keys(["0", "EC 3.4.21.4", ""])
    assert unii == set() and cas == set()


def test_related_registry_number_parenthetical_is_stripped():
    """A RelatedRegistryNumber may be annotated '<cas> (<name>)'; the classifier
    strips the parenthetical before matching (spec §7). (This is a property of the
    pure function; the membership bridge still reads RegistryNumber only.)"""
    _unii, cas = mesh.registry_keys(["50-78-2 (Aspirin)"])
    assert cas == {"50-78-2"}


# ---- refusals are counted, never silent (issue #17) ------------------------


def test_a_well_formed_release_drops_no_pa_record():
    """The counter's baseline: the real release names a descriptor on every PA
    record, so the number an operator sees is zero and any non-zero value means
    something genuinely changed upstream."""
    assert parsed().pa_records_without_descriptor == 0


def test_a_pa_record_with_no_descriptor_ui_is_counted_not_silently_dropped(tmp_path):
    """A PA record naming no descriptor cannot become a class -- there is no
    identity to key one on -- so dropping it is right. Dropping it INVISIBLY is
    not: every other refusal in this codebase is a reported worklist number, and
    this one was the last silent `continue` left (#17).

    The input is written here rather than extracted from a release, unlike every
    other fixture: a well-formed release contains no such record by definition, so
    there is nothing to extract. What is synthetic is the DEFECT, never a fact
    about MeSH's shape -- the kept record beside it is copied from the real
    fixture's D000894, so the parser is proved to keep parsing past the bad one.
    """
    pa = tmp_path / "pa.xml"
    pa.write_text(
        '<?xml version="1.0"?>\n<PharmacologicalActionSet>\n'
        ' <PharmacologicalAction><DescriptorReferredTo>'
        '<DescriptorName><String>nameless</String></DescriptorName>'
        '</DescriptorReferredTo></PharmacologicalAction>\n'
        ' <PharmacologicalAction><DescriptorReferredTo>'
        '<DescriptorUI>D000894</DescriptorUI>'
        '<DescriptorName><String>Anti-Inflammatory Agents, Non-Steroidal</String>'
        '</DescriptorName></DescriptorReferredTo></PharmacologicalAction>\n'
        '</PharmacologicalActionSet>\n', encoding="utf-8")

    result = mesh.parse(pa_path=pa, desc_path=DESC, supp_path=SUPP)
    assert result.pa_records_without_descriptor == 1
    # The good record beside it still becomes a class: the refusal is per-record.
    assert [c.descriptor_ui for c in result.classes] == ["D000894"]


# ---- the class side of the axis -------------------------------------------


def test_parses_exactly_the_pa_class_descriptors():
    """Only the descriptors named in the PA file are classes; a member-only
    descriptor (e.g. D000082 Acetaminophen) is present in desc but is NOT a PA
    class, so it must not appear among the classes."""
    uis = {c.descriptor_ui for c in parsed().classes}
    assert uis == PA_CLASSES
    assert "D000082" not in uis and "D001241" not in uis


def test_pa_classes_are_named_and_typed_pa():
    by_ui = {c.descriptor_ui: c for c in parsed().classes}
    assert by_ui["D000894"].name == "Anti-Inflammatory Agents, Non-Steroidal"
    # PaClass models a PA descriptor, so the axis is intrinsic (always 'PA').
    assert by_ui["D000894"].concept_type == "PA"


def test_pa_classes_carry_their_tree_numbers():
    """Tree numbers come from desc2026 and are what the DAG is derived from."""
    by_ui = {c.descriptor_ui: c for c in parsed().classes}
    assert set(by_ui["D000894"].tree_numbers) == {
        "D27.505.696.663.850.014.040.500", "D27.505.954.158.030", "D27.505.954.329.030"}


def test_pa_class_carries_no_registry_key():
    """Abstract action classes have no identity key of their own (spec §5.2)."""
    # A class is not a member; the parser never attaches keys to a PaClass.
    assert not hasattr(parsed().classes[0], "keys")


# ---- membership edges + key extraction (the bridge inputs) -----------------


def _members_by_class(result):
    out = {}
    for m in result.memberships:
        out.setdefault(m.descriptor_ui, set()).add(m.record_ui)
    return out


def test_membership_edges_match_the_release():
    m = _members_by_class(parsed())
    assert m["D000894"] == {"C000002", "C007609", "D001241"}
    assert m["D012102"] == {"D008278"}
    assert m["D000700"] == {"D008278", "C000002", "C007609", "D000082", "D001241"}


def _keys_of(result, record_ui):
    """The (unii, cas) key sets for a member -- identical for every class it is
    under, so any one of its membership rows carries them."""
    for m in result.memberships:
        if m.record_ui == record_ui:
            return m.keys.unii, m.keys.cas
    raise AssertionError(f"no membership for {record_ui}")


def test_descriptor_member_exposes_its_unii():
    """D000082 Acetaminophen -- UNII 362O9ITL9D in RegistryNumber (a slice-1 seed)."""
    unii, _cas = _keys_of(parsed(), "D000082")
    assert unii == {"362O9ITL9D"}


def test_aspirin_shape_unii_in_registry_cas_only_in_related():
    """The fact issue #11 got wrong AND design tension B in one record: aspirin
    D001241 carries UNII R16CO5Y76E in <RegistryNumber>, with its CAS 50-78-2 only
    in <RelatedRegistryNumber>. The bridge reads RegistryNumber only, so the member
    key set is {UNII} with NO CAS -- the Related CAS is deliberately not a key."""
    unii, cas = _keys_of(parsed(), "D001241")
    assert unii == {"R16CO5Y76E"}
    assert cas == set()


def test_cas_fallback_member_has_cas_but_no_unii():
    """D008278 Magnesium Sulfate -- no UNII in MeSH, CAS in RegistryNumber. This is
    the member that makes the bridge two-key rather than UNII-only (spec §5.3)."""
    unii, cas = _keys_of(parsed(), "D008278")
    assert unii == set()
    assert "7487-88-9" in cas


def test_a_member_can_carry_several_uniis():
    """SCR C000002 bevonium exposes two UNIIs across its concepts -- key extraction
    is set-valued, so the bridge must try every one (spec §5.2)."""
    unii, _cas = _keys_of(parsed(), "C000002")
    assert {"34B0471E08", "UWC15E373Z"} <= unii


def test_no_key_member_exposes_neither():
    """SCR C007609 (aspirin/meprobamate combination) -- RegistryNumber '0' only.
    Structurally unjoinable; must still be emitted so the orchestrator can count it
    (never a silent drop, spec §5.3)."""
    unii, cas = _keys_of(parsed(), "C007609")
    assert unii == set() and cas == set()


# ---- the class DAG from tree-number nesting (spec §5.4) --------------------


def _parents_of(result):
    out = {c.descriptor_ui: set() for c in result.classes}
    for e in result.parents:
        out[e.child_ui].add(e.parent_ui)
    return out


def test_dag_orients_child_to_parent_with_a_multi_parent_node():
    """D000894 (NSAIDs) nests under THREE kept PA classes -- a real DAG node a tree
    could not express. The edge runs child -> parent."""
    assert _parents_of(parsed())["D000894"] == {"D000893", "D018501", "D018712"}


def test_dag_nests_past_one_level():
    """D000700 -> D018712 -> D000894: nesting deeper than a single level."""
    assert _parents_of(parsed())["D018712"] == {"D000700"}


def test_a_tree_parent_that_is_not_a_pa_class_yields_no_edge():
    """A tree-number parent outside the ingested PA classes drops the edge, so these
    four attach nowhere within the subset -- roots, not orphans (spec §5.4)."""
    parents = _parents_of(parsed())
    for root in ("D000700", "D000893", "D018501", "D012102"):
        assert parents[root] == set(), f"{root} should be a root in the subset"


def test_every_dag_endpoint_is_an_ingested_pa_class():
    """Both endpoints of every edge must be a class we ingested -- the same
    endpoint-scoping MED-RT uses to keep the DAG closed over the 568."""
    uis = {c.descriptor_ui for c in parsed().classes}
    for e in parsed().parents:
        assert e.child_ui in uis and e.parent_ui in uis


# ---- the SHARED tree-nesting rule (mesh.tree_parent_edges) ------------------
#
# drugref derives TWO DAGs from MeSH tree numbers -- slice 2b's PA class DAG
# (mesh._build_dag) and slice 5b's condition DAG (mesh_concepts.parent_edges) -- and
# they are the same rule wrapped in two different edge dataclasses. The rule itself
# now lives in ONE function and is tested here, once, on controlled input; the two
# wrappers keep their own tests against real fixtures. The tests below are what makes
# the shared rule safe to share: each pins a decision that is invisible in a passing
# end-to-end ingest but changes which edges exist.


def test_tree_parent_edges_links_only_the_immediate_tree_parent():
    """A grandchild attaches to its PARENT, never leapfrogging to the grandparent.
    Leapfrogging would invent hierarchy MeSH does not assert."""
    edges = mesh.tree_parent_edges(
        {"A": ("D01",), "B": ("D01.100",), "C": ("D01.100.200",)})
    assert edges == [("B", "A"), ("C", "B")]


def test_tree_parent_edges_needs_both_endpoints_in_the_ingested_set():
    """The immediate tree-parent is absent from the set, so the child is a ROOT of
    the ingested subset. It is NOT re-attached to the more distant ancestor that IS
    present -- that would assert a direct kinship the release never states."""
    assert mesh.tree_parent_edges({"A": ("D01",), "C": ("D01.100.200",)}) == []


def test_tree_parent_edges_never_emits_a_self_edge():
    """One record bearing both a tree number and its own tree-parent's number would
    otherwise become its own parent. db/013's condition_parent_not_self CHECK rejects
    that mid-ingest, so the guard turns a silent oddity into no edge at all."""
    assert mesh.tree_parent_edges({"A": ("D01", "D01.100")}) == []


def test_tree_parent_edges_dedupes_and_sorts():
    """Deterministic output: two tree numbers under one parent give ONE edge, and the
    order is sorted rather than whatever a set happened to iterate. Both DAG writers
    insert in this order, so a non-deterministic answer would make ingests differ."""
    edges = mesh.tree_parent_edges(
        {"P": ("D01",), "Z": ("D01.100", "D01.200"), "A": ("D01.300",)})
    assert edges == [("A", "P"), ("Z", "P")]


def test_tree_parent_edges_ignores_a_top_level_tree_number():
    """A single-segment tree number has no parent segment to strip, so it contributes
    nothing -- rather than being looked up under a truncated key that could collide."""
    assert mesh.tree_parent_edges({"A": ("D01",), "B": ("D02",)}) == []
