# tests/test_composition_gap.py
"""Gap kind 12: a composite whose active component nobody has ruled on."""
import pytest

from drugref import composition, ids, questions


@pytest.fixture
def gsrs_run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('GSRS', '2026-02-26', 'test', 'gsrs_run') "
        "RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def component(conn, gsrs_run):
    moiety_uuid = ids.mint_moiety_uuid("COMPONENT1")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (moiety_uuid, "Component One", gsrs_run))
    return moiety_uuid


def test_an_unruled_composite_becomes_a_question(conn, gsrs_run, component):
    composition.add_composition(
        conn, substance_unii="UNRULED001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)

    questions.register_from_gaps(conn, gsrs_run)

    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()
    assert row is not None, "gap kind 12 produced no question"
    assert row[0] == "SUBSTANCE:UNRULED001"
    assert "UNRULED001" in row[1]


def test_a_ruled_composite_raises_no_question(conn, gsrs_run, component):
    composition.add_composition(
        conn, substance_unii="RULED00001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=True, ingest_run_id=gsrs_run)

    questions.register_from_gaps(conn, gsrs_run)

    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()[0] == 0


def test_the_question_uuid_is_stable_across_rebuilds(conn, gsrs_run, component):
    """question_uuid is immortal and externally citable. register_from_gaps takes
    the run that re-derived the register, so a second call passes the same id."""
    composition.add_composition(
        conn, substance_unii="UNRULED001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)
    questions.register_from_gaps(conn, gsrs_run)
    first = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()[0]
    questions.register_from_gaps(conn, gsrs_run)
    second = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()[0]
    assert first == second
