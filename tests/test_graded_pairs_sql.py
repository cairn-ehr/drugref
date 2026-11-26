"""db/087: the departure read and the acknowledgement as SQL functions (#283). DB-gated.

WHY SQL. The reviewer GUI (Rust, sqlx) acknowledges departures too, so the rule for
"what departed", "who left" and "what an acknowledgement closes" moved out of
`graded_pairs.py` into SQL functions both callers use: one home, not a Python copy
and a Rust copy. `graded_pairs.departed` / `leavers` / `acknowledge` are now thin
callers, so the existing tests in test_graded_pairs_departed.py and
test_graded_pairs_ack.py check the port. These tests pin what only the SQL surface
has: the SQLSTATEs the Rust service maps, and the GUI's derived
`reviewed_against`.
"""
import uuid

import psycopg
import pytest

from drugref import graded_pairs
from tests.graded_pair_helpers import commit_point
from tests.test_graded_pairs_departed import _a_rule_x_left, _moiety_rule

_ACK = ("SELECT acked_subject, acked_partner FROM drugref.acknowledge_departure(%s, %s, %s, 'accepted', "
        "'dr t', %s)")


def _sqlstate(conn, sql, params):
    """Run `sql` in a savepoint and return the SQLSTATE it raised."""
    with pytest.raises(psycopg.Error) as raised:
        with conn.transaction():
            conn.execute(sql, params)
    return raised.value.sqlstate


def test_an_unknown_ruling_raises_lg001(conn):
    assert _sqlstate(conn, _ACK, ("moiety_rule", 2_000_000_000, uuid.uuid4(),
                                  "2026")) == "LG001"


def test_nothing_departed_raises_lg002_and_writes_nothing(conn, ingest_run_id):
    ruling, _x, z, *_ = _a_rule_x_left(conn, ingest_run_id, 11)
    assert _sqlstate(conn, _ACK, ("class_rule", ruling, z, "2026")) == "LG002"
    assert conn.execute(
        f"SELECT count(*) FROM {graded_pairs.ACK_TABLE}").fetchone() == (0,)


def test_an_ack_returns_the_pairs_it_closed(conn, ingest_run_id):
    ruling, x, _z, p, *_ = _a_rule_x_left(conn, ingest_run_id, 12)
    assert conn.execute(_ACK, ("class_rule", ruling, x, "2026")).fetchall() == [(x, p)]


def _releases(conn, grain, ruling):
    return conn.execute("SELECT drugref.departure_reviewed_against(%s, %s)",
                        (grain, ruling)).fetchone()[0]


def _release_of(conn, run):
    return conn.execute(
        "SELECT source || ' ' || upstream_release FROM drugref.ingest_run "
        "WHERE ingest_run_id = %s", (run,)).fetchone()[0]


def test_reviewed_against_names_the_runs_holding_the_rulings_class_memberships(
        conn, ingest_run_id):
    """The GUI does not ask the reviewer to type a release (owner, #283): the ack is
    recorded against the ingest run(s) whose memberships of the ruling's classes the
    reviewer accepted, as `source release`."""
    ruling, *_ = _a_rule_x_left(conn, ingest_run_id, 13)
    assert _releases(conn, "class_rule", ruling) == _release_of(conn, ingest_run_id)


def test_reviewed_against_falls_back_to_the_class_sources_latest_run(
        conn, ingest_run_id):
    """A class with no members left still names a release: the latest finished run
    of the class's own source."""
    ruling, _s, _p, b = _moiety_rule(conn, ingest_run_id, 5)
    conn.execute("DELETE FROM drugref.class_membership WHERE class_uuid = %s", (b,))
    (source,) = conn.execute("SELECT source FROM drugref.substance_class "
                             "WHERE class_uuid = %s", (b,)).fetchone()
    conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer, started_at, finished_at) VALUES (%s, 'fallback-test', 'x', "
        "'unattributed', now() - interval '1 minute', now())", (source,))
    assert _releases(conn, "moiety_rule", ruling) == f"{source} fallback-test"


def test_reviewed_against_joins_the_runs_of_both_classes(conn, ingest_run_id):
    """Class grain: the subject and object classes can come from different runs;
    each is named once, joined by ' / ' (sorted, as string_agg DISTINCT returns)."""
    ruling, *_rest, b = _a_rule_x_left(conn, ingest_run_id, 16)
    (other,) = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer, started_at) SELECT source, 'second-run', 'x', 'unattributed', now() "
        "FROM drugref.ingest_run WHERE ingest_run_id = %s RETURNING ingest_run_id",
        (ingest_run_id,)).fetchone()
    conn.execute("UPDATE drugref.class_membership SET ingest_run = %s "
                 "WHERE class_uuid = %s", (other, b))
    expected = " / ".join(sorted({_release_of(conn, ingest_run_id),
                                  _release_of(conn, other)}))
    assert _releases(conn, "class_rule", ruling) == expected


def test_no_release_to_derive_raises_lg004_and_writes_nothing(conn, ingest_run_id):
    """A memberless class whose source has no finished run leaves nothing to record
    the GUI's acknowledgement against: refuse rather than record a blank release."""
    ruling, _s, p, b = _moiety_rule(conn, ingest_run_id, 6)
    conn.execute("DELETE FROM drugref.class_membership WHERE class_uuid = %s", (b,))
    # rolled back with the test's transaction (commit_point does not commit)
    conn.execute("UPDATE drugref.ingest_run SET finished_at = NULL WHERE source = "
                 "(SELECT source FROM drugref.substance_class WHERE class_uuid = %s)",
                 (b,))
    assert _releases(conn, "moiety_rule", ruling) is None
    assert _sqlstate(conn, _ACK, ("moiety_rule", ruling, p, None)) == "LG004"
    assert conn.execute(
        f"SELECT count(*) FROM {graded_pairs.ACK_TABLE}").fetchone() == (0,)


def test_a_null_reviewed_against_is_derived_and_recorded(conn, ingest_run_id):
    """The GUI passes NULL; the function derives the release (after taking the ledger
    lock, by its structure; the race tests pin the lock order) and records it."""
    ruling, x, *_ = _a_rule_x_left(conn, ingest_run_id, 14)
    (against,) = {r[0] for r in conn.execute(
        "SELECT acked_against FROM drugref.acknowledge_departure(%s, %s, %s, "
        "'accepted', 'dr t', NULL)", ("class_rule", ruling, x))}
    assert against == _release_of(conn, ingest_run_id)
    assert conn.execute(
        f"SELECT reviewed_against FROM {graded_pairs.ACK_TABLE}").fetchone() == (
            _release_of(conn, ingest_run_id),)


def test_leavers_names_who_left(conn, ingest_run_id):
    ruling, x, _z, p, *_ = _a_rule_x_left(conn, ingest_run_id, 15)
    commit_point(conn)
    (d,) = graded_pairs.departed(conn, x)
    assert conn.execute(
        "SELECT * FROM drugref.departure_leavers(%s, %s, %s, %s)",
        (d.rule_grain, d.ruling_id, [x], [p])).fetchall() == [(x,)]
