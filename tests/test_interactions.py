# tests/test_interactions.py
"""The interaction-table writer -- the ONLY module that writes
`class_contraindication`, mirroring classes.py's single-writer role for the
classification tables. Like those, it manages a rebuildable projection: inserts
dedupe, and a per-source clear lets a re-ingest replace the prior release.
"""
import uuid

import pytest

from drugref import interactions, ids


# The writer implied by each source this module's tests actually open a run
# under (db/025). A KeyError on an unlisted source beats a silent NotNullViolation.
_WRITER_BY_SOURCE = {"MED-RT": "medrt_run", "MeSH": "mesh_run",
                     "ONCHIGH": "onchigh_run"}


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'test', 'deadbeef', %s) RETURNING ingest_run_id",
        (source, _WRITER_BY_SOURCE[source])).fetchone()[0]


def _class(conn, run_id, code, cty="MoA"):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, 'MED-RT', %s, %s, 'Test Class', %s, %s)",
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
    medrt_run, other_run = _run(conn, "MED-RT"), _run(conn, "MeSH")
    m, c = _moiety(conn, medrt_run), _class(conn, medrt_run, "N0000000902")
    interactions.add_contraindication(conn, m, c, "CI_MoA", "MED-RT", medrt_run)
    interactions.add_contraindication(conn, m, c, "CI_PE", "MED-RT", other_run)
    interactions.clear_source_contraindications(conn, "MED-RT")
    survivors = conn.execute(
        "SELECT relationship FROM drugref.class_contraindication "
        "WHERE subject_moiety_uuid = %s", (m,)).fetchall()
    assert survivors == [("CI_PE",)]


# ---- Task 10: the class-subject grain (db/032, design spec section 14) -------


def test_add_class_pair_contraindication_inserts_once_and_dedupes(conn):
    """Mirrors test_add_contraindication_inserts_once_and_dedupes exactly, one
    grain over: BOTH endpoints are classes instead of a moiety and a class."""
    run_id = _run(conn, "ONCHIGH")
    subject = _class(conn, run_id, "N0000000903")
    obj = _class(conn, run_id, "N0000000904")
    assert interactions.add_class_pair_contraindication(
        conn, subject, obj, "CI_MoA", "ONCHIGH", run_id) is True
    assert interactions.add_class_pair_contraindication(
        conn, subject, obj, "CI_MoA", "ONCHIGH", run_id) is False
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_pair_contraindication "
        "WHERE subject_class_uuid = %s", (subject,)).fetchone()[0] == 1


def test_clear_source_class_pair_contraindications_removes_only_that_sources_rows(conn):
    """Rebuild semantics, mirroring the moiety-grain test above: a re-ingest
    replaces ONCHIGH's class-pair rules and leaves an unrelated feed's rows
    untouched, since class_pair_contraindication's PK includes `source`
    (db/032, mirroring db/006's own class_contraindication fix)."""
    onchigh_run, medrt_run = _run(conn, "ONCHIGH"), _run(conn, "MED-RT")
    subject = _class(conn, onchigh_run, "N0000000905")
    obj = _class(conn, onchigh_run, "N0000000906")
    interactions.add_class_pair_contraindication(
        conn, subject, obj, "CI_MoA", "ONCHIGH", onchigh_run)
    interactions.add_class_pair_contraindication(
        conn, subject, obj, "CI_PE", "MED-RT", medrt_run)
    interactions.clear_source_class_pair_contraindications(conn, "ONCHIGH")
    survivors = conn.execute(
        "SELECT relationship FROM drugref.class_pair_contraindication "
        "WHERE subject_class_uuid = %s", (subject,)).fetchall()
    assert survivors == [("CI_PE",)]


# ---- slice 5b: moiety<->condition and moiety<->moiety contraindications --------


@pytest.fixture
def a_condition(conn, ingest_run_id):
    """One registered condition, for tests that need a live FK target. Mirrors
    test_schema_mesh_contraindications.py's fixture of the same name."""
    cu = ids.mint_condition_uuid("MeSH", "D004827")
    conn.execute(
        "INSERT INTO drugref.condition (condition_uuid, source, source_code, name, "
        "record_kind, first_seen_ingest) "
        "VALUES (%s,'MeSH','D004827','Epilepsy','DESCRIPTOR',%s)", (cu, ingest_run_id))
    return cu


def test_add_condition_contraindication(conn, a_moiety, a_condition, ingest_run_id):
    assert interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", ingest_run_id)


def test_repeated_condition_contraindication_is_harmless(conn, a_moiety, a_condition,
                                                         ingest_run_id):
    """A release that states one assertion twice must not fail the ingest."""
    interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", ingest_run_id)
    assert not interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", ingest_run_id)


