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
import ast
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
# project's own verification databases, EIGHT of the nine feeds measured reported
# between 1.3 ms and 24 ms; the ninth was mesh_rel_run, which parses 750 MB of MeSH
# BETWEEN
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


def test_a_clock_cannot_be_built_from_a_wall_clock_reading():
    """THE HOLE THE isinstance CHECK LEFT: it guards the WRAPPER, not the value.

    `RunClock(time.time())` is one keystroke from `start_clock()` and is exactly the
    confusion open_run's error message describes -- and it used to pass, because a
    frozen dataclass with a public constructor and no validation accepts any float.
    What that produced was not an error but a run dated 2083 that open_run COMMITTED,
    followed by the whole ingest being thrown away when finish_run tripped db/053's
    CHECK on its last statement before the commit: hours of SPL work discarded for an
    argument error detectable on the orchestrator's first line, and a future-dated row
    left in ingest_run_incomplete forever.

    Rejected on the general predicate -- a monotonic reading cannot be in the future --
    rather than by sniffing for epoch scale, so `elapsed()`'s "never negative" becomes
    true by construction instead of by docstring.
    """
    with pytest.raises(ValueError, match="future"):
        provenance.RunClock(time.time())
    with pytest.raises(TypeError):
        provenance.RunClock("now")
    # The sanctioned constructor, and a hand-built PAST reading, both still work: this
    # must reject the wrong epoch, not every clock it did not make itself.
    assert provenance.start_clock().elapsed() >= 0
    assert provenance.RunClock(time.monotonic() - 5).elapsed() >= 5


def _clock_starters():
    """Every (file, function) in src/ whose body calls `start_clock()` anywhere.

    PARSED, NOT GREPPED, and the difference is not pedantry: the substring form of
    this test matched `onchigh_run.py` on a COMMENT (line 58 names
    `provenance.open_run(writer=WRITER)` in prose), so a module could have satisfied
    it with no call at all. `provenance.py` is excluded because `open_run`'s TypeError
    message quotes `start_clock()` as text.
    """
    for path in _sources():
        if path.name == "provenance.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and "start_clock" in ast.dump(node):
                yield path, node


def test_every_orchestrator_starts_its_clock_on_its_very_first_line(_migrated):
    """THE INVARIANT THE WHOLE ROUND RESTS ON, held structurally at last.

    ⇒ WHAT THE OLD GREP COULD NOT SEE. It asserted that a module calling `open_run`
    also contains the text `start_clock()` -- which is satisfied by a clock started on
    the line ABOVE `open_run`, measuring nothing. Its own docstring claimed to catch
    that ("what it CAN be is satisfied uselessly, and that is what this greps for");
    it did not, and could not. The only behavioural killer,
    test_a_run_records_the_work_done_before_it_opened, drives `ingest_unii` alone --
    so moving `start_clock()` down in spl_run.py, 108 lines and a 17.6 GB DailyMed
    scan above its `open_run`, silently dropped the entire figure this issue exists to
    publish and left the suite green.

    ⇒ WHAT THIS ASSERTS INSTEAD. In every function that starts a clock, that call is
    the FIRST executable statement -- docstring aside. It is exact, it is derived from
    the tree rather than hand-listed, and it costs no runtime. It also replaces eleven
    copies of a `# FIRST:` comment as the thing actually holding the rule, in a repo
    whose CLAUDE.md counts four rounds lost to one rule kept in two places.

    The positive control is not decoration: a broken parse or a renamed helper makes
    the population empty and every assertion below vacuously true.
    """
    starters = list(_clock_starters())
    assert len(starters) >= 11, (
        f"only {len(starters)} clock-starting functions found; the parse, not the "
        "tree, is wrong")
    late = []
    for path, node in starters:
        body = node.body
        # Skip the docstring, which is an Expr wrapping a bare string constant.
        first = body[1] if (isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)) else body[0]
        if "start_clock" not in ast.dump(first):
            late.append(f"{path.name}:{node.name}")
    assert late == [], (
        f"{late} start their clock after doing work; every second of that work is "
        "missing from the duration the operator reads off `drugref status`")


