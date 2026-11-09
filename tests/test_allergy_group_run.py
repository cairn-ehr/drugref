"""DB-gated tests for the allergy-group orchestrator (allergy_group_run).

The world is tests/allergy_world.py: real UNIIs for the moieties, Task 2's MeSH
fixtures, the shipped allow-list. The orchestrator commits, so every test starts
from a TRUNCATE rather than relying on the conn fixture's rollback.
"""
import pathlib
import uuid

import pytest

from drugref import class_grain_read, claims, ids, spl_class_evidence
from drugref.ingest import allergy_group_run, mesh_run
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


def test_placement_is_unii_primary_per_record(conn, moieties):
    # MeSH Amoxicillin (D000658) carries UNIIs 804826J2HU/9EM05410Q9 AND CAS
    # 26889-93-0. Its UNIIs resolve, so -- as for membership -- its CAS is never
    # tried, and a moiety holding only that CAS must stay unplaced, not "no-match".
    run_id = conn.execute("SELECT max(ingest_run_id) FROM drugref.ingest_run"
                          ).fetchone()[0]
    cas_only = ids.mint_moiety_uuid("XXXXXXXXXX")
    claims.upsert_moiety(conn, cas_only, "cas-only test moiety", run_id)
    claims.add_claim(conn, cas_only, "CAS", "26889-93-0", run_id)
    world.run_allergy_ingest(conn)
    assert "cas-only test moiety" not in _placed(conn)


def test_cas_fallback_makes_a_member_when_no_unii_resolves(conn):
    # The other half of the two-key rule: with neither amoxicillin UNII in the
    # registry, D000658's CAS 26889-93-0 is tried, and its holder joins the group.
    world.seed_moieties(conn, [n for n in world.MOIETIES
                               if not n.startswith("amoxicillin")])
    run_id = conn.execute("SELECT max(ingest_run_id) FROM drugref.ingest_run"
                          ).fetchone()[0]
    cas_only = ids.mint_moiety_uuid("XXXXXXXXXX")
    claims.upsert_moiety(conn, cas_only, "cas-only test moiety", run_id)
    claims.add_claim(conn, cas_only, "CAS", "26889-93-0", run_id)
    conn.commit()
    world.run_allergy_ingest(conn)
    assert _members(conn)["cas-only test moiety"] == {"descriptor:D000658"}
    assert "cas-only test moiety" in _placed(conn)


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


# Readers spec 6 says must not move, chosen by name from the catalog so a new
# view of these families is covered without editing this list.
_READER_VIEWS_SQL = (
    "SELECT table_name FROM information_schema.views WHERE table_schema = 'drugref' "
    "AND (table_name LIKE 'gap\\_%' OR table_name LIKE 'class\\_%' "
    "  OR table_name LIKE '%property%' OR table_name IN ('ci_class_subtree', "
    "  'curated_ddi_pair', 'ddi_candidate_pair', 'expansion_policy_unresolved')) "
    "ORDER BY 1")


def _snapshot(conn):
    """{reader: its whole output, order-free} for every reader under test."""
    views = [r[0] for r in conn.execute(_READER_VIEWS_SQL).fetchall()]
    assert {"ci_class_subtree", "class_subtree", "class_property", "member_property",
            "class_subclass_only_member", "class_pair_rule_reach", "curated_ddi_pair",
            "ddi_candidate_pair", "expansion_policy_unresolved"} <= set(views)
    snap = {v: sorted(map(repr, conn.execute(f"SELECT * FROM drugref.{v}").fetchall()))
            for v in views}
    snap["class_grain_counts"] = class_grain_read.class_grain_counts(conn)
    snap["spl_class_entries"] = sorted(
        map(repr, spl_class_evidence.load_class_entries(conn)))
    return snap


def _profile_world(conn, moieties):
    """A MED-RT world over the allergy moieties, rich enough that every class-profile
    reader has rows: an EPC (two direct members, both indicated for one condition)
    under a MoA parent, a subclass-only member, a contraindication against the EPC,
    a class-pair rule, and a CI_MoA rule with a has_MoA member."""
    r = cpw.run(conn, "MED-RT")
    epc = cpw.klass(conn, r, "Penicillin-class Antibacterial")
    top = cpw.klass(conn, r, "Antibacterial", concept_type="MoA")
    sub = cpw.klass(conn, r, "Isoxazolyl Penicillin")
    cpw.parent(conn, r, epc, top)
    cpw.parent(conn, r, sub, epc)
    infection = cpw.condition(conn, r, "Bacterial Infections")
    for name in ("amoxicillin", "amoxicillin anhydrous"):
        cpw.member(conn, r, moieties[name], epc)
        cpw.indication(conn, r, moieties[name], infection)
    cpw.member(conn, r, moieties["flucloxacillin"], sub)
    cpw.class_ci(conn, r, moieties["perindopril"], epc)
    cpw.class_pair_ci(conn, r, epc, top)
    # A CI_MoA rule (raw and curated) against the MoA class, which has a has_MoA
    # member: what ddi_candidate_pair and curated_ddi_pair expand.
    cpw.member(conn, r, moieties["cephalexin"], top, "has_MoA")
    cpw.class_ci(conn, r, moieties["perindopril"], top)
    cpw.curated_ci(conn, moieties["perindopril"], top)
    conn.commit()


