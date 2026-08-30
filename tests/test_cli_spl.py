# tests/test_cli_spl.py
"""`drugref ingest spl`'s parser wiring -- the production path's own contract.

⇒ THIS MODULE HAD NO TESTS AT ALL. 75 lines, including the wiring that decides
whether a real run asserts the measured floors. The orchestrator is covered end
to end and the CLI in front of it was not, so "`--no-pair-floor` turns off BOTH
floors" and "the default turns both ON" were claims nothing checked -- which is
how `MEASURED_NOVEL_FLOOR` could be set to 1 with the whole suite staying green.
"""
import argparse

import pytest

from drugref import cli_spl
from drugref.ingest import spl_run

_MINIMAL = ["spl", "--openfda", "/tmp/o", "--dailymed", "/tmp/a.zip",
            "--release", "openfda-2026-08-22+dailymed-2026-08-21"]


@pytest.fixture
def parse():
    """The real subparser set `cli.py` builds, so this tests the shipped wiring."""
    parser = argparse.ArgumentParser(prog="drugref")
    cli_spl.add_parser(parser.add_subparsers(dest="source"))
    return parser.parse_args


@pytest.fixture
def captured_kwargs(monkeypatch):
    """What `handle_spl` would hand the orchestrator, without running it."""
    seen: dict = {}

    def fake_ingest(conn, **kwargs):
        seen.update(kwargs)
        return "fake-summary"

    monkeypatch.setattr(cli_spl.spl_run, "ingest_spl", fake_ingest)
    return seen


def test_the_measured_floors_are_ON_by_default(parse, captured_kwargs, capsys):
    """The point of putting them at the CLI rather than in the orchestrator's
    signature: a real run asserts them without anyone remembering to."""
    assert cli_spl.handle_spl(None, parse(_MINIMAL)) == 0
    assert captured_kwargs["pair_floor"] == spl_run.MEASURED_PAIR_FLOOR
    assert captured_kwargs["novel_floor"] == spl_run.MEASURED_NOVEL_FLOOR


def test_no_pair_floor_turns_off_BOTH_measured_floors(parse, captured_kwargs):
    """One flag gates two figures. A reader of `--help` would not guess that the
    flag named for the pair floor also drops the novel one, so it is asserted
    rather than left to the name."""
    cli_spl.handle_spl(None, parse([*_MINIMAL, "--no-pair-floor"]))
    assert captured_kwargs["pair_floor"] is None
    assert captured_kwargs["novel_floor"] is None


def test_progress_is_wired_so_a_long_scan_is_not_a_silent_terminal(
        parse, captured_kwargs):
    """The DailyMed pass takes tens of minutes; an operator watching a silent
    terminal cannot tell a slow scan from a hung one."""
    cli_spl.handle_spl(None, parse(_MINIMAL))
    assert callable(captured_kwargs["progress"])


def test_the_help_text_QUOTES_the_floor_constants_rather_than_retyping_them(
        parse, capsys):
    """A figure a user reads on screen, one edit away from the constant it
    describes. Both floors were typed out as literals in this help string, so a
    change to either constant would have left `--help` quoting the old number
    with nothing to catch it -- the quote budget's three-homes defect, smaller.
    """
    with pytest.raises(SystemExit):
        parse(["spl", "--help"])
    rendered = capsys.readouterr().out
    assert f"{spl_run.MEASURED_PAIR_FLOOR:,}-pair" in rendered
    assert f"{spl_run.MEASURED_NOVEL_FLOOR:,}-novel" in rendered


def test_the_release_tag_is_REQUIRED_because_two_corpora_have_two_stamps(parse):
    """openFDA publishes an `export_date` and DailyMed a `last-modified`; a tag
    drugref guessed would be a provenance claim nobody made."""
    with pytest.raises(SystemExit):
        parse(["spl", "--openfda", "/tmp/o", "--dailymed", "/tmp/a.zip"])
