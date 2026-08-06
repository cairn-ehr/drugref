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


# ---- shared across the curated-overlay test modules (db/029, slice 5c.1) ----------
# Both fixtures below were originally defined in tests/test_curated_read_path.py and
# moved here once tests/test_curated_gap_views.py needed the same setup: pytest
# resolves conftest fixtures BY NAME with no import at all, so this is the
# established way to share a fixture across modules in this repo (a_moiety and
# ingest_run_id above are the standing example). Importing a fixture function across
# test modules instead is a workaround this repo has already rejected -- see
# tests/test_gap_views.py's test_the_condition_views_grain_is_the_gap_keys_grain,
# which renames rather than re-imports a colliding test, with a comment saying why.


@pytest.fixture
def a_graded_rule(conn, a_moiety, ingest_run_id):
    """One CI_MoA rule with a member on its axis, and drugref's grade on the rule.

    NOTE: the name is historical. It sets up a rule and its membership but
    deliberately does NOT grade it -- grading is left to the calling test -- which is
    exactly what makes it usable, unmodified, as the "uncurated rule" fixture in
    tests/test_curated_gap_views.py as well as the "graded once curated" fixture
    here.
    """
    from drugref import ids
    from tests.test_curated_overlay import _a_class
    klass = _a_class(conn, ingest_run_id)
    partner = ids.mint_moiety_uuid("TESTUNII02")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'partnerdrug', %s) ON CONFLICT DO NOTHING",
        (partner, ingest_run_id))
    # NOTE class_membership has NO `source` column -- unlike class_contraindication
    # below, and unlike every moiety_condition_* table. db/003 made the class registry
    # source-neutral; adding one here fails with UndefinedColumn.
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, 'has_MoA', %s) ON CONFLICT DO NOTHING",
        (partner, klass, ingest_run_id))
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (a_moiety, klass, ingest_run_id))
    return {"subject": a_moiety, "partner": partner, "class": klass}


@pytest.fixture
def a_contradicted_pair(conn, a_moiety, ingest_run_id):
    """Issue 51's shape: one pair asserted as BOTH may_treat and CI_with."""
    from drugref import interactions
    from tests.test_curated_overlay import _a_condition
    condition = _a_condition(conn, ingest_run_id)
    interactions.add_condition_contraindication(
        conn, a_moiety, condition, "CI_with", "MED-RT", ingest_run_id)
    conn.execute(
        "INSERT INTO drugref.moiety_condition_indication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'may_treat', 'MED-RT', %s)",
        (a_moiety, condition, ingest_run_id))
    return {"moiety": a_moiety, "condition": condition}
