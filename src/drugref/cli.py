# src/drugref/cli.py
"""The drugref command line: the first supported way to run an ingest (#16), and to
record a curator's expansion decision (#61).

WHAT THIS MODULE IS AND IS NOT. It is a thin, feed-agnostic shell: argument parsing,
one connection, one call into an orchestrator. It holds NO ingest logic and no
knowledge of a feed's format -- that all lives in drugref.ingest, which is where a
parser belongs. The step table below is the single place that knows which
orchestrators exist and in which order they must run.

IT HOLDS NO SQL. Every database access goes through a module function -- the
orchestrators for ingest, interactions.py for policy. That is load-bearing for the
policy commands specifically: test_only_the_current_view_reads_the_policy_table_directly
reads pg_rewrite, which sees views and matviews and CANNOT see a query embedded in
Python, so a handler with its own SELECT would be a reader of an append-only curated
table that no test in this repository could notice.

THE ARGUMENT LAYER TAKES NO CONNECTION, which is the sense of "pure" that matters
here: the step table, the ChainError family, `resolve_inputs`, `selected_steps`,
`check_release_agreement` and `build_parser` settle every way a chain invocation can
be wrong BEFORE a database exists to be wrong against. Deterministic and DB-free, but
not filesystem-free -- `resolve_inputs` globs the downloads tree, so its tests want a
tmp_path and nothing more.

THAT LAYER IS NOT "EVERYTHING ABOVE `main`", and the line is worth stating precisely
because the file's shape suggests otherwise: the `_run_*` wrappers and the four
`_handle_*` entry points also sit above `main`, and every one of them takes a
connection. They are deliberately thin for that reason -- what cannot be tested
without a database is kept to a dispatch the pure layer has already validated.
"""
import argparse
import logging
import pathlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import drugref
from drugref import cli_policy, db, interactions
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
    required `--name PATH` flag, and the chain resolves the same names by glob. One
    declaration, so a step cannot grow an input the chain does not know about.

    `secondary` names the inputs this step READS BUT DOES NOT DATE (#60). A step
    records one release tag, describing its PRIMARY authority; mesh-relations reads
    two -- MED-RT states the rule, MeSH defines its object -- and writes one
    ingest_run row under source='MED-RT'. So its desc/supp inputs are dated by the
    mesh step and merely consumed here, and check_release_agreement must not read
    that as one file claimed to be two releases.

    It names INPUTS, not paths, because the declaration belongs beside the glob it
    qualifies and has to survive a glob's filename changing between releases.
    """
    name: str
    inputs: tuple[tuple[str, str], ...]
    runner: Callable[[object, dict[str, pathlib.Path], str], object]
    secondary: tuple[str, ...] = ()

    def __post_init__(self):
        # A typo here would exempt nothing and leave the chain refusing the very
        # invocation the exemption exists to allow -- a silent failure, in the field,
        # of a check whose whole job is to be loud. Raised at import, where STEPS is
        # built, so it cannot reach an operator.
        undeclared = set(self.secondary) - {name for name, _ in self.inputs}
        if undeclared:
            raise ValueError(
                f"{self.name}: secondary names an input this step does not declare: "
                f"{', '.join(sorted(undeclared))}")


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


# ORDER IS A CONSTANT, NOT AN ARGUMENT, and ONE POSITION IN IT IS A DATA DEPENDENCY:
# UNII FIRST, because every other feed joins to the moieties it registers -- medrt on
# RXNORM_IN, mesh and mesh-relations on UNII/CAS, chebi on INCHIKEY, all of them
# identity_claim; pbs on substance_moiety.display_name (drugref's own label, NOT the
# INN claims -- the distinction #26 drew and slice 8a depends on). Run any of them
# against an empty registry and the chain looks like it worked and bridged nothing.
#
# THE REST OF THE ORDER IS CONVENTION, and calling it a dependency would state
# something the code does not do. medrt-before-mesh-relations in particular is NOT
# one: the MeSH-keyed run reads identity_claim and nothing else -- never
# substance_class, class_membership or class_parent, the tables medrt_run writes --
# and the single table the two share, ingest_unmatched_ingredient, was deliberately
# made order-independent (one writer per (source, reason) since #39/db/018, extended
# to a fourth bucket by #47/db/026). Both re-derive the question register last, so
# whichever runs second leaves it complete. The order stays fixed anyway, because two
# chains over the same feeds should be comparable run to run; it just is not
# load-bearing, and a reader who believed it was would go looking for a bridge that
# was never there.
#
# The globs describe the layout a real downloads/ tree has, not a tidy one invented
# here: UNII_Records_*.txt sits at the root (NOT UNII_Names_*.txt -- Names is the
# intuitively appealing wrong answer, a real file that sits right beside Records, but
# it carries only Name/TYPE/UNII/Display Name. ingest/unii.py's _REQUIRED_COLUMNS is a
# SIX-tuple the parser refuses a file without; the four of them the moiety GATE reads
# as membership signals are INN_ID, USAN_ID, RXCUI and SUBSTANCE_TYPE -- two different
# sets, and saying "the four the parser requires" conflates them. Only Records carries
# any of the four, so pointing this glob at Names fails outright, every time),
# MED-RT under MEDRT/ (extracted from Core_MEDRT_XML.zip by hand -- teaching this
# module to open archives would make it feed-aware for one feed's convenience), MeSH
# under mesh/, PBS under tables_as_csv/.
STEPS = (
    IngestStep("unii", (("unii", "UNII_Records_*.txt"),), _run_unii),
    IngestStep("chebi", (("chebi", "chebi*.tsv"),), _run_chebi),
    IngestStep("medrt", (("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),), _run_medrt),
    IngestStep("mesh", (("pa", "mesh/pa*.xml"), ("desc", "mesh/desc*.gz"),
                        ("supp", "mesh/supp*.gz")), _run_mesh),
    IngestStep("mesh-relations", (("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),
                                  ("desc", "mesh/desc*.gz"),
                                  ("supp", "mesh/supp*.gz")), _run_mesh_relations,
               secondary=("desc", "supp")),
    IngestStep("pbs", (("items", "tables_as_csv/items.csv"),), _run_pbs),
)


class ChainError(Exception):
    """A chain invocation that cannot be run without recording something untrue.

    One base so `main` catches the family rather than an ever-growing tuple, and so a
    future pre-flight check is caught by construction rather than by remembering.
    """


class InputResolutionError(ChainError):
    """A chain glob matched no file, or more than one.

    BOTH are errors, and the second is the one that bites: two releases left in one
    directory is the ordinary way this goes wrong, and silently taking either would
    record the wrong bytes as this run's provenance.
    """


class ReleaseError(ChainError):
    """A release tag that cannot be recorded honestly: absent, or self-contradicting.

    `ingest_run` IS HISTORY -- append-only, never corrected -- so a wrong tag is not a
    mistake an operator can take back. `writer` exists (db/025) precisely so a stale
    projection is visible; provenance that is confidently wrong defeats it more
    thoroughly than provenance that is missing.
    """


def _release_flag(step: IngestStep) -> str:
    """`mesh-relations` -> `mesh_relations_release`, the argparse destination."""
    return f"{step.name.replace('-', '_')}_release"


def resolve_inputs(downloads: pathlib.Path,
                   step: IngestStep) -> dict[str, pathlib.Path]:
    """Resolve one step's inputs under `downloads`, by the globs it declares.

    GLOBS RATHER THAN FIXED NAMES, because the real layout is irregular and a tidy
    invented convention would match nothing: releases carry their version in the
    filename (UNII_Records_26Feb2026.txt, Core_MEDRT_2026.07.06_XML.xml) and a fixed
    name would go stale on the next download.
    """
    resolved = {}
    for name, pattern in step.inputs:
        matches = sorted(downloads.glob(pattern))
        if len(matches) != 1:
            # "found N files" (not just "found N"): this branch only ever fires for
            # 0 or 2+ matches, so the plural reads correctly in both cases, and it is
            # the phrase an operator scanning a wall of stderr can grep for.
            raise InputResolutionError(
                f"{step.name}: expected exactly one file matching '{pattern}' under "
                f"{downloads}, found {len(matches)} files"
                + (f": {', '.join(m.name for m in matches)}" if matches else ""))
        resolved[name] = matches[0]
    return resolved


def selected_steps(args: argparse.Namespace) -> tuple[tuple[IngestStep, str], ...]:
    """The steps this chain invocation includes, in STEPS order, with their releases.

    SUPPLYING A RELEASE IS THE OPT-IN. No default set, no skip-list: a chain that ran
    feeds nobody named would record provenance nobody stated, and this project does
    not guess provenance. Returning them in STEPS order rather than flag order is what
    makes the dependency order unbreakable from the command line.

    PRESENCE, NOT TRUTHINESS, is what selects a step, and the difference is the trap
    the spec's own list names: `--medrt-release ""` is a flag the operator DID pass,
    and testing truthiness silently dropped the step it asked for -- a chain that
    reports success having never touched a feed the command line named. Absent is the
    opt-out (None); empty or blank is an error. "A convention that silently matches
    nothing is worse than none" applies to flag values exactly as it does to globs.
    """
    selected = []
    for step in STEPS:
        release = getattr(args, _release_flag(step), None)
        if release is None:
            continue
        if not release.strip():
            raise ReleaseError(
                f"--{step.name}-release was given an empty tag. It is the string "
                "recorded as this run's provenance, so it cannot be blank; omit the "
                "flag to leave the step out of the chain.")
        selected.append((step, release))
    return tuple(selected)


def check_release_agreement(
        plan: Sequence[tuple[IngestStep, str, dict[str, pathlib.Path]]]) -> None:
    """Refuse a chain in which one FILE is claimed to be two different releases.

    THE STEPS OVERLAP, and that is not incidental: `medrt` and `mesh-relations`
    resolve the SAME Core_MEDRT_*_XML.xml. Their release tags are stated
    independently, so `--medrt-release 2026.07.06 --mesh-relations-release 2026.05.04`
    writes two different releases into ingest_run FROM IDENTICAL BYTES. One of them is
    false, and ingest_run is history: nothing can take it back.

    `mesh` and `mesh-relations` also share desc/supp, and that overlap is NOT a
    conflict (#60): mesh-relations declares them `secondary`, so it reads them without
    dating them. Comparing those claims refused the documented four-source invocation
    for a disagreement that was never one -- two true statements about two different
    authorities.

    That is worse than a missing tag. db/025 added `writer` so an operator could see
    that one half of MED-RT is a release behind the other; this makes the two halves
    disagree on purpose, so the signal reports staleness that does not exist -- or
    hides staleness that does. A pre-flight check costs nothing and the alternative
    is uncorrectable.

    Pure, and run over the resolved plan rather than over the flags, because the
    question is about PATHS: two globs that happen to name one file must agree even
    though the flags look independent.
    """
    stated: dict[pathlib.Path, tuple[str, str]] = {}   # path -> (release, step name)
    for step, release, paths in plan:
        for name, path in paths.items():
            if name in step.secondary:
                # READ, NOT DATED. This step states no release for this file, so it
                # makes no claim that could contradict another step's. Skipping the
                # record entirely (rather than recording and tolerating a mismatch)
                # is what keeps a file dated by NO step from silently agreeing with
                # itself.
                continue
            first_release, first_step = stated.setdefault(path, (release, step.name))
            if first_release != release:
                raise ReleaseError(
                    f"{path} is read by both {first_step} and {step.name}, which were "
                    f"given different release tags ('{first_release}' and "
                    f"'{release}'). The same bytes cannot be two releases, and "
                    "ingest_run is history -- it cannot be corrected afterwards.")


def _handle_chain(conn, args) -> int:
    steps = selected_steps(args)
    if not steps:
        print("drugref: no sources selected; pass at least one --<source>-release",
              file=sys.stderr)
        return 2

    # EVERY step's inputs are resolved BEFORE any step runs, so a typo fails in a
    # second rather than sixty. The feeds are rebuildable projections, so a half-run
    # chain is recoverable -- but an operator who has to notice that at all has been
    # failed by the tool.
    plan = [(step, release, resolve_inputs(args.downloads, step))
            for step, release in steps]
    check_release_agreement(plan)
    for step, release, paths in plan:
        log.info("chain: %s (release=%s)", step.name, release)
        print(f"{step.name}: {step.runner(conn, paths, release)}")
    return 0


def _handle_migrate(conn, args) -> int:
    db.apply_migrations(conn)
    print("migrations applied")
    return 0


def _handle_status(conn, args) -> int:
    """What is loaded, and what died trying. Two views, one command: an operator
    asking "is this current?" needs both halves, and reading only the first would
    report a stale release as healthy."""
    # Both blocks say "none" when empty, and the symmetry is the point: a fresh
    # database printed a bare "loaded releases:" header, which reads as output that
    # got cut off rather than as an answer. Nothing loaded IS the answer there.
    loaded = conn.execute(
        "SELECT source, writer, upstream_release, finished_at "
        "FROM drugref.loaded_release").fetchall()
    print("loaded releases:" if loaded else "loaded releases: none")
    for row in loaded:
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


class _Parser(argparse.ArgumentParser):
    """The whole surface, plus the one cross-argument rule argparse cannot state.

    argparse has mutually EXCLUSIVE groups and no mutually INCLUSIVE ones, so
    "`policy show` takes both halves of the natural key or neither" has to be checked
    after parsing. It lives here rather than in `main` because the DB-free tests drive
    the surface through `build_parser().parse_args(...)`, and an invariant only `main`
    enforced would be a rule the parser's own tests could not see.

    Half a key identifies nothing: `source` means WHO DEFINES the class, not who ruled
    on it, so it cannot be defaulted.
    """

    def parse_args(self, argv=None, namespace=None):
        args = super().parse_args(argv, namespace)
        if (getattr(args, "action", None) == "show"
                and (args.source is None) != (args.code is None)):
            self.error("policy show: --source and --code must be given together")
        return args


def build_parser() -> argparse.ArgumentParser:
    """The whole command surface, built from STEPS so a new orchestrator needs one
    tuple entry rather than an edit in three places."""
    parser = _Parser(
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

    chain = sources.add_parser(
        "chain", help="run several feeds in dependency order from one directory")
    chain.add_argument("--downloads", required=True, type=pathlib.Path,
                       help="directory holding the upstream releases")
    for step in STEPS:
        chain.add_argument(
            f"--{step.name}-release",
            help=f"include {step.name}, recording this release tag")
    chain.set_defaults(handler=_handle_chain)

    cli_policy.register(commands)

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
    except (RuntimeError, ChainError, interactions.NoLiveDecisionError) as exc:
        # db.connect's "no DSN" message is written for exactly this moment; a
        # traceback would bury it.
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
