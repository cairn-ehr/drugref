# tests/test_pbs_run.py
"""DB-gated acceptance matrix for the PBS ingest.

Seeds a small moiety registry with INN claims, then ingests the committed fixture
and asserts on the BRIDGE -- which is the only thing slice 8a is really testing.
"""
import pathlib

import pytest

from drugref import claims, ids
from drugref.ingest import pbs_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "pbs_items_subset.csv"


@pytest.fixture(autouse=True)
def _clean(conn):
    """ingest_pbs COMMITS internally, so conftest's rollback cannot isolate these
    tests. Truncate first, exactly as test_medrt_run.py does, so counts are
    order-independent.

    NOTE (ROADMAP floor-hardening): this fixture depends on the very TRUNCATE
    bypass that item plans to close, so this module is now the THIRD one coupled
    to it. Add it to that note.
    """
    conn.execute(
        "TRUNCATE drugref.local_product_moiety, drugref.local_unmatched_ingredient, "
        "drugref.local_product, drugref.identity_claim, drugref.substance_moiety, "
        "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield

# INN claims are stored lower-case; PBS publishes Title-case. The fold is what
# makes these meet, so seeding them lower-case mirrors production exactly.
#
# "dimethyl" (bare, no "fumarate") is NOT a real INN -- it is planted here
# adversarially, alongside the real "dimethyl fumarate", so the ordering
# regression test below can actually distinguish the two lookup orders. Without
# it, a stripped-first lookup of "dimethyl fumarate" -> "dimethyl" simply MISSES
# (nothing was seeded under that bare key) and falls through to the exact match
# anyway, landing on the identical result regardless of order -- which is
# exactly the gap the fix-round review caught.
SEED_INNS = ["rifaximin", "abacavir", "lamivudine", "abiraterone",
             "methylprednisolone", "alfuzosin", "dimethyl fumarate", "dimethyl",
             "alendronic acid", "folic acid", "paracetamol", "aspirin"]


@pytest.fixture
def seeded_registry(conn):
    """A moiety per SEED_INN, each carrying its INN identity claim."""
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII', 'seed', 'seed') RETURNING ingest_run_id").fetchone()[0]
    out = {}
    for index, inn in enumerate(SEED_INNS):
        moiety_uuid = ids.mint_moiety_uuid(f"SEEDUNII{index:02d}")
        conn.execute(
            "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
            "first_seen_ingest) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, inn, run_id))
        claims.add_claim(conn, moiety_uuid, "INN", inn, run_id)
        out[inn] = moiety_uuid
    return out


def _bridged_names(conn, drug_name):
    return {row[0] for row in conn.execute(
        "SELECT b.component_name FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = %s", (drug_name,)).fetchall()}


def test_exact_match_bridges(conn, seeded_registry):
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Rifaximin") == {"rifaximin"}


def test_combination_fans_out_to_both_components(conn, seeded_registry):
    """'Abacavir with lamivudine' must produce TWO bridge rows, not one."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Abacavir with lamivudine") == {"abacavir", "lamivudine"}


def test_salt_stripped_match_is_labelled(conn, seeded_registry):
    """'Alfuzosin hydrochloride' matches only after stripping, and the row must
    say so -- otherwise a heuristic masquerades as an exact match."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    method = conn.execute(
        "SELECT b.match_method FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = 'Alfuzosin hydrochloride'").fetchone()[0]
    assert method == "salt_stripped"


def test_unstripped_name_wins_over_the_salt_fallback(conn, seeded_registry):
    """THE REGRESSION. 'Dimethyl fumarate' is itself an INN, and the registry ALSO
    carries a bare 'dimethyl' moiety (a synthetic plant -- see SEED_INNS) so the
    two lookup orders are forced to disagree instead of quietly agreeing:

    * correct order (exact first)  -> the 'dimethyl fumarate' moiety, 'exact'
    * wrong order (strip first)    -> the 'dimethyl' moiety, 'salt_stripped'

    Without the bare 'dimethyl' plant, a stripped-first lookup of 'dimethyl
    fumarate' would simply MISS (nothing seeded under that key) and fall through
    to the exact match anyway -- so both orderings would land on the identical
    row and this test would pass either way, pinning nothing."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    component_name, match_method, moiety_uuid = conn.execute(
        "SELECT b.component_name, b.match_method, b.moiety_uuid "
        "FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = 'Dimethyl fumarate'").fetchone()
    assert (component_name, match_method) == ("dimethyl fumarate", "exact")
    assert moiety_uuid == seeded_registry["dimethyl fumarate"]
    assert moiety_uuid != seeded_registry["dimethyl"]


def test_acid_names_match_exactly(conn, seeded_registry):
    """'Alendronic acid' and 'Folic acid' must match whole -- 'acid' is part of
    the INN, not a salt token."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Alendronic acid") == {"alendronic acid"}
    assert _bridged_names(conn, "Folic acid") == {"folic acid"}


