# tests/test_expansion_policy_writer.py
"""The writer for class_expansion_policy decisions (db/027, #35).

Correction-by-overlay is INSERT-then-point-the-old-row-at-it, and the ordering is the
part that is easy to get wrong: get it backwards and the failure arrives at COMMIT,
long after the call that caused it. That is why there is a function rather than a
paragraph of documentation telling each curator to write it themselves.

The vocabulary is deliberately NOT restated in Python. `decision` has one home -- the
CHECK in db/027 -- because a second list is a second thing to disagree with the first
(db/006's lesson). A typo therefore raises CheckViolation from the database.
"""
import pytest
import psycopg

from drugref import interactions

CODE = "N0000200001"


def _live(conn, code=CODE):
    """(decision, rationale) of the row that currently BINDS, or None."""
    return conn.execute(
        "SELECT decision, rationale FROM drugref.class_expansion_policy_current "
        "WHERE source = 'MED-RT' AND source_code = %s", (code,)).fetchone()


def test_recording_a_decision_makes_it_bind(conn):
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]",
        "abstract organ-system bucket", "test", "2026.07.06")
    assert _live(conn) == ("deny", "abstract organ-system bucket")


def test_revising_a_decision_supersedes_rather_than_overwrites(conn):
    """#35 in one test: the previous rationale must still be answerable afterwards."""
    first = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    second = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "allow", "Test Bucket [PE]", "gained a real effect",
        "test", "2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")     # a test that never commits proves nothing
    assert _live(conn) == ("allow", "gained a real effect")
    assert conn.execute(
        "SELECT superseded_by, rationale FROM drugref.class_expansion_policy "
        "WHERE policy_id = %s", (first,)).fetchone() == (second, "too abstract")


def test_withdrawing_carries_the_class_name_forward(conn):
    """A withdrawal must not be able to introduce a name nobody reviewed, so it is
    copied from the row being withdrawn rather than asked of the caller."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    withdrawn = interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "the 2026.07.06 measurement no longer holds",
        "test", "2026.08.06")
    assert _live(conn) is None, "a withdrawn decision must not bind"
    assert conn.execute(
        "SELECT decision, class_name FROM drugref.class_expansion_policy "
        "WHERE policy_id = %s", (withdrawn,)).fetchone() == ("withdrawn", "Test Bucket [PE]")


def test_withdrawing_a_decision_nobody_made_is_an_error(conn):
    """Not a no-op: it means the caller believes a judgement exists that does not, and
    silently doing nothing would leave them believing it."""
    with pytest.raises(interactions.NoLiveDecisionError, match="N0000200099"):
        interactions.withdraw_expansion_decision(
            conn, "MED-RT", "N0000200099", "x", "test", "2026.07.06")


def test_a_class_can_be_ruled_on_again_after_a_withdrawal(conn):
    """Withdrawal returns the class to unreviewed; it does not close it for ever."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "stale", "test", "2026.08.06")
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "allow", "Test Bucket [PE]", "re-reviewed",
        "test", "2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert _live(conn) == ("allow", "re-reviewed")


def test_withdrawing_a_withdrawn_decision_supersedes_it_rather_than_erroring(conn):
    """Pins LIVE vs BINDING *inside withdraw_expansion_decision itself* -- the brief
    calls the two "deliberately different" and says "do not unify them", but every
    other withdrawal test in this file withdraws a `deny`/`allow` row, which is
    simultaneously live AND binding, so none of them can tell the two lookups apart.

    A withdrawn row is UNSUPERSEDED (so it is still LIVE) but does not BIND (it is
    excluded from class_expansion_policy_current). withdraw_expansion_decision's
    lookup must use the live predicate: if it were "simplified" to query
    class_expansion_policy_current instead, withdrawing an already-withdrawn class
    would find no row and wrongly raise NoLiveDecisionError, even though there is a
    row sitting right there for the second withdrawal to supersede. Verified by
    mutation: swapping that lookup to the _current view makes this the only failing
    test in the whole suite (see task-3-report.md).
    """
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    first_withdrawal = interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "stale", "test", "2026.08.06")
    # Must NOT raise NoLiveDecisionError: the row `first_withdrawal` just wrote is
    # unsuperseded and therefore live, even though it does not bind.
    second_withdrawal = interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "still stale, confirmed on re-review", "test", "2026.09.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")     # a test that never commits proves nothing
    assert _live(conn) is None, "nothing binds after either withdrawal"
    assert conn.execute(
        "SELECT decision, superseded_by FROM drugref.class_expansion_policy "
        "WHERE policy_id = %s", (second_withdrawal,)).fetchone() == ("withdrawn", None)
    assert conn.execute(
        "SELECT superseded_by FROM drugref.class_expansion_policy "
        "WHERE policy_id = %s", (first_withdrawal,)).fetchone() == (second_withdrawal,)


def test_an_unrecognised_decision_reaches_the_database_constraint(conn):
    """The vocabulary lives in the CHECK and nowhere else."""
    with pytest.raises(psycopg.errors.CheckViolation):
        interactions.record_expansion_decision(
            conn, "MED-RT", CODE, "maybe", "Test Bucket [PE]", "x",
            "test", "2026.07.06")
