"""Two READ COMMITTED races in the ever-graded ledger (#287, db/086).

Each test holds one transaction open at the moment the race needs, commits the
other from a thread, and checks that the ledger still records the episode. They
need REAL commits on two connections, and the ledger is insert-only (a committed
row can never be cleaned up), so they run in a fresh database beside the session
one (`databases`, borrowed from the replay tests), not in the rolled-back `conn`.

The fix under test is one transaction-scoped advisory lock taken by both refresh
functions and by `graded_pairs.acknowledge`: the second transaction waits for the
first to COMMIT, and its next statement then reads a fresh snapshot.
"""
import threading
import time

import psycopg
import pytest

from drugref import graded_pairs, ids
from tests.test_release_replay import (  # noqa: F401  (`databases` is a fixture)
    A, B, X, Y, _fake_ingest, databases)
from tests.test_release_replay_graded_pairs import _rule

Z = "TESTUNI287Z"      # a third moiety, so a new member makes a real new pair

# How long a test waits for the threaded commit to block on the lock (or finish).
_WAIT_S = 10.0
# The threaded connection's own bound on any lock wait. Without it, a wait on some
# OTHER lock outlives the poll, and teardown then deadlocks client-side: the main
# thread's rollback needs psycopg's connection lock, which the blocked COMMIT holds.
_THREADED = {"options": "-c lock_timeout=20s"}


def _members(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return [path]


def _commit_in_thread(conn, work=None):
    """Run `work(conn)` (if any) then COMMIT `conn` on a thread; return the thread
    and a list that collects any exception it raised."""
    errors: list[BaseException] = []

    def run():
        try:
            if work:
                work(conn)
            conn.commit()
        except BaseException as exc:      # surfaced by the test after join()
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    return t, errors


def _wait_blocked_or_done(observer, pid, thread) -> bool:
    """Poll until `pid` waits on an advisory lock (True) or `thread` ends (False)."""
    deadline = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return False
        if observer.execute(
                "SELECT 1 FROM pg_stat_activity WHERE pid = %s "
                "AND wait_event_type = 'Lock' AND wait_event = 'advisory'",
                (pid,)).fetchone():
            return True
        time.sleep(0.05)
    raise AssertionError(f"backend {pid} neither blocked nor finished in {_WAIT_S} s")


def _episodes(conn, subject, partner) -> tuple[int, int]:
    """(all, unacknowledged) observations of the A x B ruling key for the pair."""
    return conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM "
        f"{graded_pairs.ACK_TABLE} a WHERE a.observation_id = o.observation_id)) "
        f"FROM {graded_pairs.OBSERVATION_TABLE} o "
        "WHERE o.rule_grain = 'class_rule' AND o.subject_key = %s "
        "AND o.object_class_uuid = %s AND o.subject_moiety = %s "
        "AND o.partner_moiety = %s",
        (ids.mint_class_uuid("MED-RT", A), ids.mint_class_uuid("MED-RT", B),
         ids.mint_moiety_uuid(subject), ids.mint_moiety_uuid(partner))).fetchone()


def _departed_x(dbs, tmp_path):
    """Origin where the A x B ruling was observed grading (X, Y), then X left A:
    one open, departed episode. Returns (origin DSN, ruling id, the input of a run
    that brings X back)."""
    origin, _ = dbs
    both = f"{A} {X}\n{A} {Y}\n{B} {Y}\n"
    with psycopg.connect(origin) as setup:
        _fake_ingest(setup, _members(tmp_path, "r1", both), "r1")
        rid = _rule(setup)
        setup.commit()
        _fake_ingest(setup, _members(tmp_path, "r2", f"{A} {Y}\n{B} {Y}\n"), "r2")
    return origin, rid, _members(tmp_path, "r3", both)


def _ack_x(conn, rid):
    return graded_pairs.acknowledge(conn, "class", rid, ids.mint_moiety_uuid(X),
                                    rationale="X left A", reviewed_by="test",
                                    reviewed_against="2026")


def test_a_ruling_committed_during_an_ingest_refresh_still_sees_its_new_members(
        databases, tmp_path):  # noqa: F811
    """#287 race 1: an ingest adds X to A and its full refresh runs at COMMIT before
    the A x B ruling exists; the ruling's per-key refresh cannot see the ingest's
    uncommitted member. Nobody observes (X, Z). Locked, the ruling's commit waits
    for the ingest's, then its refresh sees X in A."""
    origin, _ = databases
    with psycopg.connect(origin) as setup:
        _fake_ingest(setup, _members(tmp_path, "r1", f"{A} {Y}\n{B} {Z}\n"), "r1")
    with psycopg.connect(origin) as ingest, \
            psycopg.connect(origin, **_THREADED) as ruling, \
            psycopg.connect(origin, autocommit=True) as observer:
        _fake_ingest(ingest, _members(tmp_path, "r2", f"{A} {X}\n{A} {Y}\n{B} {Z}\n"),
                     "r2", commit=False)
        # Fire the ingest's refresh now, exactly as its COMMIT would, then hold it.
        ingest.execute("SET CONSTRAINTS drugref.ingest_run_graded_pairs IMMEDIATE")
        _rule(ruling)
        thread, errors = _commit_in_thread(ruling)
        blocked = _wait_blocked_or_done(observer, ruling.info.backend_pid, thread)
        ingest.commit()
        thread.join()
        assert not errors, errors
        assert blocked, "the ruling's commit never waited on the ledger lock"
        assert _episodes(observer, Y, Z) == (1, 1)
        assert _episodes(observer, X, Z) == (1, 1)      # the pair the race lost


