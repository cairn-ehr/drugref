# tests/test_server_messages.py
"""The channel PostgreSQL's own complaints travel on (issue 174).

⇒ THE DEFECT THIS EXISTS FOR, MEASURED ON PG 18.1 BEFORE ANY OF IT WAS WRITTEN.
A role holding `USAGE` on the schema and `SELECT`/`INSERT` on a table is refused
by `ANALYZE` -- but not with an error. The server emits

    WARNING:  permission denied to analyze "t", skipping it

returns the `ANALYZE` command tag, leaves `pg_class.reltuples` at `-1`, and
**psycopg discards the warning**, because notices go nowhere unless a handler is
installed. `grep -rn add_notice_handler src/` returned nothing before this round.

This repo had already been bitten by that exact discard and wrote it down as a
COMMENT rather than as a mechanism -- `ingest/drugcentral_run.py`'s autocommit
paragraph: "the server answers a mis-placed SET TRANSACTION with a NOTICE, not an
error, and psycopg discards notices unless a handler is installed, so the ingest
reported success having silently lost its atomicity". A comment in one module is
not a channel; this module is.

DB-FREE ON PURPOSE, MOSTLY. The mapping and the formatting are pure functions, so
they are driven directly here. The two facts that are NOT ours to assume -- what a
real `psycopg.errors.Diagnostic` is shaped like, and that the handler is called at
all -- are asserted against a live server in tests/test_analyze_guard.py, because
a stub agreeing with the author's guess about psycopg's attribute names would
prove nothing.
"""
import logging
import types

import pytest

from drugref import server_messages


def diag(severity_nonlocalized="WARNING", *, severity=None, sqlstate="01000",
         primary="something happened", detail=None, hint=None):
    """A stand-in for psycopg's Diagnostic, carrying only the fields we read.

    `severity` defaults to `severity_nonlocalized` so the ordinary case (an
    English-locale server, where the two agree) needs no ceremony, and the one
    test that cares about the difference sets them apart explicitly.
    """
    return types.SimpleNamespace(
        severity=severity if severity is not None else severity_nonlocalized,
        severity_nonlocalized=severity_nonlocalized,
        sqlstate=sqlstate, message_primary=primary,
        message_detail=detail, message_hint=hint)


# --------------------------------------------------------------------------
# The severity mapping -- the one home for "how loud is a server message"
# --------------------------------------------------------------------------

@pytest.mark.parametrize("severity, level", [
    ("DEBUG", logging.DEBUG),
    ("LOG", logging.INFO),
    ("INFO", logging.INFO),
    ("NOTICE", logging.INFO),
    ("WARNING", logging.WARNING),
    ("ERROR", logging.ERROR),
    ("FATAL", logging.CRITICAL),
    ("PANIC", logging.CRITICAL),
])
def test_every_severity_the_protocol_defines_has_a_level(severity, level):
    """All eight, not just the two we happen to have seen.

    NOTICE lands at INFO rather than DEBUG because it is visible at the CLI's
    DEFAULT `--log-level info`, and measured it costs almost nothing: a FULL
    fresh migrate of all 53 db/*.sql files emits **35** notices in total, every
    one of them an "... does not exist, skipping" that an operator watching a
    migration genuinely wants to see. (PostgreSQL 18 no longer emits the
    per-table "will create implicit index" notice that would have made this
    hundreds of lines.)
    """
    assert server_messages.message_level(severity) == level


@pytest.mark.parametrize("severity", [None, "", "WARNUNG", "AVISO", "surprise"])
def test_a_severity_we_do_not_recognise_is_treated_as_a_WARNING(severity):
    """⇒ THE FAIL-LOUD DIRECTION, AND IT IS NOT HYPOTHETICAL.

    `Diagnostic.severity` is LOCALISED -- a server running under a German or
    Spanish `lc_messages` says WARNUNG or AVISO -- so an unrecognised string is a
    real shape, not a defensive fantasy. Mapping it to DEBUG would hide exactly
    the message this whole round exists to stop hiding, on exactly the servers
    whose operators can least afford it. The unknown case is therefore as loud as
    a warning, never quieter.
    """
    assert server_messages.message_level(severity) == logging.WARNING