def test_partial_combination_is_recorded_honestly(conn, seeded_registry):
    """'Amoxicillin with clavulanic acid': neither component is seeded, so both
    must land in the unmatched list rather than the product silently vanishing."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    unmatched = {row[0] for row in conn.execute(
        "SELECT component_name FROM drugref.local_unmatched_ingredient").fetchall()}
    assert "amoxicillin" in unmatched
    assert "clavulanic acid" in unmatched


def test_null_sentinel_row_uses_the_drug_name_fallback(conn, seeded_registry):
    """The NULLCASE_1 fixture row has li_drug_name='null' and drug_name='Aspirin'."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Aspirin") == {"aspirin"}
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product WHERE drug_name = 'null'"
    ).fetchone()[0] == 0


def test_summary_counts_are_consistent(conn, seeded_registry):
    summary = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert summary.items_read == summary.products_written
    assert summary.products_bridged <= summary.products_written
    assert summary.bridge_rows_salt_stripped >= 1
    assert summary.combination_products >= 2


def test_re_ingest_is_idempotent(conn, seeded_registry):
    first = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    second = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert first.products_written == second.products_written
    total = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert total == second.products_written


# The canary values make_pbs_subset.py plants in the fixture's atc_code/amt_code
# columns. They are NOT upstream -- the fixture adds them precisely so this test
# proves drugref DISCARDS them, instead of passing merely because they were absent.
ATC_CANARY = "ZZZ_ATC_CANARY"
AMT_CANARY = "ZZZ_AMT_CANARY"


def _all_text_columns(conn):
    """Every text-ish column in the drugref schema, for an exhaustive sweep."""
    return conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND data_type IN ('text','character varying') "
        "ORDER BY table_name, column_name").fetchall()


def test_no_encumbered_value_reaches_any_drugref_table(conn, seeded_registry):
    """THE LICENCE GUARANTEE, EXECUTABLE (spec section 6).

    ATC codes are WHO-owned (NonCommercial + NoDerivatives) and AMT/SNOMED CT-AU
    is NCTS-licensed; neither may ever enter drugref -- that is a licence
    constraint, not a style preference, and a breach here is a legal problem for
    every downstream user of the data, not a bug report.

    The committed fixture (tests/fixtures/pbs_items_subset.csv) carries a planted
    ATC_CANARY/AMT_CANARY value in every row's atc_code/amt_code column. Those
    columns do not exist in the real upstream items.csv -- make_pbs_subset.py
    adds them on purpose, precisely so this test cannot pass just because the
    encumbered columns happened to be absent. It only passes if drugref actively
    reads items.csv WITHOUT those columns and never carries their values into any
    table, which is what the licence actually requires.

    This test sweeps every text/varchar column in the whole drugref schema
    (not just the PBS tables) after a full ingest, so a leak via an unexpected
    path -- a future column, a copy-paste into the wrong table -- is caught too.
    """
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    offenders = []
    for table, column in _all_text_columns(conn):
        hits = conn.execute(
            f'SELECT count(*) FROM drugref."{table}" WHERE "{column}" IN (%s, %s)',
            (ATC_CANARY, AMT_CANARY)).fetchone()[0]
        if hits:
            offenders.append(f"{table}.{column}")
    assert offenders == [], f"encumbered value leaked into: {offenders}"


def test_rebuild_is_scoped_to_pbs(conn, seeded_registry):
    """A PBS re-ingest must not touch another source's projection. The registry
    seeded by the UNII run above must survive untouched."""
    before = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'INN'").fetchone()[0]
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-08-01", "testsum2")
    after = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'INN'").fetchone()[0]
    assert after == before
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_moiety").fetchone()[0] == len(SEED_INNS)


def test_rebuild_drops_a_delisted_item(conn, seeded_registry, tmp_path):
    """The projection must SHRINK when upstream does -- the property that makes
    delete-and-rebuild the right model and an append-only floor the wrong one."""
    import csv as _csv
    rows = list(_csv.DictReader(open(FIXTURE, newline="", encoding="utf-8-sig")))
    smaller = tmp_path / "items.csv"
    with open(smaller, "w", newline="", encoding="utf-8-sig") as fh:
        writer = _csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows[:2])
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "a")
    full = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    pbs_run.ingest_pbs(conn, smaller, "2026-08-01", "b")
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product").fetchone()[0] == 2 < full
