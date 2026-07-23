# tests/test_chebi.py
import pathlib
import pytest
from drugref.ingest import run, chebi
from drugref import ids

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
CHEBI_FIX = pathlib.Path(__file__).parent / "fixtures" / "chebi_subset.tsv"
DATA = pathlib.Path("src/drugref/data")


@pytest.fixture(autouse=True)
def _clean_ingest_tables(conn):
    # enrich_from_chebi() (like ingest_unii()) commits internally, so the conn
    # fixture's rollback can't isolate these tests. Truncate the drugref tables
    # before each test so the counts are order-independent.
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def test_chebi_claim_attached_by_inchikey(conn):
    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=DATA / "usan_inn_crosswalk.tsv",
                    allowlist_path=DATA / "legacy_allowlist.tsv", upstream_release="2026-07")
    added = chebi.enrich_from_chebi(conn, chebi_path=CHEBI_FIX, upstream_release="chebi-2026-07")
    assert added == 2
    m = ids.mint_moiety_uuid("362O9ITL9D")  # paracetamol
    val = conn.execute(
        "SELECT value FROM drugref.identity_claim WHERE moiety_uuid = %s AND scheme = 'CHEBI'", (m,)).fetchone()[0]
    assert val == "CHEBI:46195"


def test_chebi_enrich_is_idempotent(conn):
    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=DATA / "usan_inn_crosswalk.tsv",
                    allowlist_path=DATA / "legacy_allowlist.tsv", upstream_release="2026-07")
    chebi.enrich_from_chebi(conn, chebi_path=CHEBI_FIX, upstream_release="chebi-2026-07")
    added_again = chebi.enrich_from_chebi(conn, chebi_path=CHEBI_FIX, upstream_release="chebi-2026-07")
    assert added_again == 0
    n_claim = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'CHEBI'").fetchone()[0]
    assert n_claim == 2
