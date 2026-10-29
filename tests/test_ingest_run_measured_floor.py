# tests/test_ingest_run_measured_floor.py
"""db/055 + db/056: the no-backfill rule db/054 wrote in prose, given a mechanism
(issue 182).

`duration_measured` says "finished_at - started_at on this row is a real duration".
db/054 forbade a backfill in a paragraph, and the #181 review moved the flag's write
into `finish_run` so an operator's hand-rolled `SET finished_at = now()` no longer
produces a measured-looking row. What was still open was the DELIBERATE path:
`UPDATE ... SET duration_measured = true` worked, and its result could not be told
apart from a measured row.

The floor's four rules, each shown REFUSING below (a constraint nobody has watched
fire is the "gate that exists and never fires" of issues 74/66/76):

* no row is INSERTed already claiming a measurement;
* false -> true only in the UPDATE that writes the row's FIRST `finished_at` --
  which is exactly `finish_run`'s statement, and is not a backfill's;
* a written `finished_at` never returns to NULL (db/056). Without this rule "first"
  was only "NULL -> value": a backfill could reopen a finished row and then pose as
  `finish_run` in a second statement (the PR 184 review reproduced it);
* once true, the flag AND the two stamps it vouches for are frozen.

Each refusal is matched on text ONLY its own rule raises. The review deleted the
frozen rule from the function and every frozen test still passed, caught by the
backfill rule instead -- a test that cannot say which rule fired pins none of them.

And `finish_run` itself still works, and so does an ordinary write to a measured
row, so the floor is proved not to fire where it must not.
"""
import psycopg
import pytest

from drugref import provenance

# One phrase per rule, each appearing in exactly one RAISE in db/056's function.
INSERTED = "may not be inserted true"
BACKFILL = "only in the UPDATE that writes its first finished_at"
UNFINISH = "may not return to NULL"
FROZEN = "are frozen"


def _run(conn, *, finished):
    """INSERT one unmeasured row, finished or still open, and return its id.

    A direct INSERT rather than `open_run`, because `open_run` COMMITs and these
    tests want the `conn` fixture's rollback to leave nothing behind."""
    finished_at = "clock_timestamp()" if finished else "NULL"
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer, finished_at) "
        f"VALUES ('UNII', 'r1', 'sum', 'unii_run', {finished_at}) "
        "RETURNING ingest_run_id").fetchone()[0]


def _measured_run(conn):
    """A row in the state `finish_run` leaves it: finished and measured."""
    run_id = _run(conn, finished=False)
    provenance.finish_run(conn, run_id)
    return run_id


def _measured(conn, run_id):
    return conn.execute(
        "SELECT duration_measured FROM drugref.ingest_run WHERE ingest_run_id = %s",
        (run_id,)).fetchone()[0]


def _refused(conn, sql, run_id, phrase):
    """Run `sql` against `run_id` and require the floor to refuse it with `phrase`."""
    with pytest.raises(psycopg.errors.RaiseException, match=phrase):
        conn.execute(sql, (run_id,))
    conn.rollback()


def test_finish_run_still_marks_a_run_measured(conn, _migrated):
    """THE HOT PATH. The false -> true flip `finish_run` makes is the one transition
    the floor must let through; a floor that refused it would abort every ingest at
    its last statement. The row is `open_run`'s shape without its COMMIT, so the
    fixture's rollback leaves nothing in the shared test schema."""
    assert _measured(conn, _measured_run(conn)) is True


def test_a_backfill_of_a_finished_row_is_refused(conn, _migrated):
    """THE ISSUE'S OWN SHAPE: db/054's forbidden `UPDATE ... SET duration_measured =
    true` against rows that finished long ago. Nothing on such a row records which
    code wrote it, so storing true would store the very inference db/054 removed."""
    _refused(conn, "UPDATE drugref.ingest_run SET duration_measured = true "
                   "WHERE ingest_run_id = %s", _run(conn, finished=True), BACKFILL)


def test_flagging_an_open_run_without_finishing_it_is_refused(conn, _migrated):
    """The flag vouches for BOTH stamps; on an open run the second does not exist."""
    _refused(conn, "UPDATE drugref.ingest_run SET duration_measured = true "
                   "WHERE ingest_run_id = %s", _run(conn, finished=False), BACKFILL)


def test_a_finished_row_cannot_be_reopened_to_pose_as_finish_run(conn, _migrated):
    """THE TWO-STATEMENT BACKFILL (db/056). `SET finished_at = NULL` on a finished,
    unmeasured row involved no true flag, so db/055's gate never ran; the row then
    looked open, and `SET finished_at = <old>, duration_measured = true` passed as
    finish_run. Refusing the first statement makes "first finished_at" mean first."""
    _refused(conn, "UPDATE drugref.ingest_run SET finished_at = NULL "
                   "WHERE ingest_run_id = %s", _run(conn, finished=True), UNFINISH)


def test_a_row_may_not_be_inserted_already_measured(conn, _migrated):
    """A direct INSERT is the other way round the UPDATE floor: nothing measured it."""
    with pytest.raises(psycopg.errors.RaiseException, match=INSERTED):
        conn.execute(
            "INSERT INTO drugref.ingest_run "
            "(source, upstream_release, source_checksum, writer, "
            " finished_at, duration_measured) "
            "VALUES ('UNII', 'r1', 'sum', 'unii_run', clock_timestamp(), true)")
    conn.rollback()


@pytest.mark.parametrize("assignment", [
    "duration_measured = false",
    "finished_at = finished_at + interval '1 hour'",
    "started_at = started_at - interval '1 hour'",
    "finished_at = NULL",
])
def test_a_measured_row_is_frozen(conn, _migrated, assignment):
    """ONCE TRUE, NEVER ANYTHING ELSE -- and neither are the stamps it signed for. A
    rewritten stamp under a true flag is a measured-looking duration nothing measured,
    which is the backfill again by a side door."""
    _refused(conn, f"UPDATE drugref.ingest_run SET {assignment} "
                   "WHERE ingest_run_id = %s", _measured_run(conn), FROZEN)


def test_finish_run_twice_is_refused(conn, _migrated):
    """A second `finish_run` would re-stamp a measured row. No orchestrator calls it
    twice; pinned so a future retry loop learns that here, not in production."""
    run_id = _measured_run(conn)
    with pytest.raises(psycopg.errors.RaiseException, match=FROZEN):
        provenance.finish_run(conn, run_id)
    conn.rollback()


def test_a_measured_row_still_takes_writes_the_flag_does_not_vouch_for(conn, _migrated):
    """The floor freezes three columns, not the row. Without this, "refuse every
    UPDATE of a measured row" would pass every refusal test above."""
    run_id = _measured_run(conn)
    conn.execute("UPDATE drugref.ingest_run SET upstream_release = 'r2' "
                 "WHERE ingest_run_id = %s", (run_id,))
    assert _measured(conn, run_id) is True


def test_an_unmeasured_row_can_still_be_finished_by_hand(conn, _migrated):
    """The floor guards the FLAG, not the operator's tidy-up: `SET finished_at` on a
    crashed run must still work, and the row must still say it is unmeasured."""
    run_id = _run(conn, finished=False)
    conn.execute("UPDATE drugref.ingest_run SET finished_at = clock_timestamp() "
                 "WHERE ingest_run_id = %s", (run_id,))
    assert _measured(conn, run_id) is False
