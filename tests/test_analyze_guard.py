# tests/test_analyze_guard.py
"""`ANALYZE` that did not happen must not report success (issue 174).

⇒ WHAT WAS MEASURED, ON THIS PROJECT'S OWN PG 18.1, BEFORE A LINE WAS WRITTEN:

    SET ROLE probe;            -- USAGE on the schema, SELECT+INSERT on the table
    ANALYZE probe_ns.t;
    WARNING:  permission denied to analyze "t", skipping it
    ANALYZE                    <- the command SUCCEEDS, no exception
    relpages | reltuples
           0 |        -1       <- and nothing was analysed

`psycopg` discards the warning, so before this round the ingest saw a successful
command tag and carried on. That matters far more since issue 160: the whole
premise of `analyze_loaded_table` is that the `ANALYZE` at that exact moment is
load-bearing -- without it the `COPY` into `spl_label_subject` spent **630 s**
pinned to an index scan over all 68,550 parent rows. A deployment that applies
migrations as an admin role and runs `ingest spl` as an application role holding
INSERT/DELETE is ordinary, nothing in this codebase forbids it, and under it
every `ANALYZE` is skipped, the 630 s comes back, **and the run still reports
success** -- `reconcile`, `read_pairs` and `check_floors` all count rows, and the
row counts are identical.

⇒ THREE CHECKS, AND NO TWO OF THEM ARE THE SAME CHECK TWICE. Each is shown below
killing a mutant the others cannot see:

* the **server's own warning**, collected around the statement, is the only one
  that carries a DIAGNOSIS -- PostgreSQL writes the sentence naming the cause;
* the **`reltuples = -1` postcondition** is the only one that needs neither a
  message nor a counter -- a connection whose notices go nowhere, which is
  precisely the state this repo was in until this round;
* the **`analyze_count` delta** is the only one that fires on a RE-INGEST without
  depending on a message arriving.

⇒ AND THE THIRD IS HERE BECAUSE THE FIRST TURNED OUT TO BE SWITCHABLE FROM OUTSIDE
DRUGREF. `client_min_messages` decides what the server SENDS and any role may set
it; above `warning` the WARNING is never transmitted, and `reltuples` is blind on
every run after the first. With the first two checks alone this guard was a no-op
on every database past its first ingest -- issue 174, inside the fix for issue 174,
found by the review of the branch that fixed it.

The probe role and the probe table are created INSIDE the test transaction and
die with it: `CREATE ROLE` and `SET ROLE` are both transactional. The role name
carries the backend pid because a role is cluster-global while the schema is not,
and issue 153 already records what two concurrent sessions do to one database.
"""
import logging

import psycopg
import pytest

from drugref import analyze, db, server_messages, spl_evidence


