# tests/test_curated_overlay.py
"""The curated overlay's floor and its completeness rules (db/029, slice 5c.1).

WHY THESE TESTS EXIST AT ALL. The overlay's whole purpose is to keep three states
apart: an assertion, an explicit "reviewed, this is not real", and NOBODY HAS LOOKED.
Slice 3 shipped a green suite in which deleting the guard that produced the third
state -- collapsing every unruled edge to `false` -- passed all 895 tests, because the
two ends were tested and the DECISION BETWEEN THEM was not. Every test below exists to
kill one specific mutation.

The vocabularies (severity, evidence_grade, relationship, ruling) are NOT restated in
Python. They live in db/029's CHECKs, which is the one place they can live without a
second list to disagree with the first (db/006). A bad value therefore raises
CheckViolation from the database, and a test asserts exactly that.
"""

import psycopg
import pytest


def _a_class(conn, ingest_run_id, code="N0000000001", name="Test MoA [MoA]"):
    """One MED-RT class, for tests needing a live FK target on the object side."""
    from drugref import ids

    class_uuid = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, 'MoA', %s) ON CONFLICT DO NOTHING",
        (class_uuid, code, name, ingest_run_id),
    )
    return class_uuid


def _a_run_and_moiety(conn, code="TESTUNII02", name="testdrug2"):
    """A FRESH ingest_run + moiety, inserted directly rather than via the
    session-standard `ingest_run_id`/`a_moiety` fixtures.

    Needed only by test_the_row_cannot_be_updated_or_deleted, which must call
    conn.rollback() mid-test to clear the aborted-transaction state a failed UPDATE
    leaves behind. That rollback undoes the WHOLE open transaction -- including
    whatever the `ingest_run_id`/`a_moiety` fixtures inserted on this same `conn`,
    since neither fixture commits. tests/test_schema_floor.py's
    test_claim_value_immutable_but_supersede_allowed hits the identical trap; its fix,
    reused here, is to re-seed fresh rows after rollback rather than reuse ids the
    rollback already erased.
    """
    from drugref import ids

    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES ('PBS', 'test', 'test', 'pbs_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    moiety_uuid = ids.mint_moiety_uuid(code)
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, name, run),
    )
    return run, moiety_uuid


def _assert_interaction(conn, moiety, klass, **over):
    """INSERT one curated_interaction row, returning its id. Defaults assert."""
    cols = dict(
        applies=True,
        severity="major",
        mechanism="additive bleeding risk",
        management="monitor INR",
        evidence_grade="established",
        source="DRUGREF",
        reviewed_by="test",
        reviewed_against="2026.07.06",
    )
    cols.update(over)
    names = ", ".join(cols)
    holes = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO drugref.curated_interaction "
        f"(subject_moiety_uuid, object_class_uuid, relationship, {names}) "
        f"VALUES (%s, %s, 'CI_MoA', {holes}) RETURNING curated_interaction_id",
        (moiety, klass, *cols.values()),
    ).fetchone()[0]


def test_an_asserting_row_must_state_severity_and_evidence(
    conn, a_moiety, ingest_run_id
):
    """The completeness CHECK, in the direction that matters most: a row that says
    "this interaction is real" with no severity would fire an alert carrying no
    grading, and a consumer would have to guess what to render."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.CheckViolation, match="curated_interaction_ruling_is_complete"
    ):
        _assert_interaction(conn, a_moiety, klass, severity=None)


def test_a_non_applying_row_must_state_neither(conn, a_moiety, ingest_run_id):
    """The other direction. A row ruling the rule NOT real has nothing to grade, and
    filler values would put a meaningless severity in a clinical table."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.CheckViolation, match="curated_interaction_ruling_is_complete"
    ):
        _assert_interaction(conn, a_moiety, klass, applies=False)


def test_a_non_applying_row_with_no_grading_is_accepted(conn, a_moiety, ingest_run_id):
    """`applies = false` is a REAL ANSWER -- "a curator looked and this rule is not a
    real interaction" -- and is what lets a reviewed rule leave the worklist instead of
    being asked about every release forever."""
    klass = _a_class(conn, ingest_run_id)
    row_id = _assert_interaction(
        conn, a_moiety, klass, applies=False, severity=None, evidence_grade=None
    )
    assert row_id is not None


def test_applies_has_no_default(conn, a_moiety, ingest_run_id):
    """THE MUTATION SLICE 3 PROVED A GREEN SUITE MISSES. If `applies` gains a DEFAULT,
    an unstated ruling silently becomes a stated one. NOT NULL with no default is what
    forces a curator to say which."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.curated_interaction "
            "(subject_moiety_uuid, object_class_uuid, relationship, source, "
            " reviewed_by, reviewed_against) "
            "VALUES (%s, %s, 'CI_MoA', 'DRUGREF', 'test', '2026.07.06')",
            (a_moiety, klass),
        )


def test_the_vocabulary_lives_in_the_database(conn, a_moiety, ingest_run_id):
    """A severity outside Plan C's four levels must be refused by the CHECK, not by a
    Python list nobody keeps in step with it."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.CheckViolation, match="curated_interaction_severity"
    ):
        _assert_interaction(conn, a_moiety, klass, severity="catastrophic")


def test_the_row_cannot_be_updated_or_deleted(conn, a_moiety, ingest_run_id):
    """The append-only floor. What drugref believed, and when, stays answerable --
    which matters most for exactly the rows that fired an alert."""
    klass = _a_class(conn, ingest_run_id)
    row_id = _assert_interaction(conn, a_moiety, klass)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "UPDATE drugref.curated_interaction SET severity = 'minor' "
            "WHERE curated_interaction_id = %s",
            (row_id,),
        )
    conn.rollback()
    # conn.rollback() clears the aborted-transaction state the failed UPDATE left
    # behind, but it also undoes everything else on this connection's one open
    # transaction -- including the a_moiety/ingest_run_id fixtures' inserts, since
    # neither commits. Reusing those ids here would fail with ForeignKeyViolation,
    # not the RaiseException this test is about, so fresh rows are seeded instead.
    run, moiety = _a_run_and_moiety(conn)
    klass = _a_class(conn, run)
    row_id = _assert_interaction(conn, moiety, klass)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "DELETE FROM drugref.curated_interaction WHERE curated_interaction_id = %s",
            (row_id,),
        )


def test_two_live_rows_on_one_natural_key_abort_at_commit(
    conn, a_moiety, ingest_run_id
):
    """A TEST THAT NEVER COMMITS PROVES NOTHING about a DEFERRED constraint, so this
    forces the check with SET CONSTRAINTS ALL IMMEDIATE -- which switches the mode for
    the rest of the transaction."""
    klass = _a_class(conn, ingest_run_id)
    _assert_interaction(conn, a_moiety, klass)
    _assert_interaction(conn, a_moiety, klass, severity="minor")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_live_key_index_exists_by_name(conn):
    """Nothing but the trigger reads this index, so nothing but a test protects it --
    and db/023 measured the cost of its absence: the single-live trigger becomes a
    sequential scan per row, so a 2,000-row load went from 42 ms to 5,773 ms."""
    assert (
        conn.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = 'drugref' "
            "AND indexname = 'curated_interaction_live_key'"
        ).fetchone()
        is not None
    )
