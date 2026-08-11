# src/drugref/cli_curate.py
"""`drugref curate` -- the operator surface for the curated OVERLAY tier (slice 5c.2,
task 7).

WHY THIS IS A SEPARATE COMMAND FROM `drugref ingest onchigh`, AND A SEPARATE MODULE FROM
cli.py. `ingest onchigh` (cli.py's STEPS table, Task 6) delete-and-rebuilds
`class_contraindication` rows with source ONCHIGH -- a rebuildable PROJECTION, safe to
re-run in a routine chain. This module writes drugref's own graded clinical judgement
-- severity, mechanism, management, evidence_grade -- into `curated_interaction`, which
is APPEND-ONLY (db/029): nothing is ever edited or deleted, only superseded. Keeping the
two behind separate commands means a routine `drugref ingest chain` can never reach the
one table in this schema where a mistake is permanent. Split into its own file for the
same reason `cli_policy.py`/`cli_signing.py` are: cli.py sits near CLAUDE.md's ~500-line
guideline (issue 89 is already open on two files that crossed it), and the STEP TABLE
above is the single place that knows which rebuildable projections exist -- the curated
overlay is a different kind of thing and does not belong in it.

LIKE `cli_policy.py` AND `cli_signing.py`, THIS MODULE WRITES NO SQL OF ITS OWN for the
curated read/write path: `curate_onchigh` reads through `curation.live_interaction_
judgement` and writes through `curation.record_interaction_judgement`, never a bare
SELECT/INSERT against `curated_interaction` embedded here.

IDEMPOTENT BY COMPARISON, NOT BY LUCK -- this is the module's central discipline, and
the reason `live_interaction_judgement` exists at all. `curated_interaction` is
append-only, so writing unconditionally on every invocation would leave a PERMANENT
duplicate for every rule in the file, and the deferred single-live trigger would only
report the damage at COMMIT -- long after the write happened, and long after `drugref
curate onchigh` had already printed a success line. `curate_onchigh` therefore reads
the live row for each resolved rule BEFORE writing, and only calls
`record_interaction_judgement` when something a curator actually judges has changed.

WHAT "CHANGED" MEANS IS DELIBERATELY NARROW: the five GRADED fields -- `applies`,
`severity`, `mechanism`, `management`, `evidence_grade` -- and nothing else.
`reviewed_at` moves on every run by definition (it defaults to `now()`) and
`reviewed_by` is whatever the operator typed on THIS invocation, not a fact about the
judgement itself. Comparing either would supersede every rule in the file on every
re-run, which is the opposite of the append-only discipline this whole module exists to
protect. See `curation.live_interaction_judgement`'s own docstring for the same point
stated from the read side.

NO VOCABULARY IS RESTATED IN PYTHON. `severity`, `evidence_grade` and `relationship`
(the CI axis) are `db/029` CHECK constraints and a foreign key into `ci_axis` --
exactly the shape `onchigh.py`'s own docstring already refuses to duplicate for the
candidate tier, for db/006's reason: a Python allow-list and a database constraint are
two lists that drift the moment one of them is widened. An illegal value reaches
`curation.record_interaction_judgement`'s INSERT and raises `psycopg.errors.
CheckViolation` there, unmodified and uncaught -- see `_handle_curate_onchigh` below
for why the CLI layer does not catch it either.
"""
import pathlib
import sys
from dataclasses import dataclass

import drugref
from drugref import curation
from drugref.ingest import onchigh, onchigh_run

# Same packaged file `ingest onchigh` defaults to (cli.py's own ONC constant) --
# re-derived here rather than imported from cli.py. cli.py imports THIS module to
# register the `curate` command group, so importing cli.ONC back would be a cycle;
# the two extra lines below are cheaper than restructuring cli.py's step table to
# avoid it.
_DATA = pathlib.Path(drugref.__file__).resolve().parent / "data"
ONC = _DATA / "onc_high_priority.toml"


class _BlankArgumentError(ValueError):
    """A required flag was passed, but its value strips to empty.

    SAME SHAPE AS `cli_policy._reject_blank`'s, deliberately duplicated rather than
    imported -- `cli_signing.py`'s own docstring is the precedent: a private
    cross-module import into a file this module shares no split history with would be
    a stranger coupling than the few lines copying it saves. `reviewed_by` and
    `reviewed_against` land in `curated_interaction.reviewed_by`/`.reviewed_against`,
    both `NOT NULL` with no non-blank CHECK (db/029) -- so a blank value satisfies
    both argparse's `required=True` (presence, not content) and the schema, then sits
    on a row the append-only floor makes UNCORRECTABLE.
    """


