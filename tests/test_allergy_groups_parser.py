"""Pure tests (no DB) for the allergy-group allow-list loader and MeSH parser."""
import pathlib

import pytest

from drugref.ingest.allergy_groups import load_allow_list, parse

FIX = pathlib.Path(__file__).parent / "fixtures"
SHIPPED = pathlib.Path(__file__).parent.parent / "src/drugref/data/allergy_groups.toml"


@pytest.fixture(scope="module")
def parsed():
    return parse(desc_path=FIX / "allergy_desc_subset.xml",
                 supp_path=FIX / "allergy_supp_subset.xml",
                 groups=load_allow_list(SHIPPED))


def _vias(parsed):
    return {m.via for m in parsed.members}


def _all_unii(parsed):
    return set().union(*(m.keys.unii for m in parsed.members))


def test_load_allow_list_shipped():
    (g,) = load_allow_list(SHIPPED)
    assert g.root_ui == "D010406"
    assert len(g.exclude) == 4 and len(g.include) == 2


_GOOD = 'root = "D010406"\nreview_status = "structural-seed"\n'


@pytest.mark.parametrize("body", [
    '[[group]]\nroot = "D010406"\nreview_status = "bogus"\n',
    "[[group]]\n" + _GOOD + "[[group]]\n" + _GOOD,
    "[[group]]\n" + _GOOD + 'exclude = [{ ui = "X1", reason = "r" }]\n',
    "[[group]]\n" + _GOOD + 'include = [{ unii = "short", reason = "r" }]\n',
])
def test_load_allow_list_rejects(tmp_path, body):
    p = tmp_path / "a.toml"
    p.write_text(body)
    with pytest.raises(ValueError):
        load_allow_list(p)


def test_flucloxacillin_in_via_descriptor(parsed):
    assert any(m.via == "descriptor:D005436" and "43B2M34G2V" in m.keys.unii
               for m in parsed.members)


def test_root_itself_counts(parsed):
    assert "descriptor:D010406" in _vias(parsed)


def test_temocillin_in_via_scr(parsed):
    assert any(m.via == "scr:C031367" and "03QB156W6I" in m.keys.unii
               for m in parsed.members)


def test_excluded_never_member(parsed):
    assert not {"S4TF6I2330", "SE10G96M8W"} & _all_unii(parsed)
    assert not _vias(parsed) & {f"descriptor:{u}" for u in
                                ("D013407", "D000078142", "D010397",
                                                  "D019980")}
    # SCR mapped only to excluded sulbactam (sulbactam pivoxyl) is out too.
    assert "scr:C035975" not in _vias(parsed)


def test_outside_tree_not_member(parsed):
    assert not _vias(parsed) & {"descriptor:D020913", "descriptor:D002506",
                                "descriptor:D011343", "scr:C000002"}


def test_placement_covers_everything(parsed):
    # sulbactam pivoxyl 2X0WTA96KX and bevonium 34B0471E08 are SCRs, too.
    # One entry per keyed record, so each can be bridged UNII-first on its own.
    all_unii = set().union(*(k.unii for k in parsed.placement_keys))
    assert {"2X0WTA96KX", "34B0471E08", "03QB156W6I",
            "S4TF6I2330", "SE10G96M8W"} <= all_unii
    assert all(k.unii or k.cas for k in parsed.placement_keys)
    # Amoxicillin's record keeps its CAS beside its UNIIs, not merged into a union.
    assert any({"804826J2HU", "9EM05410Q9"} <= k.unii and "26889-93-0" in k.cas
               for k in parsed.placement_keys)
    # 15 desc + 3 SCR records, minus the 3 keyless ones (D010406, D002511 and
    # D001561, bevonium's heading, kept so every SCR heading is in the desc file).
    assert len(parsed.placement_keys) == 15
    assert parsed.root_names["D010406"] == "Penicillins"
    assert parsed.desc_records == 15 and parsed.scr_records == 3


