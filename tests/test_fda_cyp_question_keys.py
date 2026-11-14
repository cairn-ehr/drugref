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

from drugref import gap_sources_interactions, questions
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


# Every withheld-cell gap_key the fixture mints, byte for byte. NEVER EDIT to make
# a failure pass: a change here means question UUIDs changed.
_GOLDEN_WITHHELD_KEYS = {
    "FDACYP:bupropion|CYP Strg INH|2D6",
    "FDACYP:bupropion|CYP SENS SUB|2B6",
    "FDACYP:cenobamate|CYP Mod INH|2C19",
    "FDACYP:cenobamate|CYP Mod IND|3A",
}


def test_every_role_key_branch_runs_in_postgres(conn):
    """The CASE built from ROLE_QUESTION_KEYS, run over all ten role tuples plus
    the subject half's all-NULL row. The fixture's withheld cells reach only four
    arms; this reaches the other six, including the transporter arms' `IS NULL`."""
    tuples = [*_FROZEN, (None, None, None)]
    values = ", ".join(["(%s::text, %s::text, %s::text)"] * len(tuples))
    rows = conn.execute(
        f"SELECT system, role, potency, {gap_sources_interactions._fda_cyp_role_key_sql()} "
        f"FROM (VALUES {values}) AS v(system, role, potency)",
        [part for t in tuples for part in t]).fetchall()
    assert {(s, r, p): key for s, r, p, key in rows} == {**_FROZEN, (None, None, None): ""}


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
    # Exact keys, not just "same as before": this pins the gap_key BYTES to the
    # format minted before db/074, which before==after alone cannot see.
    assert {key for key, _ in before} == _GOLDEN_WITHHELD_KEYS
    run_id = conn.execute(
        "SELECT max(ingest_run_id) FROM drugref.ingest_run WHERE source = 'FDA-CYP'"
    ).fetchone()[0]

    conn.execute("UPDATE drugref.fda_cyp_assertion "
                 "SET column_heading = 'Reworded ' || column_heading")
    questions.register_from_gaps(conn, run_id)

    assert _withheld_questions(conn) == before
