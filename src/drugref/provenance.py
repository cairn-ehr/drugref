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
import datetime
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

    `duration_measured` IS DELIBERATELY NOT WRITTEN HERE (db/054, #176), and the
    asymmetry with `finish_run` is the point. The flag claims that the two stamps are a
    duration, which is a claim about BOTH of them -- and at this moment `finished_at`
    does not exist. `finish_run` writes it, alongside the second stamp it is vouching
    for. A row in flight therefore reads false, which costs nothing: `finished_at` is
    NULL, `format_run_duration` answers "unfinished" before it consults the flag, and
    `loaded_release` filters the row out. What it buys is in `finish_run`'s docstring.
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

    ⇒ `duration_measured = true` IS WRITTEN HERE, IN THE SAME STATEMENT AS THE STAMP IT
    VOUCHES FOR (db/054, #176), and this is the only place in the project that writes
    it. The flag's claim is about BOTH stamps, so it belongs where the SECOND one is
    written: `open_run` could only ever have promised it about a value that did not
    exist yet.

    THAT PLACEMENT IS WHAT MAKES db/054's DEFAULT ARGUMENT HOLD. `DEFAULT false`
    governs INSERTs, and `finished_at` arrives by UPDATE -- so a flag set at INSERT is
    not protected by the default at all. Concretely: `open_run` commits its row so a
    crashed ingest leaves a trace, and an operator tidying that abandoned row by hand
    (`UPDATE ... SET finished_at = now()`) writes the second stamp with no measurement
    behind it. db/053's CHECK passes, the row enters `loaded_release`, and a flag set at
    INSERT would still read true -- publishing a confident runtime of hours for a run
    that never finished, which is issue 159's own failure mode with db/054's guard
    beside it saying nothing. Written here, the hand-rolled UPDATE does not name the
    column and the row keeps its false.

    EVERY OTHER PATH STILL LANDS FALSE BY DOING NOTHING -- a `curation` row, a direct
    INSERT, and an ingest driven by a client older than db/053, whose own `finish_run`
    writes `finished_at` without naming this column.
    """
    conn.execute("UPDATE drugref.ingest_run "
                 "SET finished_at = clock_timestamp(), duration_measured = true "
                 "WHERE ingest_run_id = %s", (run_id,))


# THE REFUSAL, SPELLED ONCE. `cli_status` prints an explanatory line whenever any row
# shows it, so the word reaches an operator from two places and a second spelling would
# be two things that can disagree -- db/020's source trio and WRITERS above are the same
# lesson, on the smallest possible vocabulary.
#
# IT NO LONGER NAMES A MIGRATION, and that is the point of #176 rather than a wording
# change. "pre-db/053" was a claim about WHEN the row was written, which was never the
# question and was wrong in both directions; "unmeasured" is a claim about the row
# itself, which is what `duration_measured` records.
UNMEASURED = "unmeasured"


def format_run_duration(*, started_at: datetime.datetime,
                        finished_at: datetime.datetime | None,
                        duration_measured: bool) -> str:
    """PURE: what `drugref status` prints as one run's runtime.

    KEYWORD-ONLY, for the reason open_run already gives for `writer` and `clock`: two
    of the three argument-order slips used to be SILENT -- `(finished, started,
    watershed)` printed a negative runtime, and `(started, watershed, finished)` printed
    "pre-db/053" forever. db/054 REMOVED the second of those rather than relocating it:
    the third argument is now a bool, so that order reaches the subtraction and raises
    `TypeError` on `bool - datetime`. The guard stays for the first, which the types
    cannot catch because both stamps are datetimes.

    THE SLIP THAT SURVIVES BOTH IS A KEYWORD-VALUE ONE -- `started_at=finished_at,
    finished_at=started_at`, which is what a row unpacked in the order a SELECT happened
    to list would produce -- and it is refused below rather than published.

    `duration_measured` is the row's own `drugref.ingest_run.duration_measured`
    (db/054): true exactly when `provenance.finish_run` wrote that row's `finished_at`,
    false for every other path. It is tested with `is not True` rather than for
    truthiness, so a caller migrating off the old signature -- renaming `watershed=` to
    `duration_measured=` while still passing the datetime -- is refused instead of
    publishing a number on the strength of a non-empty object.

    WHY THIS IS NOT A SUBTRACTION. A row whose stamps were not written by the current
    open_run/finish_run pair still subtracts to a number -- 0.0026 s for an SPL ingest
    that took 2 min 16 s -- and a number is what an operator believes. Refusing to print
    one is the whole point: "unmeasured" sends a reader to the column comment, where the
    meaning is written down.

    ⇒ WHY IT ASKS THE ROW AND NOT THE CLOCK (#176). Until db/054 this took a
    `watershed` -- when db/053 was applied on that database -- and compared `started_at`
    against it. That asks WHEN the row was written; the question is WHICH CODE wrote it,
    and the two come apart in both directions, one of them silently:

      * an OLDER client writing to a db/053 database took the new clock_timestamp()
        default for started_at and old finish_run's now() for finished_at, so the CHECK
        did not fire, the row was dated after the watershed, and a two-second run was
        published as "0.0s" -- issue 159's own failure mode, reproduced;
      * a genuinely new row whose backdated start preceded the migration (an ingest in
        its pre-open phase while db/053 was applied) was refused although both its
        stamps were correct.

    The flag decides both, and neither `db.migration_applied_at` nor the migration
    number survives here.

    WHAT db/054 COSTS, STATED RATHER THAN GLOSSED: a row written between db/053 and
    db/054 BY THE CURRENT CLIENT holds two true clock readings, and the watershed
    printed a real number for it. It answers false now, because nothing on disk records
    which code wrote it and db/054 deliberately backfilled nothing -- inferring it once
    at migration time would have STORED the guess this column exists to remove, for
    exactly the old-client rows it cannot tell apart. The loss is bounded and
    self-healing: that writer's next ingest records a measured duration.

    WHAT THE FLAG STILL CANNOT SAY is whether a MEASURED duration is a plausible one --
    db/053's CHECK covers the impossible half (finishing before starting) and nothing
    covers a server clock that stepped FORWARD mid-run. That is a different question
    from this one and is not pretended at here.
    """
    # "unfinished" OUTRANKS the flag, in both directions. A run in flight has no
    # finished_at for finish_run to have vouched for, so its flag is false and there is
    # still nothing to subtract; a row from an older client that never came back is not
    # "unmeasured" either -- what an operator needs to know first is that it never
    # finished.
    if finished_at is None:
        return "unfinished"
    if duration_measured is not True:
        return UNMEASURED
    # ROUND ONCE. The `< 60` decision used to be taken on `round(seconds, 1)` while
    # the minutes branch re-rounded the UNROUNDED remainder with `{:02.0f}` -- two
    # roundings of one quantity, which is one rule kept in two places -- so every
    # duration in [N*60 - 0.5, N*60) rendered as an impossible clock reading:
    # `0m60s`, `1m60s`, `60m60s`. That is 0.83 % of runs over a minute, on the single
    # figure this column exists to let an operator trust.
    seconds = (finished_at - started_at).total_seconds()
    # A NEGATIVE INTERVAL CANNOT HAVE COME FROM THE DATABASE -- db/053's CHECK forbids
    # `finished_at < started_at` on disk -- so one reaching here is proof the CALLER
    # transposed the two stamps, which type-checks perfectly because both are
    # timestamptz. This used to render as "-2.4s", a number nobody can act on, from the
    # one function whose whole job is refusing exactly that.
    if seconds < 0:
        raise ValueError(
            f"finished_at precedes started_at by {-seconds:.1f}s, which db/053's CHECK "
            f"forbids on disk -- so the two stamps reached this function transposed "
            f"(#176)")
    if seconds < 59.95:                       # i.e. rounds to at most "59.9s"
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m{secs:02d}s"
