# tests/test_ingest_run.py
import pathlib
import pytest
from drugref.ingest import run
from drugref import ids
from tests.test_unii import REAL_HEADER      # one opinion about upstream shape

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
    """The whole gate, end to end: 12 of the fixture's 15 rows are moieties.

    Admitted, one per branch of the #26 rule --
      INN_ID:           paracetamol, amlodipine, heparin sodium,
                        escitalopram, pimozide, halothane
      USAN_ID:          iron sucrose
      RXCUI+drug-like:  amoxicillin, morphine, mannitol
      allow-list:       magnesium sulfate, activated charcoal
    Rejected -- an excipient, another excipient, and a homeopathic botanical,
    all three carrying an RXCUI and excluded purely by substance type.

    escitalopram and pimozide join no new BRANCH of the gate; they are here because
    slice 5b needs both ends of a real CI_ChemClass pair registered (see
    tests/fixtures/make_unii_subset.py). They still belong in this count, because a
    gate test that ignored rows added for another slice would stop being end to end.
    Halothane (slice 5b.2) is the same story one axis over: it joins no new BRANCH
    either (INN_ID, same as paracetamol), and is here so `induces` / `may_diagnose`
    have a registered subject rather than only ibuprofen's unmatched one. Mannitol
    (#53) likewise adds no branch -- it is admitted on RXCUI + drug-like type, the
    same arm as amoxicillin -- and is here so the fixture holds one (drug, condition)
    pair carrying TWO therapeutic predicates alongside a contraindication.
    """
    n = _ingest(conn).moieties
    assert n == 12
    names = {r[0] for r in conn.execute("SELECT display_name FROM drugref.substance_moiety").fetchall()}
    # Note "magnesium sulfate, unspecified form": that IS what the real UNII
    # release calls it, and the display name is a LABEL sourced from upstream,
    # not a key -- the allow-list matched it on UNII (#17). Before #27 this row
    # was excluded entirely, because the list was keyed on a name upstream does
    # not use.
    assert names == {"paracetamol", "amlodipine", "heparin sodium", "iron sucrose",
                     "amoxicillin", "morphine", "escitalopram", "pimozide",
                     "magnesium sulfate, unspecified form", "activated charcoal",
                     "halothane", "mannitol"}
    for excluded in ("microcrystalline cellulose", "polysorbate 80",
                     "thuja occidentalis leaf"):
        assert excluded not in names


def test_gate_rejections_are_counted_not_silently_dropped(conn):
    # Every other ingest reports what it declined to carry as a worklist number
    # (medrt_run's unmatched_rxcuis, mesh_run's members_no_key). The identity
    # spine must do the same, or a legacy drug the allow-list narrowly misses
    # disappears as invisibly as an excipient.
    summary = _ingest(conn)
    # microcrystalline cellulose, polysorbate 80, thuja occidentalis leaf
    assert summary.gated_out == 3
    assert summary.rows_without_unii == 0


def test_rows_without_a_unii_are_refused_and_counted(conn, tmp_path):
    # Two rows whose UNII cell is blank but which pass the has-INN gate. Before
    # this guard both minted UUIDv5(namespace, "UNII:") and MERGED into one
    # moiety carrying both drugs' INNs, CAS numbers and RxCUIs -- and because
    # moiety_uuid is immortal and the floor forbids DELETE, that merge was
    # unrecoverable. They must be refused and counted instead.
    path = tmp_path / "blank_unii.tsv"
    path.write_text(
        REAL_HEADER +
        "362O9ITL9D\tACETAMINOPHEN\t103-90-2\t161\t1983\t626\tRZVAJINKPMORJF-UHFFFAOYSA-N\n"
        "\tMETFORMIN\t657-24-9\t6809\t4091\t4779\tXZWYZXKJPYQGQO-UHFFFAOYSA-N\n"
        "\tWARFARIN\t81-81-2\t11289\t54678486\t9312\tPJVWKTKQMONHTI-UHFFFAOYSA-N\n")
    summary = run.ingest_unii(conn, unii_path=path, crosswalk_path=XW,
                              allowlist_path=AL, upstream_release="2026-09")
    assert summary.moieties == 1
    assert summary.rows_without_unii == 2
    names = {r[0] for r in conn.execute(
        "SELECT display_name FROM drugref.substance_moiety").fetchall()}
    assert names == {"paracetamol"}
    # And nothing was minted from the empty key.
    orphan = ids.mint_moiety_uuid("")
    assert conn.execute("SELECT count(*) FROM drugref.substance_moiety "
                        "WHERE moiety_uuid = %s", (orphan,)).fetchone()[0] == 0


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
    assert n_moiety == 12
    # Counted from the REAL release rows the fixture extracts, so the arithmetic
    # is upstream's, not ours. Each moiety contributes UNII + (INN if has_inn) +
    # one claim per populated cross-ref column:
    #   paracetamol 6, amlodipine 6, escitalopram 6, pimozide 6, halothane 6,
    #   morphine 5, amoxicillin 5, mannitol 5, heparin sodium 4, iron sucrose 4,
    #   activated charcoal 3, magnesium sulfate 2  ->  58
    # Mannitol carries no INN (it is admitted on RXCUI + type), so its five are
    # UNII + CAS + RXNORM_IN + PUBCHEM_CID + INCHIKEY.
    assert n_claim == 58


def test_immortality_uuid_survives_upstream_rxcui_remap(conn, tmp_path):
    _ingest(conn)
    m = ids.mint_moiety_uuid("362O9ITL9D")
    # Simulate a new upstream release where acetaminophen's RxCUI changed.
    remapped = tmp_path / "unii_remap.tsv"
    remapped.write_text(
        REAL_HEADER +
        "362O9ITL9D\tACETAMINOPHEN\t103-90-2\t999999\t1983\t626\tRZVAJINKPMORJF-UHFFFAOYSA-N\n")
    run.ingest_unii(conn, unii_path=remapped, crosswalk_path=XW, allowlist_path=AL,
                    upstream_release="2026-08")
    # Same UUID (unchanged); the new RxCUI is an ADDED claim, the old one retained.
    still = conn.execute("SELECT count(*) FROM drugref.substance_moiety WHERE moiety_uuid = %s", (m,)).fetchone()[0]
    assert still == 1
    rxcuis = {r[0] for r in conn.execute(
        "SELECT value FROM drugref.identity_claim WHERE moiety_uuid = %s AND scheme = 'RXNORM_IN'", (m,)).fetchall()}
    assert rxcuis == {"161", "999999"}
