# tests/test_live_key_index_guard.py
"""Prove the `assert_live_key_index` guard actually fires (issue 74).

WHY THIS FILE EXISTS. Seven tables carry a single-live natural-key index, and until
issue 74 the tests protecting them asserted the property in three different strengths:
two checked all three properties, four checked existence and the WHERE clause, and one
-- `class_expansion_policy_live_key` -- counted the index by name and nothing else. A
regression that made any of them UNIQUE would have forbidden every correction the
append-only overlay exists to make, and five of the seven tests would still have passed.

Consolidating those seven call sites onto one fixture fixes the inconsistency but
creates a new single point of failure: if the shared assertion silently stops checking
something, all seven call sites go quiet at once. That is a strictly worse failure mode
than the one it replaced, and it is the exact shape this project has now been bitten by
six times -- a load-bearing clause no test kills the removal of.

So the guard gets a guard. Each test below MUTATES the real index inside the test
transaction (Postgres DDL is transactional, and the `conn` fixture rolls back, so the
schema is restored for the next test) and asserts the fixture REJECTS it. One test per
property, per the standing rule the slice 5c.1 PR review produced: for every clause in
a multi-table guard, name the test that kills its removal, one per clause.

`curated_condition` is the subject throughout because it is the simplest live-key index
in the schema -- two columns, no vocabulary FK -- so a mutation here isolates the
property under test rather than tripping some other constraint first.
"""

import pytest

INDEX = "curated_condition_live_key"
TABLE = "curated_condition"
COLUMNS = "subject_moiety_uuid, object_condition_uuid"


def _remake(conn, definition):
    """Drop the live-key index and rebuild it from `definition`.

    Runs inside the test's own transaction, so the `conn` fixture's rollback puts the
    real index back. Nothing here is committed.
    """
    conn.execute(f"DROP INDEX drugref.{INDEX}")
    conn.execute(definition)


def test_the_guard_passes_on_the_real_index(assert_live_key_index):
    """The control. Without this, a guard that rejected EVERYTHING would satisfy all
    four mutation tests below while breaking every real call site."""
    assert_live_key_index(INDEX, TABLE, COLUMNS)


def test_the_guard_rejects_a_missing_index(conn, assert_live_key_index):
    """Property 1: it exists. Nothing but the single-live trigger reads this index, so
    it looks unused to a catalog sweep and a "remove unused indexes" pass is exactly
    how it would go missing."""
    conn.execute(f"DROP INDEX drugref.{INDEX}")
    with pytest.raises(AssertionError, match="is missing from"):
        assert_live_key_index(INDEX, TABLE, COLUMNS)


def test_the_guard_rejects_a_non_partial_index(conn, assert_live_key_index):
    """Property 2: it is PARTIAL over live rows. A full index answers a different
    question than the trigger asks, and the trigger is what makes the single-live rule
    linear rather than quadratic (db/023: 2,000 rows, 5,773 ms -> 42 ms)."""
    _remake(conn, f"CREATE INDEX {INDEX} ON drugref.{TABLE} ({COLUMNS})")
    with pytest.raises(AssertionError, match="must be PARTIAL"):
        assert_live_key_index(INDEX, TABLE, COLUMNS)


def test_the_guard_rejects_a_unique_index(conn, assert_live_key_index):
    """Property 3, and the reason issue 74 was filed. This is the mutation that passed
    five of the seven original tests.

    A correction is briefly TWO live rows on the same natural key -- INSERT the new
    row, then UPDATE the old one to point at it -- and a PARTIAL index cannot be
    declared DEFERRABLE, so a UNIQUE one is enforced at statement time and rejects the
    INSERT. The overlay would still pass every other test in this suite while being
    unable to record a single correction, which is the one thing it exists to do.
    """
    _remake(
        conn,
        f"CREATE UNIQUE INDEX {INDEX} ON drugref.{TABLE} ({COLUMNS}) "
        f"WHERE superseded_by IS NULL",
    )
    with pytest.raises(AssertionError, match="must be NON-UNIQUE"):
        assert_live_key_index(INDEX, TABLE, COLUMNS)


def test_the_guard_rejects_a_narrowed_column_list(conn, assert_live_key_index):
    """The column list. This mutation survived 936 green tests during slice 5c.1 and
    was caught by review, not by the suite: dropping a column here (matching a drop
    from the trigger's argument list) leaves the index partial and non-unique -- every
    other property intact -- while silently indexing the wrong key."""
    _remake(
        conn,
        f"CREATE INDEX {INDEX} ON drugref.{TABLE} (subject_moiety_uuid) "
        f"WHERE superseded_by IS NULL",
    )
    with pytest.raises(AssertionError, match="must index exactly"):
        assert_live_key_index(INDEX, TABLE, COLUMNS)
