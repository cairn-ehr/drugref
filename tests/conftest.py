# tests/conftest.py
"""Shared DB fixtures. DB-gated tests are SKIPPED unless DRUGREF_TEST_DSN is set,
so unit tests still run anywhere. The `conn` fixture rolls back after each test,
which isolates tests whose code under test never commits. Isolation is
rollback-based, not transaction-enforced: code under test that calls
conn.commit() itself (e.g. an orchestrator that commits per run) escapes the
rollback, so a test module exercising such code needs its own explicit
cleanup (see tests/test_ingest_run.py's autouse truncate fixture)."""
import os
import pytest
import psycopg
from drugref import db


@pytest.fixture(scope="session")
def _dsn():
    dsn = os.environ.get("DRUGREF_TEST_DSN")
    if not dsn:
        # Skipping locally is a convenience; skipping in CI is a trap. Over half
        # the suite is DB-gated -- every schema, trigger, floor, writer and
        # orchestrator test -- and pytest exits 0 on a run that skipped all of
        # them, so an unset DSN would report green on a completely unexercised
        # database layer. In CI that is a failure, not a skip.
        if os.environ.get("CI"):
            pytest.fail(
                "DRUGREF_TEST_DSN is not set. Most of this suite is DB-gated, so a "
                "CI run without a database would pass while testing none of it.")
        pytest.skip("DRUGREF_TEST_DSN not set — skipping DB-gated test")
    return dsn


@pytest.fixture(scope="session")
def _migrated(_dsn):
    """Drop and recreate the drugref schema once, then apply migrations."""
    with psycopg.connect(_dsn) as conn:
        conn.execute("DROP SCHEMA IF EXISTS drugref CASCADE")
        conn.commit()
        db.apply_migrations(conn)
    return _dsn


@pytest.fixture
def conn(_migrated):
    """A connection whose work is rolled back after each test."""
    with psycopg.connect(_migrated) as c:
        yield c
        c.rollback()


@pytest.fixture
def ingest_run_id(conn):
    """A committed-in-transaction ingest_run row for provenance FKs.

    Says which writer it stands in for (db/025): `writer` is NOT NULL with no
    DEFAULT, so every insert must name one, and naming a real one keeps the fixture
    honest about what it is simulating.
    """
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('PBS', 'test', 'test', 'pbs_run') RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def a_moiety(conn, ingest_run_id):
    """One registered moiety, for tests that need a live FK target."""
    from drugref import ids
    moiety_uuid = ids.mint_moiety_uuid("TESTUNII01")
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'testdrug', %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, ingest_run_id))
    return moiety_uuid
