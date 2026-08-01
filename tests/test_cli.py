# tests/test_cli.py
"""The CLI: the first supported way to run an ingest outside a test (#16).

The parser and the step table are PURE -- no database, no filesystem -- so most of
this module runs anywhere. Only the end-to-end test is DB-gated.
"""
import pathlib

import pytest

from drugref import cli

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"


def test_every_orchestrator_has_a_subcommand():
    """A step table restated independently -- the shape test_source_clear_contract
    uses. Driving this off cli.STEPS would pass whatever cli.STEPS said; the point is
    that an orchestrator added without a subcommand fails here."""
    assert tuple(s.name for s in cli.STEPS) == (
        "unii", "chebi", "medrt", "mesh", "mesh-relations", "pbs")


def test_the_step_order_is_the_dependency_order():
    """UNII first because every other feed joins to moieties it registers; MED-RT
    before mesh-relations because the MeSH-keyed run reads classes medrt_run writes.
    Order is a property of the data, so it is a constant rather than an argument a
    caller could get wrong."""
    names = [s.name for s in cli.STEPS]
    assert names.index("unii") == 0
    assert names.index("medrt") < names.index("mesh-relations")


def test_ingest_subcommand_requires_a_release():
    """Provenance is stated, never guessed: a run with no upstream_release is a run
    whose coverage numbers cannot be compared to anything."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ingest", "unii", "--unii", str(FIX)])


def test_ingest_subcommand_parses_its_paths():
    args = cli.build_parser().parse_args(
        ["ingest", "mesh", "--release", "2026", "--pa", "a.xml",
         "--desc", "b.gz", "--supp", "c.gz"])
    assert args.pa == pathlib.Path("a.xml")
    assert args.supp == pathlib.Path("c.gz")


def test_status_and_migrate_need_no_paths():
    assert cli.build_parser().parse_args(["status"]).handler is not None
    assert cli.build_parser().parse_args(["migrate"]).handler is not None


def test_main_reports_a_missing_dsn_without_a_traceback(capsys, monkeypatch):
    """An operator running this for the first time gets an actionable line, not a
    stack trace out of psycopg."""
    monkeypatch.delenv("DRUGREF_DSN", raising=False)
    assert cli.main(["status"]) == 2
    assert "DRUGREF_DSN" in capsys.readouterr().err


def test_ingest_unii_end_to_end(_migrated, monkeypatch, capsys):
    """One real ingest through the CLI, against the committed fixture."""
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    import psycopg
    with psycopg.connect(_migrated) as c:
        c.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                  "drugref.moiety_admission, drugref.open_question, "
                  "drugref.ingest_run RESTART IDENTITY CASCADE")
        c.commit()

    assert cli.main(["ingest", "unii", "--release", "2026-07", "--unii", str(FIX)]) == 0

    with psycopg.connect(_migrated) as c:
        assert c.execute(
            "SELECT source, writer, upstream_release FROM drugref.loaded_release"
        ).fetchall() == [("UNII", "unii_run", "2026-07")]
