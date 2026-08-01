# tests/test_ingest_observability.py
"""What a run record says, and what it could not say before db/025 (#16).

Two facts this module pins, both of which the schema alone would let drift:

* `writer`, because source 'MED-RT' has TWO writers (medrt_run and mesh_rel_run) and
  a release-per-source view cannot tell them apart. That is #39 one layer up, on the
  table #39's own fix could not reach.
* `ingest_run_incomplete`, which BEFORE THIS MIGRATION COULD ONLY EVER BE EMPTY: the
  run row was written inside the work's transaction, so a crash rolled it away.
"""
import psycopg
import pytest


def _run(conn, source, writer, release="r1", finished=False):
    """One ingest_run row. `finished` decides which of the two views it lands in."""
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, %s, 'sum', %s) RETURNING ingest_run_id",
        (source, release, writer)).fetchone()[0]
    if finished:
        conn.execute("UPDATE drugref.ingest_run SET finished_at = now() "
                     "WHERE ingest_run_id = %s", (run_id,))
    return run_id


def test_writer_has_no_default(conn):
    """NO DEFAULT, deliberately -- db/018's `reason` posture, for the same reason: a
    writer that does not declare itself must fail, not inherit somebody else's
    identity. A DEFAULT would make every future orchestrator correct by accident."""
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute("INSERT INTO drugref.ingest_run "
                     "(source, upstream_release, source_checksum) "
                     "VALUES ('UNII', 'r1', 'sum')")


def test_an_unknown_writer_is_refused(conn):
    """The vocabulary is CHECKed, so a typo cannot silently create a seventh writer
    that loaded_release then reports as its own live release."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.ingest_run "
                     "(source, upstream_release, source_checksum, writer) "
                     "VALUES ('UNII', 'r1', 'sum', 'unii')")


def test_loaded_release_separates_the_two_medrt_writers(conn):
    """THE REASON THE COLUMN EXISTS. medrt_run and mesh_rel_run both open under
    'MED-RT'. Re-ingest one and not the other and a per-source view would report the
    newer as THE MED-RT release while the other half is a release behind."""
    _run(conn, "MED-RT", "medrt_run", release="2026.07.06", finished=True)
    _run(conn, "MED-RT", "mesh_rel_run", release="2026.05.04", finished=True)

    rows = dict(conn.execute(
        "SELECT writer, upstream_release FROM drugref.loaded_release "
        "WHERE source = 'MED-RT'").fetchall())
    assert rows == {"medrt_run": "2026.07.06", "mesh_rel_run": "2026.05.04"}


def test_loaded_release_keeps_only_the_newest_finished_run(conn):
    """One row per (source, writer): the release that writer last landed."""
    _run(conn, "UNII", "unii_run", release="old", finished=True)
    _run(conn, "UNII", "unii_run", release="new", finished=True)

    assert conn.execute(
        "SELECT upstream_release FROM drugref.loaded_release "
        "WHERE source = 'UNII'").fetchall() == [("new",)]


def test_loaded_release_ignores_a_run_that_never_finished(conn):
    """A crashed run is not a loaded release. The whole point of committing the row
    early is that this distinction becomes observable rather than implied."""
    _run(conn, "PBS", "pbs_run", release="landed", finished=True)
    _run(conn, "PBS", "pbs_run", release="crashed", finished=False)

    assert conn.execute(
        "SELECT upstream_release FROM drugref.loaded_release "
        "WHERE source = 'PBS'").fetchall() == [("landed",)]


def test_ingest_run_incomplete_reports_exactly_the_unfinished(conn):
    """The complementary filter on the SAME column, so the two views cannot disagree
    -- db/018's ci_rule_partner_reach shape, adopted after the interaction debt round
    found one measure stated twice with only one copy corrected."""
    _run(conn, "PBS", "pbs_run", release="landed", finished=True)
    crashed = _run(conn, "PBS", "pbs_run", release="crashed", finished=False)

    assert conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run_incomplete").fetchall() \
        == [(crashed,)]
