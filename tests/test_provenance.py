# tests/test_provenance.py
"""The run record: one writer, and a crash that leaves a trace (#16).

THE ASYMMETRY IS THE DESIGN and is what these tests exist to hold:

* open_run COMMITS, because the row has to outlive the rollback of the work it
  describes. Without that commit, `finished_at IS NULL` asserts a state that can
  never be observed and a crashed run is indistinguishable from one that never ran.
* finish_run does NOT commit, because the stamp must land in the same transaction as
  the work. A separate commit would let `finished` become true about data that is not
  there -- the same failure one line further down.
"""
import pathlib

import psycopg
import pytest

from drugref import provenance
from drugref.ingest import run

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"

SRC = pathlib.Path("src/drugref")


@pytest.fixture(autouse=True)
def _clean(conn):
    """These tests commit (that is the point), so the conn fixture's rollback cannot
    isolate them."""
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.moiety_admission, drugref.open_question, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def test_open_run_commits_the_row_immediately(conn, _migrated):
    """Visible from ANOTHER connection before any work happens -- the property the
    whole issue turns on, and one a same-connection SELECT could not prove."""
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run")

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT upstream_release FROM drugref.ingest_run WHERE ingest_run_id = %s",
            (run_id,)).fetchone() == ("r1",)


def test_finish_run_does_not_commit(conn, _migrated):
    """The stamp belongs to the work's transaction. If finish_run committed, a run
    could be marked finished and then have its work rolled back underneath it."""
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run")
    provenance.finish_run(conn, run_id)

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT finished_at FROM drugref.ingest_run WHERE ingest_run_id = %s",
            (run_id,)).fetchone() == (None,)

    conn.commit()
    assert conn.execute(
        "SELECT finished_at FROM drugref.ingest_run WHERE ingest_run_id = %s",
        (run_id,)).fetchone()[0] is not None


def test_a_crashed_ingest_leaves_its_run_row_behind(conn, _migrated, monkeypatch):
    """#16 IN ONE ASSERTION, and it cannot pass before this task.

    Reproduced the way the issue describes it: raise before the work commits, then
    look from a FRESH session. The work is gone; the provenance is not.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr("drugref.questions.register_from_gaps", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW, allowlist_path=AL,
                        upstream_release="2026-07")

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT count(*) FROM drugref.substance_moiety").fetchone()[0] == 0
        assert other.execute(
            "SELECT source, writer, finished_at FROM drugref.ingest_run").fetchall() \
            == [("UNII", "unii_run", None)]
        assert other.execute(
            "SELECT count(*) FROM drugref.ingest_run_incomplete").fetchone()[0] == 1


def test_a_successful_ingest_leaves_a_finished_run(conn, _migrated):
    """The other half of the partition: nothing in ingest_run_incomplete, one row in
    loaded_release. Without this, "the crash test passes" could mean "no run is ever
    stamped"."""
    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW, allowlist_path=AL,
                    upstream_release="2026-07")

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT count(*) FROM drugref.ingest_run_incomplete").fetchone()[0] == 0
        assert other.execute(
            "SELECT source, writer, upstream_release FROM drugref.loaded_release"
        ).fetchall() == [("UNII", "unii_run", "2026-07")]


# ---- the one-writer contract -------------------------------------------------


def _sources():
    return sorted(SRC.rglob("*.py"))


def test_only_provenance_writes_a_run_record():
    """One reader, one clear, one checksum -- and now ONE RUN RECORD (#40, #43).

    Six modules wrote these four lines by hand, and the fix for #16 had to be made in
    all six or in none. Restated as a grep rather than by importing anything, for the
    same reason test_source_clear_contract restates each writer's table tuple: driving
    the expectation off the code under test would pass whatever that code said.
    """
    writers = [p for p in _sources()
               if "INSERT INTO drugref.ingest_run" in p.read_text()]
    assert [p.name for p in writers] == ["provenance.py"]


def test_only_provenance_stamps_a_run_finished():
    """The other half of the record. A module that stamped finished_at itself could
    mark a run complete without the work being committed, which is the exact failure
    finish_run's no-commit contract exists to prevent."""
    stampers = [p for p in _sources() if "SET finished_at" in p.read_text()]
    assert [p.name for p in stampers] == ["provenance.py"]


def test_the_writer_vocabulary_matches_the_database(conn):
    """provenance.WRITERS and db/025's CHECK are a PAIR (db/020's source-trio lesson,
    one table over): a value admitted to one and not the other is either refused at
    write time or invisible to the contract above. Restated here independently."""
    assert provenance.WRITERS == (
        "unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
        "curation", "unattributed")
    for writer in provenance.WRITERS:
        conn.execute("INSERT INTO drugref.ingest_run "
                     "(source, upstream_release, source_checksum, writer) "
                     "VALUES ('UNII', 'r1', 'sum', %s)", (writer,))
    conn.rollback()
