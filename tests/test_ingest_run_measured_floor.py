# tests/test_ingest_run_measured_floor.py
"""db/055: the no-backfill rule db/054 wrote in prose, given a mechanism (issue 182).

`duration_measured` says "finished_at - started_at on this row is a real duration".
db/054 forbade a backfill in a paragraph, and the #181 review moved the flag's write
into `finish_run` so an operator's hand-rolled `SET finished_at = now()` no longer
produces a measured-looking row. What was still open was the DELIBERATE path:
`UPDATE ... SET duration_measured = true` worked, and its result could not be told
apart from a measured row.

The floor's three rules, each shown REFUSING below (a constraint nobody has watched
fire is the "gate that exists and never fires" of issues 74/66/76):

* no row is INSERTed already claiming a measurement;
* false -> true only in the UPDATE that writes the row's FIRST `finished_at` --
  which is exactly `finish_run`'s statement, and is not a backfill's;
* once true, the flag AND the two stamps it vouches for are frozen.

And `finish_run` itself still works, so the floor is proved not to fire on the hot
path.
"""
import psycopg
import pytest

from drugref import provenance


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


def test_finish_run_still_marks_a_run_measured(conn, _migrated):
    """THE HOT PATH. The false -> true flip `finish_run` makes is the one transition
    the floor must let through; a floor that refused it would abort every ingest at
    its last statement. Driven through the real `open_run`, so the row is exactly the
    one an orchestrator produces."""
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run",
                                 clock=provenance.start_clock())
    provenance.finish_run(conn, run_id)
    assert conn.execute(
        "SELECT duration_measured FROM drugref.ingest_run WHERE ingest_run_id = %s",
        (run_id,)).fetchone()[0] is True


def test_a_backfill_of_a_finished_row_is_refused(conn, _migrated):
    """THE ISSUE'S OWN SHAPE: db/054's forbidden `UPDATE ... SET duration_measured =
    true` against rows that finished long ago. Nothing on such a row records which
    code wrote it, so storing true would store the very inference db/054 removed."""
    run_id = _run(conn, finished=True)
    with pytest.raises(psycopg.errors.RaiseException, match="duration_measured"):
        conn.execute("UPDATE drugref.ingest_run SET duration_measured = true "
                     "WHERE ingest_run_id = %s", (run_id,))
    conn.rollback()


def test_flagging_an_open_run_without_finishing_it_is_refused(conn, _migrated):
    """The flag vouches for BOTH stamps; on an open run the second does not exist."""
    run_id = _run(conn, finished=False)
    with pytest.raises(psycopg.errors.RaiseException, match="duration_measured"):
        conn.execute("UPDATE drugref.ingest_run SET duration_measured = true "
                     "WHERE ingest_run_id = %s", (run_id,))
    conn.rollback()


def test_a_row_may_not_be_inserted_already_measured(conn, _migrated):
    """A direct INSERT is the other way round the UPDATE floor: nothing measured it."""
    with pytest.raises(psycopg.errors.RaiseException, match="duration_measured"):
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
])
def test_a_measured_row_is_frozen(conn, _migrated, assignment):
    """ONCE TRUE, NEVER ANYTHING ELSE -- and neither are the stamps it signed for. A
    rewritten stamp under a true flag is a measured-looking duration nothing measured,
    which is the backfill again by a side door."""
    run_id = _measured_run(conn)
    with pytest.raises(psycopg.errors.RaiseException, match="duration_measured"):
        conn.execute(f"UPDATE drugref.ingest_run SET {assignment} "
                     "WHERE ingest_run_id = %s", (run_id,))
    conn.rollback()


def test_an_unmeasured_row_can_still_be_finished_by_hand(conn, _migrated):
    """The floor guards the FLAG, not the operator's tidy-up: `SET finished_at` on a
    crashed run must still work, and the row must still say it is unmeasured."""
    run_id = _run(conn, finished=False)
    conn.execute("UPDATE drugref.ingest_run SET finished_at = clock_timestamp() "
                 "WHERE ingest_run_id = %s", (run_id,))
    assert conn.execute(
        "SELECT duration_measured FROM drugref.ingest_run WHERE ingest_run_id = %s",
        (run_id,)).fetchone()[0] is False