def _reject_blank(args, *dests: str) -> None:
    """Refuse a flag the operator passed with a blank (or whitespace-only) value,
    before any write. See `_BlankArgumentError` above for the hazard this guards."""
    for dest in dests:
        if not getattr(args, dest).strip():
            flag = "--" + dest.replace("_", "-")
            raise _BlankArgumentError(f"{flag} was given a blank value")


@dataclass(frozen=True)
class CurateSummary:
    """What one `curate onchigh` run did, mirroring every orchestrator's own summary
    dataclass (e.g. `onchigh_run.OncSummary`) so a caller or test can assert on it
    rather than parse printed output.

    `rules_seen` counts FILE ENTRIES, resolved or not -- mirroring `OncSummary.
    entries_read` -- so an operator can tell "the file defines N rules" from "N of
    them actually resolved and were graded", the same distinction `ingest onchigh`
    already reports via its own `endpoints_unresolved`.

    The other three counts are per RESOLVED SALT FORM (one curated_interaction
    natural key per element of `ResolvedEndpoint.subject_moiety_uuids`), because that
    is the grain `record_interaction_judgement` writes at: one file entry with two
    gated-in salt forms yields up to two rows, exactly as `ingest onchigh`'s own
    `salt_forms_expanded` counts `class_contraindication` rows rather than file
    entries.
    """
    rules_seen: int
    judgements_written: int
    judgements_superseded: int
    unchanged: int


def _graded_fields_match(live: dict, judgement: onchigh.OncJudgement) -> bool:
    """True when every one of the five GRADED fields already matches the file's
    judgement -- the whole test for "nothing to write here".

    Compares `live` (as `curation.live_interaction_judgement` returns it) against the
    parsed `OncJudgement` field by field, NOT by building a second dict and comparing
    dicts wholesale -- `live`'s keys are `curation`'s own column names and
    `OncJudgement`'s are the parser's dataclass fields, and the two happen to share
    spelling today only because nobody has had a reason to diverge them yet. Naming
    each comparison explicitly means a future rename on either side fails loudly
    (`AttributeError`/`KeyError`) rather than silently comparing two fields that used
    to mean the same thing and no longer do.
    """
    return (live["applies"] == judgement.applies
            and live["severity"] == judgement.severity
            and live["mechanism"] == judgement.mechanism
            and live["management"] == judgement.management
            and live["evidence_grade"] == judgement.evidence_grade)


