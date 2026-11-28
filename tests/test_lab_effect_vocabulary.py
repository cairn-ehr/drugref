"""db/089: the lab-effect vocabulary -- analytes, section grades, label terms (#294).

DB-gated. Pins the seeds exactly (they are a contract ReportTriage codes against),
the analyte floor (analyte_code is immortal), and the CHECK vocabularies.
Design: docs/superpowers/specs/2026-11-27-drugref-lab-effects-design.md section 6.
"""
import psycopg
import pytest


def test_the_seeded_analytes_are_the_test_set(conn):
    assert conn.execute(
        "SELECT analyte_code, mesh_ui, specimen FROM drugref.lab_analyte ORDER BY 1"
    ).fetchall() == [
        ("creatinine", "D003404", "blood"), ("haemoglobin", "D006454", "blood"),
        ("inr", "D019934", "blood"), ("neutrophils", "D009504", "blood"),
        ("platelets", "D001792", "blood"), ("potassium", "D011188", "blood"),
        ("sodium", "D012964", "blood")]


@pytest.mark.parametrize("statement", [
    "UPDATE drugref.lab_analyte SET display_name = 'K' WHERE analyte_code = 'potassium'",
    "DELETE FROM drugref.lab_analyte WHERE analyte_code = 'potassium'"])
def test_an_analyte_can_never_be_renamed_or_deleted(conn, statement):
    with pytest.raises(psycopg.errors.RaiseException, match="insert-only"):
        conn.execute(statement)


def _analyte(conn, code, specimen):
    conn.execute("INSERT INTO drugref.lab_analyte (analyte_code, mesh_ui, specimen, "
                 "display_name) VALUES (%s, 'D011188', %s, 'x')", (code, specimen))


def test_one_descriptor_may_name_one_analyte_per_specimen(conn):
    _analyte(conn, "potassium_urine", "urine")   # control: another specimen is allowed
    with pytest.raises(psycopg.errors.UniqueViolation):
        _analyte(conn, "potassium_dup", "blood")


def test_a_specimen_outside_the_vocabulary_is_refused(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        _analyte(conn, "potassium_csf", "csf")


def test_the_section_grade_order(conn):
    assert [s for (s,) in conn.execute(
        "SELECT section FROM drugref.lab_effect_section ORDER BY section_rank")] == [
        "boxed_warning", "warnings_and_cautions", "warnings", "precautions",
        "drug_and_or_laboratory_test_interactions", "laboratory_tests",
        "drug_interactions", "adverse_reactions", "adverse_reactions_table"]


def test_the_seeded_terms(conn):
    assert conn.execute(
        "SELECT term_id, condition_mesh_ui, analyte_code, direction, via_pathway "
        "FROM drugref.lab_effect_term ORDER BY 1").fetchall() == [
        ("acute_kidney_injury", "D058186", "creatinine", "high", False),
        ("agranulocytosis", "D000380", "neutrophils", "low", False),
        ("bleeding", "D006470", "haemoglobin", "low", True),
        ("hyperkalemia", "D006947", "potassium", "high", False),
        ("hypokalemia", "D007008", "potassium", "low", False),
        ("hyponatremia", "D007010", "sodium", "low", False),
        ("inr_increased", None, "inr", "high", False),
        ("neutropenia", "D009503", "neutrophils", "low", False),
        ("platelet_count_decreased", None, "platelets", "low", False),
        ("serum_creatinine_increased", None, "creatinine", "high", False),
        ("serum_potassium_decreased", None, "potassium", "low", False),
        ("serum_potassium_increased", None, "potassium", "high", False),
        ("siadh", "D007177", "sodium", "low", False),
        ("thrombocytopenia", "D013921", "platelets", "low", False)]


def test_a_term_direction_outside_the_vocabulary_is_refused(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.lab_effect_term (term_id, analyte_code, "
                     "direction, via_pathway) VALUES ('k_up', 'potassium', 'up', false)")
