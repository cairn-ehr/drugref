"""`drugref audit class-ruling`: reach then vs now for one ruling (#258, #256)."""
import argparse
import uuid

from drugref import class_ruling_reach, cli_audit
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
    for finished in ("%s::timestamptz - interval '2 hours'",
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
    assert "subject" in out and "partner" in out and str(x) in out
    assert isinstance(x, uuid.UUID)
