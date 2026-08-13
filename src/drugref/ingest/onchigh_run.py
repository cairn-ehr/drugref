# src/drugref/ingest/onchigh_run.py
"""Orchestrator for the ONC high-priority DDI list (slice 5c.2) -- WRITE HALF.

Task 3's onchigh.py is a pure, DB-free parser: it turns the TOML file into
frozen dataclasses but never looks anything up. `onchigh_resolve.py` (split
out of this module by Task 10 -- see its own docstring for why the seam sits
where it does) is where those raw identifiers meet the registry:
`resolve_entry` turns one OncEntry into drugref UUIDs, dispatching between a
moiety subject (Tasks 1-8) and a class subject (Task 10, design spec section
14). This module RE-EXPORTS that resolution API below, so every existing
caller and test that already spells it `onchigh_run.resolve_entry`,
`onchigh_run.ResolvedEndpoint`, `onchigh_run.EndpointMismatchError` and so on
keeps working unchanged -- the split moved the implementation, not this
module's public surface.

THE WRITE HALF -- `ingest_onchigh`, at the bottom of this module -- opens the
`ingest_run`, rebuilds `class_contraindication` for source ONCHIGH (the
moiety-subject grain) and `class_pair_contraindication` for the same source
(the class-subject grain, Task 10, db/032), records unresolved endpoints into
`ingest_unresolved_onc_endpoint`, and re-derives the open-question register.
It is a separate module from resolution, not merely a separate section:
`resolve_entry` and `subject_forms` open no transaction and write no row on
their own, and `ingest_onchigh` is their only caller here -- slice 5c.2 splits
ingest into resolution and write as two independently testable steps, one
level earlier than pbs_run/medrt_run's own read/write split usually falls.
"""
import logging
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import db, interactions, provenance, questions
from drugref.ingest import onchigh
from drugref.ingest.checksum import checksum
from drugref.ingest.onchigh_resolve import (  # noqa: F401 -- re-exported, see module docstring
    OBJECT_SCHEME,
    SUBJECT_SCHEME,
    EndpointMismatchError,
    ResolvedClassEndpoint,
    ResolvedEndpoint,
    UnresolvedEndpoint,
    resolve_entry,
    subject_forms,
)

log = logging.getLogger(__name__)


# ============================================================================
# THE WRITE HALF (Task 5, widened by Task 10 for the class-subject grain)
# ============================================================================
SOURCE = "ONCHIGH"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). Declared in provenance.WRITERS and ingest_run's already-widened
# `writer` CHECK (db/031) -- a pair. db/031 built the schema half of that pair
# (it is schema-only, per its own docstring); this task is the first to
# actually CALL provenance.open_run(writer=WRITER), so it is where the
# Python-side half gets completed.
WRITER = "onchigh_run"

# The worklist table this orchestrator owns BEYOND class_contraindication and
# class_pair_contraindication. interactions.clear_source_contraindications /
# clear_source_class_pair_contraindications already own those two -- db/006
# put `source` into class_contraindication's own primary key (and db/032
# mirrors it on class_pair_contraindication) precisely so a second authority
# could share the table safely, and the candidate tier needs no new
# contraindication writer for either. ingest_unresolved_onc_endpoint is
# different: it is ONCHIGH's OWN worklist (db/031), so clearing and writing it
# is this module's job, not interactions.py's.
#
# Restated as its own module constant, mirroring every other writer's
# declared-ownership convention (classes.CLASS_EDGE_TABLES,
# local.LOCAL_PRODUCT_TABLES, interactions.MESH_CONTRAINDICATION_TABLES, ...)
# so test_source_clear_contract.py can pin it independently of this module's
# own code: dropping a table from a writer's tuple with nothing failing is
# precisely the defect that module's docstring exists to catch, and here the
# consequence is concrete -- a stale unresolved-endpoint row would keep
# answering a question the file no longer asks, exactly as a stale
# class_contraindication row would keep asserting a rule the file no longer
# makes.
UNRESOLVED_ENDPOINT_TABLES = ("ingest_unresolved_onc_endpoint",)


