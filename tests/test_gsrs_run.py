# tests/test_gsrs_run.py
"""The orchestrator: one transaction, one run record, worklist numbers not drops."""
import pathlib

import pytest

from drugref import ids
from drugref.ingest import gsrs_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gsrs_subset.gsrs"


@pytest.fixture(autouse=True)
def _clean(conn):
    """ingest_gsrs COMMITS, so it escapes the conn fixture's rollback. Same pattern
    as tests/test_ingest_run.py's autouse truncate.

    SCOPED BY RUN, NOT BY GAP_KIND -- and this is not a stylistic choice, it is the
    difference between a clean teardown and a ForeignKeyViolation. register_from_gaps
    refreshes last_derived_ingest for EVERY currently-open gap on every call, not only
    the ones this ingest caused: the `registry` fixture below registers bare moieties
    with no has_PE membership, so every GSRS run also re-derives gap_unclassified_moiety
    and stamps those rows' last_derived_ingest with the GSRS run's id. Deleting only
    gap_kind = 'unruled_composition_activity' left those other-kind rows still pointing
    at the run this fixture was about to delete -- open_question.first_derived_ingest
    and .last_derived_ingest are both NOT NULL FKs into ingest_run, so the DELETE below
    raised. Scoping by which ingest_run a row references, across every gap_kind, is
    correct rather than a workaround: every such row is an artifact of a GSRS run this
    test made and nothing else in an isolated run of this file would derive it.
    """
    yield
    conn.execute("TRUNCATE drugref.substance_composition")
    conn.execute(
        "DELETE FROM drugref.open_question WHERE first_derived_ingest IN "
        "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = 'GSRS') "
        "OR last_derived_ingest IN "
        "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = 'GSRS')")
    conn.execute("DELETE FROM drugref.ingest_run WHERE source = 'GSRS'")
    conn.commit()


@pytest.fixture
def registry(conn):
    """Register the components the fixture's composites resolve to.

    ZINC CATION and Chlortetracycline are moieties; the counterions deliberately
    are NOT, so the run has something to COUNT as unresolved rather than drop.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for unii, name in (("13S1S8SF37", "ZINC CATION"),
                       ("WCK1KIQ23Q", "Chlortetracycline"),
                       ("ML30MJ2U7I", "Magnesium sulfate anhydrous")):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING", (moiety_uuid, name, seed_run))
        conn.execute(
            "INSERT INTO drugref.identity_claim "
            "(moiety_uuid, scheme, value, ingest_run) VALUES (%s, 'UNII', %s, %s) "
            "ON CONFLICT DO NOTHING", (moiety_uuid, unii, seed_run))
    conn.commit()


def test_ingest_writes_composition_rows(conn, registry):
    summary = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert summary.rows_written > 0
    rows = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0]
    assert rows == summary.rows_written


def test_zinc_glycinate_citrate_attaches_only_its_REGISTERED_component(conn, registry):
    """Three components upstream; only ZINC CATION is a moiety here. The other two
    are COUNTED, never silently dropped."""
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    components = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition "
        "WHERE substance_unii = 'H3472PJ7YA'").fetchone()[0]
    assert components == 1


def test_unresolved_components_are_counted_not_dropped(conn, registry):
    summary = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert summary.components_not_in_registry > 0


def test_the_active_component_is_marked_true(conn, registry):
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    active = conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition "
        "WHERE substance_unii = 'H3472PJ7YA'").fetchone()[0]
    assert active is True


def test_the_run_is_recorded_and_finished(conn, registry):
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    row = conn.execute(
        "SELECT source, writer, upstream_release, finished_at IS NOT NULL "
        "FROM drugref.ingest_run WHERE source = 'GSRS'").fetchone()
    assert row[0] == "GSRS"
    assert row[1] == "gsrs_run"
    assert row[2] == "2026-02-26"
    assert row[3] is True


def test_re_ingest_replaces_rather_than_accumulates(conn, registry):
    """The projection contract: running twice must not double the rows."""
    first = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    second = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert first.rows_written == second.rows_written
    total = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0]
    assert total == second.rows_written


def test_gsrs_is_a_declared_writer_and_source():
    from drugref import ids as ids_module
    from drugref import provenance
    assert "gsrs_run" in provenance.WRITERS
    assert ids_module.canonical_source("GSRS") == "GSRS"
