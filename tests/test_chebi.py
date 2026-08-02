# tests/test_chebi.py
import pathlib
import uuid
import pytest
from drugref.ingest import run, chebi
from drugref import claims, ids

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


def _new_run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII','r1','x','unii_run') RETURNING ingest_run_id").fetchone()[0]


def test_chebi_attaches_to_all_moieties_sharing_inchikey(conn, tmp_path):
    # An InChIKey is not guaranteed unique across moieties; ChEBI enrichment must
    # attach to EVERY match, not just the first row the lookup happens to return.
    key = "RZVAJINKPMORJF-UHFFFAOYSA-N"
    m1 = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
    m2 = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
    run_id = _new_run(conn)
    for m in (m1, m2):
        claims.upsert_moiety(conn, m, "shared", run_id)
        claims.add_claim(conn, m, "INCHIKEY", key, run_id)

    chebi_file = tmp_path / "chebi.tsv"
    chebi_file.write_text(f"CHEBI_ID\tINCHIKEY\nCHEBI:46195\t{key}\n")
    added = chebi.enrich_from_chebi(conn, chebi_path=chebi_file, upstream_release="c1")

    assert added == 2
    for m in (m1, m2):
        val = conn.execute(
            "SELECT value FROM drugref.identity_claim WHERE moiety_uuid = %s AND scheme = 'CHEBI'",
            (m,)).fetchone()[0]
        assert val == "CHEBI:46195"


def test_chebi_ignores_superseded_inchikey(conn, tmp_path):
    # A superseded INCHIKEY claim must not drag a ChEBI id back in: the lookup
    # filters superseded_by IS NULL, so a corrected-away key attaches nothing.
    key = "RZVAJINKPMORJF-UHFFFAOYSA-N"
    m = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
    run_id = _new_run(conn)
    claims.upsert_moiety(conn, m, "corrected", run_id)
    old_cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'INCHIKEY', %s, %s) RETURNING identity_claim_id",
        (m, key, run_id)).fetchone()[0]
    new_cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'INCHIKEY', 'CORRECTED-KEY', %s) RETURNING identity_claim_id",
        (m, run_id)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s",
                 (new_cid, old_cid))

    chebi_file = tmp_path / "chebi.tsv"
    chebi_file.write_text(f"CHEBI_ID\tINCHIKEY\nCHEBI:46195\t{key}\n")
    added = chebi.enrich_from_chebi(conn, chebi_path=chebi_file, upstream_release="c1")

    assert added == 0
    n = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE moiety_uuid = %s AND scheme = 'CHEBI'",
        (m,)).fetchone()[0]
    assert n == 0


def test_chebi_enrich_is_idempotent(conn):
    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=DATA / "usan_inn_crosswalk.tsv",
                    allowlist_path=DATA / "legacy_allowlist.tsv", upstream_release="2026-07")
    chebi.enrich_from_chebi(conn, chebi_path=CHEBI_FIX, upstream_release="chebi-2026-07")
    added_again = chebi.enrich_from_chebi(conn, chebi_path=CHEBI_FIX, upstream_release="chebi-2026-07")
    assert added_again == 0
    n_claim = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'CHEBI'").fetchone()[0]
    assert n_claim == 2
