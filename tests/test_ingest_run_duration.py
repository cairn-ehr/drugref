# tests/test_ingest_run_duration.py
"""db/053: the two stamps that were not a duration, and what the catalog now says.

WHAT WAS WRONG (issue 159). `started_at DEFAULT now()` and `finish_run`'s `now()` are
both `transaction_timestamp()`, so `finished_at - started_at` measured the gap between
two TRANSACTION START times. Measured on this project's own verification databases,
EIGHT of the nine feeds measured reported between 1.3 ms and 24 ms for a load, and the
ninth -- mesh_rel_run at 48.3 s -- was reporting the time it spent parsing 750 MB of
MeSH BEFORE its first write. The column an operator would size
a rebuild from was wrong for every feed, and wrong in a way that reads as an answer.

WHAT THIS FILE PINS, none of which the schema alone would hold:

* the DEFAULT, which is the path a row takes when `open_run` is not the writer -- a
  `curation` run, or any of the many tests that INSERT directly (a tally here would
  be one more number with no owner: it was written as "two dozen" and measured 47);
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

from drugref import db, provenance


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
    "before db/053",            # the watershed
    "not durations",            # what a reader must not do with the older rows
)
FINISHED_AT_CLAIMS = (
    "clock_timestamp()",        # as opposed to now(), which is the whole defect
    "final COMMIT",             # what the stamp still does not cover
    "issue 159",
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


# ---- what `drugref status` may print, and what it must refuse to (#159) ------

WATERSHED = datetime.datetime(2026, 9, 2, 12, 0, tzinfo=datetime.timezone.utc)


def _at(offset_seconds):
    return WATERSHED + datetime.timedelta(seconds=offset_seconds)


def _fmt(started, finished, watershed=WATERSHED):
    return provenance.format_run_duration(
        started_at=started, finished_at=finished, watershed=watershed)


def test_a_run_since_db053_prints_its_runtime():
    assert _fmt(_at(1), _at(3.4)) == "2.4s"


def test_a_long_run_prints_minutes_and_seconds():
    """2 min 16 s is what THIS round measured `ingest spl` at (135.86 s recorded,
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


def test_the_three_stamps_cannot_be_passed_in_the_wrong_order():
    """KEYWORD-ONLY, for the reason `open_run` already makes about `writer` and
    `clock`: three interchangeable datetimes in a row is an argument-order slip
    waiting to happen, and two of the three wrong orders are SILENT --
    (finished, started, watershed) printed a negative runtime and
    (started, watershed, finished) printed `pre-db/053` forever. The function whose
    whole job is refusing to publish a number it cannot vouch for should not be
    reachable by a positional mistake."""
    with pytest.raises(TypeError):
        provenance.format_run_duration(_at(0), _at(1), WATERSHED)


def test_a_run_from_before_db053_refuses_to_print_a_runtime():
    """THE POINT OF THE WATERSHED, and the reason this is not just a subtraction.

    Rows written before db/053 hold two transaction timestamps. Subtracting them
    yields a number -- 0.0026 s for the SPL ingest that took 2 min 09 s -- and a
    number is what an operator believes. The fix would otherwise survive as a wrong
    answer on every database that is not re-ingested, which is all of them.
    """
    assert _fmt(_at(-10), _at(-9.99)) == "pre-db/053"


def test_the_watershed_is_read_off_the_row_that_starts_first():
    """STRADDLING THE BOUNDARY, which no other test here does.

    Both stamps of a pre-db/053 row sit before the watershed and both stamps of a
    fresh one sit after it, so a formatter that tested `finished_at < watershed`
    instead of `started_at` passed every other assertion in this file. Only a row
    that STARTS before the watershed and FINISHES after it tells the two apart.
    """
    assert _fmt(_at(-10), _at(10)) == "pre-db/053"


def test_a_database_without_db053_prints_no_runtime_at_all():
    """`status` runs on any database, including one that predates the migration. No
    watershed means nothing on it can be a duration -- which is the safe direction."""
    assert _fmt(_at(1), _at(3), None) == "pre-db/053"


def test_an_unfinished_run_has_no_duration_to_print():
    assert _fmt(_at(1), None) == "unfinished"


def test_the_ledger_reports_when_a_migration_was_applied(conn, _migrated):
    """The watershed's only durable home is the ledger: db/053 and the Python that
    writes the stamps ship in one commit, so the migration's applied_at is what tells
    a reader which rows on THIS database carry the new meaning."""
    applied_at = db.migration_applied_at(conn, "053")
    assert applied_at is not None
    assert db.migration_applied_at(conn, "999") is None


def test_the_ledger_lookup_survives_a_database_that_has_no_ledger(conn, _migrated):
    """`drugref status` CRASHED HERE, mid-output, and the crash was new this round.

    migration_guard's own docstring names this database shape as reachable: the ledger
    is created by `db.apply_migrations` with CREATE TABLE IF NOT EXISTS rather than by
    any db/*.sql, so "a database bootstrapped by replaying the SQL by hand has every
    view and no ledger", and so does a partial restore. Reading it unguarded on the
    happy path of `status` printed the "loaded releases:" header and then a raw
    psycopg traceback naming a relation the operator never asked about -- and the five
    remaining blocks of a six-block command never ran. That is the misdiagnosis loop
    issue 122 built migration_guard to end, re-created by one new line inside the very
    command that module exists to protect.

    None, not an exception: no ledger means nothing on this database can be dated
    against db/053, which is the same answer as "db/053 is unapplied" and takes the
    same safe path. `status` says WHY out loud rather than silently withholding.
    """
    conn.execute("DROP TABLE drugref.schema_migration")
    assert db.migration_applied_at(conn, "053") is None
    conn.rollback()


def test_the_ledger_lookup_refuses_a_prefix_that_matches_nothing(conn, _migrated):
    """Same guard as db.migration_applied, and it must be the SAME guard: '53' for
    '053' matches no row in a zero-padded ledger, so every run would silently print
    pre-db/053 forever."""
    with pytest.raises(ValueError, match="three-digit"):
        db.migration_applied_at(conn, "53")
