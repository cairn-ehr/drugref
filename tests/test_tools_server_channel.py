# tests/test_tools_server_channel.py
"""Every entry point can hear the server, and says what it hears (issue 179).

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

Both rules are checked by PARSING SOURCE (`ast`), the idea of
`tests/test_spl_tools_smoke.py`: the lines that matter sit in `__main__` blocks
and DB-loading helpers, behind a database and a multi-gigabyte corpus, so no
import can reach them. A parse rather than a substring match, because a
substring is fooled by a comment, an alias (`import psycopg as pg`), a
`from psycopg import connect`, or a `__main__` guard spelled with single quotes
-- the scanners' own tests at the bottom pin each of those.

The bare-connect rule covers `src/drugref/` too: `db.connect` is "the one
function every orchestrator, CLI command and migration opens its connection
through", and until this file nothing held `src/` to that sentence.
"""
import ast
import functools
import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
_SRC = _ROOT / "src" / "drugref"
_TOOL_FILES = sorted(_TOOLS.glob("*.py"))
#: `db.py` is excluded because it IS the one sanctioned `psycopg.connect`.
_SRC_FILES = sorted(p for p in _SRC.rglob("*.py") if p != _SRC / "db.py")


@functools.cache
def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def bare_connects(tree: ast.Module) -> list[int]:
    """Line numbers of every call that opens a psycopg connection directly.

    First collect every local name bound to psycopg -- `import psycopg`,
    `import psycopg as pg`, `from psycopg import connect, Connection` -- then flag
    a call to `connect` through any of them: `psycopg.connect(...)`,
    `pg.connect(...)`, `Connection.connect(...)`, or a bare `connect(...)` that
    was imported from psycopg. `db.connect(...)` is not flagged, because `db` is
    not a psycopg name.
    """
    psycopg_names: dict[str, str] = {}  # local name -> what it is inside psycopg
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "psycopg":
                    psycopg_names[alias.asname or "psycopg"] = ""
        elif (isinstance(node, ast.ImportFrom)
              and (node.module or "").split(".")[0] == "psycopg"):
            for alias in node.names:
                psycopg_names[alias.asname or alias.name] = alias.name

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and psycopg_names.get(func.id) == "connect":
            hits.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "connect":
            root = func.value
            while isinstance(root, ast.Attribute):  # psycopg.Connection.connect
                root = root.value
            if isinstance(root, ast.Name) and root.id in psycopg_names:
                hits.append(node.lineno)
    return hits


