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
On the round's verification databases -- NINE of the eleven writers, which is what was
measured and not a count of drugref's feeds -- eight reported 1.3-24 ms for a load, and
the ninth (mesh_rel_run, 48.3 s) was reporting the time it spent parsing 750 MB of MeSH
BEFORE its first write, the complement of a duration. See start_clock below.
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
    NTP step or a DST change during the orchestrator's pre-open work cannot produce a
    negative duration. Its zero is arbitrary and process-relative, which is precisely
    why only the ELAPSED value ever leaves this module (see open_run).

    WHAT THIS DOES NOT COVER, because the scope is easy to over-read: only the CLIENT
    window before `open_run`. The long span BETWEEN the two stamps is bounded by two
    reads of the SERVER's wall clock, so a backward step on the database host, larger
    than the run's own duration, still trips db/053's CHECK -- see that constraint's
    catalog comment, which tells the operator what to do about it.
    """

    started: float

    def __post_init__(self) -> None:
        """Refuse a reading that is not from `time.monotonic()`, AT CONSTRUCTION.

        THE isinstance CHECK IN open_run GUARDS THE WRAPPER, NOT THE VALUE, and the
        mistake it names -- handing over `time.time()` -- is one keystroke from
        `start_clock()`: `RunClock(time.time())` passed it. What that produced was not
        an error but a run dated 2083, COMMITTED by open_run, with the whole ingest
        then thrown away when finish_run tripped db/053's CHECK on its last statement
        before the commit. Hours of SPL work discarded for an argument error that was
        detectable on the orchestrator's first line, and a future-dated row left
        standing in ingest_run_incomplete forever.

        A monotonic reading is process-uptime-scale and CANNOT be in the future, while
        an epoch reading is ~1.8e9 and always is. Testing "not in the future" rather
        than "not epoch-scale" is the general predicate: it also rejects the reading
        from a clock that has not been taken yet, and it is exactly the invariant
        `elapsed` promises, so the promise becomes true by construction rather than by
        docstring.
        """
        if not isinstance(self.started, (int, float)) or isinstance(self.started, bool):
            raise TypeError(
                f"a RunClock is a time.monotonic() reading, not "
                f"{type(self.started).__name__} (#159)")
        if self.started > time.monotonic():
            raise ValueError(
                f"{self.started!r} is not a time.monotonic() reading: it is in the "
                "future, which a monotonic reading cannot be. A time.time() value "
                "looks identical and is ~56 years off -- use provenance.start_clock() "
                "(#159)")

    def elapsed(self) -> float:
        """Seconds since this clock was started. Never negative -- see __post_init__,
        which is what makes that true rather than merely intended."""
        return time.monotonic() - self.started


def start_clock() -> RunClock:
    """Take the reading that dates a run. CALL IT FIRST IN AN ORCHESTRATOR.

    "First" is load-bearing and is the whole point of the argument. The orchestrators
    are NOT uniform in what they do before opening a run -- medrt_run, mesh_run and
    mesh_rel_run parse their whole release first, spl_run reads openFDA, loads the
    registry, scans 17.6 GB of DailyMed and checksums 19.3 GB -- and all of it is work
    an operator sizing a rebuild is asking about. A clock started on the line above
    open_run measures nothing, and used to be accepted by every test but one --
    test_a_run_records_the_work_done_before_it_opened, which drives `ingest_unii`
    alone, so the mutation was invisible in the two writers it matters most for.
    test_every_orchestrator_starts_its_clock_on_its_very_first_line now holds this
    structurally, by parsing the tree rather than grepping it.
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


# THE WATERSHED MIGRATION, SPELLED ONCE. The number is read by cli.py (to look the
# ledger row up) and printed by the refusal below, and a vocabulary written down twice
# is two things that can disagree -- db/020's source trio and WRITERS above are the same
# lesson. Renumbering db/053 while a hard-coded "pre-db/053" stayed behind would leave
# the message naming a migration nobody can look up.
WATERSHED_MIGRATION = "053"
PRE_WATERSHED = f"pre-db/{WATERSHED_MIGRATION}"


def format_run_duration(*, started_at, finished_at, watershed) -> str:
    """PURE: what `drugref status` prints as one run's runtime.

    KEYWORD-ONLY, for the reason open_run already gives for `writer` and `clock`:
    three interchangeable datetimes in a row is an argument-order slip waiting to
    happen, and two of the three wrong orders were SILENT -- (finished, started,
    watershed) printed a negative runtime, and (started, watershed, finished) printed
    "pre-db/053" forever. Neither is visible to a caller, and this is the one function
    whose whole job is refusing to publish a number it cannot vouch for.

    `watershed` is when db/053 was applied here (db.migration_applied_at), or None on a
    database that predates it.

    WHY THIS IS NOT A SUBTRACTION. Rows written before db/053 hold two TRANSACTION
    timestamps, and subtracting them still yields a number -- 0.0026 s for an SPL
    ingest that took 2 min 16 s. A number is what an operator believes, so the fix
    would otherwise survive as a wrong answer on every database not re-ingested since,
    which is all of them. Refusing to print one is the whole point: "pre-db/053" sends
    a reader to the column comment, where the meaning is written down.

    An unknown watershed is treated as "everything is old" -- both on a database that
    predates db/053 and on one with no ledger at all (a hand-replayed schema, a partial
    restore). That errs in the direction that says less rather than the one that says
    something false, and `drugref status` prints WHY rather than leaving a reader to
    guess which of the two it is.

    WHAT THIS TEST CANNOT ANSWER, AND #176 IS OPEN ABOUT IT. Comparing `started_at`
    against `applied_at` asks WHEN; the question is WHICH CODE WROTE THE ROW, and no
    column records that. Two cases come apart, one of them silently:

      * an OLDER client writing to a db/053 database takes the new clock_timestamp()
        default for started_at and old finish_run's now() for finished_at, so the CHECK
        does not fire, the row is dated after the watershed, and a two-second run is
        published as "0.0s" -- issue 159's own failure mode, reproduced;
      * a genuinely new row whose backdated start precedes the migration (an ingest in
        its pre-open phase while db/053 was applied) is refused although both its
        stamps are correct.

    A boolean set by open_run would make each row self-identifying and needs no clock
    comparison; that is #176. What is NOT acceptable meanwhile is a comment claiming
    the failure cannot happen, which is why it is written down here.
    """
    if finished_at is None:
        return "unfinished"
    if watershed is None or started_at < watershed:
        return PRE_WATERSHED
    # ROUND ONCE. The `< 60` decision used to be taken on `round(seconds, 1)` while
    # the minutes branch re-rounded the UNROUNDED remainder with `{:02.0f}` -- two
    # roundings of one quantity, which is one rule kept in two places -- so every
    # duration in [N*60 - 0.5, N*60) rendered as an impossible clock reading:
    # `0m60s`, `1m60s`, `60m60s`. That is 0.83 % of runs over a minute, on the single
    # figure this column exists to let an operator trust.
    seconds = (finished_at - started_at).total_seconds()
    if seconds < 59.95:                       # i.e. rounds to at most "59.9s"
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m{secs:02d}s"
