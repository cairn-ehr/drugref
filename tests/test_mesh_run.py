# tests/test_mesh_run.py
"""DB-gated acceptance matrix for the slice-2b MeSH PA ingest.

The expected numbers come from the real MeSH 2026 release the fixtures were
extracted from (spec §5), joined against the slice-1 seed. The bridge under test
is two-key: UNII-primary, CAS-fallback (spec §6), resolving a member's
RegistryNumber keys against the identity_claim rows slice 1 already recorded.
"""
import pathlib

import pytest

from drugref import ids
from drugref.ingest import mesh_run, medrt_run, run

FIX = pathlib.Path(__file__).parent / "fixtures"
PA = FIX / "mesh_pa_subset.xml"
DESC = FIX / "mesh_desc_subset.xml"
SUPP = FIX / "mesh_supp_subset.xml"
MEDRT_FIX = FIX / "medrt_subset.xml"
UNII_FIX = FIX / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"

PARACETAMOL = "362O9ITL9D"          # seed: UNII join (MeSH D000082 carries this UNII)
MAGNESIUM_SULFATE = "DE08037SAB"    # seed: CAS fallback (MeSH D008278 carries CAS only)

# A pinned MeSH class_uuid literal: mint_class_uuid("MeSH", "D000894"). Pins the
# derivation so a future ids.py refactor cannot silently re-key the PA axis (as
# three frozen literals pin MED-RT). See spec §4.
D000894_UUID = "297d7be6-7c4c-5a21-b3e7-430cf26e959c"


@pytest.fixture(autouse=True)
def _clean(conn):
    # The orchestrators commit internally, so the conn fixture's rollback cannot
    # isolate these tests; truncate first so counts are order-independent.
    conn.execute("TRUNCATE drugref.class_membership, drugref.class_parent, "
                 "drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


@pytest.fixture
def seeded(conn):
    """Classification needs the slice-1 moiety registry to join against."""
    run.ingest_unii(conn, unii_path=UNII_FIX, crosswalk_path=XW,
                    allowlist_path=AL, upstream_release="2026-07")
    return conn


def _ingest(conn, release="2026"):
    return mesh_run.ingest_mesh(conn, pa_path=PA, desc_path=DESC, supp_path=SUPP,
                                upstream_release=release)


def _classes_of(conn, unii, relationship=None):
    """Return {class_name: relationship} for one moiety."""
    sql = ("SELECT c.class_name, m.relationship FROM drugref.class_membership m "
           "JOIN drugref.substance_class c USING (class_uuid) WHERE m.moiety_uuid = %s")
    params = [ids.mint_moiety_uuid(unii)]
    if relationship:
        sql += " AND m.relationship = %s"
        params.append(relationship)
    return dict(conn.execute(sql, params).fetchall())


# ---- classes ---------------------------------------------------------------


def test_registers_every_pa_class(seeded):
    summary = _ingest(seeded)
    assert (summary.classes_in_release, summary.classes_added) == (6, 6)
    rows = seeded.execute(
        "SELECT concept_type, source, count(*) FROM drugref.substance_class "
        "GROUP BY 1, 2").fetchall()
    assert rows == [("PA", "MeSH", 6)]


def test_class_uuid_matches_derivation_and_a_pinned_literal(seeded):
    """class_uuid is a pure function of (source, code); pin one literal so a future
    ids.py change that re-keyed the axis would fail loudly here (spec §4)."""
    import uuid
    _ingest(seeded)
    derived = ids.mint_class_uuid("MeSH", "D000894")
    assert str(derived) == D000894_UUID
    stored = seeded.execute(
        "SELECT class_uuid FROM drugref.substance_class "
        "WHERE source = 'MeSH' AND source_code = 'D000894'").fetchone()[0]
    assert stored == uuid.UUID(D000894_UUID)


# ---- the DAG ---------------------------------------------------------------


def test_builds_the_tree_number_dag(seeded):
    """4 edges total: D000894 has three parents, D018712 one (spec §5.4)."""
    assert _ingest(seeded).parent_edges == 4
    child = ids.mint_class_uuid("MeSH", "D000894")
    parents = {r[0] for r in seeded.execute(
        "SELECT parent_class_uuid FROM drugref.class_parent WHERE child_class_uuid = %s",
        (child,)).fetchall()}
    assert parents == {ids.mint_class_uuid("MeSH", n)
                       for n in ("D000893", "D018501", "D018712")}


def test_dag_is_oriented_child_to_parent(seeded):
    """D000700 (Analgesics) sits ABOVE D018712 (Analgesics, Non-Narcotic)."""
    _ingest(seeded)
    parent, child = ids.mint_class_uuid("MeSH", "D000700"), ids.mint_class_uuid("MeSH", "D018712")
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (child, parent)).fetchone()[0] == 1
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (parent, child)).fetchone()[0] == 0


# ---- membership: the two-key bridge ----------------------------------------


def test_positive_unii_join(seeded):
    """Paracetamol's UNII (362O9ITL9D) matches MeSH member D000082 -> has_PA links
    to the two PA classes D000082 belongs to."""
    _ingest(seeded)
    assert _classes_of(seeded, PARACETAMOL, "has_PA") == {
        "Analgesics": "has_PA", "Analgesics, Non-Narcotic": "has_PA"}


