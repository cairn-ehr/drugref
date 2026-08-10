# tests/test_live_key_index_guard.py
"""Prove the `assert_live_key_index` guard actually fires (issue 74).

WHY THIS FILE EXISTS. Seven tables carry a single-live natural-key index, and until
issue 74 the tests protecting them asserted the property in three different strengths:
two checked existence, partiality and non-uniqueness, four checked existence and the
WHERE clause, and one -- `class_expansion_policy_live_key` -- counted the index by name
and nothing else. NONE of them checked the column list. A regression that made any of
them UNIQUE would have forbidden every correction the append-only overlay exists to
make, and five of the seven tests would still have passed.

Consolidating those seven call sites onto one fixture fixes the inconsistency but
creates a new single point of failure: if the shared assertion silently stops checking
something, all seven call sites go quiet at once. That is a strictly worse failure mode
than the one it replaced, and it is the exact shape this project has now been bitten by
six times -- a load-bearing clause no test kills the removal of.

So the guard gets a guard. Each test below MUTATES the real index inside the test
transaction (Postgres DDL is transactional, and the `conn` fixture rolls back, so the
schema is restored for the next test) and asserts the fixture REJECTS it. One test per
property -- four properties, four mutation tests, plus a control -- per the standing
rule the slice 5c.1 PR review produced: for every clause in a multi-table guard, name
the test that kills its removal, one per clause.

`curated_condition` is the subject throughout, and the reason is the COLUMN-LIST test
specifically. The seven live-key indexes are not uniform -- `additive_effect` and
`interaction_group_assertion` carry ONE column, `class_expansion_policy`,
`effect_contribution` and `curated_condition` carry two, `curated_interaction` and
`interaction_group_member` three -- and a one-column index cannot be narrowed at all
without ceasing to be an index, so it cannot host the mutation that matters most.
Two columns is the smallest shape on which "drop a column" is still a well-formed
index, which makes `curated_condition` the minimal honest subject rather than merely
the simplest one. (An earlier version of this note claimed a mutation here avoids
"tripping some other constraint first" -- there is no such hazard: these tests run DDL
only and insert no rows, so no table constraint is reachable.)
"""

import pytest

INDEX = "curated_condition_live_key"
TABLE = "curated_condition"
COLUMNS = "subject_moiety_uuid, object_condition_uuid"


def _single_live_tables(conn):
    """Every table carrying the single-live trigger, with the natural key it enforces.

    Returns [(table, "col, col"), ...] read from `pg_trigger.tgargs` -- the arguments
    db/NNN passed to `forbid_multiple_live_assertions`, which ARE the natural key. The
    args are a NUL-terminated C string array, hence the split.
    """
    rows = conn.execute(
        "SELECT c.relname, encode(t.tgargs, 'escape') "
        "FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_proc p ON p.oid = t.tgfoid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'drugref' AND NOT t.tgisinternal "
        "AND p.proname = 'forbid_multiple_live_assertions' "
        "ORDER BY c.relname").fetchall()
    return [(table, ", ".join(filter(None, args.split("\\000"))))
            for table, args in rows]


def test_every_single_live_trigger_has_a_matching_index(conn, assert_live_key_index):
    """THE EIGHTH TABLE. Derived from the catalog, so a new one cannot arrive unguarded.

    Coverage of the seven was three hand-maintained literal lists across three test
    files, and the number "seven" appeared in prose in four places with nothing
    asserting it. A future db/NNN adding a `*_single_live` trigger with a UNIQUE index,
    or with no index at all, was invisible to the entire suite -- which is this round's
    own thesis, one level up: the guard fires for the tables someone remembered to list.

    Nothing here is typed twice. The trigger's OWN arguments supply the expected column
    list, so this asserts the real invariant -- "the index matches what the trigger
    asks" -- rather than "the index matches a literal I wrote down". A table that gains
    a trigger, or whose natural key changes, is covered the day the migration lands.

    The sibling call sites in test_schema_accumulation.py, test_expansion_policy.py and
    test_curated_overlay.py are NOT redundant with this: they name their table, so a
    trigger AND index deleted together still fails there, while this test would simply
    stop iterating over the pair. Both directions are needed.
    """
    tables = _single_live_tables(conn)
    assert len(tables) >= 8, (
        f"expected at least the eight known single-live tables, found {len(tables)}: "
        f"{[t for t, _ in tables]} -- a trigger disappearing is itself the regression")
    for table, columns in tables:
        assert_live_key_index(f"{table}_live_key", table, columns)


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
