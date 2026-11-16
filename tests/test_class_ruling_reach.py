"""Each class ruling snapshots the drugs it reached, beside the ruling (#258, db/080)."""
import re

import psycopg
import pytest

from drugref import curation, db
from tests.test_class_subject_read_path import _a_graded_class_rule


def _reach(conn, *ids):
    return set(conn.execute(
        "SELECT curated_class_interaction_id, side, moiety_uuid, captured "
        "FROM drugref.curated_class_interaction_reach "
        "WHERE curated_class_interaction_id = ANY(%s)", (list(ids),)).fetchall())


def _ruled(conn, ingest_run_id, **extra):
    """A graded class rule; returns (ruling id, subject class, object class, x, y)."""
    sc, oc, (x,), (y,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025800", object_code="N0000025801",
        subject_members=[("TESTUNI258A", "reach subject")],
        object_members=[("TESTUNI258B", "reach partner")], **extra)
    rid = conn.execute(
        "SELECT curated_class_interaction_id FROM drugref.curated_class_interaction "
        "WHERE subject_class_uuid = %s AND superseded_by IS NULL", (sc,)).fetchone()[0]
    return rid, sc, oc, x, y


def test_a_ruling_snapshots_its_reach(conn, ingest_run_id):
    rid, _sc, _oc, x, y = _ruled(conn, ingest_run_id)
    assert _reach(conn, rid) == {(rid, "subject", x, "at_ruling"),
                                 (rid, "partner", y, "at_ruling")}


def test_reach_survives_a_member_leaving(conn, ingest_run_id):
    rid, _sc, _oc, x, y = _ruled(conn, ingest_run_id)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    assert (rid, "subject", x, "at_ruling") in _reach(conn, rid)


@pytest.mark.parametrize("sql", [
    "UPDATE drugref.curated_class_interaction_reach SET side = 'partner'",
    "DELETE FROM drugref.curated_class_interaction_reach"])
def test_reach_is_insert_only(conn, ingest_run_id, sql):
    _ruled(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.RaiseException, match="insert-only"):
        conn.execute(sql)


def test_a_correction_snapshots_again_and_keeps_the_old(conn, ingest_run_id):
    old, sc, oc, x, y = _ruled(conn, ingest_run_id)
    new = curation.record_class_interaction_judgement(
        conn, sc, oc, "CI_MoA", True, severity="contraindicated",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026")
    assert new != old
    assert {r[0] for r in _reach(conn, old, new)} == {old, new}
    assert len(_reach(conn, old)) == len(_reach(conn, new)) == 2


def test_db080_backfills_live_rulings_as_backfill(conn, ingest_run_id):
    """Runs the migration's own backfill span against a ruling inserted by raw SQL,
    so the writer's snapshot never touched it."""
    _, sc, oc, x, y = _ruled(conn, ingest_run_id)
    rid = conn.execute(
        "INSERT INTO drugref.curated_class_interaction "
        "(subject_class_uuid, object_class_uuid, relationship, applies, severity, "
        " evidence_grade, source, reviewed_by, reviewed_against) "
        "VALUES (%s, %s, 'CI_MoA', true, 'major', 'established', 'DRUGREF', 't', 't') "
        "RETURNING curated_class_interaction_id", (sc, oc)).fetchone()[0]
    conn.execute("UPDATE drugref.curated_class_interaction SET superseded_by = %s "
                 "WHERE superseded_by IS NULL AND curated_class_interaction_id <> %s "
                 "AND subject_class_uuid = %s", (rid, rid, sc))
    sql = (db.migration_dir() / "080_class_ruling_reach.sql").read_text()
    span = re.search(r"-- backfill:begin\n(.*?)-- backfill:end", sql, re.S).group(1)
    conn.execute(span)
    assert _reach(conn, rid) == {(rid, "subject", x, "backfill_db080"),
                                 (rid, "partner", y, "backfill_db080")}
