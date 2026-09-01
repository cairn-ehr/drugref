# src/drugref/provenance.py
"""The one place a run record is written (#16).

WHY THIS MODULE EXISTS. Six orchestrators hand-wrote the same four lines -- INSERT the
ingest_run row, do the work, UPDATE finished_at, commit -- and every one of them wrote
the row INSIDE the transaction that did the work. A crashed run therefore rolled its
own provenance away, so `finished_at IS NULL` ("started, never finished") asserted a
state that could never be observed. Fixing that in six places is six chances to fix it
in five, which is the same argument that collapsed the per-source clear, the MeSH
reader and the checksum into one place each (#40, #43).

THE ASYMMETRY BETWEEN THE TWO FUNCTIONS IS THE WHOLE DESIGN. Read them together:
open_run commits, finish_run does not, and neither is free to change.

AND THE TWO STAMPS ARE CLOCK READINGS, NOT TRANSACTION TIMESTAMPS (#159). Both used
to be `now()`, which is `transaction_timestamp()`, so `finished_at - started_at`
measured the gap between two transaction START times and never the work between them.
Every one of drugref's nine feeds reported 1.3-24 ms for a load, and the one feed that
did not (mesh_rel_run, 48.3 s) was reporting the time it spent parsing 750 MB of MeSH
BEFORE its first write -- the complement of a duration. See start_clock below.
"""
import dataclasses
import time

import psycopg

# The writers db/025's CHECK admits, restated in Python because a value has to be
# spelled in both places to be usable. They are a PAIR: extend this tuple and the
# CHECK together, exactly as db/020's source trio must be extended together. A value
# in one and not the other is either refused at write time (Python-only) or invisible
# to callers (database-only).
#
# `curation` is not an orchestrator: it covers a DRUGREF-sourced run opened by a
# curator writing to Plan C's overlay tier. `unattributed` is historical only -- rows
# written before db/025, when two orchestrators shared a source and nothing told them
# apart -- and no code should ever write it.
WRITERS = ("unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
           "curation", "unattributed", "gsrs_run", "onchigh_run", "fda_cyp_run",
           "drugcentral_run", "spl_run")


@dataclasses.dataclass(frozen=True)
class RunClock:
    """When an ingest actually began, as a MONOTONIC reading (#159).

    WHY A TYPE AND NOT A float. open_run cannot tell a `time.monotonic()` reading from
    a `time.time()` one by looking -- both are floats, and Python enforces no
    annotation at runtime -- but the two differ by about 56 years, and handing over the
    wrong one records a run that began in 1970 rather than raising. That is a wrong
    DURATION, silently, which is the exact failure class #159 is about. The type makes
    the mistake impossible to make quietly.

    WHY MONOTONIC AND NOT WALL-CLOCK. `time.monotonic()` cannot go backwards, so an
    NTP step or a DST change part-way through a twelve-minute ingest cannot produce a
    negative duration. Its zero is arbitrary and process-relative, which is precisely
    why only the ELAPSED value ever leaves this module (see open_run).
    """

    started: float

    def elapsed(self) -> float:
        """Seconds since this clock was started. Never negative."""
        return time.monotonic() - self.started


def start_clock() -> RunClock:
    """Take the reading that dates a run. CALL IT FIRST IN AN ORCHESTRATOR.

    "First" is load-bearing and is the whole point of the argument. The orchestrators
    are NOT uniform in what they do before opening a run -- medrt_run, mesh_run and
    mesh_rel_run parse their whole release first, spl_run reads openFDA, loads the
    registry, scans 17.6 GB of DailyMed and checksums 19.3 GB -- and all of it is work
    an operator sizing a rebuild is asking about. A clock started on the line above
    open_run is accepted by every test here except one, and measures nothing.
    """
    return RunClock(time.monotonic())


