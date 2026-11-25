"""`drugref audit departures` (#281): one listing of every open departure. DB-gated."""
from drugref import cli_departures, graded_pairs
from tests.graded_pair_helpers import commit_point  # noqa: F401  (fixture parity)
from tests.test_graded_pairs_departed import _a_rule_x_left, _moiety_rule

_WHY = dict(rationale="left on purpose", reviewed_by="dr t", reviewed_against="2026")


def test_no_departures_exits_0(conn, capsys):
    assert cli_departures.handle_audit_departures(conn, None) == 0
    assert "open departures: 0 ruling(s), 0 pair(s)" in capsys.readouterr().out


def test_departures_across_rulings_are_listed_and_exit_3(conn, ingest_run_id, capsys):
    cruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 1)
    mruling, _s, p, _b2 = _moiety_rule(conn, ingest_run_id, 2)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))

    assert cli_departures.handle_audit_departures(conn, None) == 3
    out = capsys.readouterr().out
    assert "open departures: 2 ruling(s), 2 pair(s)" in out
    assert f"class ruling {cruling}" in out and f"moiety ruling {mruling}" in out
    assert out.count("first seen:") == 2


def test_acknowledged_ones_are_not_listed(conn, ingest_run_id, capsys):
    ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 3)
    graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY)

    assert cli_departures.handle_audit_departures(conn, None) == 0
    assert "open departures: 0" in capsys.readouterr().out


def test_without_db083_exits_2_with_a_note(conn, capsys):
    conn.execute(f"DROP TABLE {graded_pairs.OBSERVATION_TABLE} CASCADE")

    assert cli_departures.handle_audit_departures(conn, None) == 2
    captured = capsys.readouterr()
    assert "open departures" not in captured.out
    assert "083" in captured.err and "DROPPED" in captured.err