def test_the_NON_LOCALISED_severity_is_what_the_mapping_reads():
    """A localised `severity` must not decide the level when the wire carries both.

    PostgreSQL has sent `severity_nonlocalized` (protocol field `V`) since 9.6 and
    it is always English. Reading the localised field instead would push every
    message on a translated server through the unknown-severity branch above --
    correct-by-accident for WARNING, and wrong for NOTICE, which would start
    logging at WARNING and drown the real ones.
    """
    message = server_messages.read_diagnostic(
        diag("NOTICE", severity="HINWEIS", primary="etwas ist passiert"))
    assert message.severity == "NOTICE"
    assert message.level == logging.INFO


def test_a_diagnostic_with_no_non_localised_severity_falls_back_to_the_localised_one():
    """The field is optional in the protocol, so its absence must not crash the
    handler that is meant to be making things MORE visible."""
    stub = diag("WARNING")
    stub.severity_nonlocalized = None
    assert server_messages.read_diagnostic(stub).severity == "WARNING"


# --------------------------------------------------------------------------
# The formatting -- everything the server said, and nothing invented
# --------------------------------------------------------------------------

def test_the_formatted_message_carries_the_detail_and_hint_the_server_supplied():
    """DETAIL and HINT are where PostgreSQL puts the actionable half.

    Dropping them would turn a message the server wrote to be diagnostic into a
    one-line symptom -- the same trade `db.constraint_definition` exists to
    refuse.
    """
    text = str(server_messages.read_diagnostic(diag(
        "WARNING", sqlstate="01000", primary="permission denied to analyze \"t\"",
        detail="the role owns no such table", hint="GRANT MAINTAIN ON t TO app")))
    assert "WARNING" in text
    assert "01000" in text
    assert 'permission denied to analyze "t"' in text
    assert "the role owns no such table" in text
    assert "GRANT MAINTAIN ON t TO app" in text


def test_a_message_with_no_sqlstate_does_not_print_the_word_None():
    """The field is optional in the protocol.

    `[WARNING None] ...` reads as a drugref bug rather than as an absent field,
    which is the wrong thing for a channel whose whole job is making the server's
    own words legible.
    """
    text = str(server_messages.read_diagnostic(diag("WARNING", sqlstate=None)))
    assert text.startswith("[WARNING] ")
    assert "None" not in text


def test_a_message_with_no_detail_or_hint_formats_without_empty_labels():
    """Most notices carry neither, so the common case must not read as truncated."""
    text = str(server_messages.read_diagnostic(diag("NOTICE", primary="skipping")))
    assert text.endswith("skipping")
    assert "DETAIL" not in text and "HINT" not in text


# --------------------------------------------------------------------------
# The filter the ANALYZE guard is built on
# --------------------------------------------------------------------------

def test_serious_messages_keeps_warnings_and_worse_and_drops_the_chatter():
    """⇒ WHY A FILTER AND NOT A `== "WARNING"` TEST AT THE CALL SITE.

    The guard in `analyze.py` refuses on anything the server considered at least
    a warning, so ERROR and FATAL -- which can reach a notice handler when they
    arrive on a connection that is not raising them as exceptions -- must not
    fall through a mapping written for the one severity we happened to measure.
    """
    messages = [server_messages.read_diagnostic(diag(s)) for s in
                ("DEBUG", "LOG", "INFO", "NOTICE", "WARNING", "ERROR", "PANIC")]
    kept = server_messages.serious_messages(messages)
    assert [m.severity for m in kept] == ["WARNING", "ERROR", "PANIC"]