def test_add_moiety_contraindication_is_directional(conn, a_moiety, ingest_run_id):
    """Subject and object are not interchangeable: the subject is the drug the
    statement is ABOUT. Both directions are storable and mean different things."""
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (gen_random_uuid(),'pimozide',%s) "
        "RETURNING moiety_uuid", (ingest_run_id,)).fetchone()[0]
    assert interactions.add_moiety_contraindication(
        conn, a_moiety, other, "CI_ChemClass", "MED-RT", ingest_run_id)
    assert interactions.add_moiety_contraindication(
        conn, other, a_moiety, "CI_ChemClass", "MED-RT", ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 2


def test_clear_source_removes_both_relations(conn, a_moiety, a_condition,
                                             ingest_run_id):
    """A re-ingest must fully REPLACE the previous release, across both tables --
    and must leave ANOTHER source's rows alone.

    NOTE: conftest's `ingest_run_id` fixture creates its run with source='PBS', so
    this test opens its OWN run under 'MED-RT' and uses the fixture's run as the
    other source. Clearing one must not touch the other.

    db/014 (as tightened after Task 5's review) constrains
    moiety_condition_contraindication.source to CHECK (source IN ('MED-RT')) --
    production only knows one authority so far. This test needs a SECOND source to
    prove clear-by-source leaves it alone, so it widens the CHECK the same way
    test_two_sources_may_each_assert_the_same_contraindication
    (tests/test_schema_interactions.py) does: inside this test's transaction only,
    which the `conn` fixture rolls back after the test, so the widening never
    reaches the schema other tests see.
    """
    conn.execute(
        "ALTER TABLE drugref.moiety_condition_contraindication "
        "DROP CONSTRAINT moiety_condition_contraindication_source")
    conn.execute(
        "ALTER TABLE drugref.moiety_condition_contraindication "
        "ADD CONSTRAINT moiety_condition_contraindication_source "
        "CHECK (source IN ('MED-RT', 'PBS'))")

    medrt_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT','test','x','medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (gen_random_uuid(),'x',%s) RETURNING moiety_uuid",
        (ingest_run_id,)).fetchone()[0]

    # One row per relation under MED-RT, plus one condition row under PBS.
    interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", medrt_run)
    interactions.add_moiety_contraindication(
        conn, a_moiety, other, "CI_ChemClass", "MED-RT", medrt_run)
    interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "PBS", ingest_run_id)

    interactions.clear_source_mesh_contraindications(conn, "MED-RT")

    # MED-RT's rows are gone from BOTH relations...
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 0
    # ...while the other source's row survives untouched.
    assert conn.execute(
        "SELECT source FROM drugref.moiety_condition_contraindication"
    ).fetchall() == [("PBS",)]


def test_clear_source_removes_the_unresolved_object_worklist(conn, ingest_run_id):
    """The THIRD table clear_source_mesh_contraindications touches, asserted on its
    own -- because the test above covers only the two relations, and this module is
    that function's SINGLE WRITER: it has to assert its own clear contract directly.

    MEASURED, NOT ASSUMED. Removing `ingest_unresolved_ci_object` from that
    function's loop fails exactly two tests: this one, and
    test_mesh_rel_run_ci.py::test_rerunning_replaces_rather_than_duplicates. The second
    is an INCIDENTAL detector -- an end-to-end check that a re-run does not duplicate
    rows, which happens to notice, and which would stop noticing the moment that
    fixture stopped re-running the ingest. Before this test, that was the only thing
    standing between the clear contract and a one-token edit.

    WHAT RIDES ON IT: gap_unresolved_ci_object reports sum(assertion_count) ACROSS
    runs (db/016). A worklist that is written but never cleared therefore does not
    merely go stale -- it MULTIPLIES the curator-facing rule count on every
    re-ingest, 405 -> 810 -> 1,215, with nothing anywhere failing.

    TWO run sources, because "cleared" and "cleared only where it should be" are
    different claims and only the second is worth having. db/014 constrains
    ingest_unresolved_ci_object.source to CHECK (source IN ('MED-RT')) -- production
    knows one authority so far -- so the second source is admitted INSIDE this test's
    transaction only, the same idiom as
    test_two_sources_may_each_assert_the_same_contraindication in
    tests/test_schema_interactions.py. The `conn` fixture rolls it back, so the
    widening never reaches the schema any other test sees.
    """
    conn.execute("ALTER TABLE drugref.ingest_unresolved_ci_object "
                 "DROP CONSTRAINT ingest_unresolved_ci_object_source")
    conn.execute("ALTER TABLE drugref.ingest_unresolved_ci_object "
                 "ADD CONSTRAINT ingest_unresolved_ci_object_source "
                 "CHECK (source IN ('MED-RT', 'PBS'))")

    # conftest's `ingest_run_id` fixture opens its run under source='PBS', so it is
    # already the "other" source; this test opens its own run under 'MED-RT'.
    medrt_run = _run(conn, "MED-RT")
    interactions.record_unresolved_ci_objects(
        conn,
        [("MED-RT", "CI_ChemClass", "MeSH", "D013449", "Sulfonamides",
          "CHEMICAL_CLASS", 36)],
        medrt_run)
    interactions.record_unresolved_ci_objects(
        conn,
        [("PBS", "CI_ChemClass", "MeSH", "D001569", "Benzodiazepines",
          "CHEMICAL_CLASS", 13)],
        ingest_run_id)

    interactions.clear_source_mesh_contraindications(conn, "MED-RT")

    assert conn.execute(
        "SELECT source, object_code FROM drugref.ingest_unresolved_ci_object"
    ).fetchall() == [("PBS", "D001569")]


def test_record_unresolved_ci_objects(conn, ingest_run_id):
    written = interactions.record_unresolved_ci_objects(
        conn,
        [("MED-RT", "CI_ChemClass", "MeSH", "D013449", "Sulfonamides",
          "CHEMICAL_CLASS", 36)],
        ingest_run_id)
    assert written == 1
    assert conn.execute(
        "SELECT object_name, assertion_count FROM drugref.ingest_unresolved_ci_object"
    ).fetchone() == ("Sulfonamides", 36)
