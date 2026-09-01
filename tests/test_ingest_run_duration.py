# tests/test_ingest_run_duration.py
"""db/053: the two stamps that were not a duration, and what the catalog now says.

WHAT WAS WRONG (issue 159). `started_at DEFAULT now()` and `finish_run`'s `now()` are
both `transaction_timestamp()`, so `finished_at - started_at` measured the gap between
two TRANSACTION START times. Measured on this project's own verification databases,
every one of the nine feeds reported between 1.3 ms and 24 ms for a load, and the one
feed that reported anything else -- mesh_rel_run at 48.3 s -- was reporting the time it
spent parsing 750 MB of MeSH BEFORE its first write. The column an operator would size
a rebuild from was wrong for every feed, and wrong in a way that reads as an answer.

WHAT THIS FILE PINS, none of which the schema alone would hold:

* the DEFAULT, which is the path a row takes when `open_run` is not the writer -- a
  `curation` run, or any of the two dozen tests that INSERT directly;
* the CHECK, shown REFUSING, because a constraint nobody has watched fire is the
  "gate that exists and never fires" of issues 74/66/76;
* three catalog comments, ASSERTED AGAINST THE CATALOG AND NEVER THE MIGRATION TEXT
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

    Both stamps are now clock readings taken in that order, so this can only be
    violated by a caller inventing one -- which is exactly the mistake a duration
    column invites, and the reason the constraint is worth its line.
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


def test_a_run_since_db053_prints_its_runtime():
    assert provenance.format_run_duration(_at(1), _at(3.4), WATERSHED) == "2.4s"


def test_a_long_run_prints_minutes_and_seconds():
    """2 min 09 s is the SPL ingest's real runtime, and the number this whole issue
    exists so that an operator can read."""
    assert provenance.format_run_duration(_at(0), _at(129.4), WATERSHED) == "2m09s"


def test_a_run_from_before_db053_refuses_to_print_a_runtime():
    """THE POINT OF THE WATERSHED, and the reason this is not just a subtraction.

    Rows written before db/053 hold two transaction timestamps. Subtracting them
    yields a number -- 0.0026 s for the SPL ingest that took 2 min 09 s -- and a
    number is what an operator believes. The fix would otherwise survive as a wrong
    answer on every database that is not re-ingested, which is all of them.
    """
    assert provenance.format_run_duration(_at(-10), _at(-9.99), WATERSHED) \
        == "pre-db/053"


def test_a_database_without_db053_prints_no_runtime_at_all():
    """`status` runs on any database, including one that predates the migration. No
    watershed means nothing on it can be a duration -- which is the safe direction."""
    assert provenance.format_run_duration(_at(1), _at(3), None) == "pre-db/053"


def test_an_unfinished_run_has_no_duration_to_print():
    assert provenance.format_run_duration(_at(1), None, WATERSHED) == "unfinished"


def test_the_ledger_reports_when_a_migration_was_applied(conn, _migrated):
    """The watershed's only durable home is the ledger: db/053 and the Python that
    writes the stamps ship in one commit, so the migration's applied_at is what tells
    a reader which rows on THIS database carry the new meaning."""
    applied_at = db.migration_applied_at(conn, "053")
    assert applied_at is not None
    assert db.migration_applied_at(conn, "999") is None


def test_the_ledger_lookup_refuses_a_prefix_that_matches_nothing(conn, _migrated):
    """Same guard as db.migration_applied, and it must be the SAME guard: '53' for
    '053' matches no row in a zero-padded ledger, so every run would silently print
    pre-db/053 forever."""
    with pytest.raises(ValueError, match="three-digit"):
        db.migration_applied_at(conn, "53")