@pytest.mark.parametrize("body, missing", [
    ('[[group]]\nroot = "D999999"\nreview_status = "structural-seed"\n', "D999999"),
    ("[[group]]\n" + _GOOD + 'exclude = [{ ui = "D999998", reason = "typo" }]\n',
     "D999998"),
])
def test_parse_refuses_a_ui_absent_from_the_release(tmp_path, body, missing):
    # A typo'd exclusion must not silently admit the inhibitor it meant to remove.
    p = tmp_path / "a.toml"
    p.write_text(body)
    with pytest.raises(ValueError, match=missing):
        parse(desc_path=FIX / "allergy_desc_subset.xml",
              supp_path=FIX / "allergy_supp_subset.xml", groups=load_allow_list(p))


def _desc_xml(*recs):
    """Minimal MeSH descriptor file: recs are (ui, tree-or-trees, unii-or-None)."""
    body = "".join(
        f"<DescriptorRecord><DescriptorUI>{ui}</DescriptorUI>"
        f"<DescriptorName><String>n{ui}</String></DescriptorName>"
        "<TreeNumberList>"
        + "".join(f"<TreeNumber>{t}</TreeNumber>"
                  for t in ((tree,) if isinstance(tree, str) else tree))
        + "</TreeNumberList>"
        + (f"<ConceptList><Concept><RegistryNumber>{unii}</RegistryNumber></Concept>"
           "</ConceptList>" if unii else "") + "</DescriptorRecord>"
        for ui, tree, unii in recs)
    return f"<DescriptorRecordSet>{body}</DescriptorRecordSet>"


def test_descriptor_strictly_below_an_excluded_one_is_not_member(tmp_path):
    # The subtree half of exclusion: a child of an excluded descriptor goes with it.
    d, s, a = tmp_path / "d.xml", tmp_path / "s.xml", tmp_path / "a.toml"
    d.write_text(_desc_xml(("D000001", "D01.1", None),
                           ("D000002", "D01.1.1", None),          # excluded
                           ("D000003", "D01.1.1.1", "AAAAAAAAAA"),  # below it
                           ("D000004", "D01.1.2", "BBBBBBBBBB")))   # sibling: stays
    s.write_text("<SupplementalRecordSet/>")
    a.write_text('[[group]]\nroot = "D000001"\nreview_status = "structural-seed"\n'
                 'exclude = [{ ui = "D000002", reason = "r" }]\n')
    got = parse(desc_path=d, supp_path=s, groups=load_allow_list(a))
    assert _vias(got) == {"descriptor:D000001", "descriptor:D000004"}


# --- Review round (PR #225): mistakes that used to pass silently ------------------

@pytest.mark.parametrize("body", [
    "",                                                   # zero groups: wipes them all
    "[[groups]]\n" + _GOOD,                               # misspelt table: zero groups
    "[[group]]\n" + _GOOD + 'exlude = [{ ui = "D013407", reason = "r" }]\n',
    "[[group]]\n" + _GOOD + 'exclude = [{ ui = "D013407", reasn = "r" }]\n',
    'title = "x"\n[[group]]\n' + _GOOD,                   # unknown top-level key
])
def test_load_allow_list_refuses_empty_or_unknown_keys(tmp_path, body):
    # Each of these used to load as "no groups" or "no exclusions" and run to exit 0.
    p = tmp_path / "a.toml"
    p.write_text(body)
    with pytest.raises(ValueError):
        load_allow_list(p)


@pytest.mark.parametrize("excluded", ["D010406",    # the root itself: empties the group
                                      "D020913"])   # perindopril, outside: a no-op
def test_parse_refuses_an_exclude_not_strictly_below_the_root(tmp_path, excluded):
    p = tmp_path / "a.toml"
    p.write_text("[[group]]\n" + _GOOD + f'exclude = [{{ ui = "{excluded}", '
                 'reason = "r" }]\n')
    with pytest.raises(ValueError, match=excluded):
        parse(desc_path=FIX / "allergy_desc_subset.xml",
              supp_path=FIX / "allergy_supp_subset.xml", groups=load_allow_list(p))


