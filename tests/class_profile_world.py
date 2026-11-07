"""Tiny row builders for the class-profile tests (Tasks 1-5 import these).

Each helper inserts the MINIMUM valid row and returns what a test needs next. Column
lists are copied from the existing schema tests (test_schema_classes.py,
test_schema_mesh_contraindications.py). Nothing here commits; the `conn` fixture rolls
back.
"""
import uuid

from drugref import ids

# ingest_run.writer is NOT NULL (db/025); name the writer each source really uses.
_WRITER = {"MED-RT": "medrt_run", "MeSH": "mesh_run", "UNII": "unii_run",
           "ONCHIGH": "onchigh_run"}


def run(conn, source="MED-RT") -> int:
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'test', 'deadbeef', %s) RETURNING ingest_run_id",
        (source, _WRITER[source])).fetchone()[0]


def moiety(conn, run, name) -> uuid.UUID:
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run))
    return m


def klass(conn, run, name, *, source="MED-RT", concept_type="EPC") -> uuid.UUID:
    cu = ids.mint_class_uuid(source, name)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, %s, %s, %s, %s, %s)", (cu, source, name, name, concept_type, run))
    return cu


def member(conn, run, moiety, klass, relationship="has_EPC"):
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) "
                 "VALUES (%s, %s, %s, %s)", (moiety, klass, relationship, run))


def parent(conn, run, child, parent):
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (child, parent, run))


def condition(conn, run, name) -> uuid.UUID:
    cu = uuid.uuid4()
    conn.execute("INSERT INTO drugref.condition (condition_uuid, source, source_code, "
                 "name, record_kind, first_seen_ingest) "
                 "VALUES (%s, 'MeSH', %s, %s, 'DESCRIPTOR', %s)", (cu, name, name, run))
    return cu


def _moiety_condition(conn, table, run, moiety, condition, relationship):
    conn.execute(
        f"INSERT INTO drugref.{table} (subject_moiety_uuid, object_condition_uuid, "
        f"relationship, source, ingest_run) VALUES (%s, %s, %s, 'MED-RT', %s)",
        (moiety, condition, relationship, run))


def indication(conn, run, moiety, condition, relationship="may_treat"):
    _moiety_condition(conn, "moiety_condition_indication", run, moiety, condition,
                      relationship)


def contraindication(conn, run, moiety, condition):
    _moiety_condition(conn, "moiety_condition_contraindication", run, moiety, condition,
                      "CI_with")


def induced(conn, run, moiety, condition):
    _moiety_condition(conn, "moiety_induced_condition", run, moiety, condition,
                      "induces")


def class_ci(conn, run, moiety, klass, relationship="CI_MoA", source="MED-RT"):
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s)", (moiety, klass, relationship, source, run))


def class_pair_ci(conn, run, subject_class, object_class, relationship="CI_MoA"):
    conn.execute(
        "INSERT INTO drugref.class_pair_contraindication (subject_class_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, 'MED-RT', %s)",
        (subject_class, object_class, relationship, run))


def moiety_ci(conn, run, subject, object):
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication (subject_moiety_uuid, "
        "object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_ChemClass', 'MED-RT', %s)", (subject, object, run))


def curated_ci(conn, moiety, klass, relationship="CI_MoA", applies=True):
    """A live curated_interaction row (drug -> class). applies=false needs NULL grading."""
    conn.execute(
        "INSERT INTO drugref.curated_interaction (subject_moiety_uuid, "
        "object_class_uuid, relationship, applies, severity, evidence_grade, source, "
        "reviewed_by, reviewed_against) VALUES (%s, %s, %s, %s, %s, %s, 'DRUGREF', "
        "'test', 'test')",
        (moiety, klass, relationship, applies, "major" if applies else None,
         "established" if applies else None))


def curated_class_ci(conn, subject_class, object_class, relationship="CI_MoA",
                     applies=True):
    """A live curated_class_interaction row (class -> class)."""
    conn.execute(
        "INSERT INTO drugref.curated_class_interaction (subject_class_uuid, "
        "object_class_uuid, relationship, applies, severity, evidence_grade, source, "
        "reviewed_by, reviewed_against) VALUES (%s, %s, %s, %s, %s, %s, 'DRUGREF', "
        "'test', 'test')",
        (subject_class, object_class, relationship, applies,
         "major" if applies else None, "established" if applies else None))
