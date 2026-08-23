# tests/test_drugcentral_writer.py
"""interactions.py's DrugCentral half: one insert, one per-source clear.

The clear is what makes "rebuildable projection" true. An assertion retracted
upstream must be able to DISAPPEAR, which an insert-only merge can never express.
"""
import pytest

from drugref import interactions


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _moiety(conn, run, name):
    """A gated-in moiety to resolve an endpoint onto. Returns its uuid.

    Takes `run` and writes it into `first_seen_ingest`: that column is
    `NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)` with no default, so a
    two-argument insert of only (moiety_uuid, display_name) raises
    NotNullViolation. Copies the approach test_drugcentral_schema.py's
    `_a_moiety`, test_drugcentral_read_path.py's `_moiety` and
    test_drugcentral_gap.py already use -- the live schema is the authority, not
    the brief that predates this column being read from it.
    """
    return conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (gen_random_uuid(), %s, %s) RETURNING moiety_uuid",
        (name, run)).fetchone()[0]


def _write(conn, run, key, one=None, two=None, route="unresolved"):
    return interactions.add_drugcentral_assertion(
        conn, ingest_run_id=run, source="DRUGCENTRAL", upstream_key=key,
        endpoint_1_name="one", endpoint_2_name="two",
        upstream_label="ONE/TWO [VA Drug Interaction]", severity_label="Critical",
        moiety_1_uuid=one, moiety_2_uuid=two,
        route_1="display_name" if one else route,
        route_2="display_name" if two else route)


@pytest.mark.usefixtures("conn")
def test_a_row_is_written_once(conn):
    run, a, b = _run(conn), None, None
    a = _moiety(conn, run, "a")
    b = _moiety(conn, run, "b")
    assert _write(conn, run, "C56.1", a, b) is True
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (1,)


@pytest.mark.usefixtures("conn")
def test_repeating_one_upstream_key_is_harmless(conn):
    """ON CONFLICT DO NOTHING, matching every sibling writer: a dump that repeats
    an assertion must not abort an ingest halfway through."""
    run = _run(conn)
    a = _moiety(conn, run, "a")
    b = _moiety(conn, run, "b")
    assert _write(conn, run, "C56.1", a, b) is True
    assert _write(conn, run, "C56.1", a, b) is False
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (1,)


@pytest.mark.usefixtures("conn")
def test_an_unresolved_row_is_written_with_null_uuids(conn):
    run = _run(conn)
    assert _write(conn, run, "C56.2") is True
    row = conn.execute(
        "SELECT moiety_1_uuid, moiety_2_uuid, route_1 "
        "FROM drugref.drugcentral_ddi_assertion").fetchone()
    assert row == (None, None, "unresolved")


@pytest.mark.usefixtures("conn")
def test_the_clear_is_per_source_and_covers_the_whole_projection(conn):
    run = _run(conn)
    a = _moiety(conn, run, "a")
    b = _moiety(conn, run, "b")
    _write(conn, run, "C56.1", a, b)
    interactions.clear_source_drugcentral(conn, "DRUGCENTRAL")
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (0,)


def test_the_projection_tuple_is_restated_independently():
    """Pinned by name, as every sibling table tuple is: dropping a table from one
    of these leaves a projection that grows a little on every ingest."""
    assert interactions.DRUGCENTRAL_TABLES == ("drugcentral_ddi_assertion",)
