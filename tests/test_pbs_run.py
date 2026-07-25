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
SEED_INNS = ["rifaximin", "abacavir", "lamivudine", "abiraterone",
             "methylprednisolone", "alfuzosin", "dimethyl fumarate",
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
    """THE REGRESSION. 'Dimethyl fumarate' is itself an INN. Trying the stripped
    form first would match nothing (or worse, the wrong moiety) and would be
    recorded as salt_stripped."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    row = conn.execute(
        "SELECT b.component_name, b.match_method FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = 'Dimethyl fumarate'").fetchone()
    assert row == ("dimethyl fumarate", "exact")


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
