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
import pytest

from tests.conftest import dsn_verdict

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