def test_serious_messages_keeps_an_UNRECOGNISED_severity():
    """The fail-loud direction again, at the point where it decides a refusal.

    A message the mapping cannot classify is a WARNING for logging AND for the
    guard; a filter that quietly dropped it would let a translated server's
    skipped ANALYZE through the one check that would have caught it.
    """
    kept = server_messages.serious_messages([server_messages.read_diagnostic(
        diag("WARNUNG", primary="Zugriff verweigert"))])
    assert len(kept) == 1


# --------------------------------------------------------------------------
# The two handlers, and the failure mode a notice handler has by construction
# --------------------------------------------------------------------------

def test_log_server_message_publishes_one_record_at_the_messages_own_level(caplog):
    """The always-on handler, driven directly rather than only through a database.

    `db.connect` installs this on every connection, so it is the reason a server
    message is visible at all -- and until this test its only coverage was a
    DB-gated end-to-end one, which is the wrong place to learn that the logger
    name or the level mapping moved.
    """
    with caplog.at_level(logging.DEBUG, logger="drugref.postgres"):
        server_messages.log_server_message(diag("NOTICE", primary="chatter"))
    record, = [r for r in caplog.records if r.name == "drugref.postgres"]
    assert record.levelno == logging.INFO
    assert "chatter" in record.getMessage()


def test_a_diagnostic_the_reader_cannot_read_does_not_escape_the_handler(caplog):
    """⇒ "RAISES NOTHING, EVER" HAD TO BECOME TRUE RATHER THAN STAY ASSERTED.

    `read_diagnostic` is duck-typed on purpose, so it reads six attributes off
    whatever psycopg hands over; a psycopg release or a wrapper that hands over
    something else makes it raise. psycopg SWALLOWS what a handler raises, so the
    observable result of that was the channel going dark with a green suite --
    issue 174's own failure mode, in the module written to end it.
    """
    with caplog.at_level(logging.DEBUG, logger="drugref.postgres"):
        server_messages.log_server_message(object())        # no fields at all
    record, = [r for r in caplog.records if r.name == "drugref.postgres"]
    assert record.levelno == logging.WARNING, (
        "a message drugref could not read is a message it must not pretend to "
        "have classified")
    assert "could not read" in record.getMessage()


def test_a_message_the_collector_cannot_read_is_collected_as_a_SERIOUS_one():
    """The enforcing half of the same rule, and the direction that matters most.

    A collector whose failure mode is an EMPTY LIST is the shape this module
    exists to abolish: `analyze_tables` reads that list to decide whether the
    server complained, so an unreadable message would be indistinguishable from
    silence and the ANALYZE guard would pass.
    """
    conn = _RecordingConnection()
    with server_messages.collect(conn) as messages:
        handler, = conn.handlers
        handler(object())                       # what psycopg would hand over
    assert len(messages) == 1, "an unreadable message must not vanish from the list"
    assert "could not read" in messages[0].primary
    assert server_messages.serious_messages(messages) == tuple(messages), (
        "and it must reach the guard as grounds to refuse, because 'we could not "
        "read what the server said' is not evidence that it said nothing")


class _RecordingConnection:
    """The two methods `collect` uses, so its handler can be driven without a
    database and without psycopg's own swallowing in the way."""

    def __init__(self):
        self.handlers = []

    def add_notice_handler(self, handler):
        self.handlers.append(handler)

    def remove_notice_handler(self, handler):
        self.handlers.remove(handler)


# --------------------------------------------------------------------------
# What "serious" means when the severity itself is unreadable
# --------------------------------------------------------------------------

