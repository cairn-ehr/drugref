# tests/test_ingest_run_duration.py
"""db/053 and db/054: the two stamps that were not a duration, what decides whether
they are one now, and what the catalog says about both.

WHAT WAS WRONG (issue 159). `started_at DEFAULT now()` and `finish_run`'s `now()` are
both `transaction_timestamp()`, so `finished_at - started_at` measured the gap between
two TRANSACTION START times. Measured on this project's own verification databases,
EIGHT of the nine feeds measured reported between 1.3 ms and 24 ms for a load, and the
ninth -- mesh_rel_run at 48.3 s -- was reporting the time it spent parsing 750 MB of
MeSH BEFORE its first write. The column an operator would size
a rebuild from was wrong for every feed, and wrong in a way that reads as an answer.

WHAT THIS FILE PINS, none of which the schema alone would hold:

* the DEFAULTS, which are the path a row takes when `open_run` is not the writer -- a
  `curation` run, or any of the many tests that INSERT directly (a tally here would
  be one more number with no owner: it was written as "two dozen" and measured 47).
  Since db/054 there are two of them: `started_at`'s clock reading, and
  `duration_measured` false -- such a row does not claim its subtraction is a
  duration, which is the whole of issue 176;
* the CHECK, shown REFUSING, because a constraint nobody has watched fire is the
  "gate that exists and never fires" of issues 74/66/76;
* four catalog comments -- three columns-and-a-view plus the CONSTRAINT's own, which
  is the only new object an operator meets by name -- ASSERTED AGAINST THE CATALOG AND
  NEVER THE MIGRATION TEXT
  (test_curated_interaction_comment.py's precedent: the file a grep could check is not
  the file that shipped once a later db/NNN replaces it). One of the three is a
  RE-ISSUE of db/025's, and this repo has already shipped a re-issue rebuilt from the
  wrong ancestor -- see tests/test_class_grain_comment.py for that whole story.

The behaviour of the stamps themselves lives in tests/test_provenance.py, next to the
functions that write them.
"""
import datetime

import psycopg
import pytest

from drugref import provenance


def _missing_claims(comment, required):
    """PURE: which of `required` a catalog comment fails to make.

    Returns the missing needles, empty when the comment is current. Pure and separate
    from the readers below so the guard test can drive it with text that is known to be
    stale, proving the check fires -- without a second copy of the rule to disagree
    with this one.

    CASE-INSENSITIVE deliberately: these comments SHOUT their load-bearing clauses, and
    a pin that broke when a clause stopped being shouted would be pinning the emphasis
    rather than the claim.
    """
    text = (comment or "").casefold()
    return [needle for needle in required if needle.casefold() not in text]


# The claims each comment must make. Deliberately short and load-bearing: a pin on the
# whole paragraph would fail on a typo fix, and a pin on nothing would pass on a
# re-issue that dropped the paragraph entirely.
STARTED_AT_CLAIMS = (
    "backdate",                 # the mechanism, not just the outcome
    "issue 159",                # the round this column's meaning changed in
    "before db/053",            # when the meaning changed
    "not durations",            # what a reader must not do with the older rows
    "duration_measured",        # db/054: what actually decides, now that a date does not
)
FINISHED_AT_CLAIMS = (
    "clock_timestamp()",        # as opposed to now(), which is the whole defect
    "final COMMIT",             # what the stamp still does not cover
    "issue 159",
    "duration_measured",        # db/054: the flag, replacing "written since db/053"
)
DURATION_MEASURED_CLAIMS = (
    "finish_run",               # the only writer that sets it, which is the mechanism
    "same update that writes",  # ...and that it lands beside the stamp it vouches for,
                                # which is the half a DEFAULT cannot cover (UPDATEs)
    "issue 176",                # the round
    "defaults to false",        # what every other path gets, and why that is safe
    "0.0s",                     # the wrong answer this column exists to stop publishing
    "backfill",                 # why no row on disk was corrected, and none could be
)
CONSTRAINT_CLAIMS = (
    "clock_timestamp()",        # what a writer must use instead of now()
    "monotonic",                # why the client-side guard does not cover this
    "CLOCK STEPPED BACKWARDS",  # the cause the migration used to deny outright
    "re-run",                   # what the operator should actually DO
)
INCOMPLETE_VIEW_CLAIMS = (
    "db/053",                   # the re-issue names why it re-issued
    "leaves no row here",       # db/025's real point, which must SURVIVE the re-issue
)