def test_an_ack_racing_an_ingest_that_regrades_the_pair_leaves_an_open_episode(
        databases, tmp_path):  # noqa: F811
    """#287 race 2: (X, Y) departed; a curator acknowledges it while an ingest
    brings X back. The ingest's refresh sees the episode still open (the ack is
    uncommitted) and opens nothing; then the ack closes it: graded, no open
    episode, so the next departure would never banner. Locked, the ingest's
    commit waits for the ack's, then its refresh opens a new episode."""
    origin, rid, back = _departed_x(databases, tmp_path)
    with psycopg.connect(origin) as ack, \
            psycopg.connect(origin, **_THREADED) as ingest, \
            psycopg.connect(origin, autocommit=True) as observer:
        _ack_x(ack, rid)
        thread, errors = _commit_in_thread(
            ingest, lambda c: _fake_ingest(c, back, "r3", commit=False))
        blocked = _wait_blocked_or_done(observer, ingest.info.backend_pid, thread)
        ack.commit()
        thread.join()
        assert not errors, errors
        assert blocked, "the ingest's commit never waited on the ledger lock"
        # The ack closed the old episode AND the ingest opened a new one.
        assert _episodes(observer, X, Y) == (2, 1)


def test_an_ack_waiting_behind_an_ingest_that_regrades_the_pair_closes_nothing(
        databases, tmp_path):  # noqa: F811
    """#287 race 2, the other order: the ingest bringing X back holds the lock, and
    its refresh sees the old episode open, so opens nothing. An ack that read BEFORE
    taking the lock would still see X departed and close the only episode. Locked
    before its reads, the ack waits, sees the pair graded again, and raises."""
    origin, rid, back = _departed_x(databases, tmp_path)
    with psycopg.connect(origin) as ingest, \
            psycopg.connect(origin, **_THREADED) as ack, \
            psycopg.connect(origin, autocommit=True) as observer:
        _fake_ingest(ingest, back, "r3", commit=False)
        ingest.execute("SET CONSTRAINTS drugref.ingest_run_graded_pairs IMMEDIATE")
        thread, errors = _commit_in_thread(ack, lambda c: _ack_x(c, rid))
        blocked = _wait_blocked_or_done(observer, ack.info.backend_pid, thread)
        ingest.commit()
        thread.join()
        assert blocked, "the ack never waited on the ledger lock"
        assert [type(e) for e in errors] == [graded_pairs.NothingDeparted], errors
        assert _episodes(observer, X, Y) == (1, 1)      # still graded, still open


def test_the_ledger_lock_refuses_a_snapshot_taken_before_its_wait(conn):
    """db/086 works only because the statement after the wait takes a FRESH
    snapshot. Under REPEATABLE READ the snapshot predates the wait and the races
    return with no error, so the lock refuses to run there."""
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    with pytest.raises(psycopg.errors.RaiseException, match="READ COMMITTED"):
        conn.execute("SELECT drugref.lock_graded_pair_ledger()")


def test_an_ack_behind_an_idle_lock_holder_fails_fast_and_names_it(
        conn, _migrated, monkeypatch):
    """#289: a session left idle in a transaction that took the ledger lock used to
    stall `acknowledge` with no output. The interactive ack now waits at most
    ACK_LOCK_TIMEOUT, then raises LedgerBusy naming the holder's pid and role, and
    leaves the caller's transaction usable with its own lock_timeout back (the wait
    runs in a savepoint). The holder takes the lock without committing, so no
    commit escapes the rolled-back `conn`. It connects with the session DSN
    `_migrated`, not `conn.info.dsn`: psycopg leaves the password out of that."""
    monkeypatch.setattr(graded_pairs, "ACK_LOCK_TIMEOUT", "100ms")
    with psycopg.connect(_migrated) as holder:
        holder.execute("SELECT drugref.lock_graded_pair_ledger()")    # then idles
        conn.execute("SET LOCAL lock_timeout = '7s'")
        pid, user = holder.info.backend_pid, holder.info.user
        with pytest.raises(graded_pairs.LedgerBusy,
                           match=rf"backend {pid} \({user}, idle in "):
            _ack_x(conn, -1)    # no such ruling: the lock comes before the lookup
        assert conn.execute("SHOW lock_timeout").fetchone() == ("7s",)
        holder.rollback()


def test_an_ack_on_an_autocommit_connection_is_refused(_migrated):
    """In autocommit the savepoint would be a BEGIN/COMMIT of its own, releasing
    the ledger lock at once and reopening #287's race without a sound."""
    with psycopg.connect(_migrated, autocommit=True) as auto:
        with pytest.raises(RuntimeError, match="autocommit"):
            _ack_x(auto, -1)
