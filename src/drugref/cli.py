# src/drugref/cli.py
"""The drugref command line: the first supported way to run an ingest (#16).

WHAT THIS MODULE IS AND IS NOT. It is a thin, feed-agnostic shell: argument parsing,
one connection, one call into an orchestrator. It holds NO ingest logic and no
knowledge of a feed's format -- that all lives in drugref.ingest, which is where a
parser belongs. The step table below is the single place that knows which
orchestrators exist and in which order they must run.

Everything above `main` is pure in the sense this codebase means it: no database
access, deterministic, testable with no fixtures.
"""
import argparse
import logging
import pathlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import drugref
from drugref import db
from drugref.ingest import chebi, medrt_run, mesh_rel_run, mesh_run, pbs_run, run

# The two closed seed files ship INSIDE the package (they are drugref's own curated
# data, not a download), so they are defaults rather than required arguments.
_DATA = pathlib.Path(drugref.__file__).resolve().parent / "data"
CROSSWALK = _DATA / "usan_inn_crosswalk.tsv"
ALLOWLIST = _DATA / "legacy_allowlist.tsv"

log = logging.getLogger("drugref")


@dataclass(frozen=True)
class IngestStep:
    """One orchestrator, as the CLI sees it.

    `inputs` pairs an ARGUMENT NAME with a GLOB relative to --downloads, and both
    consumers read the same tuple: the per-source subcommand turns each name into a
    required `--name PATH` flag, and the chain (task 5) resolves the same names by
    glob. One declaration, so a step cannot grow an input the chain does not know
    about.
    """
    name: str
    inputs: tuple[tuple[str, str], ...]
    runner: Callable[[object, dict[str, pathlib.Path], str], object]


def _run_unii(conn, paths, release):
    return run.ingest_unii(conn, unii_path=paths["unii"], crosswalk_path=CROSSWALK,
                           allowlist_path=ALLOWLIST, upstream_release=release)


def _run_chebi(conn, paths, release):
    return chebi.enrich_from_chebi(conn, chebi_path=paths["chebi"],
                                   upstream_release=release)


def _run_medrt(conn, paths, release):
    return medrt_run.ingest_medrt(conn, medrt_path=paths["medrt"],
                                  upstream_release=release)


def _run_mesh(conn, paths, release):
    return mesh_run.ingest_mesh(conn, pa_path=paths["pa"], desc_path=paths["desc"],
                                supp_path=paths["supp"], upstream_release=release)


def _run_mesh_relations(conn, paths, release):
    return mesh_rel_run.ingest_mesh_relations(
        conn, medrt_path=paths["medrt"], desc_path=paths["desc"],
        supp_path=paths["supp"], upstream_release=release)


def _run_pbs(conn, paths, release):
    return pbs_run.ingest_pbs(conn, paths["items"], release)


# ORDER IS THE DEPENDENCY ORDER and is a constant, not an argument: UNII first because
# every other feed joins to the moieties it registers, MED-RT before mesh-relations
# because the MeSH-keyed run reads classes medrt_run writes. A caller who could
# reorder these could produce a chain that looks like it worked and bridged nothing.
#
# The globs describe the layout a real downloads/ tree has, not a tidy one invented
# here: UNII_Names_*.txt sits at the root, MED-RT under MEDRT/ (extracted from
# Core_MEDRT_XML.zip by hand -- teaching this module to open archives would make it
# feed-aware for one feed's convenience), MeSH under mesh/, PBS under tables_as_csv/.
STEPS = (
    IngestStep("unii", (("unii", "UNII_Names_*.txt"),), _run_unii),
    IngestStep("chebi", (("chebi", "chebi*.tsv"),), _run_chebi),
    IngestStep("medrt", (("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),), _run_medrt),
    IngestStep("mesh", (("pa", "mesh/pa*.xml"), ("desc", "mesh/desc*.gz"),
                        ("supp", "mesh/supp*.gz")), _run_mesh),
    IngestStep("mesh-relations", (("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),
                                  ("desc", "mesh/desc*.gz"),
                                  ("supp", "mesh/supp*.gz")), _run_mesh_relations),
    IngestStep("pbs", (("items", "tables_as_csv/items.csv"),), _run_pbs),
)


def _handle_migrate(conn, args) -> int:
    db.apply_migrations(conn)
    print("migrations applied")
    return 0


def _handle_status(conn, args) -> int:
    """What is loaded, and what died trying. Two views, one command: an operator
    asking "is this current?" needs both halves, and reading only the first would
    report a stale release as healthy."""
    print("loaded releases:")
    for row in conn.execute(
            "SELECT source, writer, upstream_release, finished_at "
            "FROM drugref.loaded_release").fetchall():
        print("  {:<8} {:<14} {:<12} {}".format(*(str(c) for c in row)))

    incomplete = conn.execute(
        "SELECT ingest_run_id, source, writer, upstream_release, started_at "
        "FROM drugref.ingest_run_incomplete").fetchall()
    print("\nunfinished runs:" if incomplete else "\nunfinished runs: none")
    for row in incomplete:
        print("  #{} {:<8} {:<14} {:<12} started {}".format(*(str(c) for c in row)))
    return 0


def _handle_ingest(conn, args) -> int:
    step = args.step
    paths = {name: getattr(args, name.replace("-", "_")) for name, _ in step.inputs}
    summary = step.runner(conn, paths, args.release)
    print(f"{step.name}: {summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The whole command surface, built from STEPS so a new orchestrator needs one
    tuple entry rather than an edit in three places."""
    parser = argparse.ArgumentParser(
        prog="drugref", description="drugref.org reference-data service")
    parser.add_argument("--dsn", help="PostgreSQL DSN (default: $DRUGREF_DSN)")
    parser.add_argument("--log-level", default="info",
                        choices=("debug", "info", "warning", "error"))
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "migrate", help="apply every db/*.sql not yet applied"
    ).set_defaults(handler=_handle_migrate)
    commands.add_parser(
        "status", help="which release each writer last landed, and what died trying"
    ).set_defaults(handler=_handle_status)

    ingest = commands.add_parser("ingest", help="run one feed, or a chain of them")
    sources = ingest.add_subparsers(dest="source", required=True)
    for step in STEPS:
        sub = sources.add_parser(step.name, help=f"ingest one {step.name} release")
        sub.add_argument("--release", required=True,
                         help="the upstream release tag, recorded as provenance")
        for name, glob in step.inputs:
            sub.add_argument(f"--{name}", required=True, type=pathlib.Path,
                             help=f"path to the {name} file (chain glob: {glob})")
        sub.set_defaults(handler=_handle_ingest, step=step)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, connect, dispatch. Returns a process exit code.

    Takes `argv` so tests drive it by call rather than by subprocess -- a subprocess
    test would need a built package and would hide the traceback that makes a failure
    diagnosable.
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        with db.connect(args.dsn) as conn:
            return args.handler(conn, args)
    except RuntimeError as exc:
        # db.connect's "no DSN" message is written for exactly this moment; a
        # traceback would bury it.
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