def curate_onchigh(conn, *, path: pathlib.Path, reviewed_by: str,
                   reviewed_against: str) -> CurateSummary:
    """Grade every resolvable rule in the ONC high-priority file, writing (or
    revising) drugref's own judgement into `curated_interaction`.

    DOES NOT COMMIT. The caller owns the transaction -- `curation.
    record_interaction_judgement`'s own rule, restated here because this function is
    the one that calls it repeatedly in a loop. The CLI handler below is the only
    caller that commits; a test driving this function directly must commit its own
    work to see it survive a rollback-scoped `conn` fixture.

    RESOLUTION REUSES `onchigh_run.resolve_entry` AND `subject_forms`, THE SAME
    FUNCTIONS `ingest onchigh` calls -- not `class_contraindication` itself. The
    candidate projection and the curated overlay are independently rebuildable /
    append-only, so this function resolves straight from the file's own UNII/MED-RT
    identifiers rather than trusting whatever the candidate tier last wrote; an entry
    whose subject or object does not currently resolve is SKIPPED here exactly as it
    is queued (not written) by `ingest onchigh` -- grading an unresolvable rule would
    write a judgement with no candidate row for `curated_ddi_pair` to join against.

    PER RESOLVED SALT FORM, not per file entry: `resolve_entry` already expands the
    subject to every gated-in salt form before this function ever sees it (see
    `ResolvedEndpoint`'s own docstring), so one file entry with two salt forms writes
    up to two curated_interaction rows, one per (salt form, class, axis) natural key.

    THE COMPARISON THAT MAKES THIS IDEMPOTENT: `curation.live_interaction_judgement`
    is read for each natural key BEFORE any write. No live row -> write. A live row
    whose graded fields already match -> counted as `unchanged`, nothing written. A
    live row that differs -> `record_interaction_judgement` is called again, which
    supersedes it (INSERT the new row, then point the old one at it) rather than
    mutating it -- `curated_interaction` refuses UPDATE outright.
    """
    path = pathlib.Path(path)
    entries = onchigh.parse(path)

    judgements_written = judgements_superseded = unchanged = 0
    for entry in entries:
        resolved = onchigh_run.resolve_entry(conn, entry)
        if not isinstance(resolved, onchigh_run.ResolvedEndpoint):
            # A well-formed identifier drugref does not (yet) hold is a coverage
            # gap `ingest onchigh` already reports on its own worklist -- grading a
            # rule with no resolvable candidate would write a judgement that
            # `curated_ddi_pair` can never join to a pair.
            continue

        judgement = entry.judgement
        for subject_moiety_uuid in resolved.subject_moiety_uuids:
            live = curation.live_interaction_judgement(
                conn, subject_moiety_uuid, resolved.object_class_uuid, resolved.axis)
            if live is not None and _graded_fields_match(live, judgement):
                unchanged += 1
                continue
            curation.record_interaction_judgement(
                conn, subject_moiety_uuid, resolved.object_class_uuid, resolved.axis,
                judgement.applies, severity=judgement.severity,
                mechanism=judgement.mechanism, management=judgement.management,
                evidence_grade=judgement.evidence_grade, reviewed_by=reviewed_by,
                reviewed_against=reviewed_against)
            if live is None:
                judgements_written += 1
            else:
                judgements_superseded += 1

    return CurateSummary(
        rules_seen=len(entries), judgements_written=judgements_written,
        judgements_superseded=judgements_superseded, unchanged=unchanged)


def _handle_curate_onchigh(conn, args) -> int:
    """The `drugref curate onchigh` entry point. COMMITS -- the CLI is the caller,
    and in these modules the caller owns the transaction (`cli_policy`'s own rule).

    NO `except psycopg.errors.CheckViolation` HERE, deliberately, on `cli.main`'s own
    standing rule (see cli.py's docstring for why that catch cannot live on `main`'s
    `try`, and `cli_signing.py`'s docstring for the fullest statement of it). The
    value that could fail a CHECK here comes from the ONC FILE, not from a flag the
    operator typed on this command line -- so a violation means the packaged data (or
    a curator's hand-edit of it) carries a value db/029 forbids, which is a defect for
    a traceback to surface loudly, not an operator typo for a clean exit-2 line to
    absorb. `--reviewed-by`/`--reviewed-against` ARE operator-typed, which is exactly
    why they get the blank guard below and the file's own values do not get a second
    one.
    """
    try:
        _reject_blank(args, "reviewed_by", "reviewed_against")
    except _BlankArgumentError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
    summary = curate_onchigh(conn, path=args.path, reviewed_by=args.reviewed_by,
                             reviewed_against=args.reviewed_against)
    conn.commit()
    print(f"curate onchigh: {summary}")
    return 0


def register(commands) -> None:
    """Add the `curate` command group to an existing subparsers object.

    Called by `cli.build_parser`, mirroring `cli_policy.register`/`cli_signing.
    register` -- the global `--dsn`/`--log-level` flags and the single
    connect-and-dispatch path in `cli.main` keep serving every command this way.
    A single `onchigh` subcommand today; a future curated source (5c.3/5c.4) adds a
    sibling subparser here rather than a new top-level command, matching how `ingest`
    holds every candidate source under one group.
    """
    curate = commands.add_parser(
        "curate", help="record drugref's own graded clinical judgement (append-only)")
    curate_sources = curate.add_subparsers(dest="source", required=True)

    onchigh_parser = curate_sources.add_parser(
        "onchigh", help="grade the ONC high-priority DDI list")
    onchigh_parser.add_argument(
        "--path", type=pathlib.Path, default=ONC,
        help=f"path to the ONC file (default: packaged {ONC.name})")
    onchigh_parser.add_argument("--reviewed-by", required=True,
                                help="the curator running this command")
    onchigh_parser.add_argument(
        "--reviewed-against", required=True,
        help="the release/edition this judgement was formed against")
    onchigh_parser.set_defaults(handler=_handle_curate_onchigh)
