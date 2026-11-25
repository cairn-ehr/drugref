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

from drugref import graded_pairs, ids
from tests.test_release_replay import (  # noqa: F401  (`databases` is a fixture)
    A, B, X, Y, _fake_ingest, databases)
from tests.test_release_replay_graded_pairs import _rule

Z = "TESTUNI287Z"      # a third moiety, so a new member makes a real new pair

# How long a test waits for the threaded commit to block on the lock (or finish).
_WAIT_S = 10.0


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


def _open_episodes(conn, subject, partner) -> int:
    """Unacknowledged observations of the A x B ruling key for (subject, partner)."""
    return conn.execute(
        f"SELECT count(*) FROM {graded_pairs.OBSERVATION_TABLE} o "
        "WHERE o.rule_grain = 'class_rule' AND o.subject_key = %s "
        "AND o.object_class_uuid = %s AND o.subject_moiety = %s "
        "AND o.partner_moiety = %s AND NOT EXISTS (SELECT 1 FROM "
        f"{graded_pairs.ACK_TABLE} a WHERE a.observation_id = o.observation_id)",
        (ids.mint_class_uuid("MED-RT", A), ids.mint_class_uuid("MED-RT", B),
         ids.mint_moiety_uuid(subject), ids.mint_moiety_uuid(partner))).fetchone()[0]


def test_a_ruling_committed_during_an_ingest_refresh_still_sees_its_new_members(
        databases, tmp_path):  # noqa: F811
    """#287 race 1: an ingest adds X to A and its full refresh runs at COMMIT before
    the A x B ruling exists; the ruling's per-key refresh cannot see the ingest's
    uncommitted member. Nobody observes (X, Z). Locked, the ruling's commit waits
    for the ingest's, then its refresh sees X in A."""
    origin, _ = databases
    with psycopg.connect(origin) as setup:
        _fake_ingest(setup, _members(tmp_path, "r1", f"{A} {Y}\n{B} {Z}\n"), "r1")
    with psycopg.connect(origin) as ingest, psycopg.connect(origin) as ruling, \
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
        assert _open_episodes(observer, Y, Z) == 1
        assert _open_episodes(observer, X, Z) == 1      # the pair the race lost
        assert blocked, "the ruling's commit never waited on the ledger lock"


def test_an_ack_racing_an_ingest_that_regrades_the_pair_leaves_an_open_episode(
        databases, tmp_path):  # noqa: F811
    """#287 race 2: (X, Y) departed; a curator acknowledges it while an ingest
    brings X back. The ingest's refresh sees the episode still open (the ack is
    uncommitted) and opens nothing; then the ack closes it: graded, no open
    episode, so the next departure would never banner. Locked, the ingest's
    commit waits for the ack's, then its refresh opens a new episode."""
    origin, _ = databases
    both = f"{A} {X}\n{A} {Y}\n{B} {Y}\n"
    with psycopg.connect(origin) as setup:
        _fake_ingest(setup, _members(tmp_path, "r1", both), "r1")
        rid = _rule(setup)
        setup.commit()
        _fake_ingest(setup, _members(tmp_path, "r2", f"{A} {Y}\n{B} {Y}\n"), "r2")
    with psycopg.connect(origin) as ack, psycopg.connect(origin) as ingest, \
            psycopg.connect(origin, autocommit=True) as observer:
        graded_pairs.acknowledge(ack, "class", rid, ids.mint_moiety_uuid(X),
                                 rationale="X left A", reviewed_by="test",
                                 reviewed_against="2026")
        back = _members(tmp_path, "r3", both)
        thread, errors = _commit_in_thread(
            ingest, lambda c: _fake_ingest(c, back, "r3", commit=False))
        blocked = _wait_blocked_or_done(observer, ingest.info.backend_pid, thread)
        ack.commit()
        thread.join()
        assert not errors, errors
        assert _open_episodes(observer, X, Y) == 1      # graded again: a new episode
        assert blocked, "the ingest's commit never waited on the ledger lock"
