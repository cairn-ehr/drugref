# tests/test_drugcentral_schema.py
"""db/049's shape: the source vocabulary, the severity map, the assertion table.

WHY A SCHEMA TEST AT ALL, when later tasks exercise the same objects: a new
source spelling is not a one-line change. It must land in the database CHECK,
in ids._SOURCE_CANONICAL and in provenance.WRITERS *in the same migration*, and
the failure mode when it does not is silent -- a per-source rebuild deletes
nothing and reports success. These tests are the guard against that silence.
"""
import psycopg
import pytest

from drugref import ids, provenance


def test_drugcentral_is_a_canonical_source_spelling():
    """Listed EXPLICITLY, though the upper-case fall-through would also produce it.

    ids.py's own docstring warns by name against leaning on that fall-through:
    'openFDA-SPL' and 'MeDIC' fold to spellings a mixed-case CHECK would never
    match. 'DRUGCENTRAL' survives by luck, exactly as 'GSRS', 'DRUGREF' and
    'FDA-CYP' do, and the entry records that the luck was CHECKED.
    """
    assert ids.canonical_source("DRUGCENTRAL") == "DRUGCENTRAL"
    assert ids.canonical_source("drugcentral") == "DRUGCENTRAL"
    assert ids.canonical_source("  DrugCentral  ") == "DRUGCENTRAL"


def test_drugcentral_run_is_a_declared_writer():
    """provenance.WRITERS and db/049's CHECK are a PAIR (db/020's source-trio lesson)."""
    assert "drugcentral_run" in provenance.WRITERS


@pytest.mark.usefixtures("conn")
def test_ingest_run_admits_the_drugcentral_source_and_writer(conn):
    conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run')")


@pytest.mark.usefixtures("conn")
def test_ingest_run_still_refuses_a_misspelled_drugcentral_source(conn):
    """'DRUG-CENTRAL' is the typo db/012 finding 3 describes: it would insert
    cleanly under an unconstrained column and then match nothing, ever."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.ingest_run "
            "(source, upstream_release, source_checksum, writer) "
            "VALUES ('DRUG-CENTRAL', '11012023', 'deadbeef', 'drugcentral_run')")


@pytest.mark.usefixtures("conn")
def test_class_contraindication_source_is_NOT_widened(conn):
    """DrugCentral writes no class rule, so its source must stay OUT of that CHECK.

    HANDOVER said this CHECK needed widening for this source. It does not, and a
    widened CHECK would admit a row no writer in this project produces -- which is
    how a vocabulary grows a value nothing means.
    """
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'class_contraindication_source'").fetchone()
    assert "DRUGCENTRAL" not in definition


@pytest.mark.usefixtures("conn")
def test_the_two_va_bands_are_seeded_and_mapped(conn):
    """VA/NDF-RT's own semantics: Critical = avoid, Significant = monitor/adjust.

    `major` is deliberately unused by this source. A two-band authority has two
    bands, and spreading them across three grades would invent a distinction VA
    does not draw.
    """
    rows = conn.execute(
        "SELECT source_label, severity FROM drugref.ddi_source_severity "
        "WHERE source = 'DRUGCENTRAL' ORDER BY source_label").fetchall()
    assert rows == [("Critical", "contraindicated"), ("Significant", "moderate")]


@pytest.mark.usefixtures("conn")
def test_a_mapped_severity_must_be_a_real_grade(conn):
    """The FK into severity_kind is what stops a mapping naming a grade that has
    no rank -- and severity_rank is what decides which of two grades a consumer
    sees, so a rankless one would make that non-deterministic."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.ddi_source_severity "
            "(source, source_label, severity) "
            "VALUES ('DRUGCENTRAL', 'Catastrophic', 'apocalyptic')")


@pytest.mark.usefixtures("conn")
def test_the_mapping_is_keyed_per_source(conn):
    """Two authorities may both use the word 'Significant' and mean different
    things, so the label alone is not the key."""
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'ddi_source_severity_pkey'").fetchone()
    assert definition == "PRIMARY KEY (source, source_label)"
