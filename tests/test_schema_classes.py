# tests/test_schema_classes.py
"""Schema-level guarantees for the slice-2a classification tables.

These pin the decisions that are easiest to regress later: the CHECK constraints
that keep concept_type and relationship symmetric with what we actually ingest,
and the deliberate ABSENCE of an append-only floor on the edge tables. The edge
tables are a rebuildable projection of MED-RT and MUST stay deletable -- if a
future change adds a no-DELETE trigger there, re-ingesting a new MED-RT release
silently stops working.
"""
import uuid

import pytest
import psycopg

from drugref import classes, ids
from drugref.ingest.medrt import ClassConcept


def _run(conn, source="MED-RT"):
    """Create an ingest_run row and return its id (every row needs provenance)."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, nui, name="Test Class [MoA]", cty="MoA"):
    """Insert a class row, returning its deterministic uuid."""
    cu = ids.mint_class_uuid(nui)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, %s, %s, %s, %s, %s)", (cu, nui, nui, name, cty, run_id))
    return cu


def _moiety(conn, run_id, name="testium"):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


@pytest.mark.parametrize("cty", ["MoA", "PE", "TC", "PK", "EPC", "APC"])
def test_every_ingested_concept_type_is_accepted(conn, cty):
    _class(conn, _run(conn), f"N000000{cty}", cty=cty)


@pytest.mark.parametrize("cty", ["HC", "EXT", "nonsense"])
def test_uningested_concept_types_are_rejected(conn, cty):
    """HC is alphabetical navigation scaffolding ("A [Preparations]") and EXT has
    no ingredient membership -- neither belongs in the class registry."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _class(conn, _run(conn), "N0000000001", cty=cty)


@pytest.mark.parametrize("rel", ["has_MoA", "has_PE", "has_TC", "has_PK", "has_EPC"])
def test_every_membership_relationship_is_accepted(conn, rel):
    run_id = _run(conn)
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s)",
                 (_moiety(conn, run_id), _class(conn, run_id, "N0000000009"), rel, run_id))


@pytest.mark.parametrize("rel", ["may_treat", "CI_with", "has_SC"])
def test_overlay_relationships_are_not_membership(conn, rel):
    """Indications and contraindications are curated-overlay data for a later
    slice; has_SC points into MeSH. None of them is class membership."""
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.class_membership "
                     "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s)",
                     (_moiety(conn, run_id), _class(conn, run_id, "N0000000010"), rel, run_id))


def test_a_class_may_have_many_parents(conn):
    """The classification structure is a DAG, not a tree."""
    run_id = _run(conn)
    child = _class(conn, run_id, "N0000000003")
    for nui in ("N0000000004", "N0000000005"):
        conn.execute("INSERT INTO drugref.class_parent "
                     "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                     (child, _class(conn, run_id, nui), run_id))
    n = conn.execute("SELECT count(*) FROM drugref.class_parent WHERE child_class_uuid = %s",
                     (child,)).fetchone()[0]
    assert n == 2


def test_a_class_may_not_be_its_own_parent(conn):
    run_id = _run(conn)
    cu = _class(conn, run_id, "N0000000006")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.class_parent "
                     "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                     (cu, cu, run_id))


def test_edge_tables_are_deletable_because_they_are_rebuildable_projections(conn):
    """The append-only floor guards IDENTITY, not feed projections. Re-ingesting a
    newer MED-RT release depends on being able to delete these rows."""
    run_id = _run(conn)
    child = _class(conn, run_id, "N0000000007")
    parent = _class(conn, run_id, "N0000000008")
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (child, parent, run_id))
    conn.execute("DELETE FROM drugref.class_parent WHERE child_class_uuid = %s", (child,))
    # Scoped to this test's own row: other modules' orchestrators commit their own
    # edges, so a global count would be testing them rather than this behaviour.
    assert conn.execute("SELECT count(*) FROM drugref.class_parent "
                        "WHERE child_class_uuid = %s", (child,)).fetchone()[0] == 0


# ---- the writer module ----------------------------------------------------


def test_upsert_class_is_idempotent_and_refreshes_the_name(conn):
    """Re-ingest must not duplicate, and an upstream rename should land -- but
    first_seen_ingest records when we FIRST saw the class and must not move."""
    r1, r2 = _run(conn), _run(conn)
    cu, was_new = classes.upsert_class(
        conn, ClassConcept("N0000123456", "N0000123456", "Old Name [MoA]", "MoA"), r1)
    again, was_new_again = classes.upsert_class(
        conn, ClassConcept("N0000123456", "N0000123456", "New Name [MoA]", "MoA"), r2)
    assert cu == again == ids.mint_class_uuid("N0000123456")
    # New only the first time: that is what lets a run report "classes added"
    # separately from "classes this release asserts".
    assert (was_new, was_new_again) == (True, False)
    name, first = conn.execute(
        "SELECT class_name, first_seen_ingest FROM drugref.substance_class WHERE class_uuid = %s",
        (cu,)).fetchone()
    assert name == "New Name [MoA]"
    assert first == r1


