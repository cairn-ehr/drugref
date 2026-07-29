"""Structural guarantees of slice 5b's two contraindication relations (db/014)."""
import uuid

import psycopg
import pytest

from drugref import ids


@pytest.fixture
def a_condition(conn, ingest_run_id):
    cu = ids.mint_condition_uuid("MeSH", "D004827")
    conn.execute(
        "INSERT INTO drugref.condition (condition_uuid, source, source_code, name, "
        "record_kind, first_seen_ingest) "
        "VALUES (%s,'MeSH','D004827','Epilepsy','DESCRIPTOR',%s)", (cu, ingest_run_id))
    return cu


def test_ci_with_axis_is_seeded_and_expands(conn):
    """ROADMAP's standing instruction: decide expands_descendants per predicate
    rather than inherit a default. CI_with is declared true on Plan B's argument --
    for a contraindication, fewer rows is the harm direction."""
    assert conn.execute(
        "SELECT expands_descendants FROM drugref.condition_ci_axis "
        "WHERE relationship = 'CI_with'").fetchone() == (True,)


def test_expands_descendants_has_no_default(conn):
    """db/012 finding 5: ci_axis claimed a force-a-declaration discipline while
    supplying a DEFAULT. This table actually implements it, so a predicate added
    later cannot inherit an unexamined answer."""
    assert conn.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='drugref' AND table_name='condition_ci_axis' "
        "AND column_name='expands_descendants'").fetchone()[0] is None


def test_condition_ci_relationship_is_a_foreign_key(conn, a_moiety, a_condition,
                                                    ingest_run_id):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.moiety_condition_contraindication "
            "(subject_moiety_uuid, object_condition_uuid, relationship, source, "
            " ingest_run) VALUES (%s,%s,'CI_invented','MED-RT',%s)",
            (a_moiety, a_condition, ingest_run_id))


def test_source_is_in_the_condition_ci_primary_key(conn, a_moiety, a_condition,
                                                   ingest_run_id):
    """db/006 finding 2: without source in the key, a second authority's identical
    assertion is swallowed by ON CONFLICT and then destroyed by the FIRST source's
    next rebuild. Slice 5c plans exactly that second source.

    MED-RT is still the only authority the production CHECK admits (slice 5c is
    what adds one), so the second source is admitted only inside this test's
    transaction, which the conn fixture rolls back. Widening the CHECK for real
    without this PK would be the breaking change; that is the point of fixing it
    while the table holds one source's data.
    """
    conn.execute(
        "ALTER TABLE drugref.moiety_condition_contraindication "
        "DROP CONSTRAINT moiety_condition_contraindication_source")
    conn.execute(
        "ALTER TABLE drugref.moiety_condition_contraindication "
        "ADD CONSTRAINT moiety_condition_contraindication_source "
        "CHECK (source IN ('MED-RT', 'DRUGREF'))")
    for src in ("MED-RT", "DRUGREF"):
        conn.execute(
            "INSERT INTO drugref.moiety_condition_contraindication "
            "(subject_moiety_uuid, object_condition_uuid, relationship, source, "
            " ingest_run) VALUES (%s,%s,'CI_with',%s,%s)",
            (a_moiety, a_condition, src, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone()[0] == 2


def test_source_must_be_a_known_authority(conn, a_moiety, a_condition, ingest_run_id):
    """Widened per source exactly as substance_class.source was; MED-RT only today."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.moiety_condition_contraindication "
            "(subject_moiety_uuid, object_condition_uuid, relationship, source, "
            " ingest_run) VALUES (%s,%s,'CI_with','DrugBank',%s)",
            (a_moiety, a_condition, ingest_run_id))


def test_moiety_contraindication_round_trips(conn, a_moiety, ingest_run_id):
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (%s,'pimozide',%s) RETURNING moiety_uuid",
        (uuid.uuid4(), ingest_run_id)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication (subject_moiety_uuid, "
        "object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s,%s,'CI_ChemClass','MED-RT',%s)", (a_moiety, other, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 1


def test_a_moiety_is_not_contraindicated_with_itself(conn, a_moiety, ingest_run_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.moiety_contraindication (subject_moiety_uuid, "
            "object_moiety_uuid, relationship, source, ingest_run) "
            "VALUES (%s,%s,'CI_ChemClass','MED-RT',%s)",
            (a_moiety, a_moiety, ingest_run_id))


def test_moiety_contraindication_relationship_is_constrained(conn, a_moiety,
                                                             ingest_run_id):
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (%s,'x',%s) RETURNING moiety_uuid",
        (uuid.uuid4(), ingest_run_id)).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.moiety_contraindication (subject_moiety_uuid, "
            "object_moiety_uuid, relationship, source, ingest_run) "
            "VALUES (%s,%s,'nonsense','MED-RT',%s)", (a_moiety, other, ingest_run_id))


def test_unresolved_ci_object_records_the_class_arm(conn, ingest_run_id):
    """The 405 withheld assertions are PRESERVED as a worklist row, not dropped."""
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, object_kind, "
        "assertion_count) VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D013449',"
        "'Sulfonamides','CHEMICAL_CLASS',36)",
        (ingest_run_id,))
    assert conn.execute(
        "SELECT assertion_count FROM drugref.ingest_unresolved_ci_object"
    ).fetchone()[0] == 36


def test_object_kind_is_constrained(conn, ingest_run_id):
    """Only the two kinds the read path knows how to phrase a question for.

    An unconstrained free-text column here is db/012 finding 3 all over again: a
    mis-typed kind would insert cleanly and then fall through questions.py's CASE
    to NULL, blaming the register step for a typo made at ingest time.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
            "relationship, object_source, object_code, object_name, object_kind, "
            "assertion_count) VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D000468',"
            "'Alkalies','chemical_class',1)", (ingest_run_id,))


def test_object_kind_has_no_default(conn):
    """The force-a-declaration discipline, the same one condition_ci_axis applies.

    A writer recording a withheld object MUST say which kind it is. A default would
    answer -- silently, and in whichever direction the default happened to point --
    exactly the question whose silent answering was the defect this column closes.
    """
    assert conn.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='drugref' AND table_name='ingest_unresolved_ci_object' "
        "AND column_name='object_kind'").fetchone()[0] is None
