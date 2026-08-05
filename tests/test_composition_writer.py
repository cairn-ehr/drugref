# tests/test_composition_writer.py
"""composition.py is the ONLY module that writes substance_composition."""
import pytest

from drugref import composition, ids


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
    conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'UNII', 'COMPONENT1', %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, gsrs_run))
    return moiety_uuid


def test_add_composition_writes_a_row(conn, gsrs_run, component):
    assert composition.add_composition(
        conn, substance_unii="SALT000001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=True,
        ingest_run_id=gsrs_run) is True
    row = conn.execute(
        "SELECT substance_unii, is_active_component FROM drugref.substance_composition"
    ).fetchone()
    assert row == ("SALT000001", True)


def test_add_composition_is_idempotent(conn, gsrs_run, component):
    """A release stating one edge from both ends must not write it twice."""
    kwargs = dict(substance_unii="SALT000001", component_moiety=component,
                  relation="SALT_SOLVATE", is_active_component=None,
                  ingest_run_id=gsrs_run)
    assert composition.add_composition(conn, **kwargs) is True
    assert composition.add_composition(conn, **kwargs) is False
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0] == 1


def test_null_is_stored_as_null_not_false(conn, gsrs_run, component):
    composition.add_composition(
        conn, substance_unii="SALT000001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)
    assert conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition"
    ).fetchone()[0] is None


def test_moiety_uuid_by_unii_maps_live_claims(conn, gsrs_run, component):
    mapping = composition.moiety_uuid_by_unii(conn)
    assert mapping["COMPONENT1"] == component


def test_moiety_uuid_by_unii_excludes_superseded_claims(conn, gsrs_run, component):
    """The docstring's own promise: 'a corrected claim's OLD value must not
    resolve'. Mutation-proven -- deleting the `superseded_by IS NULL` filter left
    all 894 tests green, because nothing else in the suite supersedes a UNII
    claim."""
    corrected_id = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'UNII', 'COMPONENT1X', %s) RETURNING identity_claim_id",
        (component, gsrs_run)).fetchone()[0]
    conn.execute(
        "UPDATE drugref.identity_claim SET superseded_by = %s "
        "WHERE moiety_uuid = %s AND scheme = 'UNII' AND value = 'COMPONENT1'",
        (corrected_id, component))
    mapping = composition.moiety_uuid_by_unii(conn)
    assert "COMPONENT1" not in mapping
    assert mapping["COMPONENT1X"] == component


def test_clear_source_composition_removes_only_this_sources_rows(conn, gsrs_run, component):
    other = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('PBS', 'x', 'x', 'pbs_run') RETURNING ingest_run_id").fetchone()[0]
    composition.add_composition(
        conn, substance_unii="FROMGSRS01", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)
    composition.add_composition(
        conn, substance_unii="FROMPBS001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=other)

    composition.clear_source_composition(conn, "GSRS")

    remaining = conn.execute(
        "SELECT substance_unii FROM drugref.substance_composition").fetchall()
    assert [r[0] for r in remaining] == ["FROMPBS001"]