# db/025's sentence, now half false: `started_at` DOES cover the pre-open parse since
# db/053. Kept here verbatim so the re-issue can be checked for what it DROPPED, which
# is the check db/038's own verification was structurally blind to.
DB025_RETRACTED = "the window starts at open_run, not at the command"

# db/053's own sentence, now the WEAKER test: an out-of-date client satisfies
# "written since db/053" while writing `finished_at` the old way, which is issue 176.
DB053_RETRACTED = "only for rows written since db/053"


def _column_comment(conn, column):
    return conn.execute(
        "SELECT col_description('drugref.ingest_run'::regclass, attnum) "
        "FROM pg_attribute "
        "WHERE attrelid = 'drugref.ingest_run'::regclass AND attname = %s",
        (column,)).fetchone()[0]


def test_started_at_defaults_to_the_clock_not_the_transaction(conn, _migrated):
    """The direct-INSERT path, which `open_run` does not cover.

    THE PRODUCTION CHANGE THIS CATCHES: db/053 not altering the default. A `curation`
    row, or any test that INSERTs without naming `started_at`, would still be dated
    from its transaction's start -- which for a curator holding a transaction open is
    not when anything began.
    """
    default = conn.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'ingest_run' "
        "AND column_name = 'started_at'").fetchone()[0]
    assert default == "clock_timestamp()"


