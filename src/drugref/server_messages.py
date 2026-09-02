# src/drugref/server_messages.py
"""What PostgreSQL says when it does not raise -- and where it goes (issue 174).

**THE PROBLEM THIS MODULE IS THE ANSWER TO.** A PostgreSQL backend has two ways
of telling a client something is wrong. One is an error, which psycopg turns into
an exception nobody can miss. The other is a *notice*: a NOTICE, WARNING or LOG
message that travels beside a perfectly successful command tag -- and which
psycopg delivers to registered handlers and to nothing else. Before this module
`grep -rn add_notice_handler src/ tests/` returned nothing, so every one of them
was discarded.

That is not a theoretical loss. This project has been bitten by it twice:

* `ingest/drugcentral_run.py` records the first -- "the server answers a
  mis-placed SET TRANSACTION with a NOTICE, not an error, and psycopg discards
  notices unless a handler is installed, so the ingest reported success having
  silently lost its atomicity". The fix was a comment and an `if
  conn.autocommit: raise` in one module.
* Issue 174 is the second, and it is worse: `ANALYZE` on a table the calling role
  does not own emits `WARNING: permission denied to analyze "t", skipping it`,
  **skips the table**, and returns the ANALYZE tag. Since issue 160 that skipped
  statement is worth 630 s of a single ingest, and nothing downstream can see it,
  because every check in the orchestrator counts rows and the row counts do not
  change.

⇒ **A COMMENT IN ONE MODULE IS NOT A CHANNEL.** This module is the channel, and
`db.connect` installs it, so an orchestrator gets it by existing rather than by
remembering. `analyze.py` is its first *enforcing* reader.

**PURE WHERE IT CAN BE.** The severity mapping and the formatting are functions
over plain values, tested without a database; only `read_diagnostic` touches a
psycopg object, and only `collect` touches a connection.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import psycopg

#: The logger every server message is published on.
#:
#: NAMED FOR THE OPERATOR, not for this file. `cli.main` formats records as
#: "%(levelname)s %(name)s: %(message)s", so a skipped ANALYZE reads
#: `WARNING drugref.postgres: permission denied to analyze "spl_label", skipping
#: it` -- which says, in one line, that the message came from the database rather
#: than from drugref's own reasoning about it. `logging.getLogger(__name__)`,
#: which every other module here uses, would have printed
#: `drugref.server_messages` and invited the opposite reading.
log = logging.getLogger("drugref.postgres")

#: HOW LOUD EACH OF THE PROTOCOL'S SEVERITIES IS, and the one home for that.
#:
#: All eight the wire protocol defines, not the two this project has happened to
#: see: a mapping written for the observed cases would push the rest through the
#: unknown branch, which is deliberately the LOUDEST one and therefore the wrong
#: place for DEBUG to land.
#:
#: NOTICE sits at INFO rather than DEBUG because INFO is the CLI's default
#: `--log-level`, and the cost was measured rather than guessed: a full fresh
#: migrate of all 53 db/*.sql files emits **35** notices in total, every one an
#: "... does not exist, skipping" that an operator watching a migration wants.
#: (PostgreSQL 18 no longer emits the per-table "will create implicit index"
#: notice that would have made the same run hundreds of lines and forced the
#: opposite choice.)
#: READ-ONLY, because this dict gates a refusal and not only a log line.
#: `SEVERITY_LEVEL["WARNING"] = logging.DEBUG` from any importing module would
#: disarm `analyze.analyze_tables` for the life of the process, silently. There is
#: no type checker in this project, so the proxy is the enforcement and `Final` is
#: the documentation.
SEVERITY_LEVEL: Final[MappingProxyType[str, int]] = MappingProxyType({
    "DEBUG": logging.DEBUG,
    "LOG": logging.INFO,
    "INFO": logging.INFO,
    "NOTICE": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "FATAL": logging.CRITICAL,
    "PANIC": logging.CRITICAL,
})

#: WHAT AN UNRECOGNISED SEVERITY COSTS, and why it is not DEBUG.
#:
#: `Diagnostic.severity` is LOCALISED -- a server running under a German or
#: Spanish `lc_messages` says WARNUNG or AVISO -- so an unknown string is a real
#: shape rather than a defensive fantasy. `read_diagnostic` prefers the
#: non-localised field precisely so this branch stays rare, but when it is taken
#: the message must be as loud as a warning and never quieter: the alternative
#: hides exactly the message this module exists to stop hiding, on exactly the
#: servers whose operators can least afford it.
UNKNOWN_SEVERITY_LEVEL: Final[int] = logging.WARNING


def message_level(severity: str | None) -> int:
    """PURE: the `logging` level a server message of this severity is published at.

    `None` and the empty string take the unknown branch with everything else --
    an absent severity tells us nothing, and "nothing" is not a reason to be
    quiet.
    """
    return SEVERITY_LEVEL.get(severity or "", UNKNOWN_SEVERITY_LEVEL)


@dataclass(frozen=True, kw_only=True)
class ServerMessage:
    """One thing the server said without raising.

    A NAMED TYPE rather than the `Diagnostic` psycopg hands over, for two
    reasons. It is frozen and comparable, so a test can assert on one; and it
    holds only the five fields this project reads, so the pure functions above and
    below never depend on psycopg being importable or on a live result being
    around to read a field off. `read_diagnostic` is the single seam between the
    two.
    """

    #: The NON-LOCALISED severity where the server sent one -- see `read_diagnostic`.
    severity: str
    #: The five-character SQLSTATE. `01000` is a plain warning; the skipped
    #: ANALYZE of issue 174 arrives with exactly that.
    sqlstate: str | None
    #: The primary message text, in the server's own words.
    primary: str
    #: DETAIL and HINT, where PostgreSQL puts the actionable half. Usually absent.
    detail: str | None = None
    hint: str | None = None

    @property
    def level(self) -> int:
        """The `logging` level this message is published at."""
        return message_level(self.severity)

    def __str__(self) -> str:
        """Everything the server said, and nothing invented.

        DETAIL and HINT are appended only when present, so the common case (a
        bare notice, which is most of them) does not read as truncated -- and
        when they ARE present they are never dropped, because they are the half
        that tells an operator what to do. `db.constraint_definition` refuses the
        same trade for the same reason.
        """
        # The SQLSTATE is omitted rather than printed as "None" when the server
        # sent none: a five-character code is worth quoting, and the word None in
        # its place reads as a drugref bug rather than as an absent field.
        code = f" {self.sqlstate}" if self.sqlstate else ""
        parts = [f"[{self.severity}{code}] {self.primary}"]
        if self.detail:
            parts.append(f"DETAIL: {self.detail}")
        if self.hint:
            parts.append(f"HINT: {self.hint}")
        return " -- ".join(parts)


def read_diagnostic(diag) -> ServerMessage:
    """The one seam between psycopg's `Diagnostic` and this module's own type.

    ⇒ **`severity_nonlocalized` FIRST, AND THAT IS THE LOAD-BEARING LINE.**
    `Diagnostic.severity` is translated according to the server's `lc_messages`;
    `severity_nonlocalized` (protocol field `V`, sent by every server since 9.6)
    is always English. Reading the localised field would send every message on a
    translated server through `UNKNOWN_SEVERITY_LEVEL` -- right by accident for
    WARNING, and wrong for NOTICE, which would start logging at WARNING and drown
    the warnings this module exists to surface.

    The fallback is not decoration: the field is optional in the protocol, and a
    handler whose job is making things MORE visible must not be the thing that
    raises. `severity` is itself typed optional by psycopg, so the last resort is
    the empty string, which `message_level` treats as unknown.

    Deliberately DUCK-TYPED (no `Diagnostic` annotation): the attributes named
    here are the whole contract, which lets the pure tests drive it with a
    stand-in -- and tests/test_analyze_guard.py drives it with a REAL diagnostic
    off a real server, because a stand-in built from a guess about psycopg's
    attribute names would agree with that guess forever.
    """
    return ServerMessage(
        severity=diag.severity_nonlocalized or diag.severity or "",
        sqlstate=diag.sqlstate,
        primary=diag.message_primary or "",
        detail=diag.message_detail,
        hint=diag.message_hint)


def serious_messages(
        messages: Sequence[ServerMessage]) -> tuple[ServerMessage, ...]:
    """PURE: those of `messages` the server considered at least a warning.

    THE FILTER A REFUSAL IS BUILT ON, so it is written as "at least a warning"
    rather than as `severity == "WARNING"`. ERROR, FATAL and PANIC can reach a
    notice handler on a connection that is not raising them as exceptions, and a
    guard that tested for the one severity it had measured would let the three
    worse ones through.

    ⇒ **AN UNRECOGNISED SEVERITY ASKS THE SQLSTATE RATHER THAN THE LEVEL**, and
    that is not the same rule as the logging one. `UNKNOWN_SEVERITY_LEVEL` makes an
    unclassifiable message as loud as a warning, which is right for a LOG LINE --
    loud is free there -- and wrong for a REFUSAL, which costs a run and
    misdiagnoses it. A German server whose `severity_nonlocalized` is absent sends
    routine notices as `HINWEIS`; under a level-only filter every one of them
    aborted an ingest and blamed the ingest role's permissions.

    SQLSTATE is NOT localised. Class `00` is `successful_completion` -- measured on
    PG 18.1, a plain NOTICE arrives as `00000` and a WARNING as `01000`, and the
    skipped ANALYZE of issue 174 is `01000` -- so the unclassifiable case has a
    non-localised field to consult and does not have to guess.

    A message with NEITHER a recognised severity NOR a usable SQLSTATE is still
    kept: two unreadable fields are not a reason to be quiet, at the one point
    where being quiet means a skipped ANALYZE passes.
    """
    return tuple(m for m in messages if _is_serious(m))


def _is_serious(message: ServerMessage) -> bool:
    """PURE: whether one message is grounds for a caller to refuse."""
    if message.severity in SEVERITY_LEVEL:
        return message.level >= logging.WARNING
    if message.sqlstate and len(message.sqlstate) >= 2:
        return message.sqlstate[:2] != "00"
    return True


#: The severity a message drugref could not parse is reported under.
#:
#: Deliberately NOT one of the protocol's eight, so it takes the unknown branch of
#: `message_level` and the fail-loud branch of `_is_serious`: an unreadable message
#: is as loud as a warning in the log AND grounds for a refusal, which is the only
#: safe reading of "the server said something and we do not know what".
UNREADABLE_SEVERITY: Final[str] = "UNREADABLE"


def read_diagnostic_safely(diag) -> ServerMessage:
    """`read_diagnostic`, but it cannot raise -- WHICH IS THE WHOLE POINT.

    ⇒ **A NOTICE HANDLER THAT RAISES IS A NOTICE HANDLER THAT VANISHED.** psycopg
    calls handlers inside its result processing and SWALLOWS whatever they raise
    (`_connection_base.py`: `except Exception: logger.exception(...)`), so an
    exception here does not surface anywhere drugref looks. `read_diagnostic` is
    duck-typed on purpose -- six attributes read off whatever psycopg hands over --
    so the day a psycopg release, a pooler or a wrapper hands over something else,
    the unguarded version made the channel go dark with a green suite. That is
    issue 174's own failure mode, inside the module written to end it.

    The failure is reported as a MESSAGE rather than lost, so a collector's list
    grows a loud entry instead of staying empty: `analyze_tables` reads that list
    to decide whether the server complained, and "empty" must never be able to mean
    "something arrived and we could not read it".
    """
    try:
        return read_diagnostic(diag)
    except Exception as exc:                                  # noqa: BLE001
        return ServerMessage(
            severity=UNREADABLE_SEVERITY, sqlstate=None,
            primary=f"drugref could not read a message the server sent: {exc!r}")


def log_server_message(diag) -> None:
    """The handler `db.connect` installs: publish one server message, at its level.

    RAISES NOTHING, ever -- now enforced by `read_diagnostic_safely` rather than
    asserted about a duck-typed read. psycopg swallows whatever a handler raises,
    so a handler is a REPORTING surface and can never be an ENFORCING one;
    enforcement belongs to the caller that collected the messages -- see `collect`
    and `analyze.analyze_tables`.
    """
    message = read_diagnostic_safely(diag)
    log.log(message.level, "%s", message)


@contextlib.contextmanager
def collect(conn: psycopg.Connection) -> Iterator[list[ServerMessage]]:
    """Yield a list that receives every server message raised inside the block.

    ⇒ **IT INSTALLS ITS OWN HANDLER RATHER THAN READING `db.connect`'s.** A guard
    that depended on the connection having been opened by `db.connect` would fire
    on the CLI path and nowhere else -- not in this suite, whose `conn` fixture
    calls `psycopg.connect` directly, and not for a programmatic caller with its
    own connection. That is the "gate that exists and never fires" shape of
    issues 74, 66 and 76, in the guard meant to close issue 174.

    ADDITIVE, so the always-on logging handler keeps running: a collected message
    is still published. Removal is in a `finally` because the failing path is the
    one that raises, and a handler left behind would append to a list nobody reads
    for the life of the connection -- one more list per ANALYZE.

    The list is safe to read as soon as the statement returns: psycopg dispatches
    notices while processing that statement's result, which
    tests/test_analyze_guard.py asserts rather than assumes. THE ONE SHAPE THAT
    BREAKS THAT is an outer `conn.pipeline()` spanning the block -- `execute` then
    queues rather than fetches, and the sync happens after the `finally` below has
    removed the handler, so the list comes back empty. Nothing in this project uses
    pipeline mode (`grep -rn "pipeline()" src/ tools/` is empty); a caller that
    starts must collect INSIDE its own pipeline, not around one.
    """
    messages: list[ServerMessage] = []

    def handler(diag) -> None:
        # `read_diagnostic_safely`, for the reason its own docstring gives: this
        # list is EVIDENCE, and a reader that raised would leave it empty --
        # indistinguishable from a server that said nothing.
        messages.append(read_diagnostic_safely(diag))

    conn.add_notice_handler(handler)
    try:
        yield messages
    finally:
        conn.remove_notice_handler(handler)
