# tests/test_cli.py
"""The CLI: the first supported way to run an ingest outside a test (#16).

The parser and the step table are PURE -- no database, no filesystem -- so most of
this module runs anywhere. Only the end-to-end test is DB-gated.
"""
import ast
import pathlib

import pytest

from drugref import cli, cli_chain

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"


def test_every_orchestrator_has_a_subcommand():
    """A step table restated independently -- the shape test_source_clear_contract
    uses. Driving this off cli.STEPS would pass whatever cli.STEPS said; the point is
    that an orchestrator added without a subcommand fails here."""
    assert tuple(s.name for s in cli.STEPS) == (
        "unii", "chebi", "medrt", "mesh", "mesh-relations", "pbs", "gsrs")


def test_unii_runs_before_every_feed_that_joins_to_what_it_registers():
    """The ONE ordering constraint the data actually imposes: UNII first, because every
    other feed resolves its subjects through identity_claim (or the INN display names)
    that the UNII step populates.

    Deliberately not asserting medrt < mesh-relations. That pair IS fixed -- the tuple
    above pins it -- but as a convention, not a dependency: the MeSH-keyed run reads no
    table medrt_run writes, and the one they share is scoped per (source, reason) so
    neither order changes the answer. A test that asserted it as a dependency would
    keep a false claim alive by passing."""
    names = [s.name for s in cli.STEPS]
    assert names.index("unii") == 0


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


def test_status_says_none_for_both_halves_of_a_fresh_database(capsys):
    """SYMMETRY BETWEEN THE TWO BLOCKS. Unfinished runs already printed "none" while
    loaded releases printed a bare header, so `drugref status` on a just-migrated
    database looked like output that got cut off rather than an answer. Nothing loaded
    is the answer, and an operator checking "is this current?" must be able to tell
    the two apart.

    Driven by a stub rather than a real empty database on purpose: loaded_release
    holds COMMITTED rows that the conn fixture's rollback cannot remove, so a
    DB-gated version of this test would pass or fail on test order.
    """
    class _EmptyConn:
        def execute(self, *args, **kwargs):
            return self

        def fetchall(self):
            return []

    assert cli._handle_status(_EmptyConn(), None) == 0
    out = capsys.readouterr().out
    assert "loaded releases: none" in out
    assert "unfinished runs: none" in out


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
    with pytest.raises(cli_chain.InputResolutionError) as exc:
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
    with pytest.raises(cli_chain.InputResolutionError) as exc:
        cli.resolve_inputs(tmp_path, step)
    assert "2 files" in str(exc.value)


def test_gsrs_is_a_declared_step():
    step = next(s for s in cli.STEPS if s.name == "gsrs")
    assert step.inputs == (("dump", "GSRS/dump-public-*.gsrs"),)
    # No secondary inputs: this step reads and DATES exactly one file.
    assert step.secondary == ()


def test_the_gsrs_glob_matches_the_real_release_name(tmp_path):
    """The glob is pinned because #60's lesson was that a wrong one ships silently.
    The release file is dump-public-YYYY-MM-DD.gsrs."""
    downloads = tmp_path / "downloads"
    (downloads / "GSRS").mkdir(parents=True)
    (downloads / "GSRS" / "dump-public-2026-02-26.gsrs").write_text("")
    step = next(s for s in cli.STEPS if s.name == "gsrs")
    resolved = cli.resolve_inputs(downloads, step)
    assert resolved["dump"].name == "dump-public-2026-02-26.gsrs"


def test_two_gsrs_releases_in_one_directory_are_refused(tmp_path):
    """Silently taking either would record the wrong bytes as this run's provenance."""
    downloads = tmp_path / "downloads"
    (downloads / "GSRS").mkdir(parents=True)
    (downloads / "GSRS" / "dump-public-2026-02-26.gsrs").write_text("")
    (downloads / "GSRS" / "dump-public-2026-05-01.gsrs").write_text("")
    step = next(s for s in cli.STEPS if s.name == "gsrs")
    with pytest.raises(cli_chain.InputResolutionError):
        cli.resolve_inputs(downloads, step)


def test_a_source_joins_the_chain_only_if_its_release_is_given():
    """No default set and no skip-list: supplying a release IS the opt-in, so a run
    can never quietly include a feed whose release tag nobody stated."""
    args = cli.build_parser().parse_args(
        ["ingest", "chain", "--downloads", "d",
         "--unii-release", "26Feb2026", "--medrt-release", "2026.07.06"])
    assert [(s.name, r) for s, r in cli.selected_steps(args, cli.STEPS)] == [
        ("unii", "26Feb2026"), ("medrt", "2026.07.06")]


