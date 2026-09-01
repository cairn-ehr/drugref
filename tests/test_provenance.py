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
import datetime
import pathlib
import time

import psycopg
import pytest

from drugref import provenance
from drugref.ingest import gate, run

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
                                 source_checksum="sum", writer="unii_run",
                                 clock=provenance.start_clock())

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT upstream_release FROM drugref.ingest_run WHERE ingest_run_id = %s",
            (run_id,)).fetchone() == ("r1",)


def test_finish_run_does_not_commit(conn, _migrated):
    """The stamp belongs to the work's transaction. If finish_run committed, a run
    could be marked finished and then have its work rolled back underneath it."""
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run",
                                 clock=provenance.start_clock())
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
        "curation", "unattributed", "gsrs_run", "onchigh_run", "fda_cyp_run",
        "drugcentral_run", "spl_run")
    for writer in provenance.WRITERS:
        conn.execute("INSERT INTO drugref.ingest_run "
                     "(source, upstream_release, source_checksum, writer) "
                     "VALUES ('UNII', 'r1', 'sum', %s)", (writer,))
    conn.rollback()


# ---- the duration the two stamps are supposed to bound (#159) ----------------
#
# WHAT WAS WRONG, in one sentence: both stamps were `now()`, which is
# `transaction_timestamp()`, so `finished_at - started_at` measured the gap between
# two TRANSACTION START TIMES and never the work between them. Measured on the
# project's own verification databases, every one of the nine feeds reported between
# 1.3 ms and 24 ms -- except mesh_rel_run, which parses 750 MB of MeSH BETWEEN
# open_run and its first write and so reported 48.3 s of exactly the wrong thing: the
# time the orchestrator spent NOT touching the database.
#
# THE ISSUE'S OWN HEADLINE EXAMPLE EVAPORATED UNDER IT. #159 was written from
# `drugref_spl051`, where spl_run reported 49.85 s; the COPY-cost round then added a
# `conn.rollback()` before the DailyMed scan, which moved `open_run` onto the far side
# of it, and on the two databases built 2026-09-01 spl_run reports 2.6 ms. Nobody
# looked. These tests are what would have noticed.


def test_finish_run_stamps_the_clock_not_the_transaction(conn, _migrated):
    """`finished_at` must be read from the clock, not from the transaction's start.

    THE PRODUCTION CHANGE THIS CATCHES: `finish_run` writing `now()`. Under it this
    assertion is exactly 0 -- the stamp and `now()` are the same value by definition,
    however long the work took -- so any positive lower bound fails.

    The sleep is inside the WORK transaction, which is the one whose start `now()`
    would report: `open_run` commits, so the statement below opens a fresh one.
    """
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run",
                                 clock=provenance.start_clock())
    conn.execute("SELECT pg_sleep(0.25)")
    provenance.finish_run(conn, run_id)

    ahead_of_transaction_start = conn.execute(
        "SELECT finished_at - now() FROM drugref.ingest_run WHERE ingest_run_id = %s",
        (run_id,)).fetchone()[0]
    assert ahead_of_transaction_start >= datetime.timedelta(seconds=0.25)


def test_open_run_dates_the_row_from_the_clock_it_is_handed(conn, _migrated):
    """`started_at` must date the RUN, not the INSERT that records it.

    THE PRODUCTION CHANGE THIS CATCHES: `open_run` letting the column default fill
    itself in, which dates the row from the statement rather than from the orchestrator
    entry -- and so silently drops every parse, scan and checksum an orchestrator does
    before opening its run. mesh_rel_run does 48 s of it; spl_run does the DailyMed
    scan and a 19.3 GB checksum.

    The interval is measured SERVER-SIDE on both ends -- `clock_timestamp()` minus a
    client-measured elapsed -- so nothing here compares a client clock with a server
    one.
    """
    clock = provenance.start_clock()
    time.sleep(0.25)
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run", clock=clock)

    backdated_by = conn.execute(
        "SELECT clock_timestamp() - started_at FROM drugref.ingest_run "
        "WHERE ingest_run_id = %s", (run_id,)).fetchone()[0]
    assert backdated_by >= datetime.timedelta(seconds=0.25)


def test_open_run_refuses_a_bare_clock_reading(conn, _migrated):
    """A `float` is not a RunClock, and the difference is 56 years.

    `start_clock` wraps `time.monotonic()`, whose zero is arbitrary and
    process-relative. Handing `open_run` a `time.time()` reading instead type-checks
    under any annotation Python enforces at runtime (none), and would record a run
    that began in 1970 -- a wrong DURATION rather than a crash, which is the failure
    mode this whole issue is about. So the type is checked rather than annotated.
    """
    with pytest.raises(TypeError, match="RunClock"):
        provenance.open_run(conn, source="UNII", upstream_release="r1",
                            source_checksum="sum", writer="unii_run",
                            clock=time.time())


def test_every_module_that_opens_a_run_takes_a_clock(_migrated):
    """DERIVED FROM THE TREE, NOT HAND-LISTED -- one commit after a round whose
    hand-listed coverage named three writers where four edges existed.

    A module that calls `open_run` without calling `start_clock` can only be passing a
    clock started on the line above, which measures nothing. `open_run` requires the
    argument, so this cannot be forgotten silently; what it CAN be is satisfied
    uselessly, and that is what this greps for.

    The `assert openers` is not decoration: a typo in the needle makes the list empty
    and every later assertion vacuously true, which is the shape of the guard the last
    review round found passing with itself deleted.
    """
    openers = [p for p in _sources() if "provenance.open_run(" in p.read_text()]
    assert openers, "the needle matched no module; the grep, not the tree, is wrong"
    assert [p.name for p in openers
            if "provenance.start_clock()" not in p.read_text()] == []


def test_a_run_records_the_work_done_before_it_opened(conn, _migrated, monkeypatch):
    """The duration must cover what the orchestrator did BEFORE `open_run`.

    THE MUTANT THIS KILLS, and it is the only test here that kills it: moving
    `start_clock()` down to the line above `open_run`. Every other assertion in this
    module still holds under that mutant -- both stamps are still clock readings, the
    row is still committed early -- and the published number is still wrong for the
    two feeds it matters for, because mesh_rel_run parses 750 MB of MeSH and spl_run
    scans 17.6 GB of DailyMed before their runs exist.

    The delay is injected into `gate.load_crosswalk`, which `ingest_unii` calls BEFORE
    `open_run`. A sleep anywhere after `open_run` would pass under the mutant too, and
    so would prove nothing.
    """
    real = gate.load_crosswalk

    def slow_crosswalk(*args, **kwargs):
        time.sleep(0.25)
        return real(*args, **kwargs)

    monkeypatch.setattr("drugref.ingest.gate.load_crosswalk", slow_crosswalk)

    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW, allowlist_path=AL,
                    upstream_release="2026-07")

    recorded = conn.execute(
        "SELECT finished_at - started_at FROM drugref.ingest_run").fetchone()[0]
    assert recorded >= datetime.timedelta(seconds=0.25)
