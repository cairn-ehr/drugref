# tests/test_dsn_verdict.py
"""The gate that stops this suite reporting a vacuous green (conftest.dsn_verdict).

Over half the suite is DB-gated. Without DRUGREF_TEST_DSN those tests SKIP and pytest
still exits 0, so a CI run with a mis-wired DSN would report success while exercising
none of the database layer this project deliberately puts its integrity in. conftest
turns that skip into a failure when CI is set -- and until this file, NOTHING ASSERTED
IT. The branch never runs locally (the DSN is set) and never runs in CI (ci.yml sets it
there too), so the guard had never been observed firing.

That is precisely the shape of issues 74, 66 and 76 -- a check that exists and never
fires -- one level up, in the fixture that decides whether any of the other checks run
at all. Hence a test per branch, DB-free, driving the pure predicate directly.
"""
import os

import pytest
from psycopg.conninfo import conninfo_to_dict

from tests.conftest import dsn_verdict, session_dsn

DSN = "host=localhost port=5532 dbname=drugref_test user=postgres"


def test_a_dsn_is_used_whatever_the_environment():
    """The normal path, and it must not depend on CI: a DSN is a DSN."""
    assert dsn_verdict(DSN, in_ci=False) == ("use", DSN)
    assert dsn_verdict(DSN, in_ci=True) == ("use", DSN)


def test_a_missing_dsn_in_ci_is_a_failure_not_a_skip():
    """THE GATE. This is the one that has never fired in anger."""
    verdict, detail = dsn_verdict(None, in_ci=True)
    assert verdict == "fail"
    assert "DRUGREF_TEST_DSN is not set" in detail
    assert "testing none of it" in detail, (
        "the message must say WHY a green run would be meaningless, not merely that a "
        "variable is unset -- it is read by whoever is staring at a red CI job")


def test_a_missing_dsn_outside_ci_is_a_skip():
    """Local convenience, deliberately: unit tests still run on a machine with no
    Postgres, which is what keeps the DB-free two thirds of the suite usable."""
    verdict, detail = dsn_verdict(None, in_ci=False)
    assert verdict == "skip"
    assert "DRUGREF_TEST_DSN not set" in detail


@pytest.mark.parametrize("empty", ["", None])
def test_an_empty_dsn_counts_as_missing(empty):
    """An exported-but-blank DRUGREF_TEST_DSN is not a database. psycopg would fail on
    it far from here, with a message about connection strings rather than about the
    suite being unexercised."""
    assert dsn_verdict(empty, in_ci=True)[0] == "fail"
    assert dsn_verdict(empty, in_ci=False)[0] == "skip"


# --- issue 153: one database per pytest session ------------------------------------
#
# conftest used to DROP SCHEMA drugref on the shared DRUGREF_TEST_DSN database at the
# start of every session, so two sessions at once (two terminals, parallel review
# agents) wiped each other's schema mid-run and produced failures that looked real.
# Each session now creates, migrates and drops ITS OWN database, named by session_dsn.


def test_the_session_database_is_named_after_the_dsns_and_the_process():
    """The name says where it came from (the configured database) and which session
    owns it (the pid), so a leftover one is recognisable in `\\l`."""
    name, dsn = session_dsn(DSN, pid=4242)
    assert name == "drugref_test_pid4242"
    assert conninfo_to_dict(dsn) == conninfo_to_dict(DSN) | {"dbname": name}


def test_two_sessions_never_share_a_database():
    """THE BUG. Two processes must get two databases, or the race is back."""
    assert session_dsn(DSN, pid=1)[0] != session_dsn(DSN, pid=2)[0]


def test_a_long_database_name_is_cut_to_postgres_identifier_limit():
    """PostgreSQL silently truncates identifiers past 63 bytes, so two long names that
    differ only in the pid suffix would collapse into ONE database. The suffix must
    survive the cut, so the base is what gets shortened."""
    long_dsn = "dbname=" + "x" * 80 + " user=postgres"
    name, _ = session_dsn(long_dsn, pid=99999)
    assert len(name.encode()) <= 63
    assert name.endswith("_pid99999")


def test_a_dsn_without_a_dbname_still_gets_a_session_database():
    """libpq defaults the database to the user name; the session copy needs a base
    name of its own rather than a KeyError."""
    name, _ = session_dsn("host=localhost user=postgres", pid=7)
    assert name == "drugref_test_pid7"


def test_the_suite_runs_in_its_own_database_not_the_configured_one(conn, _dsn):
    """End to end: the database `conn` is in is NOT the one DRUGREF_TEST_DSN names.
    If this fails, every session is back on one shared schema."""
    (current,) = conn.execute("SELECT current_database()").fetchone()
    assert current != conninfo_to_dict(_dsn).get("dbname")
    assert current == session_dsn(_dsn, pid=os.getpid())[0]
