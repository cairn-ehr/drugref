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
