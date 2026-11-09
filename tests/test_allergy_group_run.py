"""DB-gated tests for the allergy-group orchestrator (allergy_group_run).

The world is tests/allergy_world.py: real UNIIs for the moieties, Task 2's MeSH
fixtures, the shipped allow-list. The orchestrator commits, so every test starts
from a TRUNCATE rather than relying on the conn fixture's rollback.
"""
import pathlib

import pytest

from drugref import class_grain_read, ids
from drugref.ingest import mesh_run
from tests import allergy_world as world
from tests import class_profile_world as cpw

MESH_FIX = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean(conn):
    conn.execute("TRUNCATE drugref.allergy_group_member, drugref.allergy_group, "
                 "drugref.allergy_mesh_placement, drugref.substance_composition, "
                 "drugref.class_membership, drugref.class_parent, "
                 "drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


@pytest.fixture
def moieties(conn):
    return world.seed_moieties(conn)


def _members(conn) -> dict:
    """{moiety display_name: set of via} over every allergy_group_member row."""
    out: dict = {}
    for name, via in conn.execute(
            "SELECT m.display_name, g.via FROM drugref.allergy_group_member g "
            "JOIN drugref.substance_moiety m USING (moiety_uuid)").fetchall():
        out.setdefault(name, set()).add(via)
    return out


def _placed(conn) -> set:
    return {r[0] for r in conn.execute(
        "SELECT m.display_name FROM drugref.allergy_mesh_placement p "
        "JOIN drugref.substance_moiety m USING (moiety_uuid)").fetchall()}


def test_pinned_literal_matches_the_derivation():
    assert str(ids.mint_class_uuid("DRUGREF-ALLERGY", "D010406")) == \
        world.PENICILLINS_UUID


def test_penicillin_group_registered(conn, moieties):
    summary = world.run_allergy_ingest(conn)
    assert summary.groups == 1
    rows = conn.execute(
        "SELECT class_uuid::text, source, concept_type, class_name "
        "FROM drugref.substance_class").fetchall()
    assert rows == [(world.PENICILLINS_UUID, "DRUGREF-ALLERGY", "AG",
                     "Penicillins (allergy group)")]
    assert conn.execute(
        "SELECT class_uuid::text, mesh_root_ui, review_status "
        "FROM drugref.allergy_group").fetchall() == \
        [(world.PENICILLINS_UUID, "D010406", "structural-seed")]


def test_members(conn, moieties):
    summary = world.run_allergy_ingest(conn)
    members = _members(conn)
    assert members["flucloxacillin"] == {"descriptor:D005436"}
    assert members["temocillin"] == {"scr:C031367"}
    assert members["amoxicillin"] == {"descriptor:D000658"}
    assert members["amoxicillin anhydrous"] == {"descriptor:D000658"}
    assert members["piperacillin anhydrous"] == {"include:9I628532GX"}
    assert not {"sulbactam", "perindopril", "cephalexin", "procaine"} & members.keys()
    # An include carries the allow-list's reason; a MeSH-derived row carries none.
    reason = conn.execute(
        "SELECT reason FROM drugref.allergy_group_member "
        "WHERE via = 'include:9I628532GX'").fetchone()[0]
    assert reason.startswith("piperacillin anhydrous:")
    assert summary.members == sum(len(v) for v in members.values())
    assert summary.includes_unresolved == ()


def test_placement(conn, moieties):
    summary = world.run_allergy_ingest(conn)
    placed = _placed(conn)
    assert {"perindopril", "cephalexin", "procaine"} <= placed
    assert "unplaced test moiety" not in placed
    # Every member is placed, including the include-only ones.
    assert set(_members(conn)) <= placed
    assert summary.placed_moieties == len(placed)


def test_unresolved_include_reported(conn, moieties, tmp_path):
    toml = tmp_path / "a.toml"
    toml.write_text(
        '[[group]]\nroot = "D010406"\nreview_status = "structural-seed"\n'
        'include = [{ unii = "YYYYYYYYYY", reason = "not seeded" }]\n')
    summary = world.run_allergy_ingest(conn, allow_list_path=toml)
    assert summary.includes_unresolved == ("YYYYYYYYYY",)


def test_rebuild_idempotent(conn, moieties):
    world.run_allergy_ingest(conn)
    first = (sorted(conn.execute("SELECT class_uuid, moiety_uuid, via, reason "
                                 "FROM drugref.allergy_group_member").fetchall()),
             _placed(conn))
    world.run_allergy_ingest(conn, release="2026b")
    again = (sorted(conn.execute("SELECT class_uuid, moiety_uuid, via, reason "
                                 "FROM drugref.allergy_group_member").fetchall()),
             _placed(conn))
    assert again == first
    # The class row was first seen by the FIRST allergy run, not re-minted.
    runs = [r[0] for r in conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run "
        "WHERE source = 'DRUGREF-ALLERGY' ORDER BY 1").fetchall()]
    assert len(runs) == 2
    assert conn.execute("SELECT first_seen_ingest FROM drugref.substance_class "
                        "WHERE source = 'DRUGREF-ALLERGY'").fetchall() == [(runs[0],)]
    # Every row now belongs to the second run: the first run's rows were cleared.
    assert conn.execute("SELECT DISTINCT ingest_run FROM drugref.allergy_group_member"
                        ).fetchall() == [(runs[1],)]