def open_run(conn: psycopg.Connection, *, source: str, upstream_release: str,
             source_checksum: str, writer: str, clock: RunClock) -> int:
    """Open a run record and COMMIT it in its own transaction. Returns its id.

    THE COMMIT IS THE FEATURE, not an implementation detail: the row has to outlive
    the rollback of the work it describes, or a crashed ingest leaves no trace at all.
    After this returns, the caller is in a FRESH transaction and everything it does
    from here is the work -- which rolls back on failure, leaving this row standing
    with finished_at NULL and ingest_run_incomplete able to report it.

    TRANSACTION OWNERSHIP, and the contract this tightens: an orchestrator now takes
    TWO transactions on one connection. A caller with pending work has it committed at
    this boundary. Callers were already required to commit their own work before
    calling an orchestrator, so this narrows an existing rule rather than adding one --
    but it is the sort of narrowing that is silent when broken, hence this paragraph.

    `writer` is required and keyword-only: it says WHICH orchestrator this is, as
    distinct from the authority `source` names. One source can have two writers
    (MED-RT does), so a release is only unambiguous per (source, writer).

    `clock` is required for the same reason `writer` is: a new orchestrator cannot
    forget an argument it cannot omit. What crosses to the server is the ELAPSED
    INTERVAL, never the client's idea of the time -- `started_at` is then
    `clock_timestamp()` minus that interval, so BOTH stamps are read off the server's
    clock and the subtraction survives an ingest run from a host whose clock is
    minutes out. Sending a client timestamp instead would fold that skew straight into
    every published duration.
    """
    if not isinstance(clock, RunClock):
        raise TypeError(
            f"open_run needs a RunClock from provenance.start_clock(), not "
            f"{type(clock).__name__}: a bare monotonic/epoch reading cannot be told "
            "apart from the other, and the two differ by decades (#159)")
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer, started_at) "
        "VALUES (%s, %s, %s, %s, clock_timestamp() - make_interval(secs => %s)) "
        "RETURNING ingest_run_id",
        (source, upstream_release, source_checksum, writer,
         clock.elapsed())).fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int) -> None:
    """Stamp the run finished. DOES NOT COMMIT -- deliberately, and read this first.

    The stamp must land in the SAME transaction as the work it describes, so the
    orchestrator's own final commit publishes both atomically. Committing here would
    let `finished_at` become true about work that is subsequently rolled back: a
    consumer reading loaded_release would be told a release had landed while the
    projection still held the previous one. That is the exact failure open_run's early
    commit exists to expose, re-created one function later.

    Symmetry with open_run would therefore be a bug, not a tidy-up.

    `clock_timestamp()`, NOT `now()`, and the no-commit contract is why it matters:
    the stamp lands in a transaction that opened when the work's first statement ran,
    so `now()` here reports the START of the work and `finished_at - started_at`
    collapses to the gap between two transaction starts (#159). What this stamp still
    does NOT cover is the caller's final COMMIT, which happens after it by
    construction -- stamping after the commit is the very thing the paragraph above
    forbids. Say "the work" and mean it, not "the command".
    """
    conn.execute("UPDATE drugref.ingest_run SET finished_at = clock_timestamp() "
                 "WHERE ingest_run_id = %s", (run_id,))


def format_run_duration(started_at, finished_at, watershed) -> str:
    """PURE: what `drugref status` prints as one run's runtime.

    `watershed` is when db/053 was applied here (db.migration_applied_at), or None on a
    database that predates it.

    WHY THIS IS NOT A SUBTRACTION. Rows written before db/053 hold two TRANSACTION
    timestamps, and subtracting them still yields a number -- 0.0026 s for an SPL
    ingest that took 2 min 09 s. A number is what an operator believes, so the fix
    would otherwise survive as a wrong answer on every database not re-ingested since,
    which is all of them. Refusing to print one is the whole point: "pre-db/053" sends
    a reader to the column comment, where the meaning is written down.

    An unknown watershed is treated as "everything is old", which errs in the direction
    that says less rather than the one that says something false.
    """
    if finished_at is None:
        return "unfinished"
    if watershed is None or started_at < watershed:
        return "pre-db/053"
    seconds = (finished_at - started_at).total_seconds()
    if round(seconds, 1) < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:.0f}m{seconds:02.0f}s"
