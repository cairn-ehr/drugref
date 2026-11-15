"""Pure tests for the class match keys (no DB)."""
from drugref.ingest import spl_match
from drugref.ingest.spl_class_vocab import (
    FORM_LABEL_ORDER, FORM_NUMBER, FORM_STORED, class_match_keys,
    fda_cyp_label_orders, is_matchable, number_variants, strip_axis_tag,
    unused_exclusions)


def test_strip_axis_tag():
    assert strip_axis_tag("Calcium Channel Blocker [EPC]") == "Calcium Channel Blocker"
    assert strip_axis_tag("CYP1A2 strong inhibitor [FDA-CYP]") == "CYP1A2 strong inhibitor"


def test_number_variants_both_directions():
    assert number_variants("Diuretics") == ("Diuretic",)
    assert number_variants("Diuretic") == ("Diuretics",)


def test_fda_cyp_label_orders_inhibitor_and_inducer():
    assert fda_cyp_label_orders("CYP1A2 strong inhibitor") == (
        "strong CYP1A2 inhibitor", "strong CYP1A2 inhibitors")
    assert fda_cyp_label_orders("CYP3A moderate inducer") == (
        "moderate CYP3A inducer", "moderate CYP3A inducers")


def test_fda_cyp_label_orders_skips_substrates_and_unbanded():
    assert fda_cyp_label_orders("CYP1A2 moderate sensitive substrate") == ()
    assert fda_cyp_label_orders("P-gp inhibitor") == ()


def test_is_matchable_excludes_only_medrt_pk_apc_and_pe():
    assert not is_matchable("MED-RT", "PK")
    assert not is_matchable("MED-RT", "PE")
    assert not is_matchable("MED-RT", "APC")
    assert is_matchable("FDA-CYP", "PK") and is_matchable("MeSH", "PA")
    assert is_matchable("MED-RT", "EPC")


def test_medrt_apc_is_never_matched():
    """All APC classes are empty by construction and are generic words (Enzyme,
    Hormone) that duplicate a MeSH name -- the #155 PK-axis reasoning (R16)."""
    assert not is_matchable("MED-RT", "APC")


def test_class_match_keys_fda_cyp():
    keys = class_match_keys("CYP1A2 strong inhibitor [FDA-CYP]", "FDA-CYP")
    assert keys[0] == ("CYP1A2 strong inhibitor", FORM_STORED)
    assert ("strong CYP1A2 inhibitors", FORM_LABEL_ORDER) in keys
    assert ("CYP1A2 strong inhibitors", FORM_NUMBER) in keys


def test_class_match_keys_no_label_order_outside_fda_cyp():
    assert {f for _, f in class_match_keys("Diuretics", "MeSH")} == {FORM_STORED, FORM_NUMBER}


def test_class_match_keys_dedupes_by_fold():
    # No real class name collides, so this uses a degenerate FDA-CYP name whose
    # "enzyme" equals its band: the label-order rewrite of
    # "strong strong inhibitor" is that same string, a genuine fold collision.
    name = "strong strong inhibitor [FDA-CYP]"
    raw = ([strip := "strong strong inhibitor"]
           + list(number_variants(strip))
           + list(fda_cyp_label_orders(strip)))
    keys = class_match_keys(name, "FDA-CYP")
    distinct = {spl_match.fold(k) for k in raw}
    assert len(keys) == len(distinct) < len(raw)
    assert keys[0] == (strip, FORM_STORED)  # first derivation wins the collision


def test_class_match_keys_empty_base_yields_nothing():
    # '[EPC]' strips to '' and number_variants('') would be 's'.
    assert class_match_keys("[EPC]", "MED-RT") == ()


def test_the_named_exclusions():
    """R19/P3: 'Hormones' (MeSH PA), the twin of the excluded APC 'Hormone' (4/10 on
    run 11). #214: 'Electrolyte [EPC]' (empty class; physiology: 'electrolyte
    depletion') and 'Ophthalmic Solutions' (MeSH PA; product names: 'timolol maleate
    ophthalmic solution'). A named set, so each addition is a visible, reviewed edit."""
    from drugref.ingest.spl_class_vocab import EXCLUDED_CLASSES
    assert EXCLUDED_CLASSES == frozenset({
        ("MeSH", "PA", "Hormones"),
        ("MED-RT", "EPC", "Electrolyte [EPC]"),
        ("MeSH", "PA", "Ophthalmic Solutions")})
    for excluded in EXCLUDED_CLASSES:
        assert not is_matchable(*excluded)
    # Only those exact classes: their neighbours and namesakes on other axes still match.
    assert is_matchable("MeSH", "PA", "Hormone Antagonists")
    assert is_matchable("MED-RT", "EPC", "Hormones")
    assert is_matchable("MeSH", "PA")  # the name is optional for axis-only callers


def test_medrt_pe_is_never_matched_except_antacid():
    """#214: PE names are physiological effects ('Decreased Blood Pressure [PE]'), so
    a match is an effect phrase, not a drug group. 'Antacid [PE]' is kept: 3 of its 6
    members (algeldrate, aluminum carbonate, ...) are not in MeSH 'Antacids'."""
    assert not is_matchable("MED-RT", "PE", "Decreased Blood Pressure [PE]")
    assert not is_matchable("MED-RT", "PE", "Neuromuscular Blockade [PE]")
    assert not is_matchable("MED-RT", "PE")
    assert is_matchable("MED-RT", "PE", "Antacid [PE]")


def test_an_allergy_group_is_not_matchable():
    # 'AG' classes are drugref's own structural seeds, not a vocabulary labels write.
    assert not is_matchable("DRUGREF-ALLERGY", "AG")
    assert not is_matchable("DRUGREF-ALLERGY", "AG", "Penicillins (allergy group)")


def test_unused_exclusions_names_every_named_entry_no_class_row_carries():
    """#248: `EXCLUDED_CLASSES` and `KEPT_PE_CLASSES` are keyed by exact name, so a
    renamed class makes its entry match nothing and no signal fires: a renamed
    'Electrolyte [EPC]' brings its ~2,349 false spans back, a renamed 'Antacid [PE]'
    drops antacid advice for algeldrate and two more. A row on another axis or
    source is not the entry (the namesake check `is_matchable` makes too)."""
    rows = [("MeSH", "PA", "Hormones"), ("MED-RT", "EPC", "Electrolyte [EPC]"),
            ("MED-RT", "PE", "Antacid [PE]"), ("MED-RT", "EPC", "Ophthalmic Solutions")]
    assert unused_exclusions(rows) == ("Ophthalmic Solutions",)
    assert unused_exclusions(rows + [("MeSH", "PA", "Ophthalmic Solutions")]) == ()
    assert unused_exclusions([]) == (
        "Antacid [PE]", "Electrolyte [EPC]", "Hormones", "Ophthalmic Solutions")
