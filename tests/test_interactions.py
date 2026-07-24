# tests/test_interactions.py
"""The interaction-table writer -- the ONLY module that writes
`class_contraindication`, mirroring classes.py's single-writer role for the
classification tables. Like those, it manages a rebuildable projection: inserts
dedupe, and a per-source clear lets a re-ingest replace the prior release.
"""
import uuid

from drugref import interactions, ids


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, code, cty="MoA"):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, 'MED-RT', %s, %s, 'C', %s, %s)",
        (cu, code, code, cty, run_id))
    return cu


def _moiety(conn, run_id, name="testium"):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def test_add_contraindication_inserts_once_and_dedupes(conn):
    """A file that asserts the same contraindication twice is one row; the return
    value distinguishes the new insert from the repeat, as add_membership does."""
    run_id = _run(conn)
    m, c = _moiety(conn, run_id), _class(conn, run_id, "N0000000901")
    assert interactions.add_contraindication(conn, m, c, "CI_MoA", "MED-RT", run_id) is True
    assert interactions.add_contraindication(conn, m, c, "CI_MoA", "MED-RT", run_id) is False
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_contraindication "
        "WHERE subject_moiety_uuid = %s", (m,)).fetchone()[0] == 1


def test_clear_source_contraindications_removes_only_that_sources_rows(conn):
    """Rebuild semantics, mirroring classes.clear_source_edges: a new MED-RT release
    replaces MED-RT's contraindications and leaves an unrelated feed's untouched.
    Scoped by the run's source, so this run's own rows survive until it rewrites."""
    medrt_run, other_run = _run(conn, "MED-RT"), _run(conn, "SOMETHING-ELSE")
    m, c = _moiety(conn, medrt_run), _class(conn, medrt_run, "N0000000902")
    interactions.add_contraindication(conn, m, c, "CI_MoA", "MED-RT", medrt_run)
    interactions.add_contraindication(conn, m, c, "CI_PE", "MED-RT", other_run)
    interactions.clear_source_contraindications(conn, "MED-RT")
    survivors = conn.execute(
        "SELECT relationship FROM drugref.class_contraindication "
        "WHERE subject_moiety_uuid = %s", (m,)).fetchall()
    assert survivors == [("CI_PE",)]