@dataclass(frozen=True)
class OncSummary:
    """What one ONC high-priority ingest did (design spec section 11) --
    returned so a caller (or test) can assert on it, mirroring every other
    orchestrator's summary dataclass.

    TWO GRAINS' WORTH OF CANDIDATE ROWS, COUNTED SEPARATELY, on the same
    reasoning `salt_forms_expanded` was already kept apart from `rules_
    written`: `salt_forms_expanded`/`rules_written` describe the MOIETY-
    subject grain, where one file entry can become several
    `class_contraindication` rows; `class_rules_written` (Task 10) describes
    the CLASS-subject grain, where one file entry becomes AT MOST ONE
    `class_pair_contraindication` row (design spec section 14.3 -- a class
    has no salt forms). Folding the two counts together would hide that
    difference behind a single number that means two different things
    depending on which grain contributed it.

    `rules_written` counts only MOIETY-grain rows that were actually NEW
    (`interactions.add_contraindication`'s ON CONFLICT DO NOTHING return
    value) -- it can be LOWER than `salt_forms_expanded` only when two salt
    forms of the same file collide on the same (subject, object,
    relationship, source) key within ONE run, since the clear step (below)
    already removed every row from any earlier run before this run writes a
    single one. `class_rules_written` is the equivalent count for the
    class-subject grain, via `interactions.add_class_pair_contraindication`.

    BOTH GRAINS CARRY AN ATTEMPTED COUNT AS WELL AS A WRITTEN ONE, so both can
    be reconciled the same way. On the moiety grain that pair has always been
    `salt_forms_expanded` (attempted) against `rules_written`; the gap between
    them IS the number of rows that folded together on one key. The class grain
    shipped with only `class_rules_written`, which stays un-incremented when
    add_class_pair_contraindication hits ON CONFLICT DO NOTHING -- so nine class
    entries producing seven rows looked exactly like seven class entries, and
    nothing in the summary could tell an operator which had happened.
    `class_rules_attempted` closes that: attempted - written is the collision
    count, on either grain, read the same way.
    """
    entries_read: int
    rules_written: int
    salt_forms_expanded: int
    class_rules_attempted: int
    class_rules_written: int
    endpoints_unresolved: int


def _record_unresolved(conn: psycopg.Connection, run_id: int,
                       endpoints: list[UnresolvedEndpoint]) -> None:
    """Persist every UnresolvedEndpoint this run found, in one batch.

    Batched via executemany rather than a per-endpoint execute(), mirroring
    interactions.record_unresolved_ci_objects: nobody needs the per-row
    insert-vs-conflict answer, only the rows landing.

    ON CONFLICT DO NOTHING (against the table's PK -- ingest_run, source,
    entry_id, endpoint_role) is belt and braces here, not a needed safeguard:
    resolve_entry reports at most one UnresolvedEndpoint per (entry, role)
    already, and onchigh.parse refuses a duplicate entry_id before any of
    this runs, so a conflict would mean this function was handed the same
    endpoint twice -- a caller bug, not a real collision between two upstream
    facts (contrast db/016's object worklist, where two DIFFERENT source
    releases can legitimately name the same object).
    """
    if not endpoints:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO drugref.ingest_unresolved_onc_endpoint "
            "(ingest_run, source, entry_id, endpoint_role, identifier_scheme, "
            " identifier_value, endpoint_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(run_id, SOURCE, e.entry_id, e.endpoint_role, e.identifier_scheme,
              e.identifier_value, e.endpoint_name) for e in endpoints])


