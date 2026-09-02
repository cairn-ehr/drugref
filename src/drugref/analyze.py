# src/drugref/analyze.py
"""`ANALYZE`, and proof that it happened (issues 160 and 174).

**WHY THIS IS A MODULE AND NOT THREE LINES AT THE CALL SITE.** Issue 160 measured
what a missing `ANALYZE` costs this project: a `COPY` into `spl_label_subject`
spent **630 s** pinned to an index scan over all 68,550 parent rows because the
parent's statistics still said it was empty, and the orchestrator's own read-backs
ran **25 minutes at 100% CPU** without finishing. `spl_evidence.analyze_source_tables`
therefore says, in bold, that the statement is *NOT optional, and not a tidy-up*;
`spl_evidence.analyze_loaded_table` states the matching rule about WHEN.

Issue 174 then found that the statement can decline to run and say nothing about
it. PostgreSQL does **not** raise when the caller lacks ownership (or `MAINTAIN`)
of a table named in an explicit `ANALYZE` list. It emits

    WARNING:  permission denied to analyze "t", skipping it

**skips that table**, and returns the `ANALYZE` command tag. psycopg discards the
warning unless a handler is installed (`server_messages` is that handler), so the
ingest sees success. Every check downstream -- `reconcile`, `read_pairs`,
`check_floors` -- counts rows, and the row counts are identical either way. An
admin-migrates/app-ingests deployment split is ordinary and nothing in this
codebase forbids it, so this is a real configuration in which the 630 s comes
back and the summary line does not change.

⇒ **SO A STATEMENT DECLARED LOAD-BEARING VERIFIES ITSELF.** Three checks, and no
two of them are one check written twice -- each fires where the others are blind:

1. **The server's own warning**, collected around the statement. The only one that
   carries a DIAGNOSIS: PostgreSQL writes the sentence that names the cause, and a
   second copy of it in Python is one more thing that can drift.
2. **`pg_class.reltuples = -1`** afterwards. The only one that needs neither a
   message nor a counter -- the state every connection in this project was in
   until issue 174 was fixed, and the state it returns to the day a pooler, a
   wrapper or a psycopg release stops forwarding notices.
3. **`pg_stat_all_tables.analyze_count` did not move.** The only one that fires on
   a RE-INGEST -- where a previous privileged run left plausible statistics, so
   check 2 is blind -- **without** depending on a message arriving.

⇒ **AND CHECK 3 IS HERE BECAUSE CHECK 1 TURNED OUT TO BE SWITCHABLE FROM OUTSIDE
drugref.** The first review of this guard measured it: `client_min_messages` is
`PGC_USERSET` and decides what the server SENDS, so `ALTER ROLE ... SET
client_min_messages='error'` -- or `ALTER DATABASE`, `postgresql.conf`, a pooler's
`server_settings`, or a DSN `options=`, which `docs/HANDOVER.md` already flags as a
live concern here -- silences check 1 entirely. Check 2 is blind on every run after
the first. So with checks 1 and 2 alone the guard was a no-op on every database
past its first ingest, one `ALTER ROLE` away: issue 174, inside the fix for issue
174.

**NO CHECK MAY BE SILENTLY UNAVAILABLE**, which is the other half of that lesson.
Check 3 needs `track_counts`; check 1 needs `client_min_messages` at `warning` or
below. When BOTH are off nothing can see a skipped re-ingest, so `analyze_tables`
REFUSES BEFORE RUNNING THE STATEMENT rather than proceeding on evidence it knows it
cannot collect.

**SOURCE-AGNOSTIC ON PURPOSE.** Nothing here knows about SPL. The caller owns the
rule about WHICH tables it may name (`spl_evidence._analyze` checks them against
`SPL_TABLES`); this module owns building the statement safely and proving it ran,
which is what every future source's writer will want too.
"""
from __future__ import annotations

from collections.abc import Sequence

import psycopg
from psycopg import sql

from drugref import server_messages

