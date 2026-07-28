"""The condition registry's structural guarantees (slice 5b, db/013)."""
import uuid

import psycopg
import pytest

from drugref import ids


@pytest.fixture
def a_condition(conn, ingest_run_id):
    """One registered condition, for tests needing a live FK target."""
    cu = ids.mint_condition_uuid("MeSH", "D004827")
    conn.execute(
        "INSERT INTO drugref.condition (condition_uuid, source, source_code, name, "
        "record_kind, tree_numbers, first_seen_ingest) "
        "VALUES (%s, 'MeSH', 'D004827', 'Epilepsy', 'DESCRIPTOR', %s, %s)",
        (cu, ["C10.228.140.490"], ingest_run_id))
    return cu


def test_condition_round_trips(conn, a_condition):
    row = conn.execute(
        "SELECT name, record_kind, tree_numbers FROM drugref.condition "
        "WHERE condition_uuid = %s", (a_condition,)).fetchone()
    assert row == ("Epilepsy", "DESCRIPTOR", ["C10.228.140.490"])


def test_source_is_constrained(conn, ingest_run_id):
    """As db/003 constrains class sources: an unknown authority is refused, so a
    typo cannot open a parallel registry nothing reconciles."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) "
            "VALUES (%s, 'SNOMED', 'X', 'x', 'DESCRIPTOR', %s)",
            (uuid.uuid4(), ingest_run_id))


def test_record_kind_is_constrained(conn, ingest_run_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) "
            "VALUES (%s, 'MeSH', 'D1', 'x', 'QUALIFIER', %s)",
            (uuid.uuid4(), ingest_run_id))


def test_source_code_is_unique_per_source(conn, a_condition, ingest_run_id):
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) "
            "VALUES (%s, 'MeSH', 'D004827', 'dup', 'DESCRIPTOR', %s)",
            (uuid.uuid4(), ingest_run_id))


def test_condition_parent_requires_both_endpoints(conn, a_condition, ingest_run_id):
    """The DAG is closed over the registry: an edge to an unregistered condition is
    refused, which is what keeps un-ingested MeSH content out of the tree."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.condition_parent (child_condition_uuid, "
            "parent_condition_uuid, ingest_run) VALUES (%s, %s, %s)",
            (a_condition, uuid.uuid4(), ingest_run_id))


def test_condition_cannot_parent_itself(conn, a_condition, ingest_run_id):
    """Self-parenting is the one cycle a recursive walk cannot survive; db/002
    forbids it for classes and the same reasoning applies here."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.condition_parent (child_condition_uuid, "
            "parent_condition_uuid, ingest_run) VALUES (%s, %s, %s)",
            (a_condition, a_condition, ingest_run_id))


def test_condition_supports_multiple_parents(conn, a_condition, ingest_run_id):
    """1,690 of the 5,190 conditions in the real release have several parents, so
    the DAG must be many-to-many, never a single parent FK."""
    for code, name in (("D000001", "P1"), ("D000002", "P2")):
        cu = ids.mint_condition_uuid("MeSH", code)
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) VALUES (%s,'MeSH',%s,%s,"
            "'DESCRIPTOR',%s)", (cu, code, name, ingest_run_id))
        conn.execute(
            "INSERT INTO drugref.condition_parent (child_condition_uuid, "
            "parent_condition_uuid, ingest_run) VALUES (%s, %s, %s)",
            (a_condition, cu, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition_parent WHERE child_condition_uuid = %s",
        (a_condition,)).fetchone()[0] == 2
