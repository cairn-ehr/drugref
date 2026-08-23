# tests/test_drugcentral_run.py
"""The orchestrator: it reconciles, and it is the only writer.

Issue 71's standing rule, re-learned by curate_onchigh and again by the
re-measurement's Measurement guard: a summary whose buckets do not sum is a
number that cannot be checked, and every row must land in exactly one of them.
"""
import pathlib

import pytest

from drugref.ingest import drugcentral, drugcentral_run

FIXTURE = pathlib.Path("tests/fixtures/drugcentral_ddi_subset.sql.gz")


def test_the_summary_refuses_to_exist_unless_its_buckets_sum():
    with pytest.raises(ValueError, match="do not sum"):
        drugcentral_run.DrugCentralSummary(
            rows_read=10, rows_excluded_by_reference=2, rows_bundleable=8,
            rows_resolved=5, rows_self_pair=0, rows_unresolved=1,  # 6, not 8
            pairs=5, duplicate_keys=0)


def test_the_summary_accepts_buckets_that_sum():
    summary = drugcentral_run.DrugCentralSummary(
        rows_read=10, rows_excluded_by_reference=2, rows_bundleable=8,
        rows_resolved=7, rows_self_pair=0, rows_unresolved=1,
        pairs=7, duplicate_keys=0)
    assert summary.rows_bundleable == 8


def test_the_summary_refuses_a_read_count_that_excludes_more_than_it_read():
    with pytest.raises(ValueError, match="do not sum"):
        drugcentral_run.DrugCentralSummary(
            rows_read=10, rows_excluded_by_reference=3, rows_bundleable=8,
            rows_resolved=8, rows_self_pair=0, rows_unresolved=0,
            pairs=8, duplicate_keys=0)


@pytest.fixture
def _clean(conn):
    """ingest_drugcentral COMMITS, so the conn fixture's rollback cannot undo it.
    Same shape as tests/test_ingest_run.py's autouse truncate fixture."""
    yield
    conn.execute("TRUNCATE drugref.drugcentral_ddi_assertion, "
                 "drugref.open_question, drugref.ingest_run CASCADE")
    conn.commit()


@pytest.mark.usefixtures("_clean")
def test_the_fixture_dump_ingests_and_reconciles(conn):
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    assert summary.rows_excluded_by_reference > 0, "the rule-6 filter did nothing"
    assert summary.rows_bundleable > 0
    stored = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone()[0]
    assert stored == summary.rows_bundleable


@pytest.mark.usefixtures("_clean")
def test_no_excluded_row_reaches_the_database(conn):
    """Rule 6 enforced by EXECUTION, not by reading the filter."""
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    leaked = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion "
        "WHERE upstream_label LIKE '%redacted%'").fetchone()[0]
    assert leaked == 0


@pytest.mark.usefixtures("_clean")
def test_a_second_ingest_replaces_rather_than_accumulates(conn):
    first = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    stored = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone()[0]
    assert stored == first.rows_bundleable


@pytest.mark.usefixtures("_clean")
def test_a_renumbered_reference_writes_nothing_at_all(conn, tmp_path):
    """The refusal must leave the database exactly as it was -- including no
    ingest_run row, which is why the guard runs before open_run."""
    import gzip as _gzip
    forged = tmp_path / "forged.sql.gz"
    original = _gzip.open(FIXTURE, "rt", encoding="utf-8").read()
    with _gzip.open(forged, "wt", encoding="utf-8") as out:
        out.write(original.replace("Veterans Health Administration", "Lexicomp"))
    before = conn.execute("SELECT count(*) FROM drugref.ingest_run").fetchone()[0]
    with pytest.raises(drugcentral.ReferenceIdentityError):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=forged, release="11012023")
    after = conn.execute("SELECT count(*) FROM drugref.ingest_run").fetchone()[0]
    assert after == before