def ingest_onchigh(conn: psycopg.Connection, *, path: pathlib.Path,
                   upstream_release: str) -> OncSummary:
    """Ingest one ONC high-priority DDI file. Owns the transaction end to end.

    ORDER, mirroring pbs_run's convention rather than mesh_run's/gsrs_run's
    (task-5 brief, step 3):

      1. Checksum the file, then open the provenance row -- WHICH COMMITS
         (provenance.open_run's own docstring), so a crash anywhere after this
         point still leaves a traceable run with `finished_at IS NULL`
         (ingest_run_incomplete reports it). Opened BEFORE parsing, unlike
         mesh_run/gsrs_run (whose releases are large enough that protecting a
         long parse window from leaving no trace at all is worth it) -- this
         file is a small, hand-curated list, so there is no comparable window
         to protect, and pbs_run's precedent (open first, read after) is the
         closer match.
      2. Parse the file. A structurally malformed file (OncFormatError) is
         raised here, AFTER the run row exists: a curator sees the run and
         its unfinished state, rather than the failure leaving no trace.
      3. CLEAR this source's previous projection -- EVERY table a re-ingest
         must replace rather than accumulate: `class_contraindication` (via
         interactions.clear_source_contraindications, the moiety-subject
         grain), `class_pair_contraindication` (via interactions.
         clear_source_class_pair_contraindications, Task 10's class-subject
         grain -- db/032), both scoped to SOURCE so MED-RT's rows are
         untouched, and `ingest_unresolved_onc_endpoint` (this module's own
         worklist, via UNRESOLVED_ENDPOINT_TABLES above). Clearing the
         worklist in this SAME step, alongside the candidate rows, is
         deliberate (task-5 brief): a stale unresolved-endpoint row would
         keep answering a question the file no longer asks, exactly as a
         stale class_contraindication row would keep asserting a rule the
         file no longer makes.
      4. Per entry, resolve_entry returns one of THREE shapes (Task 10 added
         the third): a ResolvedEndpoint (one class_contraindication row per
         resolved salt form, via subject_forms already expanded onto it), a
         ResolvedClassEndpoint (exactly one class_pair_contraindication row,
         no salt expansion -- design spec section 14.3), or a
         list[UnresolvedEndpoint] (queued and written once, batched, after
         the loop).
      5. Re-derive the open-question register (Plan A) -- LAST, because it
         reads the very tables this run just rebuilt in steps 3-4, and
         reading them mid-rebuild would close, then reopen, every question
         they feed.
      6. Stamp the run finished and commit, atomically with everything step 4
         wrote.

    TRANSACTION OWNERSHIP: TWO transactions on one connection, exactly as
    pbs_run/mesh_run document. provenance.open_run commits the run record
    before the writes, so a crash during them leaves it standing with
    `finished_at` NULL; everything from there on is the work, which this
    function owns, commits on success, and rolls back before re-raising. A
    caller with pending work of its own must commit it before calling --
    provenance.open_run's early commit will otherwise sweep that work along
    with the run record.

    A NAME/IDENTIFIER MISMATCH (EndpointMismatchError) aborts the WHOLE
    ingest, not just the one entry -- it propagates out of the loop below like
    any other exception, is caught by the `except` clause, rolls back, and
    re-raises. resolve_entry's own docstring is where the reasoning for that
    lives: a mismatch is a bug in the hand-authored file, and continuing past
    it would mean this run wrote (or silently skipped) a row nobody actually
    approved. Applies identically whether the mismatch is on a moiety or a
    class subject (Task 10).
    """
    path = pathlib.Path(path)
    try:
        run_id = provenance.open_run(
            conn, source=SOURCE, upstream_release=upstream_release,
            source_checksum=checksum(path), writer=WRITER)

        entries = onchigh.parse(path)

        interactions.clear_source_contraindications(conn, SOURCE)
        interactions.clear_source_class_pair_contraindications(conn, SOURCE)
        db.clear_source_tables(conn, UNRESOLVED_ENDPOINT_TABLES, SOURCE)

        rules_written = salt_forms_expanded = endpoints_unresolved = 0
        class_rules_attempted = class_rules_written = 0
        unresolved_batch: list[UnresolvedEndpoint] = []

        for entry in entries:
            result = resolve_entry(conn, entry)
            if isinstance(result, ResolvedEndpoint):
                for moiety_uuid in result.subject_moiety_uuids:
                    salt_forms_expanded += 1
                    if interactions.add_contraindication(
                            conn, moiety_uuid, result.object_class_uuid,
                            result.axis, SOURCE, run_id):
                        rules_written += 1
            elif isinstance(result, ResolvedClassEndpoint):
                class_rules_attempted += 1
                if interactions.add_class_pair_contraindication(
                        conn, result.subject_class_uuid, result.object_class_uuid,
                        result.axis, SOURCE, run_id):
                    class_rules_written += 1
            else:
                # WARNED, not merely counted -- parity with `curate onchigh`,
                # which already names the entry. This is the half that owns the
                # durable worklist and the half where fewer rows is the harm
                # direction, so an operator scanning a chain run for WARNINGs
                # must not see a clean run while entries failed to bridge. The
                # closing log.info's total says how many; this says WHICH.
                log.warning(
                    "onchigh: entry %r has %d unresolved endpoint(s) (%s) -- "
                    "it cannot be projected and is on the worklist instead",
                    entry.entry_id, len(result),
                    ", ".join(e.endpoint_role for e in result))
                endpoints_unresolved += len(result)
                unresolved_batch.extend(result)

        _record_unresolved(conn, run_id, unresolved_batch)

        # Re-derive the open-question register (Plan A), last and for the
        # same reason mesh_run/gsrs_run do: this run rewrote
        # class_contraindication, class_pair_contraindication and
        # ingest_unresolved_onc_endpoint, all of which gap views read, so
        # every currently-open gap (not only this run's own gap kind) is
        # refreshed here.
        questions.register_from_gaps(conn, run_id)

        provenance.finish_run(conn, run_id)
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("ONC high-priority ingest failed for release %s; rolled back",
                      upstream_release)
        raise

    summary = OncSummary(entries_read=len(entries), rules_written=rules_written,
                         salt_forms_expanded=salt_forms_expanded,
                         class_rules_attempted=class_rules_attempted,
                         class_rules_written=class_rules_written,
                         endpoints_unresolved=endpoints_unresolved)
    log.info("ONC high-priority ingest %s complete: %s", upstream_release, summary)
    return summary
