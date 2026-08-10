# src/drugref/cli.py
"""The drugref command line: the first supported way to run an ingest (#16), and to
record a curator's expansion decision (#61).

WHAT THIS MODULE IS AND IS NOT. It is a thin, feed-agnostic shell: argument parsing,
one connection, one call into an orchestrator. It holds NO ingest logic and no
knowledge of a feed's format -- that all lives in drugref.ingest, which is where a
parser belongs. The step table below is the single place that knows which
orchestrators exist and in which order they must run.

THE POLICY COMMANDS HOLD NO SQL -- that is a claim about `cli_policy.py`, not about this
whole file; `_handle_status` below is the stated exception. Every policy read and write
goes through interactions.py, never a query embedded in Python. That is load-bearing
specifically because test_only_the_current_view_reads_the_policy_table_directly reads
pg_rewrite, which sees views and matviews and CANNOT see a query embedded in Python, so
a handler with its own SELECT would be a reader of an append-only curated table that no
test in this repository could notice.

`_handle_status` IS THE EXCEPTION, and deliberately so: it embeds two SELECTs, against
`drugref.loaded_release` and `drugref.ingest_run_incomplete`. Neither is curated,
append-only data a silent Python reader could corrupt unnoticed -- they are
operational views nothing governs that way -- so the pg_rewrite discipline above does
not apply to them, and tests/test_cli.py drives them directly through a stub
connection instead of a grep.

THE EXCEPTION STOPS THERE, and a grep now says so. `_handle_status`' third block reads
the CURATED overlay, so it goes through `curation.unresolved_targets` rather than a
third embedded SELECT, and test_curation_orphans.py's
test_the_cli_embeds_no_sql_against_a_curated_table parses this file (and cli_policy.py)
and fails on any string constant naming a curated table. Note what that test is and is
not: it is a grep, not a pg_rewrite reader, because there is no way to make a
Python-embedded query visible to pg_rewrite -- moving the SQL to curation.py does not
achieve that either. What the placement achieves is OWNERSHIP: the read sits beside the
curated write path it belongs to, exactly as `unresolved_expansion_policy` sits in
interactions.py.

THE ARGUMENT LAYER TAKES NO CONNECTION, which is the sense of "pure" that matters
here: the step table, the ChainError family, `resolve_inputs`, `selected_steps`,
`check_release_agreement` and `build_parser` settle every way a chain invocation can
be wrong BEFORE a database exists to be wrong against. Deterministic and DB-free, but
not filesystem-free -- `resolve_inputs` globs the downloads tree, so its tests want a
tmp_path and nothing more.

THAT ARGUMENT LAYER NOW LIVES IN cli_chain.py, extracted in slice 5c.4 -- the step
table's type, the ChainError family, `resolve_inputs`, `selected_steps` and
`check_release_agreement`. What remains here takes a connection or builds the parser:
the `_run_*` wrappers, the four `_handle_*` entry points, `_Parser`, `build_parser` and
`main`. The extraction ran in that direction because cli_chain can import nothing from
drugref, which is what makes an import cycle structurally impossible; moving the
handlers out instead creates one, since STEPS references the runners while
`_handle_chain` needs the planning functions.
"""
import argparse
import logging
import pathlib
import sys
from collections.abc import Sequence

import psycopg

import drugref
from drugref import cli_policy, cli_signing, curation, db, interactions, signatures
from drugref.cli_chain import (ChainError, IngestStep, check_release_agreement,
                               resolve_inputs, selected_steps)
from drugref.ingest import (chebi, gsrs_run, medrt_run, mesh_rel_run, mesh_run,
                            pbs_run, run)

# The two closed seed files ship INSIDE the package (they are drugref's own curated
# data, not a download), so they are defaults rather than required arguments.
_DATA = pathlib.Path(drugref.__file__).resolve().parent / "data"
CROSSWALK = _DATA / "usan_inn_crosswalk.tsv"
ALLOWLIST = _DATA / "legacy_allowlist.tsv"

log = logging.getLogger("drugref")


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


def _run_gsrs(conn, paths, release):
    return gsrs_run.ingest_gsrs(conn, dump_path=paths["dump"],
                                upstream_release=release)


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
    IngestStep("gsrs", (("dump", "GSRS/dump-public-*.gsrs"),), _run_gsrs),
)


