"""db/091: the SPL_LAB label-reading storage, empty until #294 step 3.

DB-gated. Pins the source separation (SPL_LAB is not SPL, so neither ingest's
rebuild can delete the other's rows) and the occurrence table's CHECKs, which keep a
machine reading's fields from contradicting each other.
"""
import psycopg
import pytest

from drugref import provenance
from tests import class_profile_world as w
from tests import lab_effect_world as lw


@pytest.fixture
def worded(conn):
    run = lw.lab_run(conn)
    lw.label(conn, run, "set-1", None)
    lw.section(conn, run, "set-1", "warnings", "k1")
    return conn, run


def test_an_spl_lab_run_is_accepted(worded):
    conn, run = worded
    assert conn.execute("SELECT source, writer FROM drugref.ingest_run "
                        "WHERE ingest_run_id = %s", (run,)).fetchone() == (
        "SPL_LAB", "spl_lab_run")


def test_the_writer_is_registered_in_python_too():
    assert "spl_lab_run" in provenance.WRITERS


def test_an_spl_lab_table_refuses_the_ddi_source(worded):
    conn, run = worded
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.spl_lab_label (ingest_run, source, set_id, "
                     "version) VALUES (%s, 'SPL', 'set-2', '1')", (run,))


def test_a_reading_round_trips(worded):
    conn, run = worded
    lw.reading(conn, run, "k1", "hyperkalemia")
    assert conn.execute(
        "SELECT term_id, direction_read, role_read, role_rule "
        "FROM drugref.spl_lab_effect_occurrence").fetchall() == [
        ("hyperkalemia", "high", "causes", "test.rule")]


def _occurrence(conn, run, **overrides):
    row = dict(char_start=0, char_end=10, term_id="hyperkalemia", direction_read="high",
               role_read="causes", role_rule="r", co_medication_moiety_uuid=None,
               co_medication_class_uuid=None, scope="sentence", scope_start=0,
               scope_end=50) | overrides
    conn.execute(
        "INSERT INTO drugref.spl_lab_effect_occurrence (ingest_run, source, text_key, "
        + ", ".join(row) + ") VALUES (%s, 'SPL_LAB', 'k1', "
        + ", ".join(["%s"] * len(row)) + ")", (run, *row.values()))


@pytest.mark.parametrize("bad", [
    {"role_read": "not_read"},                  # not_read with a rule
    {"role_rule": None},                         # a strength role with no rule
    {"role_read": "precondition"},               # outside the vocabulary
    {"direction_read": "up"},
    {"scope_start": 5},                          # the scope misses the span
    {"scope": "paragraph"},
    {"char_end": 0},                             # an empty span
])
def test_the_occurrence_checks_refuse(worded, bad):
    conn, run = worded
    with pytest.raises(psycopg.errors.CheckViolation):
        _occurrence(conn, run, **bad)


def test_a_reading_may_name_only_one_co_medication(worded):
    conn, run = worded
    r = w.run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        _occurrence(conn, run, co_medication_moiety_uuid=w.moiety(conn, r, "warfarin"),
                    co_medication_class_uuid=w.klass(conn, r, "Anticoagulants"))


def test_not_read_with_no_rule_and_not_an_effect_are_accepted(worded):
    conn, run = worded
    _occurrence(conn, run, role_read="not_read", role_rule=None)
    _occurrence(conn, run, char_start=20, char_end=30, scope_start=20, scope_end=60,
                role_read="not_an_effect", role_rule="precondition.do_not_use")
