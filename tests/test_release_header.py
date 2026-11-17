"""The release header is the FIRST stdout line of `interactions` and `status` (#258).

Four states: published, drifted, none published, and a database predating db/081.
The command's own exit code must be unchanged by the header. DB-gated.
"""
import pytest

from drugref import cli, cli_interactions, release_state
from tests.test_cli_interactions import _args
from tests.test_release_state import LATER, _old_run, _publish, _run


def _first_line(capsys) -> str:
    return capsys.readouterr().out.splitlines()[0]


def _make_state(conn, state):
    if state == "published":
        _publish(conn, [_old_run(conn, "UNII")])
    elif state == "drifted":
        _publish(conn, [_old_run(conn, "UNII")])
        _run(conn, "UNII", LATER)
    elif state == "predates":
        conn.execute("DROP VIEW drugref.current_release")


EXPECTED = {
    "published": "drugref release: r1",
    "drifted": "drugref release: r1 (DRIFTED since publication: re-ingested) "
               "— answers may differ from that release",
    "none": "drugref release: none published "
            "(answers are not tied to a reconstructable release)",
    "predates": "drugref release: unknown (database predates db/081)",
}


@pytest.mark.parametrize("state", list(EXPECTED))
def test_interactions_prints_the_header_first(conn, a_moiety, capsys, state):
    _make_state(conn, state)
    assert cli_interactions._handle_interactions(conn, _args(a_moiety)) == 0
    assert _first_line(capsys) == EXPECTED[state]


@pytest.mark.parametrize("state", list(EXPECTED))
def test_status_prints_the_header_first(conn, capsys, monkeypatch, state):
    _make_state(conn, state)
    args = cli.build_parser().parse_args(["status"])
    rc = cli._handle_status(conn, args)
    assert _first_line(capsys) == EXPECTED[state]
    # Same state, header replaced by a constant: the exit code must not move.
    monkeypatch.setattr(release_state, "release_header", lambda conn: "stub")
    assert cli._handle_status(conn, args) == rc


def test_the_header_for_a_missing_view_never_aborts(conn):
    conn.execute("DROP VIEW drugref.current_release")
    assert release_state.release_header(conn) == EXPECTED["predates"]