def _handle_chain(conn, args) -> int:
    steps = selected_steps(args, STEPS)
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
    """What is loaded, what died trying, and what a rebuild orphaned. THREE blocks,
    one command: an operator asking "is this current?" needs all of them, and reading
    only the first would report a stale release as healthy.

    The third block is issue 76. It goes through `curation.unresolved_targets` rather
    than a SELECT embedded here, unlike the two above it: those read operational views,
    while this one reads the CURATED overlay, which belongs to curation.py. See the
    module docstring for what that placement does and does not buy."""
    # All three blocks say "none" when empty, and the symmetry is the point: a fresh
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

    # Issue 76. Expected empty, and reported in a LOUDER voice than the two blocks
    # above when it is not: a loaded release is news, an orphan is a curator's
    # judgement now pointing at nothing, which only a rebuild can have caused.
    #
    # A DATABASE PREDATING db/029 HAS NO VIEW TO READ, and psycopg's raw UndefinedTable
    # traceback is the wrong way to say so -- it arrives AFTER two blocks of real
    # answers, so the run reads as a partial success, and it names neither the cause nor
    # the fix. `main` renders RuntimeError without a traceback, so re-raise as one. The
    # catch is around this call alone, deliberately: widening it would swallow the
    # UndefinedTable that a genuinely mis-shaped view should still raise.
    try:
        orphans = curation.unresolved_targets(conn)
    except psycopg.errors.UndefinedTable as exc:
        raise RuntimeError(
            "drugref.curated_target_unresolved is missing: this database predates "
            "db/029, so orphaned curator judgement cannot be reported. Run "
            "`drugref migrate` and re-run status.") from exc
    if orphans:
        print(f"\nunresolved curated targets: {len(orphans)}"
              "  ** a rebuild left curator judgement pointing at nothing **")
        for o in orphans:
            # `is not None`, not a falsy test: an empty relationship is not the same
            # thing as a condition ruling's absent one, and only the latter should
            # render without the bracket.
            print("  {:<20} {} -> {}{} reviewed by {} against {}".format(
                o.target_table, o.subject_moiety, o.object_uuid,
                f" [{o.relationship}]" if o.relationship is not None else "",
                o.reviewed_by, o.reviewed_against))
    else:
        print("\nunresolved curated targets: none")

    # THE FOURTH BLOCK, and it exists because a detector without a caller is not a
    # detector (review I7). `signature_backdated` was written, commented and tested, and
    # then read by nothing in `src/` -- so the one residual signal against a stolen key
    # backdating past a TIME-SCOPED revocation was reachable only by an operator who
    # wrote their own SQL. Issue 76 gave `curated_target_unresolved` a block here for
    # the same reason; this is that precedent applied to the view modelled on it.
    #
    # AN OPERATOR SIGNAL, NOT A FAILURE, so `status` still returns 0: an air-gapped
    # curator submitting a week late lands here legitimately. The wording says what to
    # check rather than asserting an attack.
    #
    # SAME UndefinedTable GUARD, SAME NARROW SCOPE as the block above: a database
    # predating db/030 has no view to read, and that must be one sentence rather than a
    # traceback arriving after three blocks of real answers.
    try:
        backdated = signatures.backdated(conn)
    except psycopg.errors.UndefinedTable as exc:
        raise RuntimeError(
            "drugref.signature_backdated is missing: this database predates db/030, so "
            "backdated signatures cannot be reported. Run `drugref migrate` and re-run "
            "status.") from exc
    if backdated:
        print(f"\nbackdated signatures: {len(backdated)}"
              "  ** signed_at long precedes recording -- confirm each was a late "
              "submission, not a key in the wrong hands **")
        for b in backdated:
            print(f"  #{b.signature_id} {b.target_kind} {b.target_id} "
                  f"by {b.key_fingerprint[:12]}... signed {b.signed_at} "
                  f"recorded {b.recorded_at} (lag {b.lag})")
    else:
        print("\nbackdated signatures: none")
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

    def parse_args(self, args=None, namespace=None):
        # `args` here shadows argparse.ArgumentParser.parse_args's own parameter name
        # on purpose -- a subclass override changing a positional parameter's name is
        # a Liskov violation, even with no live keyword caller today. `parsed` is the
        # local for the result so the two never collide.
        parsed = super().parse_args(args, namespace)
        if (getattr(parsed, "action", None) == "show"
                and (parsed.source is None) != (parsed.code is None)):
            self.error("policy show: --source and --code must be given together")
        return parsed


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
    cli_signing.register(commands)

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
        #
        # NO `except psycopg.errors.CheckViolation` HERE, deliberately, and it is worth
        # saying why since one round put it here and had to take it back. This `try`
        # wraps EVERY handler, ingest included, and the same exception means opposite
        # things on the two surfaces: from `policy` it is an operator's typo in a value
        # they typed, and one line is the right answer; from an ingest it is a defect in
        # drugref -- a parser feeding a value db/006 or db/014 forbids -- where the
        # traceback naming the writer is the most useful thing this process can print,
        # and exit 2 would additionally misreport a drugref bug as operator error. Only
        # the caller can tell the two apart, so the catch lives at the caller:
        # cli_policy._write.
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