def test_cas_fallback_join(seeded):
    """Magnesium sulfate carries NO UNII in MeSH (D008278); it must still join, via
    its CAS 7487-88-9 -> the case that makes the bridge two-key (spec §5.3/§6)."""
    _ingest(seeded)
    assert _classes_of(seeded, MAGNESIUM_SULFATE, "has_PA") == {
        "Analgesics": "has_PA", "Reproductive Control Agents": "has_PA"}


def test_membership_count_and_relationship(seeded):
    """4 rows: paracetamol (2 classes) + magnesium (2 classes); all has_PA."""
    summary = _ingest(seeded)
    assert summary.memberships == 4
    rels = {r[0] for r in seeded.execute(
        "SELECT DISTINCT relationship FROM drugref.class_membership").fetchall()}
    assert rels == {"has_PA"}


def test_no_key_member_is_counted_never_dropped(seeded):
    """SCR C007609 (aspirin/meprobamate combination) exposes neither UNII nor CAS.
    It must produce no membership and increment the no-key worklist number."""
    summary = _ingest(seeded)
    assert summary.members_no_key == 1


def test_key_not_in_registry_is_counted(seeded):
    """Aspirin (D001241, UNII not seeded) and bevonium (C000002, UNIIs not seeded)
    both carry a key that no gated-in moiety holds -> the second worklist number."""
    summary = _ingest(seeded)
    assert summary.members_key_not_in_registry == 2


def test_no_membership_points_outside_the_registry(seeded):
    _ingest(seeded)
    orphans = seeded.execute(
        "SELECT count(*) FROM drugref.class_membership m "
        "LEFT JOIN drugref.substance_moiety s USING (moiety_uuid) WHERE s.moiety_uuid IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_every_moiety_claiming_a_key_gets_classified(seeded):
    """Two moieties may carry the same identity claim; the bridge must classify
    BOTH, not an arbitrary one. Give paracetamol a UNII twin and check it, too, is
    linked to D000082's PA classes."""
    run_id = seeded.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII', 'test', 'deadbeef') RETURNING ingest_run_id").fetchone()[0]
    twin = ids.mint_moiety_uuid("TWIN-OF-PARACETAMOL")
    seeded.execute("INSERT INTO drugref.substance_moiety "
                   "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                   (twin, "paracetamol twin", run_id))
    seeded.execute("INSERT INTO drugref.identity_claim "
                   "(moiety_uuid, scheme, value, ingest_run) "
                   "VALUES (%s, 'UNII', %s, %s)", (twin, PARACETAMOL, run_id))
    seeded.commit()

    summary = _ingest(seeded)
    assert summary.memberships == 6          # 4 + a second copy of paracetamol's 2
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_membership WHERE moiety_uuid = %s",
        (twin,)).fetchone()[0] == 2


# ---- rebuild + provenance + source isolation -------------------------------


def test_reingest_rebuilds_edges_without_duplicating(seeded):
    first = _ingest(seeded)
    second = _ingest(seeded, release="2027")
    assert (second.classes_in_release, second.parent_edges, second.memberships) == \
           (first.classes_in_release, first.parent_edges, first.memberships)
    # Classes accumulate (nothing re-added); edges are rebuilt, not duplicated.
    assert (first.classes_added, second.classes_added) == (6, 0)
    counts = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.substance_class), "
        "       (SELECT count(*) FROM drugref.class_parent), "
        "       (SELECT count(*) FROM drugref.class_membership)").fetchone()
    assert counts == (6, 4, 4)


def test_a_mesh_rebuild_leaves_medrt_edges_intact(seeded):
    """Per-source clear_source_edges: re-ingesting MeSH must not touch MED-RT's
    DAG/membership rows, and vice-versa (spec §3)."""
    medrt_run.ingest_medrt(seeded, medrt_path=MEDRT_FIX, upstream_release="2026.07.06")
    medrt_edges = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.class_parent p JOIN drugref.ingest_run r "
        "        ON p.ingest_run = r.ingest_run_id WHERE r.source = 'MED-RT'), "
        "       (SELECT count(*) FROM drugref.class_membership m JOIN drugref.ingest_run r "
        "        ON m.ingest_run = r.ingest_run_id WHERE r.source = 'MED-RT')").fetchone()
    _ingest(seeded)
    after = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.class_parent p JOIN drugref.ingest_run r "
        "        ON p.ingest_run = r.ingest_run_id WHERE r.source = 'MED-RT'), "
        "       (SELECT count(*) FROM drugref.class_membership m JOIN drugref.ingest_run r "
        "        ON m.ingest_run = r.ingest_run_id WHERE r.source = 'MED-RT')").fetchone()
    assert after == medrt_edges and medrt_edges[0] > 0


def test_ingest_run_provenance_is_recorded(seeded):
    _ingest(seeded)
    source, release, finished = seeded.execute(
        "SELECT source, upstream_release, finished_at FROM drugref.ingest_run "
        "WHERE source = 'MeSH' ORDER BY ingest_run_id DESC LIMIT 1").fetchone()
    assert (source, release) == ("MeSH", "2026")
    assert finished is not None
