# tests/test_lab_effect_survey.py
"""The pure parts of `tools/lab_effect_survey.py` (issue #294's source survey).

The tool itself reads 14 openFDA partitions (~9 GB uncompressed) and cannot run
here; what is pinned is the grading rule every published figure rests on:
which label section counts as the STRONGEST statement of an effect.
"""
from tools.lab_effect_survey import EFFECTS, single_substance, strongest_section


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
