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


def test_resolve_inputs_finds_each_file_by_its_glob(tmp_path):
    (tmp_path / "MEDRT").mkdir()
    (tmp_path / "MEDRT" / "Core_MEDRT_2026.07.06_XML.xml").write_text("x")

    step = next(s for s in cli.STEPS if s.name == "medrt")
    assert cli.resolve_inputs(tmp_path, step) == {
        "medrt": tmp_path / "MEDRT" / "Core_MEDRT_2026.07.06_XML.xml"}


def test_resolve_inputs_refuses_a_glob_that_matches_nothing(tmp_path):
    """A convention that silently matches nothing is worse than no convention: the
    chain would report success having ingested a feed it never read."""
    step = next(s for s in cli.STEPS if s.name == "medrt")
    with pytest.raises(cli.InputResolutionError) as exc:
        cli.resolve_inputs(tmp_path, step)
    assert "MEDRT/Core_MEDRT_*_XML.xml" in str(exc.value)
    assert str(tmp_path) in str(exc.value)


def test_resolve_inputs_refuses_an_ambiguous_glob(tmp_path):
    """Two releases in one directory is the normal way this goes wrong, and picking
    one would record the wrong bytes as provenance."""
    (tmp_path / "MEDRT").mkdir()
    for release in ("2026.05.04", "2026.07.06"):
        (tmp_path / "MEDRT" / f"Core_MEDRT_{release}_XML.xml").write_text("x")

    step = next(s for s in cli.STEPS if s.name == "medrt")
    with pytest.raises(cli.InputResolutionError) as exc:
        cli.resolve_inputs(tmp_path, step)
    assert "2 files" in str(exc.value)


def test_a_source_joins_the_chain_only_if_its_release_is_given():
    """No default set and no skip-list: supplying a release IS the opt-in, so a run
    can never quietly include a feed whose release tag nobody stated."""
    args = cli.build_parser().parse_args(
        ["ingest", "chain", "--downloads", "d",
         "--unii-release", "26Feb2026", "--medrt-release", "2026.07.06"])
    assert [(s.name, r) for s, r in cli.selected_steps(args)] == [
        ("unii", "26Feb2026"), ("medrt", "2026.07.06")]


def test_the_chain_runs_selected_steps_in_dependency_order():
    """Flags are given in any order; the chain is not."""
    args = cli.build_parser().parse_args(
        ["ingest", "chain", "--downloads", "d",
         "--pbs-release", "2026-07", "--unii-release", "26Feb2026"])
    assert [s.name for s, _ in cli.selected_steps(args)] == ["unii", "pbs"]


def test_the_chain_needs_at_least_one_release():
    args = cli.build_parser().parse_args(["ingest", "chain", "--downloads", "d"])
    assert cli.selected_steps(args) == ()


def test_the_chain_resolves_every_steps_inputs_before_running_any(tmp_path, monkeypatch):
    """`_handle_chain` builds `plan` as a LIST comprehension, not a generator, so
    every step's glob is checked before the first runner fires. That property is
    invisible to a test that only checks the exception propagates -- a generator
    would raise the same InputResolutionError, just one runner call later than it
    should. The property only shows up in whether the EARLY step's runner ran, so
    that is what this test pins.

    Uses two throwaway IngestSteps (not the real STEPS) so the fake runner can be
    trusted to run only from this code path, and so failure needs no real database:
    _handle_chain never touches `conn` itself, only forwards it to `runner`.
    """
    calls = []

    def _early_runner(conn, paths, release):
        calls.append("early")
        return "early ok"

    early = cli.IngestStep("early", (("early", "early.txt"),), _early_runner)
    late = cli.IngestStep("late", (("late", "late.txt"),),
                          lambda conn, paths, release: "late ok")
    monkeypatch.setattr(cli, "STEPS", (early, late))

    (tmp_path / "early.txt").write_text("x")
    # late.txt is deliberately absent, so resolving "late"'s input is what fails.

    args = cli.build_parser().parse_args(
        ["ingest", "chain", "--downloads", str(tmp_path),
         "--early-release", "r1", "--late-release", "r2"])

    with pytest.raises(cli.InputResolutionError):
        cli._handle_chain(object(), args)

    # The assertion that carries the property: on a generator-based `plan`, "early"
    # would already have run by the time "late" failed to resolve, and this would
    # read calls == ["early"] instead.
    assert calls == []
