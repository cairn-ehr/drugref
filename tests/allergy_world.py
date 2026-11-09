"""A small, real-data world for the allergy-group tests (Tasks 3, 4 and 6 import it).

Every UNII here is the REAL one drugref's registry holds for that moiety (checked
against drugref_profiles, 2026-11-09), so the allergy ingest bridges to these
moieties through the same UNII claims a production database carries. The MeSH side
is Task 2's fixture pair (tests/fixtures/allergy_*_subset.xml) and the shipped
allow-list.

Nothing here commits by itself, but `run_allergy_ingest` calls the orchestrator,
which DOES commit -- so a test module using this needs an autouse TRUNCATE (see
tests/test_allergy_group_run.py), not the conn fixture's rollback.
"""
import pathlib
import uuid

from drugref import claims, composition, ids
from drugref.ingest import allergy_group_run

FIX = pathlib.Path(__file__).parent / "fixtures"
DESC = FIX / "allergy_desc_subset.xml"
SUPP = FIX / "allergy_supp_subset.xml"
SHIPPED_ALLOW_LIST = (pathlib.Path(__file__).parent.parent
                      / "src/drugref/data/allergy_groups.toml")

# The pinned class_uuid literal of "Penicillins (allergy group)":
# mint_class_uuid("DRUGREF-ALLERGY", "D010406"). cairn stores this in allergy
# records permanently, so a refactor of ids.py must not be able to re-key it.
PENICILLINS_UUID = "55a1d1a0-66b0-5831-8bd7-b1cc29be1f80"

# name -> real UNII. Grouped by the role each plays in the fixture world.
MOIETIES = {
    # In the penicillin group via a MeSH descriptor (D005436, D000658).
    "flucloxacillin": "43B2M34G2V",
    "amoxicillin": "804826J2HU",             # the trihydrate drugref calls amoxicillin
    "amoxicillin anhydrous": "9EM05410Q9",
    # In via a supplementary concept record (C031367 -> *D010406).
    "temocillin": "03QB156W6I",
    # In via the allow-list's `include` (MeSH carries no bridge key for these forms).
    "piperacillin anhydrous": "9I628532GX",
    "carbenicillin indanyl sodium": "4OUL81K2RT",
    # Under Penicillins structurally, but excluded by the allow-list (D013407).
    "sulbactam": "S4TF6I2330",
    # Placed in MeSH, outside the group's subtree.
    "perindopril": "Y5GMK36KGY",
    "cephalexin": "OBN7UDS42Y",
    "procaine": "4Z8Y51M438",
    # Composition components for Task 4's fold cases. NOTE: the fixtures carry no
    # Penicillin G / Ampicillin descriptor, so these two are NOT members here.
    "penicillin g": "Q42T66VG0C",
    "clemizole": "T97CB3796L",
    "ampicillin": "7C782967RD",
    # Made-up but well-formed: no MeSH record carries it, so it stays unplaced.
    "unplaced test moiety": "ZZZZZZZZZZ",
}

# substance UNII -> [(component name, is_active_component)] -- real GSRS rows.
COMPOSITIONS = {
    "17R794ESYN": [("penicillin g", True), ("procaine", True)],   # penicillin g procaine
    "5UL276H6TF": [("penicillin g", False), ("clemizole", True)],  # clemizole penicillin
    "JFN36L5S8K": [("ampicillin", True)],                          # ampicillin sodium
}


def _run(conn, source: str, writer: str) -> int:
    """Insert a bare ingest_run row (writer is NOT NULL since db/025)."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES (%s, 'test', 'deadbeef', %s) RETURNING ingest_run_id",
        (source, writer)).fetchone()[0]


def seed_moiety(conn, name: str, unii: str, run_id: int | None = None) -> uuid.UUID:
    """Register one moiety under its UNII-derived UUID, with its live UNII claim."""
    if run_id is None:
        run_id = _run(conn, "UNII", "unii_run")
    moiety_uuid = ids.mint_moiety_uuid(unii)
    claims.upsert_moiety(conn, moiety_uuid, name, run_id)
    claims.add_claim(conn, moiety_uuid, "UNII", unii, run_id)
    return moiety_uuid


def seed_moieties(conn, names=None) -> dict[str, uuid.UUID]:
    """Seed `names` (default: all of MOIETIES); return {name: moiety_uuid}."""
    run_id = _run(conn, "UNII", "unii_run")
    return {name: seed_moiety(conn, name, MOIETIES[name], run_id)
            for name in (names or MOIETIES)}


def seed_compositions(conn, moieties: dict[str, uuid.UUID]) -> None:
    """Insert COMPOSITIONS as substance_composition rows (needs those components
    seeded, e.g. by seed_moieties)."""
    run_id = _run(conn, "GSRS", "gsrs_run")
    for substance_unii, components in COMPOSITIONS.items():
        for name, active in components:
            composition.add_composition(
                conn, substance_unii=substance_unii, component_moiety=moieties[name],
                relation="SALT_SOLVATE", is_active_component=active,
                ingest_run_id=run_id)


def run_allergy_ingest(conn, allow_list_path=SHIPPED_ALLOW_LIST, release="2026"):
    """Run the allergy-group orchestrator on the fixtures. COMMITS (see above)."""
    return allergy_group_run.ingest_allergy_groups(
        conn, desc_path=DESC, supp_path=SUPP, allow_list_path=allow_list_path,
        upstream_release=release)
