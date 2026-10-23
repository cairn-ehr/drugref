# tests/test_tools_server_channel.py
"""Every tool under `tools/` can hear the server, and says what it hears (issue 179).

⇒ WHY THIS FILE EXISTS. `db.connect` installs `server_messages.log_server_message`
on every connection it opens (issue 174), so a NOTICE or WARNING the server sends
is published on the `drugref.postgres` logger instead of discarded. Two things
kept that channel dark under `tools/`:

1. three tools opened their own bare `psycopg.connect`, so no handler was ever
   installed -- including `spl_recovery_probe`, a diagnostic whose whole job is
   telling an operator what state the database is in;
2. no tool configured logging, so the root logger had no handler and only
   WARNING and worse reached stderr through logging's last-resort handler. Every
   NOTICE the severity mapping deliberately places at INFO was dropped.

Both rules are checked by READING SOURCE, the shape of
`tests/test_spl_tools_smoke.py`: the lines that matter sit inside `main()`,
behind a database and a multi-gigabyte corpus, so no import can reach them.
"""
import pathlib
import subprocess
import sys

import pytest

_TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
_TOOL_FILES = sorted(_TOOLS.glob("*.py"))
_ENTRY_POINTS = [p for p in _TOOL_FILES if '__name__ == "__main__"' in p.read_text()]


def test_the_scans_HAVE_something_to_check():
    """⇒ A SOURCE SCAN THAT MATCHES NOTHING PASSES, so this says how much it must find.

    A moved `tools/` directory or a changed `__main__` spelling would otherwise
    leave both gates below green while guarding nothing -- the "gate that exists
    and never fires" of issues 74, 66 and 76. Nine entry points exist today; a
    tenth raises the number rather than slipping past.
    """
    assert len(_TOOL_FILES) >= 20, f"found only {len(_TOOL_FILES)} tool file(s) in {_TOOLS}"
    assert len(_ENTRY_POINTS) >= 9, f"found only {len(_ENTRY_POINTS)} entry point(s)"
    connecting = [p.name for p in _TOOL_FILES if "db.connect(" in p.read_text()]
    assert len(connecting) >= 6, f"only {connecting} connect through db.connect"


@pytest.mark.parametrize("path", _TOOL_FILES, ids=lambda p: p.name)
def test_no_tool_opens_a_bare_psycopg_connection(path):
    """A bare `psycopg.connect` installs no notice handler: the server goes unheard."""
    assert "psycopg.connect(" not in path.read_text(), (
        f"{path.name} opens a bare psycopg.connect -- use drugref.db.connect, which "
        "installs server_messages' notice handler (issue 174)")


@pytest.mark.parametrize("path", _ENTRY_POINTS, ids=lambda p: p.name)
def test_every_tool_entry_point_configures_logging(path):
    """Without a root handler, every INFO-level server NOTICE is silently dropped."""
    assert "server_messages.configure_logging(" in path.read_text(), (
        f"{path.name} has a __main__ entry point but never calls "
        "server_messages.configure_logging(), so NOTICEs mapped to INFO vanish")


def test_configure_logging_makes_an_INFO_server_message_visible():
    """The behaviour the scan above stands in for, run in a FRESH interpreter.

    A subprocess because pytest installs its own handlers on the root logger, and
    `logging.basicConfig` is a no-op on a root that already has one -- in-process,
    this test could not tell a working helper from an empty one.
    """
    code = ("import logging\n"
            "from drugref import server_messages\n"
            "server_messages.configure_logging()\n"
            "server_messages.log.info('table t does not exist, skipping')\n")
    result = subprocess.run([sys.executable, "-c", code],
                            capture_output=True, text=True, check=True)
    assert result.stderr == "INFO drugref.postgres: table t does not exist, skipping\n"