#: The schema this module's callers use. A DEFAULT rather than a constant spelled
#: at each call site, and a parameter rather than a hard-coded literal, so the probe
#: tables this module is tested against can be named without the tests reaching
#: around the function they are testing --
#: `test_the_schema_argument_names_a_table_OUTSIDE_drugref` is that test, and it
#: exists because the claim went one round unexercised.
#:
#: NOT a claim to be the one home for the literal `drugref`: the ingest modules
#: still spell it inline in their own SQL. It is the default for this module's
#: callers and nothing wider.
DEFAULT_SCHEMA = "drugref"

#: The values of `client_min_messages` that still let a WARNING reach the client.
#:
#: WRITTEN AS THE ACCEPTING SET, NOT THE REFUSING ONE, on `UNKNOWN_SEVERITY_LEVEL`'s
#: reasoning: an unrecognised value takes the loud branch. PostgreSQL 18 accepts
#: exactly these eight plus `error` (`fatal` and `panic` are rejected for this
#: parameter, though older servers took them), so the set is closed and a value
#: outside it means the server is not one this guard has been reasoned about on.
CHANNEL_OPEN_VERBOSITY = frozenset({
    "debug5", "debug4", "debug3", "debug2", "debug1", "log", "notice", "warning"})


def never_analyzed(conn: psycopg.Connection, tables: Sequence[str], *,
                   schema: str = DEFAULT_SCHEMA) -> tuple[str, ...]:
    """Of `tables`, those PostgreSQL has never gathered statistics for.

    ⇒ **`-1` IS THE SENTINEL, AND `0` IS NOT A MILDER VERSION OF IT.**
    `pg_class.reltuples` is `-1` for a relation that has never been analysed or
    vacuumed, and `0` for one analysed **while empty**. The issue-160 review round
    already had to make exactly this distinction -- a `reltuples >= 0` assertion
    let two mutants live, and reading `> 0` instead killed them -- so this reads
    the sentinel itself rather than any inequality that happens to include it.

    READABLE INSIDE THE ANALYSING TRANSACTION, which is what makes it usable as a
    postcondition: `ANALYZE` writes `relpages`/`reltuples` through
    `vac_update_relstats`, a NON-transactional in-place update. Measured on PG
    18.1 in one transaction: `-1` before, `500` immediately after. (The same
    property is why `analyze_source_tables` records that a rolled-back run leaves
    `pg_statistic` clean but `relpages`/`reltuples` behind.)

    A NAME THAT MATCHES NO RELATION IS NOT REPORTED, deliberately: `ANALYZE` would
    have raised `UndefinedTable` on it long before this runs, so a row missing
    here means the caller passed something that never reached the statement, and
    inventing a second diagnosis for it would only compete with psycopg's.

    Returned in the caller's order so the refusal message is stable.
    """
    rows = conn.execute(
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = ANY(%s) AND c.reltuples = -1",
        (schema, list(tables))).fetchall()
    without = {name for (name,) in rows}
    return tuple(name for name in tables if name in without)


def _verbosity(conn: psycopg.Connection) -> str:
    """This session's `client_min_messages`. ONE HOME: `notice_channel_is_open`
    decides on it and both refusal messages quote it."""
    (verbosity,) = conn.execute(
        "SELECT current_setting('client_min_messages')").fetchone()
    return verbosity


def notice_channel_is_open(conn: psycopg.Connection) -> bool:
    """Whether this session's `client_min_messages` still lets a WARNING through.

    THE PRECONDITION OF CHECK 1, and the reason it is a read rather than a `SET`.
    `SET LOCAL client_min_messages` would look like the tidier fix and is a trap:
    outside a transaction block the server answers it with a WARNING of its own
    ("SET LOCAL can only be used in transaction blocks"), which a surrounding
    collector then reads as a complaint about the ANALYZE. Refusing and naming the
    setting leaves the operator with a `GRANT`-shaped answer instead of a guard
    that quietly rewrites its own environment.
    """
    return _verbosity(conn).lower() in CHANNEL_OPEN_VERBOSITY


