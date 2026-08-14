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
SELECT/INSERT against `curated_interaction` embedded here. Task 10 (design spec
section 14) widens this to a SECOND overlay table, `curated_class_interaction`, for
a class-subject rule -- through the identically-shaped `curation.
live_class_interaction_judgement` / `curation.record_class_interaction_judgement`
pair, dispatched on `onchigh_run.resolve_entry`'s return type rather than a second
command: an operator runs one `curate onchigh`, and this module tells the two grains
apart internally.

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
(the CI axis) are database constraints -- since db/035, `severity` is a foreign key
into `drugref.severity_kind` and `relationship` one into `ci_axis`, with
`evidence_grade` still a db/029 CHECK -- exactly the shape `onchigh.py`'s own docstring
already refuses to duplicate for the candidate tier, for db/006's reason: a Python
allow-list and a database constraint are two lists that drift the moment one of them is
widened. An illegal value reaches `curation.record_interaction_judgement`'s INSERT and
raises there, unmodified and uncaught -- `ForeignKeyViolation` for the two keyed
columns, `CheckViolation` for `evidence_grade`. WHICH class it is does not change the
handling anywhere, which is the point: see `_handle_curate_onchigh` below for why the
CLI layer catches neither.
"""
import logging
import pathlib
import sys
from collections.abc import Callable
from dataclasses import dataclass

import drugref
from drugref import curation
from drugref.ingest import onchigh, onchigh_run

log = logging.getLogger(__name__)

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


class CollidingRuleError(ValueError):
    """Two entries in one file resolve to the SAME curated natural key.

    `onchigh.parse` already refuses a duplicate `entry_id`, but that is not the
    key a curated row is written under -- (subject, object, relationship) is,
    and reaching it needs resolution, which needs a database, which the pure
    parser must never touch. So two entries naming the same subject and object
    parse cleanly and collide only here.

    WHY THIS IS A RAISE AND NOT A "LAST ONE WINS". Ungarded, entry A writes its
    judgement and entry B reads A's row as `live`, sees a different grade, and
    SUPERSEDES it -- within a single run. Two clinical claims collapse to
    whichever the file happened to list last, counted into
    `judgements_superseded`, which is the same counter an ordinary regrade
    increments, so the summary reads as routine. And it never settles: every
    later invocation writes two more permanent rows into an append-only table,
    with the deferred single-live trigger silent throughout because exactly one
    row IS live at commit.

    The file is hand-authored, so this is a defect IN THE FILE -- the same
    judgement `onchigh_resolve.EndpointMismatchError` makes one stage earlier
    when the file's own review aid disagrees with what its identifier resolves
    to. Raising leaves the caller's transaction uncommitted, so neither claim
    lands and the curator fixes the file rather than discovering later that
    half of it was overwritten.
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

    TWO GRAINS, DELIBERATELY KEPT SEPARATE AND EACH INTERNALLY RECONCILED -- fix
    round 1 found that an earlier version of this dataclass counted `rules_seen` at
    the FILE-ENTRY grain while silently dropping every entry that failed to resolve
    on the floor, so the four numbers a caller could see could never be reconciled
    against each other (issue 71's lesson: a dropped row counted into nothing is
    exactly the defect a worklist exists to catch). Task 10 widens BOTH grains to
    cover the class-subject shape (design spec section 14) without adding a third:
    a class-subject entry is still exactly one ENTRY-grain outcome, and its single
    graded rule is still exactly one FORM-grain outcome -- see `test_cli_curate.py::
    test_the_reconciliation_equation_holds_across_both_grains`, which exercises both
    grains together in one run rather than trusting each grain's own test alone.

    1. ENTRY grain: `rules_seen == entries_resolved + entries_unresolved`, always --
       every entry `onchigh.parse` returns lands in EXACTLY ONE of those two buckets,
       regardless of whether its subject is a moiety or a class (Task 10), and
       `test_cli_curate.py::test_every_entry_is_accounted_for_in_exactly_one_bucket`
       pins the equation so a third outcome added later without a counter fails the
       build instead of going quiet.
    2. FORM grain: `judgements_written + judgements_superseded + unchanged` sums to
       the total number of RESOLVED JUDGEMENT TARGETS across every entry in
       `entries_resolved` -- one `curated_interaction` natural key per element of a
       moiety-subject entry's `ResolvedEndpoint.subject_moiety_uuids` (exactly as
       `ingest onchigh`'s own `salt_forms_expanded` counts `class_contraindication`
       rows rather than file entries), OR one `curated_class_interaction` natural
       key for a class-subject entry's single `ResolvedClassEndpoint` (Task 10 --
       design spec section 14.3: a class has no salt forms, so it contributes
       exactly one target, never several). This total is NOT `entries_resolved`,
       because one moiety-subject entry with two gated-in salt forms contributes
       two form-grain outcomes -- possibly two DIFFERENT ones, if a salt form the
       composition tree only just gated in has no live judgement yet while its
       sibling was already graded identically on a previous run.

    `entries_unresolved` counts ENTRIES, not endpoints (contrast `onchigh_run.
    OncSummary.endpoints_unresolved`, which can be up to 2 per entry): an entry whose
    subject OR object fails to resolve contributes exactly 1 here, because
    `curate_onchigh` skips the whole entry rather than half-grading it. See
    `curate_onchigh`'s own docstring for why this module does not also write
    `ingest_unresolved_onc_endpoint` -- that table's PRIMARY KEY includes
    `ingest_run`, which this command never opens.
    """
    rules_seen: int
    entries_resolved: int
    entries_unresolved: int
    judgements_written: int
    judgements_superseded: int
    unchanged: int


def _graded_fields_match(live: dict, judgement: onchigh.OncJudgement) -> bool:
    """True when every one of the five GRADED fields already matches the file's
    judgement -- the whole test for "nothing to write here".

    Compares `live` (as `curation.live_interaction_judgement` OR `curation.
    live_class_interaction_judgement` returns it -- both share the same five
    column names, Task 10) against the parsed `OncJudgement` field by field, NOT by
    building a second dict and comparing dicts wholesale -- `live`'s keys are
    `curation`'s own column names and `OncJudgement`'s are the parser's dataclass
    fields, and the two happen to share spelling today only because nobody has had
    a reason to diverge them yet. Naming each comparison explicitly means a future
    rename on either side fails loudly (`AttributeError`/`KeyError`) rather than
    silently comparing two fields that used to mean the same thing and no longer
    do.
    """
    return (live["applies"] == judgement.applies
            and live["severity"] == judgement.severity
            and live["mechanism"] == judgement.mechanism
            and live["management"] == judgement.management
            and live["evidence_grade"] == judgement.evidence_grade)


def _grade(live: dict | None, judgement: onchigh.OncJudgement,
          write: Callable[[], int]) -> str:
    """Decide, idempotently, whether one judgement target needs a write --
    shared by both grains (Task 10) so the compare-then-write sequence is
    stated once rather than twice with two different natural keys.

    `live` is the target's already-fetched live graded fields (or None if
    nothing is curated yet). `write` is a zero-argument callable that performs
    the actual INSERT-then-supersede when called -- a closure the caller
    builds so this function stays ignorant of WHICH curation writer
    (`record_interaction_judgement` or `record_class_interaction_judgement`)
    and WHICH natural key it closes over.

    Returns 'written', 'superseded', or 'unchanged' -- named outcomes rather
    than three separately-incremented integers, so the call site reads as
    English and the three-way tally lives in exactly one place (the dict
    `curate_onchigh` accumulates into) instead of three repeated
    `if/elif/else` blocks, one per grain.
    """
    if live is not None and _graded_fields_match(live, judgement):
        return "unchanged"
    write()
    return "written" if live is None else "superseded"


def curate_onchigh(conn, *, path: pathlib.Path, reviewed_by: str,
                   reviewed_against: str) -> CurateSummary:
    """Grade every resolvable rule in the ONC high-priority file, writing (or
    revising) drugref's own judgement into `curated_interaction` (a moiety-subject
    rule) or `curated_class_interaction` (Task 10's class-subject rule, design spec
    section 14) -- whichever `onchigh_run.resolve_entry` says the entry is.

    DOES NOT COMMIT. The caller owns the transaction -- `curation.
    record_interaction_judgement`'s own rule, restated here because this function is
    the one that calls it repeatedly in a loop. The CLI handler below is the only
    caller that commits; a test driving this function directly must commit its own
    work to see it survive a rollback-scoped `conn` fixture.

    RESOLUTION REUSES `onchigh_run.resolve_entry`, THE SAME FUNCTION `ingest onchigh`
    calls -- not `class_contraindication`/`class_pair_contraindication` themselves.
    The candidate projection and the curated overlay are independently rebuildable /
    append-only, so this function resolves straight from the file's own
    UNII/MED-RT identifiers rather than trusting whatever the candidate tier last
    wrote. `resolve_entry` already dispatches on the entry's subject kind (Task 10),
    so this function only has to tell apart the THREE shapes it can return.

    AN UNRESOLVED ENTRY IS SKIPPED, NOT SILENTLY -- fix round 1 found an earlier
    version of this loop dropped it with neither a counter nor a log line, which is
    precisely the defect issue 71 was filed to stop: a dropped row counted into
    nothing is a number nobody can act on. This function counts it into
    `CurateSummary.entries_unresolved` and logs it (`entry_id` plus how many of its
    two endpoints failed) so an operator reading the summary or the log can tell "the
    whole file graded" from "some entries vanished quietly".

    THIS FUNCTION DELIBERATELY DOES NOT ALSO WRITE `ingest_unresolved_onc_endpoint`.
    That table's PRIMARY KEY is `(ingest_run, source, entry_id, endpoint_role)`
    (db/031) -- an `ingest_run` row is not optional context, it is part of the key --
    and `curate_onchigh` never opens one: db/029's own docstring on
    `curated_interaction` is explicit that "a human curator's assertion has no
    ingest run at all". Manufacturing a fake ingest_run here purely to satisfy that
    key would misrepresent this command as an ingest, and `ingest onchigh` already
    owns that worklist (gap kind fifteen, `unresolved_onc_endpoint`) -- writing to it
    a second time from a command with no run of its own would either need to borrow
    someone else's `ingest_run_id` (attributing the finding to the wrong run) or
    invent one (a run that ingested nothing). `entries_unresolved` plus the log line
    is therefore the ONLY signal this command gives; the durable, queryable worklist
    entry is `ingest onchigh`'s job, run separately.

    ONE TARGET PER RESOLVED SALT FORM ON THE MOIETY GRAIN, EXACTLY ONE TARGET ON THE
    CLASS GRAIN: `resolve_entry` already expands a moiety subject to every gated-in
    salt form before this function ever sees it (see `ResolvedEndpoint`'s own
    docstring), so one moiety-subject entry with two salt forms writes up to two
    `curated_interaction` rows, one per (salt form, class, axis) natural key. A
    class-subject entry (`ResolvedClassEndpoint`, Task 10) is never expanded --
    design spec section 14.3, a class has no salt forms -- so it writes AT MOST ONE
    `curated_class_interaction` row.

    THE COMPARISON THAT MAKES THIS IDEMPOTENT, ON EITHER GRAIN: `curation.
    live_interaction_judgement` / `curation.live_class_interaction_judgement` is read
    for each natural key BEFORE any write, and `_grade` (above) turns that read plus
    the file's judgement into one of three outcomes -- see its own docstring. No live
    row -> written. A live row that already matches -> unchanged, nothing written. A
    live row that differs -> superseded (INSERT the new row, then point the old one
    at it) rather than mutated -- both curated tables refuse UPDATE outright.
    """
    path = pathlib.Path(path)
    entries = onchigh.parse(path)

    entries_resolved = entries_unresolved = 0
    outcomes = {"written": 0, "superseded": 0, "unchanged": 0}
    # Every curated natural key this run has already claimed -> the entry_id that
    # claimed it. See CollidingRuleError: without this, the SECOND entry on a key
    # reads the FIRST's freshly-written row as `live` and supersedes it, so the
    # file's two claims collapse silently into one and the run never converges.
    # Keyed by grain as well as by UUIDs, because a moiety subject and a class
    # subject write to different tables and so cannot collide with one another.
    claimed: dict[tuple, str] = {}

    def _claim(key: tuple, entry_id: str) -> None:
        """Record that `entry_id` owns `key`, or raise if something else does."""
        if key in claimed:
            raise CollidingRuleError(
                f"entries {claimed[key]!r} and {entry_id!r} resolve to the same "
                f"curated rule (subject, object, relationship) -- one clinical "
                f"fact stated twice, with two gradings. Reconcile them in the "
                f"file; neither has been written.")
        claimed[key] = entry_id

    for entry in entries:
        resolved = onchigh_run.resolve_entry(conn, entry)
        judgement = entry.judgement

        if isinstance(resolved, onchigh_run.ResolvedClassEndpoint):
            entries_resolved += 1
            _claim(("class", resolved.subject_class_uuid,
                    resolved.object_class_uuid, resolved.axis), entry.entry_id)
            live = curation.live_class_interaction_judgement(
                conn, resolved.subject_class_uuid, resolved.object_class_uuid,
                resolved.axis)

            def _write_class_judgement() -> int:
                return curation.record_class_interaction_judgement(
                    conn, resolved.subject_class_uuid, resolved.object_class_uuid,
                    resolved.axis, judgement.applies, severity=judgement.severity,
                    mechanism=judgement.mechanism, management=judgement.management,
                    evidence_grade=judgement.evidence_grade, reviewed_by=reviewed_by,
                    reviewed_against=reviewed_against)

            outcomes[_grade(live, judgement, _write_class_judgement)] += 1

        elif isinstance(resolved, onchigh_run.ResolvedEndpoint):
            entries_resolved += 1
            for subject_moiety_uuid in resolved.subject_moiety_uuids:
                # Per SALT FORM, not per entry: two entries naming different
                # UNIIs of one drug (warfarin and warfarin sodium) expand onto
                # a shared form, so the collision appears here rather than at
                # the entry's own subject.
                _claim(("moiety", subject_moiety_uuid,
                        resolved.object_class_uuid, resolved.axis),
                       entry.entry_id)
                live = curation.live_interaction_judgement(
                    conn, subject_moiety_uuid, resolved.object_class_uuid,
                    resolved.axis)

                def _write_judgement(smu=subject_moiety_uuid) -> int:
                    # Default-arg capture, not a closure over the loop variable
                    # directly: every lambda/def created inside a for-loop
                    # shares the SAME cell for `subject_moiety_uuid` unless the
                    # current value is bound as a default at definition time,
                    # so without this every salt form's write would silently
                    # use whichever UUID the loop landed on LAST.
                    return curation.record_interaction_judgement(
                        conn, smu, resolved.object_class_uuid, resolved.axis,
                        judgement.applies, severity=judgement.severity,
                        mechanism=judgement.mechanism,
                        management=judgement.management,
                        evidence_grade=judgement.evidence_grade,
                        reviewed_by=reviewed_by, reviewed_against=reviewed_against)

                outcomes[_grade(live, judgement, _write_judgement)] += 1

        else:
            # A well-formed identifier drugref does not (yet) hold is a coverage
            # gap -- not a bug in the file (see `resolve_entry`'s own docstring) --
            # so this is a WARNING an operator can act on, not a silent drop.
            # Grading a rule with no resolvable candidate would write a judgement
            # `curated_ddi_pair` can never join to a pair.
            entries_unresolved += 1
            log.warning(
                "curate onchigh: entry %r did not resolve (%d of 2 endpoints) -- "
                "no judgement written; run `drugref ingest onchigh` to record it on "
                "the coverage-gap worklist", entry.entry_id, len(resolved))

    return CurateSummary(
        rules_seen=len(entries), entries_resolved=entries_resolved,
        entries_unresolved=entries_unresolved,
        judgements_written=outcomes["written"],
        judgements_superseded=outcomes["superseded"],
        unchanged=outcomes["unchanged"])


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
    absorb. SINCE db/035 AN ILLEGAL `severity` RAISES ForeignKeyViolation RATHER THAN
    CheckViolation (the four levels became drugref.severity_kind, so #97's precedence
    could order them) -- "a different exception class naming the identical hazard",
    cli_signing.py's phrase for the same substitution one column over. Nothing catches
    either class on this path, so the operator-visible behaviour is unchanged; the
    reasoning above holds for both and the absent catch stays absent.
    `--reviewed-by`/`--reviewed-against` ARE operator-typed, which is exactly
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