def _supp_xml(*recs, ns=""):
    """Minimal MeSH supp file: recs are (ui, heading UIs, unii, indexing UIs)."""
    body = "".join(
        f"<SupplementalRecord><SupplementalRecordUI>{ui}</SupplementalRecordUI>"
        "<HeadingMappedToList>"
        + "".join(f"<HeadingMappedTo><DescriptorReferredTo><DescriptorUI>{h}"
                  "</DescriptorUI></DescriptorReferredTo></HeadingMappedTo>"
                  for h in heads)
        + "</HeadingMappedToList>"
        + "".join(f"<IndexingInformationList><IndexingInformation><DescriptorReferredTo>"
                  f"<DescriptorUI>{i}</DescriptorUI></DescriptorReferredTo>"
                  "</IndexingInformation></IndexingInformationList>" for i in indexing)
        + f"<ConceptList><Concept><RegistryNumber>{unii}</RegistryNumber></Concept>"
        "</ConceptList></SupplementalRecord>"
        for ui, heads, unii, indexing in recs)
    return f'<SupplementalRecordSet{ns}>{body}</SupplementalRecordSet>'


_TINY_DESC = (("D000001", "D01.1", None),             # the root
              ("D000005", "D02.1", "CCCCCCCCCC"))     # outside the tree
_TINY_ALLOW = '[[group]]\nroot = "D000001"\nreview_status = "structural-seed"\n'


def _tiny(tmp_path, supp, desc=_TINY_DESC, allow=_TINY_ALLOW):
    d, s, a = tmp_path / "d.xml", tmp_path / "s.xml", tmp_path / "a.toml"
    d.write_text(_desc_xml(*desc))
    s.write_text(supp)
    a.write_text(allow)
    return parse(desc_path=d, supp_path=s, groups=load_allow_list(a))


def test_indexing_information_never_makes_an_scr_a_member(tmp_path):
    # Mapped outside the tree, INDEXED against the root: placed, but not a member.
    got = _tiny(tmp_path, _supp_xml(("C1", ["*D000005"], "DDDDDDDDDD", ["D000001"])))
    assert "scr:C1" not in _vias(got)
    assert any("DDDDDDDDDD" in k.unii for k in got.placement_keys)


def test_scr_membership_survives_an_xml_namespace(tmp_path):
    # iter_records/texts match on local names; the HeadingMappedTo walk must too, or
    # a namespaced release would drop every SCR member (a missed allergy warning).
    got = _tiny(tmp_path, _supp_xml(("C1", ["*D000001"], "DDDDDDDDDD", []),
                                    ns=' xmlns="urn:example:mesh"'))
    assert "scr:C1" in _vias(got)


def test_parse_refuses_an_scr_heading_absent_from_the_desc_file(tmp_path):
    # desc and supp from different releases: the SCR would be placed but could not
    # be a member, answering no-match. A consistent release has none (MeSH 2026: 0).
    with pytest.raises(ValueError, match="C1"):
        _tiny(tmp_path, _supp_xml(("C1", ["*D009999"], "DDDDDDDDDD", [])))


def test_descriptor_with_several_trees(tmp_path):
    # Exclusion wins if ANY tree is under an excluded one; a tree elsewhere is harmless.
    desc = (("D000001", "D01.1", None),
            ("D000002", "D01.1.1", None),                         # excluded
            ("D000003", ("D01.1.2", "D01.1.1.5"), "AAAAAAAAAA"),  # in AND under excluded
            ("D000004", ("D01.1.3", "D09.9"), "BBBBBBBBBB"))      # in AND elsewhere
    got = _tiny(tmp_path, "<SupplementalRecordSet/>", desc=desc,
                allow=_TINY_ALLOW + 'exclude = [{ ui = "D000002", reason = "r" }]\n')
    assert _vias(got) == {"descriptor:D000001", "descriptor:D000004"}
