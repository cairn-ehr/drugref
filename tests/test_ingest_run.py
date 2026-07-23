# tests/test_ingest_run.py
import pathlib
import pytest
from drugref.ingest import run
from drugref import ids

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"


@pytest.fixture(autouse=True)
def _clean_ingest_tables(conn):
    # ingest_unii() commits internally, so the conn fixture's rollback can't
    # isolate these tests. Truncate the drugref tables before each test so the
    # acceptance-matrix counts are order-independent.
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def _ingest(conn, release="2026-07"):
    return run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW,
                           allowlist_path=AL, upstream_release=release)


def test_registers_only_gated_moieties(conn):
    n = _ingest(conn)
    # acetaminophen + amlodipine (has-INN) + magnesium sulfate (allow-list) = 3;
    # microcrystalline cellulose excluded.
    assert n == 3
    names = {r[0] for r in conn.execute("SELECT display_name FROM drugref.substance_moiety").fetchall()}
    assert names == {"paracetamol", "amlodipine", "magnesium sulfate"}
    assert "microcrystalline cellulose" not in names


def test_cross_reference_claims_present(conn):
    _ingest(conn)
    m = ids.mint_moiety_uuid("362O9ITL9D")  # acetaminophen
    claims = dict(conn.execute(
        "SELECT scheme, value FROM drugref.identity_claim WHERE moiety_uuid = %s", (m,)).fetchall())
    assert claims["UNII"] == "362O9ITL9D"
    assert claims["INN"] == "paracetamol"
    assert claims["CAS"] == "103-90-2"
    assert claims["RXNORM_IN"] == "161"
    assert claims["PUBCHEM_CID"] == "1983"


def test_reingest_is_idempotent(conn):
    _ingest(conn)
    _ingest(conn)  # run again — same UUIDs, no duplicate claims
    n_moiety = conn.execute("SELECT count(*) FROM drugref.substance_moiety").fetchone()[0]
    n_claim = conn.execute("SELECT count(*) FROM drugref.identity_claim").fetchone()[0]
    assert n_moiety == 3
    # acetaminophen: UNII+INN+CAS+RXNORM_IN+PUBCHEM_CID+INCHIKEY = 6; amlodipine = 6;
    # magnesium sulfate (no INN): UNII+CAS+RXNORM_IN = 3. Total = 15.
    assert n_claim == 15


def test_immortality_uuid_survives_upstream_rxcui_remap(conn, tmp_path):
    _ingest(conn)
    m = ids.mint_moiety_uuid("362O9ITL9D")
    # Simulate a new upstream release where acetaminophen's RxCUI changed.
    remapped = tmp_path / "unii_remap.tsv"
    remapped.write_text(
        "UNII\tPT\tRN\tRXCUI\tPUBCHEM\tINN_ID\tINCHIKEY\n"
        "362O9ITL9D\tACETAMINOPHEN\t103-90-2\t999999\t1983\t6689\tRZVAJINKPMORJF-UHFFFAOYSA-N\n")
    run.ingest_unii(conn, unii_path=remapped, crosswalk_path=XW, allowlist_path=AL,
                    upstream_release="2026-08")
    # Same UUID (unchanged); the new RxCUI is an ADDED claim, the old one retained.
    still = conn.execute("SELECT count(*) FROM drugref.substance_moiety WHERE moiety_uuid = %s", (m,)).fetchone()[0]
    assert still == 1
    rxcuis = {r[0] for r in conn.execute(
        "SELECT value FROM drugref.identity_claim WHERE moiety_uuid = %s AND scheme = 'RXNORM_IN'", (m,)).fetchall()}
    assert rxcuis == {"161", "999999"}