def main_guard(tree: ast.Module) -> ast.If | None:
    """The module-level `if __name__ == "__main__":` block, in either spelling."""
    for node in tree.body:
        test = getattr(node, "test", None)
        if not (isinstance(node, ast.If) and isinstance(test, ast.Compare)
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
            continue
        sides = [test.left, *test.comparators]
        if (any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
                and any(isinstance(s, ast.Constant) and s.value == "__main__"
                        for s in sides)):
            return node
    return None


def _first_statement_calling(block: ast.If, is_target) -> int | None:
    """Index of the first statement in `block` that contains a matching call."""
    for i, stmt in enumerate(block.body):
        if any(isinstance(n, ast.Call) and is_target(n.func) for n in ast.walk(stmt)):
            return i
    return None


def _is_configure_logging(func: ast.expr) -> bool:
    return (isinstance(func, ast.Attribute) and func.attr == "configure_logging"
            and isinstance(func.value, ast.Name) and func.value.id == "server_messages")


def _is_main(func: ast.expr) -> bool:
    return isinstance(func, ast.Name) and func.id == "main"


def configures_logging_before_main(block: ast.If) -> bool:
    """True when the guard calls `server_messages.configure_logging` BEFORE `main`.

    After `raise SystemExit(main())` the call would never run, and a call that
    appears only in a comment or docstring is not a call at all.
    """
    configure = _first_statement_calling(block, _is_configure_logging)
    main = _first_statement_calling(block, _is_main)
    return configure is not None and main is not None and configure < main


_ENTRY_POINTS = [p for p in _TOOL_FILES if main_guard(_tree(p)) is not None]


def test_the_scans_HAVE_something_to_check():
    """⇒ A SOURCE SCAN THAT MATCHES NOTHING PASSES, so this says how much it must find.

    A moved `tools/` or `src/` directory, or a parser that stopped recognising the
    guard, would otherwise leave every gate below green while guarding nothing --
    the "gate that exists and never fires" of issues 74, 66 and 76. Nine tool
    entry points exist today; a new one is covered by the parametrisation, and the
    floor should be raised with it.
    """
    assert len(_TOOL_FILES) >= 20, f"found only {len(_TOOL_FILES)} tool file(s) in {_TOOLS}"
    assert len(_SRC_FILES) >= 70, f"found only {len(_SRC_FILES)} module(s) in {_SRC}"
    assert len(_ENTRY_POINTS) >= 9, f"found only {len(_ENTRY_POINTS)} entry point(s)"
    connecting = [p.name for p in _TOOL_FILES if "db.connect(" in p.read_text()]
    assert len(connecting) >= 6, f"only {connecting} connect through db.connect"


@pytest.mark.parametrize("path", _TOOL_FILES + _SRC_FILES,
                         ids=lambda p: str(p.relative_to(_ROOT)))
def test_nothing_opens_a_bare_psycopg_connection(path):
    """A bare `psycopg.connect` installs no notice handler: the server goes unheard."""
    lines = bare_connects(_tree(path))
    assert not lines, (
        f"{path.name} line(s) {lines} open a psycopg connection directly -- use "
        "drugref.db.connect, which installs server_messages' notice handler (issue 174)")


@pytest.mark.parametrize("path", _ENTRY_POINTS, ids=lambda p: p.name)
def test_every_tool_entry_point_configures_logging(path):
    """Without a root handler, every INFO-level server NOTICE is silently dropped."""
    assert configures_logging_before_main(main_guard(_tree(path))), (
        f"{path.name}'s __main__ block does not call server_messages.configure_logging() "
        "before main(), so NOTICEs mapped to INFO vanish")


# --- the scanners themselves: each spelling a substring match would have missed ---

@pytest.mark.parametrize("source", [
    "import psycopg\npsycopg.connect(dsn)\n",
    "import psycopg as pg\npg.connect(dsn)\n",
    "from psycopg import connect\nconnect(dsn)\n",
    "from psycopg import connect as c\nc(dsn)\n",
    "import psycopg\npsycopg.Connection.connect(dsn)\n",
    "from psycopg import Connection\nConnection.connect(dsn)\n",
    "import psycopg\npsycopg.connect (dsn)\n",
])
def test_bare_connects_catches_every_spelling(source):
    assert bare_connects(ast.parse(source)) == [2]


@pytest.mark.parametrize("source", [
    "from drugref import db\ndb.connect(dsn)\n",
    "# psycopg.connect(dsn) is what we must NOT do\nimport socket\nsocket.connect(x)\n",
    'import psycopg\n"""psycopg.connect(dsn)"""\n',
])
def test_bare_connects_ignores_what_is_not_one(source):
    assert bare_connects(ast.parse(source)) == []


@pytest.mark.parametrize("source, expected", [
    ('if __name__ == "__main__":\n    server_messages.configure_logging()\n'
     '    raise SystemExit(main())\n', True),
    ("if __name__ == '__main__':\n    server_messages.configure_logging()\n"
     "    main()\n", True),
    ('if "__main__" == __name__:\n    server_messages.configure_logging()\n'
     '    main()\n', True),
    ('if __name__ == "__main__":\n    raise SystemExit(main())\n'
     '    server_messages.configure_logging()\n', False),
    ('if __name__ == "__main__":\n    # server_messages.configure_logging()\n'
     '    raise SystemExit(main())\n', False),
    ('if __name__ == "__main__":\n    server_messages.configure_logging()\n', False),
])
def test_configures_logging_before_main(source, expected):
    guard = main_guard(ast.parse(source))
    assert guard is not None
    assert configures_logging_before_main(guard) is expected


def test_main_guard_ignores_other_ifs():
    assert main_guard(ast.parse('if __name__ != "__main__":\n    pass\n')) is None


# --- the helper's behaviour, run in a FRESH interpreter ---

def _stderr_of(code: str, **env: str) -> str:
    """Run `code` in a fresh interpreter and return what it wrote to stderr.

    A subprocess because pytest installs its own handlers on the root logger, and
    `logging.basicConfig` is a no-op on a root that already has one -- in-process,
    these tests could not tell a working helper from an empty one.
    """
    child_env = {k: v for k, v in os.environ.items() if k != "DRUGREF_LOG_LEVEL"}
    result = subprocess.run([sys.executable, "-c",
                             "from drugref import server_messages\n" + code],
                            capture_output=True, text=True, check=True,
                            env={**child_env, **env})
    return result.stderr


def test_configure_logging_makes_an_INFO_server_message_visible():
    """The behaviour the entry-point scan above stands in for."""
    assert _stderr_of("server_messages.configure_logging()\n"
                      "server_messages.log.info('table t does not exist, skipping')\n"
                      ) == "INFO drugref.postgres: table t does not exist, skipping\n"


def test_configure_logging_honours_its_level():
    """`drugref --log-level warning` must actually hide INFO (the `cli.main` path)."""
    assert _stderr_of("server_messages.configure_logging('warning')\n"
                      "server_messages.log.info('hidden')\n") == ""


def test_DRUGREF_LOG_LEVEL_sets_the_default():
    """The tools take no `--log-level`; the environment is how an operator asks for
    DEBUG -- e.g. alongside `PGOPTIONS='-c client_min_messages=debug1'`."""
    assert _stderr_of("server_messages.configure_logging()\n"
                      "server_messages.log.debug('debug1 line')\n",
                      DRUGREF_LOG_LEVEL="debug") == "DEBUG drugref.postgres: debug1 line\n"


def test_an_explicit_level_beats_DRUGREF_LOG_LEVEL():
    assert _stderr_of("server_messages.configure_logging('warning')\n"
                      "server_messages.log.info('hidden')\n",
                      DRUGREF_LOG_LEVEL="debug") == ""
