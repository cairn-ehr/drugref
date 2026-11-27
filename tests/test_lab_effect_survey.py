# tests/test_lab_effect_survey.py
"""The pure parts of `tools/lab_effect_survey.py` (issue #294's source survey).

The real run reads 14 openFDA partitions (~9 GB uncompressed) and cannot run
here; what is pinned is the grading rule every published figure rests on (which
label section counts as the STRONGEST statement of an effect), what each effect
pattern does and does not match, and the per-substance aggregation, run over a
one-partition zip built in `tmp_path`.
"""
import json
import zipfile

from tools.lab_effect_survey import (
    EFFECTS,
    main,
    single_substance,
    skip_reason,
    strongest_section,
    survey,
)


def _label(**sections):
    """An openFDA record carries each section as a LIST of strings."""
    return {key: [text] for key, text in sections.items()}


def test_the_boxed_warning_outranks_the_adverse_reaction_list():
    record = _label(
        adverse_reactions="Hyperkalemia occurred in 2% of patients.",
        boxed_warning="Fatal hyperkalemia has been reported.",
    )
    assert strongest_section(record, EFFECTS["potassium high"]) == "boxed_warning"


def test_a_label_that_never_names_the_effect_has_no_section():
    record = _label(adverse_reactions="Headache, dizziness.")
    assert strongest_section(record, EFFECTS["potassium high"]) is None


def test_the_interference_section_is_not_a_physiological_grade():
    """A false laboratory reading is the fourth role (ReportTriage#36), so the
    section that states it must never be read as the drug CAUSING the change."""
    record = _label(drug_and_or_laboratory_test_interactions=(
        "May cause falsely elevated serum potassium on some analysers."))
    assert strongest_section(record, EFFECTS["potassium high"]) is None


def test_hyponatraemia_is_matched_through_siadh_too():
    record = _label(warnings_and_cautions="Cases of SIADH have been reported.")
    assert strongest_section(record, EFFECTS["sodium low"]) == "warnings_and_cautions"


def test_single_substance_reads_openfdas_substance_list():
    assert single_substance({"openfda": {"substance_name": ["SPIRONOLACTONE"]}}) == "spironolactone"
    assert single_substance({"openfda": {"substance_name": ["A", "B"]}}) is None
    assert single_substance({}) is None


# --- each fixed pattern: one true positive and the false positive it now rejects


def _mentions(effect: str, text: str) -> bool:
    return strongest_section(_label(warnings=text), EFFECTS[effect]) is not None


def test_a_potassium_salt_in_the_product_name_is_not_a_potassium_effect():
    """Losartan potassium and diclofenac potassium are test-set drugs: matching
    the salt word would grade them on their own name, not on an effect."""
    assert not _mentions("potassium high", "Losartan potassium tablets may increase the risk.")
    assert not _mentions("potassium low", "Losartan potassium lowers blood pressure.")


def test_potassium_is_read_in_both_word_orders():
    assert _mentions("potassium high", "Serum potassium may be elevated.")
    assert _mentions("potassium high", "May cause an increase in serum potassium.")
    assert _mentions("potassium low", "Low serum potassium has been reported.")
    assert _mentions("potassium low", "Decreases in blood potassium occurred.")


def test_creatinine_clearance_dosing_text_is_not_a_creatinine_rise():
    assert not _mentions("creatinine high", (
        "In patients with creatinine clearance below 30 mL/min, increase the interval."))
    assert not _mentions("creatinine high", "creatinine kinase elevations")
    assert _mentions("creatinine high", "Increases in serum creatinine were observed.")


def test_platelet_aggregation_is_not_a_low_platelet_count():
    assert not _mentions("platelets low", "Inhibits platelet aggregation; low-dose aspirin.")
    assert _mentions("platelets low", "Platelet counts decreased in 3% of patients.")


def test_inr_reads_no_direction_by_design():
    """Pinned, not a bug: for the warfarin role any INR mention counts."""
    assert _mentions("INR high", "A decreased INR may occur.")


# --- skip reasons: an unkeyed label is not a combination product


def test_each_skip_reason_is_told_apart():
    rx = {"product_type": ["HUMAN PRESCRIPTION DRUG"]}
    assert skip_reason({}) == "no_openfda_block"
    assert skip_reason({"openfda": {"product_type": ["HUMAN OTC DRUG"],
                                    "substance_name": ["X"]}}) == "not_prescription"
    assert skip_reason({"openfda": rx}) == "no_substance_name"
    assert skip_reason({"openfda": {**rx, "substance_name": ["A", "B"]}}) == "combination"
    assert skip_reason({"openfda": {**rx, "substance_name": ["A"]}}) is None


# --- survey(): the per-substance aggregation every published figure rests on


def _rx(substance, **sections):
    return {"openfda": {"product_type": ["HUMAN PRESCRIPTION DRUG"],
                        "substance_name": [substance]}, **_label(**sections)}


def _partition(tmp_path, records):
    path = tmp_path / "part.json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("part.json", json.dumps({"results": records}))
    return path


def test_survey_grades_a_substance_by_its_strongest_label(tmp_path):
    path = _partition(tmp_path, [
        _rx("LOSARTAN POTASSIUM", adverse_reactions="Hyperkalemia occurred."),
        _rx("LOSARTAN POTASSIUM", boxed_warning="Hyperkalemia may be fatal."),
        {"openfda": {"product_type": ["HUMAN OTC DRUG"], "substance_name": ["IBUPROFEN"]},
         **_label(boxed_warning="Hyperkalemia.")},
        {"openfda": {"product_type": ["HUMAN PRESCRIPTION DRUG"],
                     "substance_name": ["A", "B"]}, **_label(boxed_warning="Hyperkalemia.")},
        _label(boxed_warning="Hyperkalemia."),
    ])
    result = survey([path])
    assert result["records_read"] == 5
    assert result["single_substance_rx_labels"] == 2
    assert result["single_substance_rx_substances"] == 1
    assert result["skipped"] == {"no_openfda_block": 1, "not_prescription": 1,
                                 "no_substance_name": 0, "combination": 1}
    assert result["unkeyed_with_prescription_sections"] == 1
    # counted once per SUBSTANCE, at its strongest section across both labels
    assert result["by_effect"]["potassium high"]["any_mention"] == 1
    assert result["by_effect"]["potassium high"]["by_strongest_section"]["boxed_warning"] == 1
    losartan = result["test_set"][0]["drugs"]["losartan"]
    assert losartan == {"substances": ["losartan potassium"], "strongest": "boxed_warning"}
    # a prefix with no kept substance says so, distinct from "no mention"
    assert result["test_set"][0]["drugs"]["lisinopril"] == {"substances": [],
                                                           "strongest": None}


def test_main_refuses_a_partition_given_twice(tmp_path):
    path = str(_partition(tmp_path, [_rx("X")]))
    assert main([path, path]) == 2


def test_main_refuses_a_run_that_never_saw_a_ranked_section(tmp_path):
    """Zeros from a misspelt field name must not pass for real zeros."""
    assert main([str(_partition(tmp_path, [_rx("X", warnings="None.")]))]) == 1
