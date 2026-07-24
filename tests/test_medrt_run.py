# tests/test_medrt_run.py
"""DB-gated acceptance matrix for the slice-2a MED-RT ingest.

The expected numbers come from the real MED-RT release the fixture was extracted
from, so they assert against upstream reality rather than against a fixture that
merely agrees with our assumptions.
"""
import pathlib

import pytest

from drugref import ids
from drugref.ingest import medrt_run, run

MEDRT_FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"
UNII_FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"

PARACETAMOL = "362O9ITL9D"
AMLODIPINE = "1J444QC288"
MAGNESIUM_SULFATE = "DE08037SAB"


@pytest.fixture(autouse=True)
def _clean(conn):
    # Both orchestrators commit internally, so the conn fixture's rollback cannot
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


def _ingest(conn, release="2026.07.06"):
    return medrt_run.ingest_medrt(conn, medrt_path=MEDRT_FIX, upstream_release=release)


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


def test_registers_every_ingested_class(seeded):
    assert _ingest(seeded).classes == 49
    types = dict(seeded.execute(
        "SELECT concept_type, count(*) FROM drugref.substance_class GROUP BY 1").fetchall())
    assert types == {"MoA": 12, "PE": 18, "EPC": 3, "TC": 5, "PK": 6, "APC": 5}


def test_hc_navigation_bins_never_become_classes(seeded):
    """'A [Preparations]' is an alphabetical bin, not a pharmacologic class."""
    _ingest(seeded)
    names = [r[0] for r in seeded.execute(
        "SELECT class_name FROM drugref.substance_class WHERE class_name LIKE '%[Preparations]%'"
    ).fetchall()]
    assert names == []


# ---- the DAG ---------------------------------------------------------------


def test_builds_the_dag_the_right_way_up(seeded):
    """The broad APC must come out ABOVE the specific EPC, not below it."""
    _ingest(seeded)
    parent, child = ids.mint_class_uuid("N0000193892"), ids.mint_class_uuid("N0000175421")
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (child, parent)).fetchone()[0] == 1
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (parent, child)).fetchone()[0] == 0


def test_a_class_keeps_both_of_its_parents(seeded):
    assert _ingest(seeded).parent_edges == 39
    child = ids.mint_class_uuid("N0000193892")
    parents = {r[0] for r in seeded.execute(
        "SELECT parent_class_uuid FROM drugref.class_parent WHERE child_class_uuid = %s",
        (child,)).fetchall()}
    assert parents == {ids.mint_class_uuid("N0000193904"), ids.mint_class_uuid("N0000193893")}


# ---- membership ------------------------------------------------------------


def test_links_paracetamol_on_the_right_axes(seeded):
    _ingest(seeded)
    assert _classes_of(seeded, PARACETAMOL, "has_MoA") == {
        "Prostaglandin Receptor Antagonists [MoA]": "has_MoA"}
    assert _classes_of(seeded, PARACETAMOL, "has_TC") == {
        "Anti-inflammatory Agent [TC]": "has_TC"}
    assert len(_classes_of(seeded, PARACETAMOL)) == 8


def test_amlodipine_gets_both_of_its_epc_classes(seeded):
    """EPC membership is hierarchical in MED-RT, not an association -- this is the
    clinically recognisable axis, so it is pinned explicitly."""
    _ingest(seeded)
    assert set(_classes_of(seeded, AMLODIPINE, "has_EPC")) == {
        "Dihydropyridine Calcium Channel Blocker [EPC]", "Calcium Channel Blocker [EPC]"}


def test_magnesium_sulfate_is_left_unclassified(seeded):
    """Its only MED-RT parent is the 'M [Preparations]' bin. Filing it under "M"
    would be worse than leaving it unclassified."""
    _ingest(seeded)
    assert _classes_of(seeded, MAGNESIUM_SULFATE) == {}


def test_unmatched_ingredient_is_skipped_and_counted_not_silently_dropped(seeded):
    """Ibuprofen is classified upstream but absent from our registry; it must be
    reported as a worklist number rather than vanishing."""
    summary = _ingest(seeded)
    assert summary.memberships == 17          # paracetamol 8 + amlodipine 9 + magnesium 0
    assert summary.unmatched_rxcuis == 1      # ibuprofen (RxCUI 5640)


def test_no_membership_points_outside_the_registry(seeded):
    _ingest(seeded)
    orphans = seeded.execute(
        "SELECT count(*) FROM drugref.class_membership m "
        "LEFT JOIN drugref.substance_moiety s USING (moiety_uuid) WHERE s.moiety_uuid IS NULL"
    ).fetchone()[0]
    assert orphans == 0


# ---- rebuild + provenance --------------------------------------------------


def test_reingest_rebuilds_edges_without_duplicating(seeded):
    """A second release REPLACES the previous edges; class UUIDs are unchanged."""
    first = _ingest(seeded)
    second = _ingest(seeded, release="2026.08.03")
    assert (second.classes, second.parent_edges, second.memberships) == \
           (first.classes, first.parent_edges, first.memberships)
    counts = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.substance_class), "
        "       (SELECT count(*) FROM drugref.class_parent), "
        "       (SELECT count(*) FROM drugref.class_membership)").fetchone()
    assert counts == (49, 39, 17)


def test_rebuild_drops_edges_that_vanished_upstream(seeded, tmp_path):
    """The point of a rebuildable projection: a class that lost a parent upstream
    must lose it here too, which an insert-only merge could never express."""
    _ingest(seeded)
    shrunk = tmp_path / "medrt_shrunk.xml"
    shrunk.write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n<terminology>\n'
        "\t<namespace><name>MED-RT</name><version>x</version></namespace>\n"
        "\t<concept><namespace>MED-RT</namespace><name>Lone Class [MoA]</name>"
        "<code>N0000000223</code><status>A</status>"
        "<property><namespace>MED-RT</namespace><name>CTY</name><value>MoA</value></property>"
        "<property><namespace>MED-RT</namespace><name>NUI</name><value>N0000000223</value></property>"
        "</concept>\n</terminology>\n", encoding="utf-8")
    summary = medrt_run.ingest_medrt(seeded, medrt_path=shrunk, upstream_release="2026.09.07")
    assert summary.parent_edges == 0 and summary.memberships == 0
    counts = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.class_parent), "
        "       (SELECT count(*) FROM drugref.class_membership)").fetchone()
    assert counts == (0, 0)
    # Edges rebuild, but class IDENTITY is immortal: every class from the earlier
    # release is still present with its UUID intact, and the release's one new
    # class is simply added alongside. Classes accumulate; they are never deleted.
    surviving = seeded.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE class_uuid = ANY(%s)",
        ([ids.mint_class_uuid(n) for n in ("N0000175421", "N0000175566", "N0000193892")],)
    ).fetchone()[0]
    assert surviving == 3
    assert seeded.execute("SELECT count(*) FROM drugref.substance_class").fetchone()[0] == 50


def test_ingest_run_provenance_is_recorded(seeded):
    _ingest(seeded)
    source, release, finished = seeded.execute(
        "SELECT source, upstream_release, finished_at FROM drugref.ingest_run "
        "WHERE source = 'MED-RT' ORDER BY ingest_run_id DESC LIMIT 1").fetchone()
    assert (source, release) == ("MED-RT", "2026.07.06")
    assert finished is not None