def _mesh(conn):
    return mesh_run.ingest_mesh(
        conn, pa_path=MESH_FIX / "mesh_pa_subset.xml",
        desc_path=MESH_FIX / "mesh_desc_subset.xml",
        supp_path=MESH_FIX / "mesh_supp_subset.xml", upstream_release="2026")


def _counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM drugref.{t}").fetchone()[0]
            for t in ("class_membership", "class_parent", "allergy_group_member",
                      "allergy_mesh_placement")}


def test_coexists_with_mesh_run(conn, moieties):
    # Paracetamol is the MeSH PA fixtures' known UNII join, so PA writes a membership.
    world.seed_moiety(conn, "paracetamol", "362O9ITL9D")
    _mesh(conn)
    pa_only = _counts(conn)
    assert pa_only["class_membership"] > 0 and pa_only["class_parent"] > 0
    world.run_allergy_ingest(conn)
    both = _counts(conn)
    assert both["allergy_group_member"] > 0 and both["allergy_mesh_placement"] > 0
    assert (both["class_membership"], both["class_parent"]) == \
        (pa_only["class_membership"], pa_only["class_parent"])
    _mesh(conn)
    assert _counts(conn) == both


def _snapshot(conn):
    """Every reader the spec says must not move (spec 6), as comparable values."""
    views = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.views WHERE table_schema = "
        "'drugref' AND (table_name LIKE 'gap\\_%' OR table_name = 'ci_class_subtree')"
    ).fetchall()]
    assert "ci_class_subtree" in views and len(views) > 1
    snap = {v: sorted(map(repr, conn.execute(f"SELECT * FROM drugref.{v}").fetchall()))
            for v in views}
    snap["class_grain_counts"] = class_grain_read.class_grain_counts(conn)
    return snap


def test_existing_readers_unchanged(conn, moieties):
    # A MED-RT world over the same moieties: a penicillin EPC under a parent, a
    # contraindication against it, and an unclassified moiety (gap_unclassified).
    r = cpw.run(conn, "MED-RT")
    epc = cpw.klass(conn, r, "Penicillin-class Antibacterial")
    top = cpw.klass(conn, r, "Antibacterial", concept_type="MoA")
    cpw.parent(conn, r, epc, top)
    cpw.member(conn, r, moieties["amoxicillin"], epc)
    cpw.class_ci(conn, r, moieties["perindopril"], epc)
    cpw.class_pair_ci(conn, r, epc, top)
    conn.commit()
    before, edges = _snapshot(conn), _counts(conn)

    world.run_allergy_ingest(conn)

    assert _snapshot(conn) == before
    after = _counts(conn)
    assert (after["class_membership"], after["class_parent"]) == \
        (edges["class_membership"], edges["class_parent"])
