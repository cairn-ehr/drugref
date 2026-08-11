# tests/test_onchigh_schema.py
"""db/031's four changes, each asserted for the reason it exists.

Every test here fails against db/030 alone, which is the point: a migration
whose absence no test notices is a migration nobody can safely re-order.
"""
import uuid
import pytest
import psycopg


@pytest.fixture
def an_ingest_run(ingest_run_id):
    """Alias for conftest's shared `ingest_run_id` fixture (source='PBS',
    writer='pbs_run'). This module needs a plain provenance FK for rows that are
    not themselves about a specific source's ingest, so it reuses the existing
    convention under the name this task's tests were written against rather than
    inventing a second way to get an ingest_run row."""
    return ingest_run_id


@pytest.fixture
def medrt_class(conn, an_ingest_run):
    """One MED-RT class, for tests needing a live FK target on the object side.

    Same shape as tests/test_curated_overlay.py's `_a_class` helper (source
    'MED-RT', concept_type 'MoA') -- copied rather than imported, following this
    repo's established precedent (conftest.py's own note on `a_graded_rule`) that
    cross-module fixture sharing goes through conftest.py by name, not through
    importing a test module's private helper.
    """
    from drugref import ids

    code = "N0000000001"
    class_uuid = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, 'MoA', %s) ON CONFLICT DO NOTHING",
        (class_uuid, code, "Test MoA [MoA]", an_ingest_run),
    )
    return class_uuid


def test_class_contraindication_admits_onchigh(conn, medrt_class, a_moiety, an_ingest_run):
    """The whole slice rests on this CHECK being widened."""
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'ONCHIGH', %s)",
        (a_moiety, medrt_class, an_ingest_run))
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_contraindication WHERE source = 'ONCHIGH'"
    ).fetchone()[0] == 1


def test_class_contraindication_still_refuses_an_unknown_source(conn, medrt_class,
                                                               a_moiety, an_ingest_run):
    """Widening a CHECK must not turn it into no CHECK at all."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.class_contraindication "
            "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
            "VALUES (%s, %s, 'CI_MoA', 'DRUGBANK', %s)",
            (a_moiety, medrt_class, an_ingest_run))


def test_ingest_run_admits_the_onchigh_source_and_writer(conn):
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES ('ONCHIGH', 'test', 'abc', 'onchigh_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    assert run_id


def test_ci_epc_axis_exists_and_expands(conn):
    """The axis maps CI_EPC onto has_EPC memberships, and expands descendants --
    65 of the 811 EPC classes have children, so expansion is not decorative."""
    row = conn.execute(
        "SELECT membership_relationship, expands_descendants FROM drugref.ci_axis "
        "WHERE relationship = 'CI_EPC'").fetchone()
    assert row == ("has_EPC", True)


def test_unresolved_endpoint_table_is_keyed_per_run_and_role(conn, an_ingest_run):
    """Two unresolved endpoints in ONE entry are two rows, not one that flickers:
    the subject and the object can each fail independently."""
    for role in ("subject", "object"):
        conn.execute(
            "INSERT INTO drugref.ingest_unresolved_onc_endpoint "
            "(ingest_run, source, entry_id, endpoint_role, identifier_scheme, "
            " identifier_value, endpoint_name) "
            "VALUES (%s, 'ONCHIGH', 'warfarin-nsaid', %s, 'UNII', 'ZZZZZZZZZZ', 'x')",
            (an_ingest_run, role))
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unresolved_onc_endpoint").fetchone()[0] == 2


def test_open_question_admits_the_new_gap_kind(conn, an_ingest_run):
    # first_derived_ingest/last_derived_ingest are NOT NULL with no default
    # (db/007) -- the brief's snippet omitted them, but every existing direct
    # INSERT into open_question (tests/test_schema_question_registry.py) supplies
    # both, so this follows that convention rather than a snippet that cannot
    # insert regardless of which gap_kind vocabulary is in force.
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
        "question_text, first_derived_ingest, last_derived_ingest) "
        "VALUES (%s, 'unresolved_onc_endpoint', 'ONCHIGH:x:UNII:Y', 'why?', %s, %s)",
        (uuid.uuid4(), an_ingest_run, an_ingest_run))


def test_open_question_still_refuses_an_invented_gap_kind(conn, an_ingest_run):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.open_question "
            "(question_uuid, gap_kind, gap_key, question_text, first_derived_ingest, "
            " last_derived_ingest) "
            "VALUES (%s, 'not_a_real_kind', 'k', 'why?', %s, %s)",
            (uuid.uuid4(), an_ingest_run, an_ingest_run))