@pytest.fixture
def probe_role(conn, probe_table):
    """A role with USAGE on `drugref` and no ownership of anything in it.

    The shape issue 174 is about: an application role that can read and write the
    projection but does not own it, which is what an admin-migrates/app-ingests
    split produces. It takes `probe_table` so the `ON ALL TABLES` grant covers it
    -- an ordering detail that decides WHICH scenario is under test, because a
    role with no privileges at all on the table would be refused by `ANALYZE` for
    a reason the issue is not about.
    """
    role = f"drugref_probe_{conn.info.backend_pid}"
    conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
    conn.execute(f'GRANT USAGE ON SCHEMA drugref TO "{role}"')
    conn.execute(f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA drugref TO "{role}"')
    return role


@pytest.fixture
def probe_table(conn):
    """A freshly created, freshly loaded table: `relpages = 0`, `reltuples = -1`.

    The state that makes issue 160's bad plan possible, reproduced in miniature so
    the guard is tested against the condition rather than against the SPL schema's
    five tables.
    """
    conn.execute("CREATE TABLE drugref.analyze_probe (id int primary key, x text)")
    conn.execute("INSERT INTO drugref.analyze_probe "
                 "SELECT g, 'x' FROM generate_series(1, 500) g")
    return "analyze_probe"


def _reltuples(conn, table):
    (value,) = conn.execute(
        "SELECT reltuples FROM pg_class c JOIN pg_namespace n "
        "ON n.oid = c.relnamespace WHERE n.nspname = 'drugref' AND c.relname = %s",
        (table,)).fetchone()
    return value


# --------------------------------------------------------------------------
# The reproduction, and the two mutants
# --------------------------------------------------------------------------

def test_an_ANALYZE_the_server_SKIPPED_is_refused_instead_of_reported_as_success(
        conn, probe_role, probe_table):
    """⇒ THE DEFECT, END TO END. Before this round the call below returned None."""
    conn.execute(f'SET ROLE "{probe_role}"')
    # THE ROLE CAN READ AND WRITE THE TABLE. Asserted, not assumed: a role with
    # no access at all would also be refused by ANALYZE, for a reason issue 174
    # is not about, and the test would pass while testing the wrong scenario.
    assert conn.execute(
        f"SELECT count(*) FROM drugref.{probe_table}").fetchone() == (500,)
    with pytest.raises(RuntimeError) as raised:
        analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET ROLE")
    message = str(raised.value)
    assert "permission denied to analyze" in message, (
        "the refusal must quote the SERVER's own words rather than restate them: "
        "PostgreSQL wrote the only sentence here that names the actual cause, and a "
        "second copy of it in Python is one more thing that can drift")
    assert probe_table in message
    assert probe_role in message, (
        "an operator reading this needs the role that could not analyze, not only "
        "the table it could not analyze -- the fix is a GRANT on one of the two")
    assert _reltuples(conn, probe_table) == -1


def test_the_refusal_still_fires_on_a_RE_INGEST_that_already_has_statistics(
        conn, probe_role, probe_table):
    """⇒ KILLS THE `reltuples`-ONLY MUTANT, and it is not a contrived case.

    The FIRST ingest on a database ran as a privileged role, or the table was
    analysed by autovacuum; the SECOND runs after the deployment split the roles.
    `reltuples` is then a perfectly plausible number left over from before, the
    postcondition passes, and only the server's warning says the statistics
    describe the PREVIOUS load. The assertion below states that explicitly: the
    postcondition is checked and found EMPTY at the moment the guard refuses.
    """
    analyze.analyze_tables(conn, (probe_table,))          # the privileged first run
    assert _reltuples(conn, probe_table) == 500

    conn.execute(f'SET ROLE "{probe_role}"')
    assert analyze.never_analyzed(conn, (probe_table,)) == (), (
        "precondition of this test: with statistics already present the postcondition "
        "cannot fire, so anything that refuses below did so on the WARNING")
    with pytest.raises(RuntimeError, match="permission denied to analyze"):
        analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET ROLE")


def test_the_refusal_still_fires_when_NO_server_message_arrives_at_all(
        conn, probe_role, probe_table, monkeypatch):
    """⇒ KILLS THE WARNING-ONLY MUTANT, on the state this repo was actually in.

    `psycopg` delivers notices to registered handlers and to nothing else, so
    "the warning never arrived" is not a defensive fantasy -- it is what happened
    on every connection in this project until this round, and it is what happens
    again the day a wrapper, a pooler or a psycopg release stops forwarding them.
    A guard whose only evidence is a message that may never come is a gate that
    fires only while someone else keeps a promise.

    Simulated at OUR seam, not psycopg's: `serious_messages` is made to report
    nothing, which is exactly the observable state of a silent channel.
    """
    monkeypatch.setattr(server_messages, "serious_messages", lambda messages: ())
    conn.execute(f'SET ROLE "{probe_role}"')
    with pytest.raises(RuntimeError) as raised:
        analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET ROLE")
    assert probe_table in str(raised.value)
    assert "no statistics" in str(raised.value)


def test_a_table_the_role_owns_is_actually_analysed(conn, probe_table):
    """The happy path, asserted on the CATALOGUE and not on the absence of a raise.

    `reltuples` moving off `-1` is the whole point; a test that only checked the
    call returned would pass against a guard that never ran the statement.
    """
    assert _reltuples(conn, probe_table) == -1
    analyze.analyze_tables(conn, (probe_table,))
    assert _reltuples(conn, probe_table) == 500


def test_never_analyzed_names_exactly_the_tables_with_no_statistics(conn, probe_table):
    """The postcondition's reader, driven on its own.

    `0` is NOT `-1`: a table analysed WHILE EMPTY has statistics and must not be
    reported -- the same distinction the issue-160 review round had to make when
    `reltuples >= 0` let two mutants live.
    """
    conn.execute("CREATE TABLE drugref.analyze_probe_empty (id int primary key)")
    conn.execute("ANALYZE drugref.analyze_probe_empty")
    assert _reltuples(conn, "analyze_probe_empty") == 0
    assert analyze.never_analyzed(
        conn, (probe_table, "analyze_probe_empty")) == (probe_table,)


def test_an_empty_table_list_is_refused_before_any_SQL_is_built():
    """A bare `ANALYZE` means EVERY table in the database, locked until COMMIT.

    NO `conn` FIXTURE, and that is the assertion: the refusal happens before the
    statement is composed and before any evidence channel is read, so it needs no
    database -- and `spl_evidence._analyze` delegates here, which is what keeps the
    rule in ONE place if a second module ever starts building the statement.
    """
    with pytest.raises(ValueError, match="no tables"):
        analyze.analyze_tables(None, ())


# --------------------------------------------------------------------------
# The tie-back: the caller issue 160 is about
# --------------------------------------------------------------------------

def test_the_SPL_ingests_own_ANALYZE_refuses_under_the_split_role(conn, probe_role):
    """⇒ ISSUE 174'S ACTUAL SCENARIO, through the function issue 160 added.

    Not `analyze.analyze_tables` in the abstract: `spl_evidence`'s own entry
    point, on the real five tables, under the real role shape. Before this round
    it returned None and the ingest went on to pay 630 s for a plan the skipped
    statement was supposed to fix.
    """
    conn.execute(f'SET ROLE "{probe_role}"')
    with pytest.raises(RuntimeError, match="permission denied to analyze"):
        spl_evidence.analyze_source_tables(conn)
    conn.execute("RESET ROLE")


# --------------------------------------------------------------------------
# The channel itself, against a real server
# --------------------------------------------------------------------------

def test_a_REAL_diagnostic_carries_every_field_the_reader_names(conn):
    """⇒ THE FIELD NAMES ARE PSYCOPG'S, NOT OURS, so they are read from psycopg.

    tests/test_server_messages.py drives the pure half with a stand-in, and a
    stand-in built from the author's guess about `Diagnostic` would agree with
    that guess forever. This one raises a real WARNING and reads a real
    Diagnostic -- including `severity_nonlocalized`, whose PRESENCE is what makes
    the localised-severity fallback a fallback rather than the live path.
    """
    seen = []
    conn.add_notice_handler(lambda d: seen.append(server_messages.read_diagnostic(d)))
    conn.execute("DO $$ BEGIN RAISE WARNING 'probe primary' USING "
                 "DETAIL = 'probe detail', HINT = 'probe hint'; END $$")
    assert len(seen) == 1
    message, = seen
    assert message.severity == "WARNING"
    assert message.sqlstate == "01000"
    assert message.primary == "probe primary"
    assert message.detail == "probe detail"
    assert message.hint == "probe hint"
    assert server_messages.serious_messages(seen) == (message,)


def test_the_collector_has_the_message_BEFORE_the_execute_call_returns(conn):
    """The ordering `analyze_tables` is built on, asserted rather than assumed.

    The guard reads the collected list immediately after `conn.execute`, which is
    only correct if psycopg dispatches notices while processing that statement's
    result rather than at some later fetch.
    """
    with server_messages.collect(conn) as messages:
        conn.execute("DO $$ BEGIN RAISE WARNING 'inside'; END $$")
        assert [m.primary for m in messages] == ["inside"]


def test_the_collector_stops_collecting_when_its_block_ends(conn):
    """A handler left installed would make every later statement's messages land
    in a list nobody reads -- and would grow one list per ANALYZE for the life of
    the connection."""
    with server_messages.collect(conn) as messages:
        conn.execute("DO $$ BEGIN RAISE WARNING 'inside'; END $$")
    conn.execute("DO $$ BEGIN RAISE WARNING 'outside'; END $$")
    assert [m.primary for m in messages] == ["inside"]


def test_the_collector_is_removed_even_when_the_block_raises(conn):
    """The `finally`, against ANY exception leaving the block.

    NOT because `analyze_tables` raises from inside it -- it does not; every one of
    its refusals is composed after the `with` has already exited. The statement
    inside CAN raise (`UndefinedTable` on a name that reaches no relation), and a
    handler left installed after that would append to a list nobody reads for the
    life of the connection, one more list per ANALYZE.
    """
    before = list(conn._notice_handlers)
    with pytest.raises(ZeroDivisionError):
        with server_messages.collect(conn):
            1 / 0
    assert conn._notice_handlers == before, (
        "reading psycopg's private list deliberately: it is the only way to show "
        "the handler is GONE rather than merely unused, and a leak here is silent. "
        "Compared against what the connection arrived with rather than against [], "
        "so this keeps testing the collector if the fixture ever opens through "
        "db.connect -- which installs a handler of its own")


def test_db_connect_gives_every_connection_the_channel(_migrated, caplog):
    """⇒ THE OTHER HALF OF ISSUE 174, and the reason it lives in `db.connect`.

    Every orchestrator, every CLI command and every migration runs on a connection
    from this one function, so installing the handler here is what makes a server
    message visible by default instead of by remembering. The CLI's default
    `--log-level info` is what the level mapping is chosen against.
    """
    with caplog.at_level(logging.INFO, logger="drugref.postgres"):
        with db.connect(_migrated) as conn:
            conn.execute("DO $$ BEGIN RAISE WARNING 'from the server'; END $$")
            conn.execute("DO $$ BEGIN RAISE NOTICE 'routine chatter'; END $$")
            conn.rollback()
    levels = {record.levelno: record.getMessage() for record in caplog.records}
    assert logging.WARNING in levels and "from the server" in levels[logging.WARNING]
    assert logging.INFO in levels and "routine chatter" in levels[logging.INFO]


def test_a_connection_psycopg_opened_directly_has_no_channel(_migrated, caplog):
    """The negative control, and it is why the ANALYZE guard installs its OWN
    collector rather than trusting whatever handler the connection arrived with.

    `tests/conftest.py`'s own `conn` fixture is one such connection, and so is any
    programmatic caller that opened its own -- so a guard depending on
    `db.connect` would be a gate that fires only on the CLI path.
    """
    with caplog.at_level(logging.INFO, logger="drugref.postgres"):
        with psycopg.connect(_migrated) as conn:
            conn.execute("DO $$ BEGIN RAISE WARNING 'discarded'; END $$")
            conn.rollback()
    # Filtered to this logger rather than asserting the whole list empty: any
    # future logging inside psycopg.connect would fail a bare `== []` for a reason
    # this test is not about.
    assert [r for r in caplog.records if r.name == "drugref.postgres"] == []


# --------------------------------------------------------------------------
# The channel the WARNING half depends on, and the counter that does not
# --------------------------------------------------------------------------

def test_a_SKIPPED_analyze_is_refused_even_when_the_server_was_told_to_be_QUIET(
        conn, probe_role, probe_table):
    """⇒ THE HOLE THE FIRST REVIEW OF THIS GUARD FOUND, and it reopened issue 174.

    `serious_messages` can only see what the server chose to SEND, and
    `client_min_messages` decides that. It is `PGC_USERSET` -- any role may set
    it, and so may `ALTER ROLE`, `ALTER DATABASE`, `postgresql.conf`, a pooler's
    `server_settings` or a DSN `options=` (which docs/HANDOVER.md already flags as
    a live concern in this project). Set it above `warning` and the WARNING is
    never transmitted.

    That alone would be survivable if the postcondition covered it. It does not:
    `reltuples` is blind on a RE-INGEST, which is every run after the first. So
    before `analyze_count` landed, this exact configuration made the guard a
    no-op on every database past its first ingest -- measured, not feared.
    """
    analyze.analyze_tables(conn, (probe_table,))          # the privileged first run
    conn.execute("SET client_min_messages = 'error'")
    conn.execute(f'SET ROLE "{probe_role}"')
    assert analyze.never_analyzed(conn, (probe_table,)) == (), (
        "precondition: the postcondition cannot fire on a re-ingest")
    with server_messages.collect(conn) as heard:
        conn.execute("DO $$ BEGIN RAISE WARNING 'is anyone listening'; END $$")
    assert heard == [], (
        "precondition: with client_min_messages above 'warning' the server sends "
        "nothing, so the WARNING check cannot fire either")

    with pytest.raises(RuntimeError, match="did not move"):
        analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET ROLE")


def test_the_counter_check_names_the_role_and_the_silenced_channel(
        conn, probe_role, probe_table):
    """The refusal that fires with no server message must carry the diagnosis
    the server would have given, because nothing else will.

    An operator seeing "the statistics did not move" needs to know WHOSE
    permission was missing AND that the server was told not to explain itself --
    otherwise the obvious next step is to go looking for a message that was
    never sent.
    """
    analyze.analyze_tables(conn, (probe_table,))
    conn.execute("SET client_min_messages = 'error'")
    conn.execute(f'SET ROLE "{probe_role}"')
    with pytest.raises(RuntimeError) as raised:
        analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET ROLE")
    message = str(raised.value)
    assert probe_role in message
    assert "client_min_messages" in message and "error" in message


def test_the_counter_check_does_not_fire_on_an_ANALYZE_that_really_ran(
        conn, probe_table):
    """The control the counter check needs, and the one a stats SNAPSHOT breaks.

    `pg_stat_all_tables` is read through a per-transaction snapshot whose default
    `stats_fetch_consistency` is `cache`: the FIRST read of a table's row pins it
    for the rest of the transaction, so a before/after pair taken without
    `pg_stat_clear_snapshot()` returns the same number twice and the delta is
    always zero. That mistake does not fail loudly -- it refuses every healthy
    run -- which is why the happy path is asserted here as well as the fault.
    """
    analyze.analyze_tables(conn, (probe_table,))
    analyze.analyze_tables(conn, (probe_table,))          # a re-ingest, twice over
    analyze.analyze_tables(conn, (probe_table,))


def test_analyze_counts_reads_a_MOVING_number_inside_one_transaction(
        conn, probe_table):
    """The property the whole counter check rests on, asserted rather than assumed.

    `pg_stat_all_tables.analyze_count` is cumulative and non-transactional, so it
    advances where the caller can see it without the ANALYZE having committed.
    """
    before = analyze.analyze_counts(conn, (probe_table,))
    conn.execute(f"ANALYZE drugref.{probe_table}")
    after = analyze.analyze_counts(conn, (probe_table,))
    assert after[probe_table] == before[probe_table] + 1


def test_a_server_that_can_neither_count_nor_speak_is_REFUSED_before_the_statement(
        conn, probe_table, monkeypatch):
    """⇒ NO CHECK MAY BE SILENTLY UNAVAILABLE. The two checks that can see a
    skipped RE-INGEST are the server's warning and the analyze counter; with
    `track_counts` off the counter is gone, and with `client_min_messages` above
    `warning` the message is gone. Together they leave only the postcondition,
    which is blind on exactly the run this guard exists for.

    `track_counts` needs a server restart to change, so it is simulated at OUR
    seam -- `analyze_counts` returning None is precisely the observable state of
    a server that counts nothing.
    """
    monkeypatch.setattr(analyze, "analyze_counts",
                        lambda conn, tables, **kwargs: None)
    conn.execute("SET client_min_messages = 'error'")
    with pytest.raises(RuntimeError, match="cannot prove"):
        analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET client_min_messages")


def test_a_quiet_channel_alone_is_not_refused_while_the_counter_still_works(
        conn, probe_table):
    """The other side of the precondition, so it is a diagnosis and not a mood.

    A deployment that silences notices is not thereby broken -- the counter can
    still prove the work happened. Refusing here would make the guard fire on a
    configuration it can in fact see through, which is its own kind of wrong
    answer.
    """
    conn.execute("SET client_min_messages = 'error'")
    analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET client_min_messages")
    assert _reltuples(conn, probe_table) == 500


# --------------------------------------------------------------------------
# The reads the two postconditions are built from
# --------------------------------------------------------------------------

def test_never_analyzed_does_not_report_a_name_that_matches_no_relation(conn):
    """The rule this module's docstring states in bold, pinned.

    `ANALYZE` raises `UndefinedTable` on such a name long before the
    postcondition runs, so a row missing here means the caller passed something
    that never reached the statement -- and inventing a second diagnosis for it
    would only compete with psycopg's. Unreachable from `analyze_tables`, which
    is exactly why it needs a direct test: the guard cannot cover it.
    """
    assert analyze.never_analyzed(conn, ("no_such_table_anywhere",)) == ()


def test_never_analyzed_answers_in_the_CALLERS_order(conn, probe_table):
    """"Returned in the caller's order so the refusal message is stable" --
    the sentence, as a test. A set-ordered answer would make the same fault
    print its table names differently on different runs.
    """
    conn.execute("CREATE TABLE drugref.zzz_probe_a (id int primary key)")
    conn.execute("CREATE TABLE drugref.aaa_probe_b (id int primary key)")
    conn.execute("ANALYZE drugref.zzz_probe_a")
    asked = ("aaa_probe_b", "zzz_probe_a", probe_table)
    assert analyze.never_analyzed(conn, asked) == ("aaa_probe_b", probe_table)


def test_the_no_statistics_refusal_names_the_role_too(conn, probe_role,
                                                      probe_table, monkeypatch):
    """The path that runs when the channel is DARK names the role, for the reason
    the path that quotes the server does -- and more urgently, because here the
    server's own sentence is not available to name it instead.
    """
    monkeypatch.setattr(server_messages, "serious_messages", lambda messages: ())
    conn.execute(f'SET ROLE "{probe_role}"')
    with pytest.raises(RuntimeError) as raised:
        analyze.analyze_tables(conn, (probe_table,))
    conn.execute("RESET ROLE")
    assert "no statistics" in str(raised.value)
    assert probe_role in str(raised.value)


def test_the_schema_argument_names_a_table_OUTSIDE_drugref(conn):
    """⇒ `DEFAULT_SCHEMA` IS A PARAMETER FOR THIS, so the claim is exercised.

    The constant's docstring says the parameter exists so a probe table can be
    named without the tests reaching around the function they are testing. That
    was true of the design and false of the suite until this test: every other
    probe here lives in `drugref` and takes the default.
    """
    conn.execute("CREATE SCHEMA probe_schema")
    conn.execute("CREATE TABLE probe_schema.t (id int primary key)")
    conn.execute("INSERT INTO probe_schema.t SELECT generate_series(1, 7)")
    assert analyze.never_analyzed(conn, ("t",), schema="probe_schema") == ("t",)
    analyze.analyze_tables(conn, ("t",), schema="probe_schema")
    assert analyze.never_analyzed(conn, ("t",), schema="probe_schema") == ()