def test_only_provenance_turns_the_two_stamps_into_a_duration():
    """THE MIRROR OF THE ONE-WRITER CONTRACT, on the side this round is about.

    The write side has had two grep guards since #16; the READ side had none, in a
    round whose entire subject is a wrong read. Rows written before db/053 hold two
    transaction timestamps whose difference is a plausible number and not a duration,
    and `format_run_duration` is the one place that knows it. The next exporter,
    report or `tools/` script to write the subtraction itself would get that number
    back with nothing failing -- the schema's only defence is a column COMMENT, and no
    query reads a comment.

    ⇒ provenance.py IS EXCLUDED RATHER THAN EXPECTED. It holds the needle only in
    PROSE -- the docstrings explaining what the subtraction used to mean -- and
    asserting that prose stays put would pin an explanation rather than a contract.
    What this pins is that no OTHER module writes the subtraction at all.
    """
    subtractors = [p for p in _sources()
                   if p.name != "provenance.py"
                   and "finished_at - started_at" in p.read_text()]
    assert [p.name for p in subtractors] == []


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


# ---- the row says whether its own subtraction is a duration (#176) -----------
#
# WHAT WAS WRONG. db/053 changed what the two stamps MEAN, and a reader told the two
# meanings apart by comparing `started_at` with when db/053 was applied here. That
# asks WHEN the row was written; the question is WHICH CODE wrote it, and nothing on
# the row recorded that. The two come apart in both directions -- an older client
# publishing a confident wrong number, and a genuinely new row refused. db/054's
# boolean is self-identifying, and these are the writer's half of it; the reader's
# half is tests/test_ingest_run_duration.py.


def test_open_run_records_that_it_measured_the_duration(conn, _migrated):
    """`open_run` is the ONLY writer that sets the flag, which is what makes false the
    safe default for every other path. The production change this catches is `open_run`
    taking db/054's default like everyone else: every runtime `drugref status` prints
    would then read "unmeasured" forever -- a gate that refuses everything, which is
    the failure mode a refusal-based design has to be watched for."""
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run",
                                 clock=provenance.start_clock())
    assert conn.execute(
        "SELECT duration_measured FROM drugref.ingest_run WHERE ingest_run_id = %s",
        (run_id,)).fetchone()[0] is True


def test_a_run_backdated_before_the_migration_still_reports_its_runtime(conn,
                                                                       _migrated):
    """⇒ ISSUE 176'S OTHER HALF, and it is the one no reader could have seen.

    `open_run` BACKDATES `started_at` over the work an orchestrator does before any run
    row exists -- spl_run reads openFDA, scans 17.6 GB of DailyMed and checksums
    19.3 GB first -- so a run whose pre-open phase began before db/053 was applied is
    dated before the watershed although BOTH its stamps are correct server clock
    readings. The old reader refused it and pointed the operator at a column comment
    describing a defect their row did not have. That is a wrong statement about the
    first real measurement the fix exists to produce.

    The hour of backdating is what makes it reproducible: db/053 was applied by this
    session's `_migrated` fixture minutes ago, so a row backdated an hour lands on the
    far side of it with certainty rather than by timing. The LEDGER READ is what pins
    that -- without it the test would pass on any machine and prove nothing about the
    boundary it is here for.

    THE SECONDS FIELD IS NOT PINNED. The test's own wall clock is inside the recorded
    duration (the INSERT, its COMMIT and the UPDATE all land after the clock is
    constructed), so `60m00s` and `60m01s` are both correct answers on a loaded
    machine. What must hold is that the row is ACCEPTED and reports about an hour.
    """
    clock = provenance.RunClock(time.monotonic() - 3600)
    run_id = provenance.open_run(conn, source="SPL", upstream_release="r1",
                                 source_checksum="sum", writer="spl_run", clock=clock)
    provenance.finish_run(conn, run_id)

    started_at, finished_at, measured = conn.execute(
        "SELECT started_at, finished_at, duration_measured FROM drugref.ingest_run "
        "WHERE ingest_run_id = %s", (run_id,)).fetchone()
    applied_at = conn.execute(
        r"SELECT applied_at FROM drugref.schema_migration WHERE filename LIKE '053\_%'"
    ).fetchone()[0]

    assert started_at < applied_at, (
        "the backdated start must land BEFORE db/053 was applied, or this row is not "
        "the case #176 is about and the test proves nothing")
    assert measured is True
    printed = provenance.format_run_duration(
        started_at=started_at, finished_at=finished_at, duration_measured=measured)
    assert printed != provenance.UNMEASURED
    assert printed.startswith("60m"), printed