def test_an_unclassifiable_severity_on_a_SUCCESS_sqlstate_is_not_a_complaint():
    """⇒ THE FALSE-ABORT THE FAIL-LOUD RULE CREATED, closed with the field the
    server sends for exactly this.

    `Diagnostic.severity` is localised, so a German server's NOTICE reads
    `HINWEIS`; where `severity_nonlocalized` is absent -- optional in the
    protocol -- that reaches the mapping unrecognised and comes out as WARNING,
    which is right for LOGGING and wrong for a REFUSAL. Before this, routine
    chatter on a translated server aborted the ingest and blamed the role's
    permissions for it.

    SQLSTATE is not localised. Class `00` is `successful_completion`; PostgreSQL
    sends `00000` with a plain NOTICE and `01000` with a WARNING -- measured on
    PG 18.1 -- and the skipped ANALYZE this module was built for is `01000`. So
    an unclassifiable severity asks the CODE instead of guessing.
    """
    hinweis = server_messages.read_diagnostic(
        diag("HINWEIS", sqlstate="00000", primary="Tabelle existiert nicht"))
    assert hinweis.level == logging.WARNING, "still loud in the LOG"
    assert server_messages.serious_messages([hinweis]) == (), "but not a refusal"


def test_an_unclassifiable_severity_on_a_WARNING_sqlstate_is_still_a_complaint():
    """The direction that must not have been weakened: a translated WARNING keeps
    its `01000`, so the skipped ANALYZE is caught on a German server too."""
    warnung = server_messages.read_diagnostic(
        diag("WARNUNG", sqlstate="01000", primary="Zugriff verweigert"))
    assert len(server_messages.serious_messages([warnung])) == 1


def test_an_unclassifiable_severity_with_NO_sqlstate_stays_loud():
    """Neither field readable is not a reason to be quiet -- the module's whole
    stance, at the one point where being quiet costs a refusal."""
    nothing = server_messages.read_diagnostic(
        diag("???", sqlstate=None, primary="unclassifiable"))
    assert len(server_messages.serious_messages([nothing])) == 1


def test_the_severity_mapping_cannot_be_amended_at_runtime():
    """ONE HOME, and no importer may quietly move the number that gates a refusal.

    `SEVERITY_LEVEL["WARNING"] = logging.DEBUG` would disarm `analyze_tables`
    from any module that imported this one, silently and for the life of the
    process.
    """
    with pytest.raises(TypeError):
        server_messages.SEVERITY_LEVEL["WARNING"] = logging.DEBUG


# --------------------------------------------------------------------------
# The rendered line, whole rather than by containment
# --------------------------------------------------------------------------

def test_the_formatted_message_is_rendered_EXACTLY(caplog):
    """⇒ CONTAINMENT COULD NOT SEE THE LABELS SWAP PLACES.

    The previous assertions checked that five substrings were present, which
    stays true when DETAIL renders the hint and HINT renders the detail. This
    module's entire purpose is a line an operator can act on, so the line is
    pinned whole.
    """
    message = server_messages.read_diagnostic(diag(
        "WARNING", sqlstate="01000", primary='permission denied to analyze "t"',
        detail="the role owns no such table", hint="GRANT MAINTAIN ON t TO app"))
    assert str(message) == (
        '[WARNING 01000] permission denied to analyze "t" -- '
        'DETAIL: the role owns no such table -- '
        'HINT: GRANT MAINTAIN ON t TO app')


def test_a_message_with_only_a_DETAIL_does_not_grow_an_empty_HINT():
    """One half present is the common shape, and neither label may be unconditional."""
    message = server_messages.read_diagnostic(
        diag("NOTICE", sqlstate="00000", primary="p", detail="d"))
    assert str(message) == "[NOTICE 00000] p -- DETAIL: d"


def test_a_message_with_only_a_HINT_does_not_grow_an_empty_DETAIL():
    """The mirror of the above -- together they pin which label goes with which
    field, which is what containment assertions cannot do."""
    message = server_messages.read_diagnostic(
        diag("NOTICE", sqlstate="00000", primary="p", hint="h"))
    assert str(message) == "[NOTICE 00000] p -- HINT: h"


def test_a_diagnostic_with_no_primary_text_renders_without_the_word_None():
    """`message_primary` is typed optional by psycopg, and the fallback is the one
    line of `read_diagnostic` nothing drove."""
    message = server_messages.read_diagnostic(
        diag("NOTICE", sqlstate="00000", primary=None))
    assert str(message) == "[NOTICE 00000] "