def test_existing_readers_unchanged(conn, moieties):
    _profile_world(conn, moieties)
    before, edges = _snapshot(conn), _counts(conn)
    for reader in ("class_property", "member_property", "class_subclass_only_member",
                   "class_subtree", "ci_class_subtree", "spl_class_entries",
                   "class_pair_rule_reach", "curated_ddi_pair", "ddi_candidate_pair",
                   "expansion_policy_unresolved"):
        assert before[reader], f"{reader} is empty: the comparison would be vacuous"

    world.run_allergy_ingest(conn)

    after = _snapshot(conn)
    # The ONE accepted delta: class_subtree (db/021) lists every class as its own
    # root, so the AG class adds its self-row. An isolated root (no class_parent
    # edges) reaches nothing, and its consumers join class_membership, which the
    # allergy run never writes -- so no profile or expansion output can move.
    pen = uuid.UUID(world.PENICILLINS_UUID)
    assert after.pop("class_subtree") == sorted(
        before.pop("class_subtree") + [repr((pen, pen))])
    assert after == before
    counts = _counts(conn)
    assert (counts["class_membership"], counts["class_parent"]) == \
        (edges["class_membership"], edges["class_parent"])


@pytest.mark.parametrize("empty", ["desc", "supp"])
def test_empty_mesh_file_is_refused_before_any_write(conn, moieties, tmp_path, empty):
    # A truncated download parses to zero records and would silently rebuild an empty
    # group set (a shrunk allergy table = missed warnings). Refuse, name the file.
    bad = tmp_path / "empty.xml"
    bad.write_text("<Set/>")
    paths = {"desc_path": world.DESC, "supp_path": world.SUPP}
    paths[f"{empty}_path"] = bad
    with pytest.raises(ValueError, match="empty.xml"):
        allergy_group_run.ingest_allergy_groups(
            conn, allow_list_path=world.SHIPPED_ALLOW_LIST, upstream_release="x",
            **paths)
    assert conn.execute("SELECT count(*) FROM drugref.ingest_run").fetchone()[0] == 0


def test_summary_reports_the_record_counts(conn, moieties):
    s = world.run_allergy_ingest(conn)
    assert (s.desc_records, s.scr_records) == (15, 3)
    # Worklist: the root D010406 is keyless; bevonium (C000002) maps outside the
    # group, so nothing in the group fails to bridge to the seeded registry.
    assert (s.members_no_key, s.members_key_not_in_registry) == (1, 0)


def test_member_key_not_in_registry_is_counted(conn):
    # Without flucloxacillin seeded, its descriptor's keys bridge to nothing.
    world.seed_moieties(conn, [n for n in world.MOIETIES if n != "flucloxacillin"])
    conn.commit()
    assert world.run_allergy_ingest(conn).members_key_not_in_registry == 1


def _member_rows(conn):
    return sorted(conn.execute("SELECT class_uuid, moiety_uuid, via "
                               "FROM drugref.allergy_group_member").fetchall())


def test_a_failure_after_the_clear_keeps_the_previous_build(conn, moieties,
                                                            monkeypatch):
    # The clear and the rewrite share one transaction: a crash mid-write must roll
    # back to the last good groups, not leave them half-deleted.
    world.run_allergy_ingest(conn)
    before, placed = _member_rows(conn), _placed(conn)
    real, calls = allergy_group_run._insert_member, []

    def flaky(*a):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("boom")
        return real(*a)

    monkeypatch.setattr(allergy_group_run, "_insert_member", flaky)
    with pytest.raises(RuntimeError):
        world.run_allergy_ingest(conn, release="2026b")
    assert (_member_rows(conn), _placed(conn)) == (before, placed)


def test_a_group_with_no_member_rows_is_refused(conn, moieties, tmp_path):
    # D001561's only record in the fixture is keyless and its one SCR (bevonium) is
    # not seeded: the group would commit EMPTY, and an empty group answers no-match
    # for every placed drug. Refuse it, and keep the previous build.
    world.run_allergy_ingest(conn)
    before = _member_rows(conn)
    toml = tmp_path / "a.toml"
    toml.write_text(world.SHIPPED_ALLOW_LIST.read_text()
                    + '\n[[group]]\nroot = "D001561"\nreview_status = "structural-seed"\n')
    with pytest.raises(ValueError, match="D001561"):
        world.run_allergy_ingest(conn, allow_list_path=toml, release="2026b")
    assert _member_rows(conn) == before


def test_an_empty_allow_list_keeps_the_previous_build(conn, moieties, tmp_path):
    world.run_allergy_ingest(conn)
    before = _member_rows(conn)
    toml = tmp_path / "a.toml"
    toml.write_text("")
    with pytest.raises(ValueError):
        world.run_allergy_ingest(conn, allow_list_path=toml, release="2026b")
    assert _member_rows(conn) == before
