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
    # 14 desc + 3 SCR records, minus the 2 keyless ones (D010406, D002511).
    assert len(parsed.placement_keys) == 15
    assert parsed.root_names["D010406"] == "Penicillins"
    assert parsed.desc_records == 14 and parsed.scr_records == 3


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
    """Minimal MeSH descriptor file: recs are (ui, tree, unii-or-None)."""
    body = "".join(
        f"<DescriptorRecord><DescriptorUI>{ui}</DescriptorUI>"
        f"<DescriptorName><String>n{ui}</String></DescriptorName>"
        f"<TreeNumberList><TreeNumber>{tree}</TreeNumber></TreeNumberList>"
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