def test_the_chain_runs_selected_steps_in_dependency_order():
    """Flags are given in any order; the chain is not."""
    args = cli.build_parser().parse_args(
        ["ingest", "chain", "--downloads", "d",
         "--pbs-release", "2026-07", "--unii-release", "26Feb2026"])
    assert [s.name for s, _ in cli.selected_steps(args, cli.STEPS)] == ["unii", "pbs"]


def test_the_chain_needs_at_least_one_release():
    args = cli.build_parser().parse_args(["ingest", "chain", "--downloads", "d"])
    assert cli.selected_steps(args, cli.STEPS) == ()


def test_an_empty_release_tag_is_an_error_not_a_silent_skip():
    """PRESENCE, NOT TRUTHINESS, selects a step. `--medrt-release ""` is a flag the
    operator DID pass; a truthiness test dropped the step it asked for and the chain
    reported success having never touched that feed -- the exact shape the spec's trap
    list forbids ("a convention that silently matches nothing is worse than none").
    Whitespace counts as empty: a tag is what lands in ingest_run."""
    for tag in ("", "   "):
        args = cli.build_parser().parse_args(
            ["ingest", "chain", "--downloads", "d", "--medrt-release", tag])
        with pytest.raises(cli_chain.ReleaseError, match="empty tag"):
            cli.selected_steps(args, cli.STEPS)


def test_one_file_cannot_be_recorded_as_two_releases():
    """medrt and mesh-relations resolve the SAME MED-RT XML but state their tags
    independently, so this pair writes two releases into ingest_run from identical
    bytes. ingest_run is history -- one of them is false and nothing can take it
    back -- and it makes db/025's staleness signal report a difference that does not
    exist. Checked on the RESOLVED PATHS, because the flags look independent."""
    medrt = next(s for s in cli.STEPS if s.name == "medrt")
    mesh_rel = next(s for s in cli.STEPS if s.name == "mesh-relations")
    xml = pathlib.Path("downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml")

    with pytest.raises(cli_chain.ReleaseError, match="cannot be two releases"):
        cli.check_release_agreement([
            (medrt, "2026.07.06", {"medrt": xml}),
            (mesh_rel, "2026.05.04", {"medrt": xml, "desc": pathlib.Path("d.gz"),
                                      "supp": pathlib.Path("s.gz")})])


def test_steps_sharing_a_file_are_fine_when_they_agree():
    """The overlap itself is normal and must stay cheap -- the round's own measurement
    ran medrt and mesh-relations together off one XML."""
    medrt = next(s for s in cli.STEPS if s.name == "medrt")
    mesh_rel = next(s for s in cli.STEPS if s.name == "mesh-relations")
    xml = pathlib.Path("downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml")

    cli.check_release_agreement([
        (medrt, "2026.07.06", {"medrt": xml}),
        (mesh_rel, "2026.07.06", {"medrt": xml})])


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

    with pytest.raises(cli_chain.InputResolutionError):
        cli._handle_chain(object(), args)

    # The assertion that carries the property: on a generator-based `plan`, "early"
    # would already have run by the time "late" failed to resolve, and this would
    # read calls == ["early"] instead.
    assert calls == []


def _plan(*entries):
    """The resolved shape check_release_agreement takes: (step, release, paths).

    Built by hand rather than through resolve_inputs so these stay pure -- the
    question is about tags and paths, not about what is on disk.
    """
    by_name = {s.name: s for s in cli.STEPS}
    return [(by_name[name], release, paths) for name, release, paths in entries]


MEDRT_XML = pathlib.Path("downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml")
DESC = pathlib.Path("downloads/mesh/desc2026.gz")
SUPP = pathlib.Path("downloads/mesh/supp2026.gz")
PA = pathlib.Path("downloads/mesh/pa2026.xml")
UNII = pathlib.Path("downloads/UNII_Records_26Feb2026.txt")


def test_the_documented_four_source_invocation_passes_pre_flight():
    """#60: the command HANDOVER, the ingest-operability spec and #35's own plan all
    document could not run on merged main.

    mesh-relations reads desc/supp but records MED-RT's tag, because mesh_rel_run
    writes ONE ingest_run row under source='MED-RT'. Reading that as "one file claimed
    to be two releases" was the defect: the file is dated once, by mesh, and merely
    READ by mesh-relations.
    """
    cli.check_release_agreement(_plan(
        ("unii", "26Feb2026", {"unii": UNII}),
        ("medrt", "2026.07.06", {"medrt": MEDRT_XML}),
        ("mesh", "2026", {"pa": PA, "desc": DESC, "supp": SUPP}),
        ("mesh-relations", "2026.07.06",
         {"medrt": MEDRT_XML, "desc": DESC, "supp": SUPP})))


