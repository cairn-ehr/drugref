"""`drugref audit class-ruling`: reach then vs now for one ruling (#258, #256)."""
import argparse
import re

from drugref import class_ruling_reach, cli_audit, db
from tests.test_class_ruling_reach import _ruled


def test_audit_reports_a_member_who_left(conn, ingest_run_id):
    rid, _sc, _oc, x, y = _ruled(conn, ingest_run_id)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    audit = class_ruling_reach.audit_ruling(conn, rid)
    subject, partner = audit.sides
    assert subject.side == "subject" and partner.side == "partner"
    assert subject.removed == {x} and subject.added == frozenset()
    assert subject.captured == "at_ruling"
    assert partner.then == partner.now == {y}


def test_audit_names_the_runs_loaded_at_review(conn, ingest_run_id):
    rid, *_ = _ruled(conn, ingest_run_id)
    at = conn.execute(
        "SELECT reviewed_at FROM drugref.curated_class_interaction "
        "WHERE curated_class_interaction_id = %s", (rid,)).fetchone()[0]
    ids = []
    for finished in ("%s::timestamptz",
                     "%s::timestamptz + interval '2 hours'"):
        ids.append(conn.execute(
            "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum,"
            " writer, started_at, finished_at) VALUES ('PBS', 'r', 'c', 'pbs_run', "
            "%s::timestamptz - interval '3 hours', "
            + finished + ") RETURNING ingest_run_id", (at, at)).fetchone()[0])
    runs = [r for r in class_ruling_reach.audit_ruling(conn, rid).loaded_runs
            if r[0] == "PBS"]
    assert runs == [("PBS", "pbs_run", ids[0], "r")]


def test_audit_of_an_unknown_id_is_none(conn):
    assert class_ruling_reach.audit_ruling(conn, -1) is None


def test_cli_unknown_id_exits_2(conn, capsys):
    assert cli_audit._handle_class_ruling(conn, argparse.Namespace(ruling_id=-1)) == 2
    assert "no class ruling" in capsys.readouterr().err


def test_cli_prints_both_sides(conn, ingest_run_id, capsys):
    rid, _sc, _oc, x, _y = _ruled(conn, ingest_run_id)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    assert cli_audit._handle_class_ruling(
        conn, argparse.Namespace(ruling_id=rid)) == 0
    out = capsys.readouterr().out
    assert "subject" in out and "partner" in out
    removed = out.split("removed: 1\n", 1)[1]
    assert removed.lstrip().startswith(str(x))


def _raw_ruling(conn, sc, oc):
    """A live ruling written by raw SQL, so the writer never snapshotted it."""
    rid = conn.execute(
        "INSERT INTO drugref.curated_class_interaction "
        "(subject_class_uuid, object_class_uuid, relationship, applies, severity, "
        " evidence_grade, source, reviewed_by, reviewed_against) "
        "VALUES (%s, %s, 'CI_MoA', true, 'major', 'established', 'DRUGREF', 't', 't') "
        "RETURNING curated_class_interaction_id", (sc, oc)).fetchone()[0]
    conn.execute("UPDATE drugref.curated_class_interaction SET superseded_by = %s "
                 "WHERE superseded_by IS NULL AND curated_class_interaction_id <> %s "
                 "AND subject_class_uuid = %s", (rid, rid, sc))
    return rid


def test_captured_is_none_when_a_side_has_no_snapshot(conn, ingest_run_id, capsys):
    _, sc, oc, _x, _y = _ruled(conn, ingest_run_id)
    rid = _raw_ruling(conn, sc, oc)
    audit = class_ruling_reach.audit_ruling(conn, rid)
    assert [s.captured for s in audit.sides] == ["none", "none"]
    cli_audit._handle_class_ruling(conn, argparse.Namespace(ruling_id=rid))
    assert "no ruling-time snapshot for this side" in capsys.readouterr().out


def test_backfill_wins_over_at_ruling_on_a_mixed_side(conn, ingest_run_id, capsys):
    rid, _sc, _oc, _x, y = _ruled(conn, ingest_run_id)
    # one extra backfill row on the subject side only (y is a valid moiety)
    conn.execute(
        "INSERT INTO drugref.curated_class_interaction_reach "
        "(curated_class_interaction_id, side, moiety_uuid, captured) "
        "VALUES (%s, 'subject', %s, 'backfill_db080')", (rid, y))
    audit = class_ruling_reach.audit_ruling(conn, rid)
    assert [s.captured for s in audit.sides] == ["backfill_db080", "at_ruling"]
    cli_audit._handle_class_ruling(conn, argparse.Namespace(ruling_id=rid))
    assert "reconstructed by db/080" in capsys.readouterr().out


def test_an_empty_snapshot_reads_as_reached_no_one(conn, ingest_run_id, capsys):
    """A correction made after the subject class emptied: the snapshot WAS taken and
    reached nobody, the opposite of "never recorded" (#266)."""
    from drugref import curation
    _rid, sc, oc, x, _y = _ruled(conn, ingest_run_id)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    rid = curation.record_class_interaction_judgement(
        conn, sc, oc, "CI_MoA", True, severity="contraindicated",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026")
    subject, partner = class_ruling_reach.audit_ruling(conn, rid).sides
    assert (subject.captured, subject.then) == ("at_ruling", frozenset())
    assert partner.captured == "at_ruling" and partner.then
    cli_audit._handle_class_ruling(conn, argparse.Namespace(ruling_id=rid))
    out = capsys.readouterr().out
    assert "subject: then 0, now 0 (captured: at_ruling)" in out
    assert "snapshot taken: this side reached no one then" in out
    assert "no ruling-time snapshot" not in out


def test_an_empty_backfilled_side_says_db080_found_no_one(conn, ingest_run_id, capsys):
    """db/082 marks a db/080-backfilled ruling `backfill_db080` as a whole, so its
    empty side reached no one when db/080 rebuilt it, NOT at ruling time (#266)."""
    _, sc, oc, _x, y = _ruled(conn, ingest_run_id)
    rid = _raw_ruling(conn, sc, oc)
    conn.execute(
        "INSERT INTO drugref.curated_class_interaction_reach "
        "(curated_class_interaction_id, side, moiety_uuid, captured) "
        "VALUES (%s, 'partner', %s, 'backfill_db080')", (rid, y))
    sql = (db.migration_dir() / "082_class_ruling_snapshot.sql").read_text()
    conn.execute(re.search(r"-- backfill:begin\n(.*?)-- backfill:end", sql, re.S)[1])
    subject, partner = class_ruling_reach.audit_ruling(conn, rid).sides
    assert (subject.captured, subject.then) == ("backfill_db080", frozenset())
    assert (partner.captured, partner.then) == ("backfill_db080", {y})
    cli_audit._handle_class_ruling(conn, argparse.Namespace(ruling_id=rid))
    out = capsys.readouterr().out.split("partner:", 1)[0]
    assert "reconstructed by db/080" in out
    assert "this side reached no one when db/080 rebuilt it" in out
    assert "no ruling-time snapshot" not in out and "no one then" not in out
