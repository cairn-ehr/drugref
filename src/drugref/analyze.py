# src/drugref/analyze.py
"""`ANALYZE`, and proof that it happened (issues 160 and 174).

**WHY THIS IS A MODULE AND NOT THREE LINES AT THE CALL SITE.** Issue 160 measured
what a missing `ANALYZE` costs this project: a `COPY` into `spl_label_subject`
spent **630 s** pinned to an index scan over all 68,550 parent rows because the
parent's statistics still said it was empty, and the orchestrator's own read-backs
ran **25 minutes at 100% CPU** without finishing. `spl_evidence.analyze_loaded_table`
therefore says, in bold, that the statement is *not optional and not a tidy-up*.

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

⇒ **SO A STATEMENT DECLARED LOAD-BEARING VERIFIES ITSELF.** Two checks, and they
are not one check written twice -- each fires where the other is blind:

1. **The server's own warning**, collected around the statement. The only check
   that fires on a RE-INGEST, where a previous privileged run (or autovacuum)
   already left plausible statistics behind.
2. **`pg_class.reltuples = -1`** afterwards. The only check that fires when no
   message arrives at all -- the state every connection in this project was in
   until issue 174 was fixed, and the state it returns to the day a pooler, a
   wrapper or a psycopg release stops forwarding notices.

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

#: The schema every drugref table lives in. A DEFAULT rather than a constant
#: spelled at each call site, and a parameter rather than a hard-coded literal, so
#: the probe tables this module is tested against can be named without the tests
#: reaching around the function they are testing.
DEFAULT_SCHEMA = "drugref"


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


def analyze_tables(conn: psycopg.Connection, tables: Sequence[str], *,
                   schema: str = DEFAULT_SCHEMA) -> None:
    """`ANALYZE` the named tables, and REFUSE unless the server actually did it.

    Raises `ValueError` for a caller mistake (an empty list) and `RuntimeError`
    for a database that declined the work -- which `cli.main` already turns into
    one printed line and exit code 2, because the message below is written to be
    the whole diagnosis.

    THE TRANSACTION SURVIVES THE REFUSAL. A warning does not abort anything, so
    the connection is still usable when this raises: the orchestrator's own
    `except` clause rolls the run back, leaving the `ingest_run` row standing with
    `finished_at IS NULL` exactly as every other mid-run failure does.
    """
    names = tuple(tables)
    if not names:
        # ONE HOME for this rule, now that two modules can build the statement.
        # A bare `ANALYZE` means EVERY table in the database, taking a lock on
        # each until COMMIT. Unreachable from any caller today -- one passes a
        # 1-tuple, the other `SPL_TABLES` -- and refused rather than handled on
        # `_copy`'s stated grounds: the day it becomes reachable is not the day
        # to discover what it does.
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

    with server_messages.collect(conn) as messages:
        conn.execute(statement)

    qualified = ", ".join(f"{schema}.{name}" for name in names)
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


def _current_role(conn: psycopg.Connection) -> str:
    """The role the failing statement ran as -- the other half of the diagnosis.

    An operator reading "permission denied to analyze" needs to know WHOSE
    permission, because the fix is a `GRANT` naming exactly this role. Read only
    on the refusal paths, and safe there: a warning aborts no transaction, so the
    connection that just ran the `ANALYZE` can still answer a `SELECT`.
    """
    (role,) = conn.execute("SELECT current_user").fetchone()
    return role