def test_two_steps_still_cannot_date_the_same_primary_file_differently():
    """The case check_release_agreement's docstring calls uncorrectable, and the one
    the secondary exemption must NOT weaken.

    The MED-RT xml is PRIMARY for both medrt and mesh-relations -- both record a tag
    describing it -- so two tags for identical bytes is still a pre-flight error.
    db/025 added `writer` precisely so an operator could see one half of MED-RT running
    a release behind the other; letting the halves disagree on purpose makes that
    signal report staleness that does not exist.
    """
    with pytest.raises(cli_chain.ReleaseError) as exc:
        cli.check_release_agreement(_plan(
            ("medrt", "2026.07.06", {"medrt": MEDRT_XML}),
            ("mesh-relations", "2026.05.04",
             {"medrt": MEDRT_XML, "desc": DESC, "supp": SUPP})))
    assert "2026.07.06" in str(exc.value) and "2026.05.04" in str(exc.value)


def test_a_secondary_input_may_disagree_with_the_step_that_dates_it():
    """ASSERTED AS A PASS, deliberately, not left as the absence of a failure.

    This is the behaviour change #60 buys, and a guard that quietly stops guarding is
    worse than one that never existed -- so the exemption gets a test that fails if it
    is ever narrowed back, rather than only tests that fail if it is widened.

    mesh dates desc/supp as '2026'; mesh-relations reads the same bytes while recording
    MED-RT's '2026.07.06'. Both statements are true about different authorities.
    """
    cli.check_release_agreement(_plan(
        ("mesh", "2026", {"pa": PA, "desc": DESC, "supp": SUPP}),
        ("mesh-relations", "2026.07.06",
         {"medrt": MEDRT_XML, "desc": DESC, "supp": SUPP})))


def test_secondary_must_name_an_input_the_step_declares():
    """A typo would silently exempt nothing and leave the chain refusing -- the third
    place this project's rule bites: a convention that silently matches nothing is
    worse than none (resolve_inputs' globs and selected_steps' empty tag are the other
    two). Raised at construction, where STEPS is built, so it fires at import.
    """
    with pytest.raises(ValueError) as exc:
        cli.IngestStep("broken", (("desc", "mesh/desc*.gz"),), lambda *a: None,
                       secondary=("dsc",))
    assert "dsc" in str(exc.value)


def test_mesh_relations_is_the_only_step_with_a_secondary_input():
    """Restated independently, the shape test_every_orchestrator_has_a_subcommand uses:
    driving this off cli.STEPS would pass whatever cli.STEPS said. A step that gains an
    exemption without anyone deciding to grant it fails here."""
    assert {s.name: s.secondary for s in cli.STEPS if s.secondary} == {
        "mesh-relations": ("desc", "supp")}


def _drugref_imports_in(source: str) -> set[str]:
    """Every import in `source` that reaches into the `drugref` package, reported as
    the construct itself rather than a boolean -- so a failing assertion says WHICH
    import tripped it, not just that one did.

    RELATIVE IMPORTS COUNT, and that is the fix this helper exists to pin (found in
    review of the first version of this guard). `cli_chain.py` lives INSIDE the
    `drugref` package, so `from . import db` or `from .db import get_connection`
    reaches exactly as far into `drugref` as `from drugref import db` does --
    `ast.ImportFrom.level` is the only signal that tells the two apart. A scan that
    reads `node.module` alone treats a relative import as importing nothing: `from .
    import db` is `level=1, module=None`, and `node.module or ""` yields `""`, which
    matches neither `"drugref"` nor a `"drugref."` prefix. That is the SAME defect one
    construct over as the line-split string literal this project already learned to
    distrust source text for (test_curation_orphans.py's
    test_the_cli_embeds_no_sql_against_a_curated_table) -- a guard shaped like the
    parse, not like the source text, has to use every field the parse gives it.

    Extracted from the test body so the relative-import branch is checkable directly,
    over a source string built for the purpose, without needing `cli_chain.py` itself
    to carry a violation to prove the guard would catch one.
    """
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names
                         if alias.name == "drugref" or alias.name.startswith("drugref."))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # A relative import inside a module that lives IN the `drugref`
                # package reaches into `drugref` by construction -- no `node.module`
                # value could make it not do so. Reported with its leading dots so the
                # failure message shows a relative import, not a bare module name.
                found.add("." * node.level + (node.module or ""))
            elif node.module == "drugref" or (node.module or "").startswith("drugref."):
                found.add(node.module)
    return found


