# tests/test_allergy_schema.py
"""db/067's shape: the allergy-group tables and the widened four-place vocabulary.

A new source spelling must land in the database CHECKs, ids._SOURCE_CANONICAL and
provenance.WRITERS together; the failure mode otherwise is SILENT (a per-source
rebuild deletes nothing and reports success), so these tests guard it.
"""
import re

import psycopg
import pytest

from drugref import db, ids, provenance

# The values each widened CHECK admitted BEFORE db/067 (restated from the live
# catalog at db/066); every one must survive the widening.
_OLD = {
    ("ingest_run", "ingest_run_source"): {
        "UNII", "CHEBI", "MED-RT", "MeSH", "PBS", "DRUGREF", "GSRS", "ONCHIGH",
        "FDA-CYP", "DRUGCENTRAL", "SPL"},
    ("ingest_run", "ingest_run_writer"): {
        "unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
        "curation", "unattributed", "gsrs_run", "onchigh_run", "fda_cyp_run",
        "drugcentral_run", "spl_run"},
    ("substance_class", "substance_class_source"): {
        "MED-RT", "MeSH", "DRUGREF", "FDA-CYP"},
    ("substance_class", "substance_class_concept_type"): {
        "MoA", "PE", "TC", "PK", "EPC", "APC", "PA"},
}
_NEW = {
    "ingest_run_source": "DRUGREF-ALLERGY",
    "ingest_run_writer": "allergy_group_run",
    "substance_class_source": "DRUGREF-ALLERGY",
    "substance_class_concept_type": "AG",
}


def test_tables_exist(conn):
    assert db.missing_relations(
        conn, "drugref.allergy_group", "drugref.allergy_group_member",
        "drugref.allergy_mesh_placement") == ()


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGREF-ALLERGY', 'x', 'deadbeef', 'allergy_group_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _class(conn, run):
    return conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source_code, class_name, "
        "concept_type, first_seen_ingest, source) VALUES (gen_random_uuid(), 'T', 'T', "
        "'AG', %s, 'DRUGREF-ALLERGY') RETURNING class_uuid", (run,)).fetchone()[0]


def test_checks_admit_new_values(conn):
    _class(conn, _run(conn))


def test_checks_keep_old_values(conn):
    # Exact, except the writer CHECK: db/070 widened it again ('moiety_name_run'),
    # so here it is a subset; its exact pin is test_provenance's WRITERS match.
    for (table, name), old in _OLD.items():
        live = set(re.findall(r"'([^']+)'", db.constraint_definition(conn, table, name)))
        if name == "ingest_run_writer":
            assert old | {_NEW[name]} <= live, name
        else:
            assert live == old | {_NEW[name]}, name


def test_review_status_closed(conn):
    run = _run(conn)
    cls = _class(conn, run)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.allergy_group (class_uuid, mesh_root_ui, review_status, "
            "ingest_run) VALUES (%s, 'D000000', 'draft', %s)", (cls, run))


def test_source_canonical_pair():
    assert ids.canonical_source("drugref-allergy") == "DRUGREF-ALLERGY"
    assert "allergy_group_run" in provenance.WRITERS
