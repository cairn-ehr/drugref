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


def test_live_decisions_reports_what_binds(conn):
    """The read `drugref policy show` prints with no arguments. Goes through
    class_expansion_policy_current, so a withdrawn row is correctly absent."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    rows = interactions.live_decisions(conn)
    assert ("MED-RT", CODE, "deny", "Test Bucket [PE]") in rows
    # The 14 seeded roots are binding too, so this is a superset check by design.
    assert len(rows) >= 15


def test_live_decisions_omits_a_withdrawn_class(conn):
    """WITHDRAWN IS NOT A DECISION THAT BINDS. It means "no current judgement", so the
    class returns to gap_unreviewed_expansion_root -- and an operator asking what binds
    must not be shown it."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "the measurement no longer holds", "test", "2026.07.06")
    assert [r for r in interactions.live_decisions(conn) if r[1] == CODE] == []


def test_decision_history_keeps_every_ruling_in_order(conn):
    """The whole of #35 in one read: what did we last say, against which release, and
    why did we change our mind. The superseded row must still carry its ORIGINAL
    rationale -- that is what an in-place UPDATE destroyed."""
    first = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "alice", "2026.07.06")
    second = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "allow", "Test Bucket [PE]", "subtree is narrow",
        "bob", "2026.07.06")

    history = interactions.decision_history(conn, "MED-RT", CODE)
    assert [(h[0], h[1], h[2], h[3]) for h in history] == [
        (first, "deny", "too abstract", "alice"),
        (second, "allow", "subtree is narrow", "bob")]
    assert history[0][5] == second      # the first row points at the second
    assert history[1][5] is None        # the second is live


def test_decision_history_is_empty_for_a_class_nobody_ruled_on(conn):
    """Not an error: "nobody has looked" is a legitimate answer to `policy show`, and
    is exactly what absent means -- unreviewed, which expands AND raises a question."""
    assert interactions.decision_history(conn, "MED-RT", "N0000000404") == []


def test_the_withdrawn_constant_is_the_value_the_writer_actually_stores(conn):
    """`withdrawn` is a member of db/027's CHECK, which is the vocabulary's one home.
    interactions.WITHDRAWN exists so the CLI can refuse it (a curation surface should
    not offer a verb that bypasses withdraw_expansion_decision's two guarantees)
    WITHOUT adding a second literal. This pins that it is the same string the writer
    itself uses -- a drift would leave the CLI refusing a value the database accepts.

    NAMED FOR WHAT IT CHECKS. It previously claimed to pin that the vocabulary lives in
    "exactly one Python name", which it cannot see: a stray `== "withdrawn"` added
    anywhere in src/ would leave this green. That pin is a grep, and lives in
    tests/test_overlay_contract.py where the other grep contracts are.
    """
    assert interactions.WITHDRAWN == "withdrawn"
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "r", "test", "2026.07.06")
    interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "stale", "test", "2026.07.06")
    assert conn.execute(
        "SELECT decision FROM drugref.class_expansion_policy "
        "WHERE source_code = %s AND superseded_by IS NULL", (CODE,)
    ).fetchone()[0] == interactions.WITHDRAWN
