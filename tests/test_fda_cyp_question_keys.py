# tests/test_fda_cyp_question_keys.py
"""Issue 134, second half: an FDA-CYP question's identity keys on the column's ROLE.

question_uuid = uuid5(gap_kind, gap_key) is immortal and externally citable. Until
db/074 the withheld-cell gap_key embedded FDA's PRINTED column heading, so a
rewording FDA made -- even one drugref accepted deliberately by updating
fda_cyp_cell.ROLE_COLUMN_HEADINGS -- retired every withheld question and minted
new ones. The key now carries a frozen per-role label instead
(fda_cyp_cell.ROLE_QUESTION_KEYS), and the printed heading is evidence only.
"""
import pathlib

from drugref import questions
from drugref.ingest import fda_cyp_cell, fda_cyp_run
# Reused, not copied: the ingest COMMITS, so these autouse fixtures seed the two
# moieties a withheld cell needs (bupropion, cenobamate) and clean up after.
from tests.test_fda_cyp_run import _clean, _registry  # noqa: F401

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "fda_cyp_table.html"

# The frozen labels, spelled out once more ON PURPOSE: this is the golden copy.
# They are FDA's headings as printed on the 2026-05-29 release, which is what
# every gap_key minted before db/074 already carries, so no existing
# question_uuid changes. NEVER EDIT THIS DICT: if this test fails, question
# UUIDs have changed.
_FROZEN = {
    ("CYP", "inhibitor", "strong"): "CYP Strg INH",
    ("CYP", "inhibitor", "moderate"): "CYP Mod INH",
    ("CYP", "inhibitor", "weak"): "CYP WK INH",
    ("CYP", "inducer", "strong"): "CYP Strg IND",
    ("CYP", "inducer", "moderate"): "CYP Mod IND",
    ("CYP", "inducer", "weak"): "CYP WK IND",
    ("CYP", "substrate", "sensitive"): "CYP SENS SUB",
    ("CYP", "substrate", "moderate sensitive"): "CYP Mod SENS SUB",
    ("transporter", "inhibitor", None): "TRNSP INH",
    ("transporter", "substrate", None): "TRNSP SUB",
}


def test_the_question_key_labels_are_frozen():
    assert fda_cyp_cell.ROLE_QUESTION_KEYS == _FROZEN


def test_every_role_column_has_a_question_key():
    assert set(fda_cyp_cell.ROLE_QUESTION_KEYS) == set(fda_cyp_cell.ROLE_COLUMNS.values())


def _withheld_questions(conn) -> set[tuple[str, object]]:
    return set(conn.execute(
        "SELECT gap_key, question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated' AND gap_key NOT LIKE '%||'"
    ).fetchall())


def test_a_reworded_heading_keeps_every_question_uuid(conn):
    """Simulates FDA rewording every heading AND drugref accepting it: the stored
    column_heading changes, the role does not, so no question may change."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    before = _withheld_questions(conn)
    assert before, "the fixture must still produce withheld-cell questions"
    run_id = conn.execute(
        "SELECT max(ingest_run_id) FROM drugref.ingest_run WHERE source = 'FDA-CYP'"
    ).fetchone()[0]

    conn.execute("UPDATE drugref.fda_cyp_assertion "
                 "SET column_heading = 'Reworded ' || column_heading")
    questions.register_from_gaps(conn, run_id)

    assert _withheld_questions(conn) == before