def analyze_counts(conn: psycopg.Connection, tables: Sequence[str], *,
                   schema: str = DEFAULT_SCHEMA) -> dict[str, int] | None:
    """`pg_stat_all_tables.analyze_count` per table, or None if nothing counts them.

    ⇒ **`pg_stat_clear_snapshot()` FIRST, AND THAT IS THE LOAD-BEARING LINE.**
    `stats_fetch_consistency` defaults to `cache`, which pins a relation's stats
    row at its FIRST read for the rest of the transaction -- so a before/after pair
    taken without this returns the same number twice and the delta is always zero.
    That mistake does not fail loudly: it refuses every healthy run, and it was
    made once while writing this function. Measured on PG 18.1 in one transaction:
    `0 -> 0` without the clear, `0 -> 1` with it.

    READABLE INSIDE THE ANALYSING TRANSACTION for `never_analyzed`'s reason, one
    layer along: the cumulative statistics are non-transactional, so the counter
    advances where the caller can see it without the ANALYZE having committed.
    (A rolled-back run therefore leaves the counter advanced, which is correct --
    the server really did do the work.)

    None, NOT an empty dict, when `track_counts` is off: "this server counts
    nothing" and "these tables were never analysed" are different answers, and a
    caller that conflated them would refuse every ANALYZE on such a server.

    A table with no row in `pg_stat_all_tables` counts as 0 rather than being
    dropped, so the caller's delta is defined for every name it asked about.
    """
    (track,) = conn.execute("SELECT current_setting('track_counts')").fetchone()
    if track.lower() != "on":
        return None
    conn.execute("SELECT pg_stat_clear_snapshot()")
    rows = conn.execute(
        "SELECT relname, analyze_count FROM pg_stat_all_tables "
        "WHERE schemaname = %s AND relname = ANY(%s)",
        (schema, list(tables))).fetchall()
    counted = dict(rows)
    return {name: counted.get(name, 0) for name in tables}