def test_cli_chain_imports_nothing_from_drugref():
    """THE PROPERTY THAT MAKES THE IMPORT CYCLE IMPOSSIBLE, and the reason this split
    runs in this direction rather than the other.

    The first attempt at this task moved the HANDLERS out instead, and could not work.
    `STEPS` eagerly references the `_run_*` wrappers, so cli must import whatever module
    holds them; `_handle_chain` calls selected_steps/resolve_inputs/
    check_release_agreement, so that module must import cli. Mutual -- and Python raises
    `AttributeError: partially initialized module ... has no attribute 'run_unii'` as
    soon as anything imports the handler module first, which the signing tests would.

    Extracting the pure layer has no such hazard because it depends on nothing in
    drugref. This test is what keeps that true: cli_chain must never grow a drugref
    import, and if it ever needs one, the layering is wrong rather than the test.

    Absolute AND relative both count -- `_drugref_imports_in` is what makes that so,
    and `test_the_import_guard_catches_a_relative_import` pins the relative half
    directly, since `cli_chain.py` carrying a violation is not how this test is meant
    to prove that branch works.
    """
    import inspect
    from drugref import cli_chain

    from_drugref = _drugref_imports_in(inspect.getsource(cli_chain))
    assert from_drugref == set(), (
        f"cli_chain imports {sorted(from_drugref)} from drugref. It must import "
        "nothing from drugref -- that is what makes the cycle structurally impossible "
        "rather than merely absent today.")


def test_the_import_guard_catches_a_relative_import():
    """KILLS THE REMOVAL of the `node.level > 0` branch in `_drugref_imports_in`.

    The first version of this guard read `node.module or ""` and nothing else, which
    treats `from . import db` (level=1, module=None) as importing the empty string --
    matching neither `"drugref"` nor a `"drugref."` prefix, so a relative import into
    the very package `cli_chain.py` lives in slipped past undetected. Driven over a
    source STRING rather than `cli_chain.py` itself: the property under test is
    "the guard would catch this if it ever happened," and making that true requires
    the violation to exist somewhere, not for `cli_chain.py` to be made to carry one.

    Both shapes of relative import are asserted, because they parse to different
    `ast.ImportFrom` fields: `from . import db` carries the name on the alias
    (`module=None`), `from .db import get_connection` carries it on `module` itself
    (`module="db"`) -- a fix that only handled one would still be a fix a reviewer
    could not tell from a coincidence.
    """
    assert _drugref_imports_in("from . import db\n") == {"."}
    assert _drugref_imports_in("from .db import get_connection\n") == {".db"}


def test_cli_py_is_under_the_size_cap():
    """CLAUDE.md rule 4, measured rather than assumed. 500 is the stated cap."""
    import pathlib
    from drugref import cli
    lines = len(pathlib.Path(cli.__file__).read_text().splitlines())
    assert lines <= 500, f"cli.py is {lines} lines, over the ~500 cap"


def test_main_does_not_swallow_a_check_violation_from_an_ingest(monkeypatch, capsys):
    """A CHECK a `policy` argument trips is an operator's typo, and cli_policy._write
    renders it as one line. THE SAME EXCEPTION FROM AN INGEST IS A DEFECT IN DRUGREF --
    a parser feeding a value db/006 or db/014 forbids -- and must keep its traceback,
    which names the writer that produced the bad value.

    This is a regression test in the strict sense: the catch briefly lived on main's
    `try`, which wraps every handler, so an ingest bug printed one context-free line and
    exited 2. Exit 2 is this CLI's OPERATOR-ERROR code, so that did not merely lose the
    traceback -- it reported a drugref bug as the operator's mistake.

    Driven through a stub connection and a stub handler: the assertion is about which
    exceptions main lets past, and needs neither a database nor a real ingest.
    """
    import psycopg

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _explode(conn, args):
        raise psycopg.errors.CheckViolation("substance_class_concept_type")

    monkeypatch.setattr(cli.db, "connect", lambda dsn: _Conn())
    # Patched on the MODULE, before main builds the parser: build_parser resolves
    # `_handle_ingest` as a global when it runs `set_defaults`, which is inside main.
    monkeypatch.setattr(cli, "_handle_ingest", _explode)

    with pytest.raises(psycopg.errors.CheckViolation):
        cli.main(["--dsn", "x", "ingest", "unii", "--release", "r",
                  "--unii", str(FIX)])
