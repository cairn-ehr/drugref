# tests/test_fda_cyp_schema.py
"""db/039's shape: the three-place source vocabulary, the projection, the gap view.

WHY A SCHEMA TEST AT ALL, when later tasks exercise the same objects: a new
source spelling is not a one-line change (issue 101 recorded the lesson for
DRUGCENTRAL and it applies unchanged here). It must land in the database CHECK,
in ids._SOURCE_CANONICAL and in provenance.WRITERS *in the same migration*, and
the failure mode when it does not is silent -- a per-source rebuild deletes
nothing and reports success. These tests are the guard against that silence.
"""
import re

import psycopg
import pytest

from drugref import db, ids, provenance


def test_fda_cyp_is_a_canonical_source_spelling():
    """Listed EXPLICITLY, though the upper-case fall-through would also produce it.

    ids.py's own docstring warns by name against leaning on that fall-through:
    'openFDA-SPL' and 'MeDIC' fold to spellings a mixed-case CHECK would never
    match. 'FDA-CYP' survives by luck, exactly as 'GSRS' and 'DRUGREF' do, and
    the entry records that the luck was CHECKED rather than assumed.
    """
    assert ids.canonical_source("FDA-CYP") == "FDA-CYP"
    assert ids.canonical_source("fda-cyp") == "FDA-CYP"
    assert ids.canonical_source("  FDA-CYP  ") == "FDA-CYP"


def test_fda_cyp_run_is_a_declared_writer():
    """provenance.WRITERS and db/039's CHECK are a PAIR (db/020's source-trio lesson)."""
    assert "fda_cyp_run" in provenance.WRITERS


@pytest.mark.usefixtures("conn")
def test_ingest_run_admits_the_fda_cyp_source_and_writer(conn):
    conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES ('FDA-CYP', '2026-05-29T14:00', 'deadbeef', 'fda_cyp_run')")


@pytest.mark.usefixtures("conn")
def test_substance_class_admits_the_fda_cyp_source(conn):
    """db/003 created this CHECK with a comment instructing exactly this edit:
    'Extend it and _SOURCE_CANONICAL together when a source lands.' FDA-CYP is
    the first source to land since, so this is that instruction being followed.

    THE FULL WIDENED SET IS CHECKED, not merely two of its four members. The
    PREVIOUS version of this test asserted 'MED-RT' and 'MeSH' survived but
    NOT 'DRUGREF' -- which is precisely the value db/039's own header records
    as the one a retyped list would have silently DROPPED ("would have DROPPED
    'DRUGREF' had its list been retyped instead of copied"). A test that
    checks two of three pre-existing values and calls that "widening must not
    drop a value" cannot catch the exact failure its own docstring names.
    """
    live = db.constraint_definition(conn, "substance_class", "substance_class_source")
    values = set(re.findall(r"'([^']+)'", live))
    assert values == {"MED-RT", "MeSH", "DRUGREF", "FDA-CYP"}, (
        "the widened set must be exactly the pre-db/039 set ({'MED-RT', 'MeSH', "
        f"'DRUGREF'}} plus 'FDA-CYP' -- got {values!r}")


@pytest.mark.usefixtures("conn")
def test_the_assertion_projection_and_gap_view_exist(conn):
    # Schema-qualified, matching every other call site (test_db.py,
    # migration_guard.py): to_regclass resolves through search_path, which is
    # `"$user", public` here and does not include `drugref` -- an unqualified
    # name would report a real relation missing.
    assert db.missing_relations(
        conn, "drugref.fda_cyp_assertion", "drugref.gap_fda_cyp_unadjudicated") == ()


@pytest.mark.usefixtures("conn")
def test_disposition_is_a_closed_set_of_exactly_five_values(conn):
    """Five, not nine -- spec section 7.1. Only combination_regimen and
    non_drug_entity name a CATEGORY, because only those two are asserted by FDA
    rather than inferred by drugref from a string prefix.

    THE COUNT ITSELF IS ASSERTED, not just presence of the five named values.
    The PREVIOUS version of this test checked the five ARE present and four
    named "inferred" categories are ABSENT, but nothing stopped a SIXTH,
    unnamed value also being present -- which would still satisfy every
    assertion here despite the set no longer being exactly five. The function
    name promises "exactly five"; only a count check can honour it.
    """
    live = db.constraint_definition(conn, "fda_cyp_assertion", "fda_cyp_assertion_disposition")
    values = set(re.findall(r"'([^']+)'", live))
    assert values == {"member", "withheld_qualified", "unresolved_substance",
                      "combination_regimen", "non_drug_entity"}, (
        f"the disposition CHECK must be exactly these five values -- got {values!r}")
    for inferred in ("enantiomer", "synonym", "metabolite", "group_term"):
        assert inferred not in live, (
            f"{inferred!r} is a cause drugref would be INFERRING from a name, which is "
            "issue 122's manufactured-cause defect. Spec section 7.1.")


@pytest.mark.usefixtures("conn")
def test_the_new_gap_kind_is_admitted(conn):
    live = db.constraint_definition(conn, "open_question", "open_question_gap_kind")
    assert "'fda_cyp_unadjudicated'" in live


def test_the_pathway_vocabulary_is_closed_in_SQL_too(conn):
    """db/043. Every other axis of a tuple carries a CHECK; `pathway` -- the
    module's HEADLINE invariant, the one that stops 'cyp:1a2 20' being minted
    with an immortal UUID -- was enforced only in Python.

    A closed vocabulary that exists in one language is a vocabulary any other
    writer can widen by accident.
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('FDA-CYP', 'probe', 'probe', 'fda_cyp_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.fda_cyp_assertion "
            "(ingest_run, source, row_ordinal, raw_substance, column_heading, "
            " raw_cell, system, pathway, role, potency, disposition) "
            "VALUES (%s, 'FDA-CYP', 1, 'probe', 'CYP Strg INH', '1A2 20 strong "
            "inhibitor', 'CYP', '1a2 20', 'inhibitor', 'strong', 'member')",
            (run_id,))
    conn.rollback()


def test_a_real_pathway_under_the_wrong_system_is_refused_in_SQL(conn):
    """'OATP1B1' is a genuine transporter, and a CYP row naming it would mint a
    class under the wrong system. fda_cyp.parse_cell refuses this; db/043 makes
    the DB refuse it too, so the two cannot disagree.
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('FDA-CYP', 'probe', 'probe', 'fda_cyp_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.fda_cyp_assertion "
            "(ingest_run, source, row_ordinal, raw_substance, column_heading, "
            " raw_cell, system, pathway, role, potency, disposition) "
            "VALUES (%s, 'FDA-CYP', 1, 'probe', 'CYP Strg INH', "
            "'OATP1B1 strong inhibitor', 'CYP', 'OATP1B1', 'inhibitor', "
            "'strong', 'member')",
            (run_id,))
    conn.rollback()


def test_the_SQL_pathway_vocabulary_equals_the_parsers(conn):
    """ONE vocabulary, pinned as an EQUALITY across the two languages that
    hold it -- the "written down twice" hazard this project keeps paying for.
    A subset assertion would let either side drift silently.
    """
    from drugref.ingest import fda_cyp
    live = {(row[0], row[1]) for row in conn.execute(
        "SELECT system, pathway FROM drugref.fda_cyp_pathway").fetchall()}
    expected = {(system, pathway)
                for system, pathways in fda_cyp._PATHWAYS_BY_SYSTEM.items()
                for pathway in pathways}
    assert live == expected