def analyze_tables(conn: psycopg.Connection, tables: Sequence[str], *,
                   schema: str = DEFAULT_SCHEMA) -> None:
    """`ANALYZE` the named tables, and REFUSE unless the server actually did it.

    Raises `ValueError` for a caller mistake (an empty list) and `RuntimeError`
    for a database that declined the work OR that cannot be asked whether it did
    -- which `cli.main` already turns into one printed line and exit code 2,
    because each message below is written to be the whole diagnosis.

    THE TRANSACTION SURVIVES THE REFUSAL. A warning does not abort anything, so
    the connection is still usable when this raises: the orchestrator's own
    `except` clause rolls the run back, leaving the `ingest_run` row standing with
    `finished_at IS NULL` exactly as every other mid-run failure does.
    """
    names = tuple(tables)
    if not names:
        # ONE HOME for this rule, so that a second module which starts building
        # an `ANALYZE` cannot carry a second copy of it. A bare `ANALYZE` means
        # EVERY table in the database, taking a lock on each until COMMIT.
        # Unreachable today -- the sole caller is `spl_evidence._analyze`, whose
        # own two callers pass a 1-tuple and `SPL_TABLES` -- and refused rather
        # than handled on `spl_evidence._copy`'s stated grounds: the day it
        # becomes reachable is not the day to discover what it does.
        raise ValueError(
            "no tables to ANALYZE; a bare ANALYZE would analyse every table in "
            "the database, which is never what this project means")

    # `sql.Identifier` rather than an f-string. Identifiers cannot be bind
    # parameters, so they have to be interpolated one way or another -- this way
    # psycopg quotes and escapes them, which is the mechanism, and the caller's
    # own module-constant check (spl_evidence._analyze against SPL_TABLES) is the
    # POLICY about which tables a source may name. Two different rules; keeping
    # both is not one rule written twice.
    statement = sql.SQL("ANALYZE {}").format(
        sql.SQL(", ").join(sql.Identifier(schema, name) for name in names))

    qualified = ", ".join(f"{schema}.{name}" for name in names)
    # ⇒ THE EVIDENCE IS SECURED BEFORE THE WORK, not looked for afterwards. Both
    # reads below are about what this server WILL be able to tell us; a guard that
    # discovered afterwards that it could not have seen a failure has already let
    # the run continue on the strength of a check that never ran.
    counts_before = analyze_counts(conn, names, schema=schema)
    channel_open = notice_channel_is_open(conn)
    if counts_before is None and not channel_open:
        raise RuntimeError(
            f"drugref cannot prove an ANALYZE of {qualified} ran on this server, "
            f"so it will not run one. track_counts is off, so "
            f"pg_stat_all_tables.analyze_count cannot witness the work, and "
            f"client_min_messages is {_verbosity(conn)!r}, above 'warning', so the "
            f"server will not send the WARNING that is how a skipped ANALYZE "
            f"announces itself. The only check left is pg_class.reltuples, which is "
            f"blind on any re-ingest -- and these statistics are load-bearing "
            f"(issue 160 measured 630 s of one ingest spent on the plan their "
            f"absence pins). Set client_min_messages to 'warning' or lower for the "
            f"ingest role, or turn track_counts on (issue 174).")

    with server_messages.collect(conn) as messages:
        conn.execute(statement)

    complaints = server_messages.serious_messages(messages)
    if complaints:
        raise RuntimeError(
            f"ANALYZE of {qualified} reported success and the server "
            f"simultaneously complained, which is how PostgreSQL says it SKIPPED "
            f"a table rather than analysed it. Running as role "
            f"{_current_role(conn)}. The server said: "
            + " | ".join(str(m) for m in complaints)
            + ". drugref will not continue: these statistics are load-bearing "
              "(issue 160 measured 630 s of one ingest spent on the plan their "
              "absence pins), and a run that skipped them reports success while "
              "publishing the same rows far more slowly. Grant the ingest role "
              "MAINTAIN on these tables, or make it their owner.")

    unanalyzed = never_analyzed(conn, names, schema=schema)
    if unanalyzed:
        raise RuntimeError(
            f"ANALYZE of {qualified} reported success but left no statistics for "
            + ", ".join(f"{schema}.{name}" for name in unanalyzed)
            + f": pg_class.reltuples is still -1, which means PostgreSQL has "
              f"never gathered statistics for it. Running as role "
              f"{_current_role(conn)}. The server sent no message explaining "
              "why, so check the ingest role's ownership of these tables first "
              "(issue 174) -- and check that notices are reaching drugref at "
              "all, because a skipped ANALYZE normally says so out loud.")

    # CHECK 3, LAST because it is the one with no diagnosis of its own to offer:
    # when the two above have both stayed silent, this is what is left, and on a
    # re-ingest with a quiet channel it is the ONLY thing left.
    if counts_before is not None:
        counts_after = analyze_counts(conn, names, schema=schema)
        stalled = tuple(name for name in names
                        if counts_after[name] == counts_before[name])
        if stalled:
            raise RuntimeError(
                f"ANALYZE of {qualified} reported success but PostgreSQL's own "
                f"counter did not move for "
                + ", ".join(f"{schema}.{name}" for name in stalled)
                + f": pg_stat_all_tables.analyze_count is unchanged across the "
                  f"statement, which means the server did not analyse the table. "
                  f"Running as role {_current_role(conn)}. "
                + _channel_note(conn, channel_open)
                + " drugref will not continue: these statistics are load-bearing "
                  "(issue 160 measured 630 s of one ingest spent on the plan their "
                  "absence pins). Grant the ingest role MAINTAIN on these tables, "
                  "or make it their owner (issue 174).")


def _channel_note(conn: psycopg.Connection, channel_open: bool) -> str:
    """Why the server said nothing -- the half check 3 cannot infer on its own.

    An operator told only "the counter did not move" will go looking for the
    warning PostgreSQL normally emits. When `client_min_messages` is what
    suppressed it, saying so is the difference between one `ALTER ROLE` and an
    afternoon; when it is not, the silence itself is the finding and points at the
    notice channel rather than at a setting.
    """
    if not channel_open:
        return (f"The server sent no message explaining why because "
                f"client_min_messages is {_verbosity(conn)!r}, above 'warning', so "
                f"it was asked not to.")
    return ("The server sent no message explaining why even though "
            "client_min_messages would have allowed one, so check that notices "
            "reach drugref at all.")


def _current_role(conn: psycopg.Connection) -> str:
    """The role the failing statement ran as -- the other half of the diagnosis.

    An operator reading "permission denied to analyze" needs to know WHOSE
    permission, because the fix is a `GRANT` naming exactly this role. Read only
    on the refusal paths, and safe there: a warning aborts no transaction, so the
    connection that just ran the `ANALYZE` can still answer a `SELECT`.
    """
    (role,) = conn.execute("SELECT current_user").fetchone()
    return role
