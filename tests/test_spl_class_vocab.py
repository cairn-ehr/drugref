"""Pure tests for the class match keys (no DB)."""
from drugref.ingest import spl_match
from drugref.ingest.spl_class_vocab import (
    FORM_LABEL_ORDER, FORM_NUMBER, FORM_STORED, class_match_keys,
    fda_cyp_label_orders, is_matchable, number_variants, strip_axis_tag)


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


def test_is_matchable_excludes_only_medrt_pk():
    assert not is_matchable("MED-RT", "PK")
    assert is_matchable("FDA-CYP", "PK") and is_matchable("MeSH", "PA")
    assert is_matchable("MED-RT", "EPC")


def test_class_match_keys_fda_cyp():
    keys = class_match_keys("CYP1A2 strong inhibitor [FDA-CYP]", "FDA-CYP")
    assert keys[0] == ("CYP1A2 strong inhibitor", FORM_STORED)
    assert ("strong CYP1A2 inhibitors", FORM_LABEL_ORDER) in keys
    assert ("CYP1A2 strong inhibitors", FORM_NUMBER) in keys


def test_class_match_keys_no_label_order_outside_fda_cyp():
    assert {f for _, f in class_match_keys("Diuretics", "MeSH")} == {FORM_STORED, FORM_NUMBER}


def test_class_match_keys_dedupes_by_fold():
    keys = class_match_keys("Thiazides [EPC]", "MED-RT")
    assert len({spl_match.fold(k) for k, _ in keys}) == len(keys)