def test_a_run_may_not_finish_before_it_started(conn):
    """The CHECK, shown REFUSING rather than merely present.

    Both stamps are now clock readings taken in that order, so a violation means a
    writer invented one (this test), a hand-built RunClock carried a time.time()
    reading, or the DATABASE HOST's clock stepped backwards mid-run. The migration's
    COMMENT ON CONSTRAINT names all three; an earlier draft of it named only the first
    and denied the other two.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.ingest_run "
            "(source, upstream_release, source_checksum, writer, "
            " started_at, finished_at) "
            "VALUES ('UNII', 'r1', 'sum', 'unii_run', "
            "        now(), now() - interval '1 second')")
    conn.rollback()


def test_a_run_that_has_not_finished_is_still_admitted(conn):
    """The other half of the CHECK's partition: NULL is the normal open state, and a
    constraint that refused it would abort every ingest at `open_run`."""
    conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'r1', 'sum', 'unii_run')")
    conn.rollback()


def test_started_at_comment_says_what_it_now_means(conn, _migrated):
    assert _missing_claims(_column_comment(conn, "started_at"),
                           STARTED_AT_CLAIMS) == []


def test_the_constraint_says_what_to_do_about_it(conn, _migrated):
    """THE ONLY NEW OBJECT THAT REACHES A HUMAN AS AN ERROR MESSAGE, and it shipped
    without catalog text while three columns and a view got some.

    An operator meets `ingest_run_finishes_after_it_starts` by name, mid-ingest, with
    a rolled-back run behind them -- and the diagnosis lived only in the migration
    file, which is not what anyone reads when psycopg raises. The comment must name
    the cause the migration once denied outright (a backward server clock) and say
    what to do, or it is decoration.
    """
    comment = conn.execute(
        "SELECT obj_description(oid, 'pg_constraint') FROM pg_constraint "
        "WHERE conname = 'ingest_run_finishes_after_it_starts'").fetchone()[0]
    assert _missing_claims(comment, CONSTRAINT_CLAIMS) == []


def test_finished_at_comment_says_what_it_excludes(conn, _migrated):
    assert _missing_claims(_column_comment(conn, "finished_at"),
                           FINISHED_AT_CLAIMS) == []


def test_the_incomplete_view_comment_was_re_issued_not_reverted(conn, _migrated):
    """The re-issue is checked for what it DROPPED as well as for what it set.

    db/025 said the window "starts at open_run, not at the command", which was true of
    BOTH halves of that sentence and is now true of only one: since db/053 the
    timestamp on a row here is backdated to the orchestrator's first line, while the
    row's EXISTENCE still begins at `open_run`. A re-issue that kept the retracted
    sentence, or that dropped db/025's surviving point along with it, is the failure
    this asserts against.
    """
    comment = conn.execute(
        "SELECT obj_description('drugref.ingest_run_incomplete'::regclass, "
        "'pg_class')").fetchone()[0]
    assert _missing_claims(comment, INCOMPLETE_VIEW_CLAIMS) == []
    assert DB025_RETRACTED not in comment


def test_the_comment_check_fires():
    """The guard, on the guard. `_missing_claims` returning [] for a comment that says
    nothing is the shape of the assertion the last review round found passing with the
    thing it guarded deleted."""
    assert _missing_claims("", STARTED_AT_CLAIMS) == list(STARTED_AT_CLAIMS)
    assert _missing_claims(DB025_RETRACTED, INCOMPLETE_VIEW_CLAIMS) == \
        list(INCOMPLETE_VIEW_CLAIMS)


# ---- db/054: the row says whether its own subtraction is a duration (#176) ----


def test_a_row_open_run_did_not_write_does_not_claim_a_measured_duration(conn,
                                                                        _migrated):
    """THE DEFAULT, which is the whole safety argument for the column.

    `duration_measured` is set by `provenance.finish_run` and by nothing else, so every
    other path -- a `curation` row, a direct INSERT, an ingest driven by a client older
    than db/053 -- lands false without anyone having to remember. The production change
    this catches is db/054 shipping the column with `DEFAULT true`, or with no default
    at all and a NULL that reads as "maybe": either turns a row nothing vouches for back
    into a number an operator believes.
    """
    measured = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'r1', 'sum', 'curation') RETURNING duration_measured"
    ).fetchone()[0]
    assert measured is False


def test_the_flag_reaches_the_view_a_consumer_actually_reads(conn, _migrated):
    """`drugref status` reads `loaded_release`, not `ingest_run`.

    A column added to the table and left out of the view is invisible to every
    consumer, and the block would have to fall back on the very inference db/054
    removes -- or, worse, subtract regardless. db/054 re-issues the view for this one
    reason; without the re-issue the migration is half a feature, which is this
    project's "a detector nobody calls" rule wearing the other hat.
    """
    columns = [row[0] for row in conn.execute(
        "SELECT attname FROM pg_attribute "
        "WHERE attrelid = 'drugref.loaded_release'::regclass AND attnum > 0 "
        "AND NOT attisdropped").fetchall()]
    assert "duration_measured" in columns

    kind = conn.execute(
        "SELECT atttypid::regtype::text FROM pg_attribute "
        "WHERE attrelid = 'drugref.loaded_release'::regclass "
        "AND attname = 'duration_measured'").fetchone()[0]
    assert kind == "boolean", (
        "the NAME reaching the view is not enough: `format_run_duration` tests this "
        "value with `is not True`, so a column arriving as text would make every "
        "non-empty string -- including 'false' -- read as a measured duration")


def test_the_duration_measured_comment_says_why_it_is_not_a_date(conn, _migrated):
    """A COMMENT ON is shipped data (the standing rule from PR #78's review), and this
    one carries the argument a consumer needs: what the column means, that nothing but
    `finish_run` sets it, what false does NOT mean, and why db/054 corrected no row on
    disk."""
    assert _missing_claims(_column_comment(conn, "duration_measured"),
                           DURATION_MEASURED_CLAIMS) == []


def test_the_finished_at_re_issue_dropped_the_clause_db054_makes_false(conn, _migrated):
    """THE SAME CHECK db/053 HAD TO MAKE ABOUT db/025's, for the same reason.

    `COMMENT ON` overwrites rather than merges, so db/054 rebuilds db/053's text -- and
    a re-issue rebuilt from the wrong ancestor is how db/038 silently reverted db/036
    (tests/test_class_grain_comment.py is that whole story). Asserting only what the new
    text SAYS would pass on a re-issue that kept "a real duration only for rows written
    since db/053" beside it: the weaker test, which the older client of issue 176
    satisfies while writing the stamp the old way. What must be gone is checked here.
    """
    assert DB053_RETRACTED not in _column_comment(conn, "finished_at")


def test_an_older_client_against_a_migrated_database_is_refused_a_runtime(conn,
                                                                         _migrated):
    """⇒ ISSUE 176'S REPRODUCTION, ON THE READER'S SIDE, and the point of the round.

    The row built here is EXACTLY the shape #176 describes: a client older than db/053
    INSERTs without naming `started_at`, so it takes db/053's `clock_timestamp()`
    DEFAULT -- the real insert time, comfortably after the migration -- and then stamps
    `finished_at` itself. Both stamps land after the watershed, milliseconds apart,
    describing a run that really took seconds.

    THREE ASSERTIONS, AND EACH RULES OUT ONE WAY OF PASSING FOR THE WRONG REASON. The
    ledger read shows the row is dated AFTER db/053 was applied, so the watershed this
    round replaces would NOT have fired on it; the subtraction shows what it would have
    printed instead (a fraction of a second, i.e. issue 159's `0.0s` verbatim); and the
    flag is what makes the refusal correct. Without the first two this test would pass
    on a formatter that refused everything, which is the permanent-skip failure the
    suite-count round had to build a negative control against.

    WHAT IS NOT REPRODUCED, deliberately. The old `finish_run` wrote `now()` from the
    WORK's transaction, which needs a commit between the two statements; what reaches
    the reader is the same either way -- two stamps, after the watershed, that subtract
    to a plausible number nothing vouches for -- and a committed row would escape the
    `conn` fixture's rollback into every module that sorts after this one.
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('SPL', 'r1', 'sum', 'spl_run') RETURNING ingest_run_id").fetchone()[0]
    conn.execute("UPDATE drugref.ingest_run SET finished_at = clock_timestamp() "
                 "WHERE ingest_run_id = %s", (run_id,))
    started_at, finished_at, measured = conn.execute(
        "SELECT started_at, finished_at, duration_measured FROM drugref.ingest_run "
        "WHERE ingest_run_id = %s", (run_id,)).fetchone()
    applied_at = conn.execute(
        r"SELECT applied_at FROM drugref.schema_migration WHERE filename LIKE '053\_%'"
    ).fetchone()[0]

    assert started_at > applied_at, (
        "the row must be dated AFTER db/053 was applied, or the watershed this round "
        "replaces would have refused it anyway and the test proves nothing")
    assert (finished_at - started_at).total_seconds() < 0.1, (
        "the two stamps must subtract to the plausible near-zero number issue 159 is "
        "about -- that number, not an error, is what the operator used to be shown")
    assert measured is False
    assert provenance.format_run_duration(
        started_at=started_at, finished_at=finished_at,
        duration_measured=measured) == provenance.UNMEASURED


class _RowsConn:
    """The smallest connection `print_loaded_release_block` can read, holding fixed rows.

    A STUB RATHER THAN A DATABASE, following the precedent tests/test_cli.py sets for
    the same block: `loaded_release` holds COMMITTED rows the `conn` fixture's rollback
    cannot remove, so a DB-gated version of the two renderings below would pass or fail
    on test ORDER -- which is the diagnosis-resistant failure conftest's
    `an_uningested_registry` was added to eliminate, re-created here for no gain. What
    is under test is the RENDERING, and these rows are exactly the two states it has.
    """

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    def execute(self, sql, params=None):
        # RECORDED, not discarded. What this block must NOT do is as load-bearing as
        # what it prints -- see test_the_happy_path_reads_no_migration_ledger_at_all --
        # and a stub that throws its input away can assert nothing about it.
        self.statements.append(sql)
        return self

    def fetchall(self):
        return self._rows


def _render(*rows, capsys):
    from drugref import cli_status
    cli_status.print_loaded_release_block(_RowsConn(list(rows)))
    return capsys.readouterr().out


def _row(measured):
    """One `loaded_release` row: (source, writer, release, finished_at, started_at,
    duration_measured), three seconds long, differing only in the flag."""
    started = datetime.datetime(2026, 9, 2, 12, 0, tzinfo=datetime.timezone.utc)
    return ("SPL", "spl_run", "r1", started + datetime.timedelta(seconds=3),
            started, measured)


def test_the_refusal_explains_itself_exactly_when_a_row_shows_it(capsys):
    """⇒ THE TWO RENDERINGS MUST DIFFER, which is the standing rule issue 111 wrote for
    the class-grain block one file over: a banner asserted only by what it says is
    satisfied by any two identical outputs.

    An operator reading `unmeasured` on a line needs to know what would change it --
    withholding every runtime with no reason given is a wrong answer by omission, the
    same fault one level up as publishing a number nobody can vouch for. And an
    operator whose database is fully re-ingested must NOT read an explanation of a
    state they are not in: a note printed on every run is a note that gets skipped, on
    the day it means something.
    """
    unmeasured = _render(_row(False), capsys=capsys)
    measured = _render(_row(True), capsys=capsys)

    assert provenance.UNMEASURED in unmeasured
    assert "Re-ingest" in unmeasured
    assert "3.0s" in measured
    assert "Re-ingest" not in measured
    assert unmeasured != measured

    # THE MIXED DATABASE, which is what every real one looks like the day after db/054:
    # one source re-ingested, the rest not. Every assertion above holds with `_render`
    # given a single row, so an implementation that tested only `rows[0]` -- or only
    # `rows[-1]` -- would pass all of them and go silent on exactly this case.
    mixed = _render(_row(True), _row(False), capsys=capsys)
    assert "3.0s" in mixed and provenance.UNMEASURED in mixed
    assert "Re-ingest" in mixed, (
        "one unmeasured row among many still needs its explanation")


def test_a_view_one_column_short_degrades_at_the_DATABASE_rather_than_raising(
        conn, _migrated, capsys):
    """THE GUARD ON THE NEW READ, SHOWN FIRING AGAINST A REAL DATABASE. db/054 widens a
    view a block reads, which is the standing rule cli.py states -- and the state it
    produces is not exotic: every deployment between pulling this code and running
    `drugref migrate` has `loaded_release` present and one column short.

    Unguarded, `drugref status` printed a raw psycopg traceback and the five remaining
    blocks of a six-block command never ran -- the misdiagnosis issue 122 built
    migration_guard to end. GUARDED BY RAISING, it printed one sentence and those five
    blocks still never ran, which is the same loss with better prose. So this block
    degrades: the listing prints from db/025's columns, every runtime is withheld, and
    the command continues.

    `ALTER VIEW ... RENAME COLUMN` reaches the older shape on controlled input inside
    the rolled-back fixture transaction -- this project's rule for a state the release
    cannot otherwise exercise, and the same device tests/test_class_grain_detectors.py
    uses for db/037's site. Nothing else reads the column, so no dependent view breaks.

    WHAT THIS TEST CAN AND CANNOT SHOW, stated rather than left to be discovered.
    `db.missing_relations` rolls back before probing, and that rollback undoes the
    rename AND any row this test inserted -- so what is proved here is that the block
    RETURNS instead of raising and that the guard's own diagnosis is what reaches the
    operator. It also lands in the "NOT a missing migration" branch, because after the
    rollback db/054 is applied and the view is intact again.

    The two things it cannot reach -- the listing still printing from db/025's columns,
    and the branch an upgrading operator is actually in -- need a connection whose state
    a rollback cannot undo, which is `_OneMigrationShortConn` below.
    """
    from drugref import cli_status

    conn.execute("ALTER VIEW drugref.loaded_release "
                 "RENAME COLUMN duration_measured TO before_db054")

    cli_status.print_loaded_release_block(conn)      # must NOT raise
    out = capsys.readouterr().out

    assert "duration_measured" in out, (
        "Postgres named the column; that is the one string that resolves this quickly")
    assert "drugref migrate" in out or "NOT a missing migration" in out, (
        "the guard's own diagnosis reaches the operator, not a sentence written here")


def test_the_happy_path_reads_no_migration_ledger_at_all(capsys):
    """⇒ WHAT REPLACED THE LEDGERLESS-DATABASE TEST db/054 DELETED, and the requirement
    outlived it.

    Until db/054 this block read `drugref.schema_migration` on every run to date the
    watershed, and a database with every view and NO ledger -- a hand-replayed bootstrap
    or a partial restore -- made `drugref status` print its first header and then a raw
    psycopg traceback. `db.migration_applied_at` grew a `to_regclass` check for that,
    and the test of it went with the function.

    What makes it safe now is not a check but an ABSENCE: the happy path asks the row,
    so there is no ledger read left to fail. An absence is exactly what no test states
    on its own, and the db/053 round shipped that very crash from one new line inside
    this very function -- so it is stated here. The ledger is still read on the FAILURE
    path, inside the guard, where `undiagnosed_message` handles its absence.
    """
    from drugref import cli_status

    conn = _RowsConn([_row(True)])
    cli_status.print_loaded_release_block(conn)

    assert conn.statements, "the block did read something"
    assert not any("schema_migration" in sql for sql in conn.statements), (
        f"the happy path reads the ledger again: {conn.statements}")


class _OneMigrationShortConn:
    """A database in db/025's shape: `loaded_release` present, `duration_measured` not,
    db/054 not recorded applied. THE STATE EVERY DEPLOYMENT IS IN between pulling this
    code and running `drugref migrate`.

    ⇒ A STUB, BECAUSE THE FIXTURE CANNOT REACH THIS BRANCH -- the same reason
    tests/test_migration_guard.py gives for `_LedgerlessConn`. `db.missing_relations`
    rolls back before probing, so an `ALTER VIEW ... RENAME COLUMN` staged inside the
    rolled-back `conn` fixture is UNDONE by the guard itself, and the diagnosis lands in
    the "db/054 IS applied and the view is intact, so this is NOT a missing migration"
    branch instead. That is a real branch and it is tested above -- but it is not the
    one an upgrading operator meets, and until this stub existed the branch they DO meet
    had no test at this call site. On a PR whose subject is a guard that was silently
    wrong, that was the gap worth closing.
    """

    def __init__(self, rows):
        self._rows = rows
        self.saw_ledger_probe = False

    def rollback(self):
        """`missing_relations` rolls back first; the aborted transaction is real."""

    def execute(self, sql, params=None):
        if "duration_measured" in sql:
            raise psycopg.errors.UndefinedColumn(
                'column "duration_measured" does not exist')
        if "to_regclass" in sql:
            self._answer = [("drugref.loaded_release",)]      # the view IS there
            return self
        if "schema_migration" in sql:
            self.saw_ledger_probe = True
            self._answer = [(False,)]                          # db/054 is NOT applied
            return self
        self._answer = self._rows                              # db/025's column list
        return self

    def fetchall(self):
        return self._answer

    def fetchone(self):
        return self._answer[0]


def _db025_row():
    """One `loaded_release` row in db/025's shape -- the db/054 row without its flag."""
    return _row(True)[:-1]


def test_a_database_one_migration_short_still_gets_the_other_five_blocks(capsys):
    """⇒ THE BLOCK DEGRADES; IT DOES NOT TAKE `drugref status` DOWN WITH IT.

    This is the FIRST of six blocks, and `cli.main` renders a RuntimeError as one line
    and exits 2 -- so a guard that raises here costs an operator the unfinished runs,
    the orphaned curated targets, the backdated signatures, the class grain and the
    unrankable severities as well. Before db/054 this exact database printed all six:
    the watershed read None, every runtime said `pre-db/053`, and status carried on.
    Turning that into a total failure would have been a regression dressed as a guard,
    and PROJECT-NOTES records "status crashed mid-output ... killing the five later
    blocks" as a defect the PREVIOUS round fixed.

    So the refusal is scoped to what actually cannot be answered -- the RUNTIMES -- and
    the listing an operator came for is still printed from db/025's columns, every row
    reading `unmeasured`. That is this round's own principle applied to itself: refuse
    the number, not the report.

    THE DIAGNOSIS IS STILL THE GUARD'S, word for word, so the operator is told what to
    run. And it is the branch they are actually in: "db/054 is NOT recorded applied ...
    Run `drugref migrate`", which the DB-level test above structurally cannot reach.
    """
    conn = _OneMigrationShortConn([_db025_row()])
    cli_status_out = _capture(conn, capsys)

    assert "SPL" in cli_status_out, "the listing an operator came for still prints"
    assert provenance.UNMEASURED in cli_status_out, "no runtime is published"
    assert "3.0s" not in cli_status_out, (
        "a runtime computed from an unmigrated row is exactly what db/054 refuses")
    assert "NOT recorded applied" in cli_status_out, (
        "the branch an upgrading operator is really in, not the assumption-refuted one")
    assert "drugref migrate" in cli_status_out
    assert conn.saw_ledger_probe, "the diagnosis was the guard's, not a hand-rolled one"


def _capture(conn, capsys):
    from drugref import cli_status
    cli_status.print_loaded_release_block(conn)
    return capsys.readouterr().out


# ---- what `drugref status` may print, and what it must refuse to (#159, #176) ----
#
# ⇒ THE DISCRIMINATOR IS THE ROW, NOT THE CLOCK. Until db/054 these tests passed a
# WATERSHED -- when db/053 was applied on this database -- and the formatter compared
# `started_at` against it. That asks WHEN a row was written; the question is WHICH CODE
# wrote it, and the two come apart in both directions (#176). The reader's half of that
# is reproduced above; the writer's half -- a genuinely new row backdated over its
# orchestrator's pre-open parse, which the watershed REFUSED -- is in
# tests/test_provenance.py, beside the function that backdates it.

START = datetime.datetime(2026, 9, 2, 12, 0, tzinfo=datetime.timezone.utc)


def _at(offset_seconds):
    return START + datetime.timedelta(seconds=offset_seconds)


def _fmt(started, finished, measured=True):
    return provenance.format_run_duration(
        started_at=started, finished_at=finished, duration_measured=measured)


def test_a_run_that_measured_itself_prints_its_runtime():
    assert _fmt(_at(1), _at(3.4)) == "2.4s"


def test_a_long_run_prints_minutes_and_seconds():
    """2 min 16 s is what the db/053 round measured `ingest spl` at (135.86 s recorded,
    140.06 s wall). The earlier 2 min 09 s that this test used to pin is the previous
    round's figure for the CORPUS READ alone, and pinning it here would have carried a
    borrowed number into the one column this issue exists to make trustworthy -- which
    is the failure #159 is about, one level down."""
    assert _fmt(_at(0), _at(135.86)) == "2m16s"


def test_the_seconds_field_can_never_read_sixty():
    """THE CARRY BOUNDARY, and it printed an impossible clock reading.

    The `< 60` decision was taken on `round(seconds, 1)` while the minutes branch
    re-rounded the UNROUNDED remainder with `{:02.0f}`, so anything in [N*60-0.5, N*60)
    rendered as `0m60s`, `1m60s`, `60m60s`. That is 0.83 % of all durations over a
    minute, on the single number an operator reads off `status`. Rounding ONCE and
    deriving both branches from the rounded value is what closes it -- two roundings
    of one quantity is the same shape of defect as one rule kept in two places.
    """
    assert _fmt(_at(0), _at(59.94)) == "59.9s"
    assert _fmt(_at(0), _at(59.95)) == "1m00s"
    assert _fmt(_at(0), _at(60)) == "1m00s"
    assert _fmt(_at(0), _at(119.5)) == "2m00s"
    assert _fmt(_at(0), _at(3659.7)) == "61m00s"


def test_the_stamps_and_the_flag_cannot_be_passed_positionally():
    """KEYWORD-ONLY, for the reason `open_run` already makes about `writer` and
    `clock`: three arguments in a row is an argument-order slip waiting to happen, and
    the two that mattered were SILENT -- `(finished, started, watershed)` printed a
    negative runtime and `(started, watershed, finished)` printed "pre-db/053" forever.

    WHAT db/054 CHANGED, stated as the improvement it is rather than as a surviving
    defect: the third argument stopped being a datetime and became a bool, so
    `(started, measured, finished)` no longer compares two plausible stamps -- it
    reaches `finished_at - started_at` and raises `TypeError: unsupported operand
    type(s) for -: 'bool' and 'datetime.datetime'`. That slip was REMOVED, not
    relocated. The keyword-only guard still earns its place against the other one,
    which the types cannot catch because both stamps are datetimes.
    """
    with pytest.raises(TypeError):
        provenance.format_run_duration(_at(0), _at(1), True)


def test_the_two_stamps_arriving_TRANSPOSED_are_refused_not_printed_negative():
    """⇒ THE ONE SLIP THE TYPES CANNOT CATCH, and the function that exists to refuse
    unvouchable numbers was publishing it.

    Both stamps are `timestamptz`, so passing them the wrong way round -- by keyword,
    from a row unpacked in the order the SELECT happened to list -- type-checks
    perfectly and rendered `-2.4s`. A negative runtime is not a number an operator can
    act on, and it cannot have come from the database: db/053's CHECK forbids
    `finished_at < started_at` on disk. So a negative interval reaching here is proof
    of a CALLER-SIDE transposition, which makes it exactly the thing this function is
    for -- and it was the one case it waved through.

    Loud rather than eloquent, following `RunClock.__post_init__` one module up: the
    promise becomes true by construction instead of by docstring.
    """
    with pytest.raises(ValueError, match="transposed"):
        _fmt(_at(3), _at(0))


def test_a_row_that_does_not_claim_a_measured_duration_refuses_to_print_one():
    """THE POINT OF THE FLAG, and the reason this is not just a subtraction.

    Rows written before db/053 hold two transaction timestamps; rows written since by
    an older client hold one of each. Subtracting either yields a number -- 0.0026 s
    for an SPL ingest that took 2 min 16 s -- and a number is what an operator
    believes. The fix would otherwise survive as a wrong answer on every database not
    re-ingested since, which is all of them.

    NOTE THE STAMPS ARE THE SAME ONES the test above prints "2.4s" for. The ONLY
    difference is the flag, which is exactly the claim db/054 makes: nothing about the
    timestamps themselves can settle this.
    """
    assert _fmt(_at(1), _at(3.4), measured=False) == "unmeasured"


def test_the_refusal_is_the_one_constant_the_reader_and_the_operator_share():
    """The string is a name, not a literal spelled at each site. `cli_status` prints an
    explanatory line when any row shows it, and a second spelling would let the two
    drift -- the vocabulary-written-down-twice rule, on the smallest possible
    vocabulary."""
    assert provenance.UNMEASURED == "unmeasured"


def test_an_unfinished_run_has_no_duration_to_print():
    """AND IT OUTRANKS THE FLAG, in both directions. A run in flight was opened by
    `open_run`, so its flag is true and there is still nothing to subtract; a row from
    an older client that never finished is not "unmeasured" either -- what an operator
    needs to know first is that it never came back."""
    assert _fmt(_at(1), None) == "unfinished"
    assert _fmt(_at(1), None, measured=False) == "unfinished"