def test_upsert_class_stores_the_published_code_not_the_nui(conn):
    """medrt_code is the code AS PUBLISHED. It equals the NUI throughout the
    2026.07.06 release, so only a concept where they differ can show the column is
    genuinely carrying the code rather than a second copy of the identity key."""
    cu, _ = classes.upsert_class(
        conn, ClassConcept("N0000654321", "SOME-CODE", "Odd One [MoA]", "MoA"), _run(conn))
    assert conn.execute(
        "SELECT medrt_nui, medrt_code FROM drugref.substance_class WHERE class_uuid = %s",
        (cu,)).fetchone() == ("N0000654321", "SOME-CODE")


def test_moieties_by_rxcui_indexes_the_rxnorm_in_claims(conn):
    """The membership join key: MED-RT ingredients carry an RxCUI, and slice 1
    already stores one as an RXNORM_IN claim per moiety."""
    run_id = _run(conn, source="UNII")
    m = _moiety(conn, run_id)
    conn.execute("INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
                 "VALUES (%s, 'RXNORM_IN', '4242', %s)", (m, run_id))
    index = classes.moieties_by_rxcui(conn)
    assert index["4242"] == [m]
    assert "no-such-rxcui" not in index


def test_moieties_by_rxcui_keeps_every_claimant_not_just_one(conn):
    """identity_claim is unique on (moiety, scheme, value), so two moieties CAN
    claim one RxCUI -- slice 1 copies the value straight out of the UNII feed
    without checking. Keeping one of them would drop a real membership and make the
    ingest non-reproducible, since an unordered single-row read may pick
    differently run to run. chebi.py made the same call for InChIKey."""
    run_id = _run(conn, source="UNII")
    a, b = _moiety(conn, run_id, "alphium"), _moiety(conn, run_id, "betium")
    for m in (a, b):
        conn.execute("INSERT INTO drugref.identity_claim "
                     "(moiety_uuid, scheme, value, ingest_run) "
                     "VALUES (%s, 'RXNORM_IN', '7777', %s)", (m, run_id))
    assert classes.moieties_by_rxcui(conn)["7777"] == sorted([a, b])


def test_moieties_by_rxcui_ignores_superseded_claims(conn):
    """A corrected-away RxCUI must not keep dragging in stale memberships."""
    run_id = _run(conn, source="UNII")
    m = _moiety(conn, run_id)
    old = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'RXNORM_IN', '5555', %s) RETURNING identity_claim_id", (m, run_id)).fetchone()[0]
    new = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'RXNORM_IN', '6666', %s) RETURNING identity_claim_id", (m, run_id)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s",
                 (new, old))
    index = classes.moieties_by_rxcui(conn)
    assert "5555" not in index
    assert index["6666"] == [m]


def test_moieties_by_rxcui_ignores_other_schemes(conn):
    """Only RXNORM_IN is a membership join key. A UNII or CAS value that happens to
    read like an RxCUI must not classify anything."""
    run_id = _run(conn, source="UNII")
    m = _moiety(conn, run_id)
    conn.execute("INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
                 "VALUES (%s, 'CAS', '9999', %s)", (m, run_id))
    assert "9999" not in classes.moieties_by_rxcui(conn)


def test_clear_source_edges_removes_only_that_sources_rows(conn):
    """Rebuild semantics: a new MED-RT release replaces MED-RT edges and leaves any
    other source's edges untouched."""
    medrt_run, other_run = _run(conn, source="MED-RT"), _run(conn, source="SOMETHING-ELSE")
    child = _class(conn, medrt_run, "N0000222222")
    parent = _class(conn, medrt_run, "N0000333333")
    classes.add_parent_edge(conn, child, parent, medrt_run)
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (parent, child, other_run))
    classes.clear_source_edges(conn, "MED-RT")
    # Scoped to this test's own two classes, for the reason the deletability test
    # above gives: other modules' orchestrators commit their own edges, so a global
    # query would be asserting about their rows rather than about clear_source_edges.
    assert conn.execute(
        "SELECT ingest_run FROM drugref.class_parent "
        "WHERE child_class_uuid = ANY(%s) AND parent_class_uuid = ANY(%s)",
        ([child, parent], [child, parent])).fetchall() == [(other_run,)]


def test_repeated_edges_and_memberships_do_not_duplicate(conn):
    """A file that asserts the same edge twice must not create two rows."""
    run_id = _run(conn)
    child, parent = _class(conn, run_id, "N0000444444"), _class(conn, run_id, "N0000555555")
    assert classes.add_parent_edge(conn, child, parent, run_id) is True
    assert classes.add_parent_edge(conn, child, parent, run_id) is False
    m = _moiety(conn, run_id)
    assert classes.add_membership(conn, m, child, "has_MoA", run_id) is True
    assert classes.add_membership(conn, m, child, "has_MoA", run_id) is False
