# src/drugref/ingest/onchigh_run.py
"""Orchestrator for the ONC high-priority DDI list (slice 5c.2) -- RESOLUTION HALF.

Task 3's onchigh.py is a pure, DB-free parser: it turns the TOML file into
frozen dataclasses but never looks anything up. This module is where those
raw identifiers meet the registry: `resolve_entry` turns one OncEntry's
`subject_unii` / `object_medrt_code` into a `moiety_uuid` / `class_uuid` pair
(or reports that one or both could not be found), and `subject_forms` expands
a resolved subject to every salt form of it drugref actually holds.

THE WRITE HALF -- `ingest_onchigh`, at the bottom of this module -- opens the
`ingest_run`, rebuilds `class_contraindication` for source ONCHIGH, records
unresolved endpoints into `ingest_unresolved_onc_endpoint`, and re-derives the
open-question register. It is a separate function from the resolution
functions above, not merely a separate section: `resolve_entry` and
`subject_forms` (Task 4) open no transaction and write no row on their own,
and `ingest_onchigh` (Task 5) is their only caller in this module -- slice
5c.2 splits ingest into resolution and write as two independently testable
steps, one level earlier than pbs_run/medrt_run's own read/write split
usually falls.

WHY SALT EXPANSION LIVES HERE, ON THE PROJECTION SIDE, AND NOT AT READ TIME
OR IN THE CURATED ROWS THEMSELVES (design spec §6, three reasons, in order of
weight):

1. Issue 68 measured that ~19% of moieties (3,631) carry a questionable GSRS
   `ACTIVE MOIETY` edge. Inheriting clinical *advice* along that population at
   READ time -- joining through the composition tree on every
   `curated_ddi_pair` lookup -- would spread advice across a suspect edge set,
   and that is the wrong first use of the composition tree to make.
2. A REBUILDABLE PROJECTION RE-DERIVES. Doing the expansion here means a salt
   form arriving in a later GSRS release becomes a visible ungraded candidate
   in `gap_uncurated_interaction_rule` on the next rebuild. Baking the same
   expansion into the *curated* rows instead would make a newly-arrived salt
   form a silent hole forever, because curated rows are append-only and
   immortal -- nothing ever re-derives them.
3. It costs the ~1.4 ms `curated_ddi_pair` read hot path nothing: the
   expansion happens once, here, during ingest, not on every read.

The cost of doing it this way is real and is meant to be visible rather than
hidden: one file entry becomes several `class_contraindication` rows (one per
resolved salt form), all written from the one entry by the one orchestrator
run, so they can never disagree with each other -- but the row count grows,
and the write-half task must report it (design spec §11).
"""
import logging
import pathlib
import uuid
from dataclasses import dataclass

import psycopg

from drugref import db, ids, interactions, provenance, questions
from drugref.ingest import onchigh
from drugref.ingest.checksum import checksum

log = logging.getLogger(__name__)

# The `identifier_scheme` values recorded when an endpoint fails to resolve
# (and, symmetrically, describes what each endpoint's identifier IS in the
# first place). Not a vocabulary the database enforces with a CHECK -- unlike
# severity/evidence_grade/relationship, `ingest_unresolved_onc_endpoint.
# identifier_scheme` is free text (db/031), a descriptive label rather than a
# closed set -- so keeping the two spellings as named constants here is only
# about not hand-typing the same literal at every call site, not a second copy
# of a CHECK.
#
# SUBJECT_SCHEME mirrors `identity_claim.scheme` exactly ('UNII').
#
# OBJECT_SCHEME MUST SPELL THE AUTHORITY THE WAY THE REST OF THE REPO SPELLS
# IT -- 'MED-RT', never the hyphen-less 'MEDRT' the OncCandidate field name
# (`object_medrt_code`) might tempt a reader to copy. `ids.canonical_source`
# exists precisely because this project already lost a round to spelling
# drift: three spellings of one source minted one class_uuid but were stored
# under three different strings, and a per-source rebuild silently missed
# rows it owned (ids.py's own docstring). Getting this wrong here is worse
# than that: Task 5's `_GAP_SOURCES` entry builds an unresolved endpoint's
# `gap_key` as `'ONCHIGH:' || entry_id || ':' || identifier_scheme || ':' ||
# identifier_value`, and `question_uuid = uuid5(gap_kind, gap_key)` is
# immortal and cited by external tooling -- so 'MEDRT' would not just be an
# inconsistent label, it would be PERMANENTLY BAKED into a question a curator
# might already be tracking. Do not "tidy" this back to match the Python
# field name; the value must match `substance_class.source` and
# `ids._SOURCE_CANONICAL`'s canonical spelling instead.
SUBJECT_SCHEME = "UNII"
OBJECT_SCHEME = "MED-RT"


class EndpointMismatchError(ValueError):
    """The file's human-readable `name` disagrees with what its identifier
    resolves to in drugref.

    ALWAYS a bug in the hand-authored file, never a coverage gap -- contrast
    with an endpoint that resolves to nothing at all, which is reported as an
    UnresolvedEndpoint instead of raising (see resolve_entry's docstring).
    The name field exists only so a human reviewing a diff of the TOML file
    can tell what an identifier means; if the name and the identifier point at
    two different substances, the reviewer approved a different substance
    from the one that would land, and that must stop the whole ingest rather
    than pass silently.
    """


@dataclass(frozen=True)
class ResolvedEndpoint:
    """One ONC entry, fully resolved: a candidate `class_contraindication` row
    per subject salt form, sharing one object class and one axis.

    `subject_moiety_uuids` is ALREADY salt-expanded (via subject_forms) by the
    time resolve_entry returns it -- the base moiety is always a member of the
    tuple, so a caller writing one candidate row per element never needs to
    call subject_forms again.
    """
    entry_id: str
    subject_moiety_uuids: tuple[uuid.UUID, ...]
    object_class_uuid: uuid.UUID
    axis: str


@dataclass(frozen=True)
class UnresolvedEndpoint:
    """One endpoint (subject OR object) of one ONC entry that named a
    well-formed identifier drugref does not currently hold.

    This is a COVERAGE GAP, not a bug (issue 71's lesson: a dropped row
    counted only into a transient integer is a number nobody can act on) --
    the write-half task turns a list of these into rows in
    `ingest_unresolved_onc_endpoint`, which is what feeds gap kind fifteen,
    `unresolved_onc_endpoint`.
    """
    entry_id: str
    endpoint_role: str          # 'subject' | 'object' -- matches the DB CHECK
    identifier_scheme: str      # SUBJECT_SCHEME | OBJECT_SCHEME above
    identifier_value: str
    endpoint_name: str


def _resolve_subject(conn: psycopg.Connection,
                     unii: str) -> tuple[uuid.UUID, str] | None:
    """Look up a live UNII claim's moiety, or None if drugref holds none.

    `identity_claim` is append-only and claims can be superseded (a
    correction), so only a LIVE claim (`superseded_by IS NULL`) may resolve --
    an old, corrected UNII value must not still answer. Joined to
    `substance_moiety` for its `display_name`, which the caller compares
    against the file's `subject_name` review aid.
    """
    row = conn.execute(
        "SELECT sm.moiety_uuid, sm.display_name "
        "FROM drugref.identity_claim ic "
        "JOIN drugref.substance_moiety sm ON sm.moiety_uuid = ic.moiety_uuid "
        "WHERE ic.scheme = 'UNII' AND ic.superseded_by IS NULL "
        "AND ic.value = %s",
        (ids.canonical_claim_value("UNII", unii),)).fetchone()
    return (row[0], row[1]) if row is not None else None


def _resolve_object(conn: psycopg.Connection,
                    medrt_code: str) -> tuple[uuid.UUID, str] | None:
    """Look up a MED-RT class by its NUI (`source_code`), or None if absent.

    The ONC list names its drug classes by MED-RT concept code exclusively
    (design spec §4) -- never MeSH -- so `source = 'MED-RT'` is fixed here
    rather than taken as a parameter: `db/031` deliberately widens no other
    vocabulary for this slice.
    """
    row = conn.execute(
        "SELECT class_uuid, class_name FROM drugref.substance_class "
        "WHERE source = 'MED-RT' AND source_code = %s",
        (medrt_code.strip(),)).fetchone()
    return (row[0], row[1]) if row is not None else None


def _check_name_match(entry_id: str, role: str, identifier: str,
                      file_name: str, resolved_name: str) -> None:
    """Raise EndpointMismatchError unless `file_name` and `resolved_name` name
    the same thing, compared case-insensitively and whitespace-normalised.

    `ids.normalise_name` is the one fold already used everywhere two
    independently-produced human-readable strings must be compared (its own
    docstring: the INN claim and PBS drug names) -- reused here rather than
    inventing a second ad-hoc `.lower().strip()` that could fold differently.
    """
    if ids.normalise_name(file_name) != ids.normalise_name(resolved_name):
        raise EndpointMismatchError(
            f"entry {entry_id!r}: {role} identifier {identifier!r} resolves "
            f"to {resolved_name!r} in drugref, but the file names it "
            f"{file_name!r} -- the name field is a review aid a human reads "
            "in the diff while the database reads the identifier; a "
            "mismatch means the reviewer approved a different substance "
            "from the one that would land")


def subject_forms(conn: psycopg.Connection,
                  base_moiety_uuid: uuid.UUID) -> tuple[uuid.UUID, ...]:
    """The base moiety plus every GATED-IN moiety the composition tree marks
    as carrying it as an active component -- a deterministic (sorted) tuple.

    Reads `moiety_active_in_composite` (slice 3's view over
    `substance_composition WHERE is_active_component IS TRUE`) rather than
    the base table, per the task brief -- it already states the predicate
    this function needs.

    ONLY GATED-IN MOIETIES ARE ADMITTED. A composition edge's `substance_unii`
    is a raw UNII from the GSRS release, not a drugref identity (db/028's own
    comment: 4,425 of 7,377 composites are not moieties) -- so a salt form
    that exists as a composition edge but was refused by drugref's moiety gate
    must not be returned here: `class_contraindication`'s foreign key would
    reject it if it were written, and reaching the FK is the wrong place to
    find out. The INNER JOIN to `identity_claim` (itself only ever populated
    for a moiety that already exists, by that table's own FK) is what makes
    this filter -- the second join to `substance_moiety` restates the same
    guarantee explicitly rather than relying on the FK alone, so the query
    reads as the invariant it enforces.

    Sorted rather than returned in whatever order Postgres happens to emit:
    a nondeterministic order would make a diff of the generated candidate
    rows unreadable (design spec §6 / task brief).
    """
    rows = conn.execute(
        "SELECT sm.moiety_uuid "
        "FROM drugref.moiety_active_in_composite mac "
        "JOIN drugref.identity_claim ic "
        "  ON ic.scheme = 'UNII' AND ic.superseded_by IS NULL "
        "  AND ic.value = mac.substance_unii "
        "JOIN drugref.substance_moiety sm ON sm.moiety_uuid = ic.moiety_uuid "
        "WHERE mac.moiety_uuid = %s",
        (base_moiety_uuid,)).fetchall()
    forms = {base_moiety_uuid, *(row[0] for row in rows)}
    return tuple(sorted(forms))


def resolve_entry(
        conn: psycopg.Connection,
        entry: onchigh.OncEntry) -> ResolvedEndpoint | list[UnresolvedEndpoint]:
    """Resolve one OncEntry's subject and object to drugref UUIDs.

    TWO FAILURE MODES, DELIBERATELY TREATED DIFFERENTLY -- the heart of this
    task (design spec §7, issue 71's lesson):

    * A NAME that disagrees with its identifier RAISES EndpointMismatchError.
      The file is hand-authored, so a mismatch is a bug in it, and the right
      response is to stop the whole ingest rather than silently write (or
      silently drop) a row a reviewer never actually approved.
    * A well-formed identifier naming a substance or class drugref does not
      hold is a COVERAGE GAP, not a bug. It is RETURNED AS DATA -- a
      `list[UnresolvedEndpoint]`, one entry per endpoint role that failed to
      resolve (up to two: subject and object fail independently) -- never
      raised. The write-half task turns this list into rows the worklist can
      show a curator, rather than a count nobody can act on.

    Returns a single `ResolvedEndpoint` (not a one-element list) when BOTH
    endpoints resolve, so a caller can tell "fully resolved" from "partially
    resolved" by the return type alone without inspecting a list's length.
    The subject is already expanded to every salt form via `subject_forms`
    before this returns.
    """
    candidate = entry.candidate
    subject = _resolve_subject(conn, candidate.subject_unii)
    obj = _resolve_object(conn, candidate.object_medrt_code)

    unresolved: list[UnresolvedEndpoint] = []

    if subject is None:
        unresolved.append(UnresolvedEndpoint(
            entry_id=entry.entry_id, endpoint_role="subject",
            identifier_scheme=SUBJECT_SCHEME,
            identifier_value=candidate.subject_unii,
            endpoint_name=candidate.subject_name))
    else:
        _check_name_match(entry.entry_id, "subject", candidate.subject_unii,
                          candidate.subject_name, subject[1])

    if obj is None:
        unresolved.append(UnresolvedEndpoint(
            entry_id=entry.entry_id, endpoint_role="object",
            identifier_scheme=OBJECT_SCHEME,
            identifier_value=candidate.object_medrt_code,
            endpoint_name=candidate.object_name))
    else:
        _check_name_match(entry.entry_id, "object", candidate.object_medrt_code,
                          candidate.object_name, obj[1])

    if unresolved:
        return unresolved

    return ResolvedEndpoint(
        entry_id=entry.entry_id,
        subject_moiety_uuids=subject_forms(conn, subject[0]),
        object_class_uuid=obj[0],
        axis=candidate.axis)


# ============================================================================
# THE WRITE HALF (Task 5)
# ============================================================================
SOURCE = "ONCHIGH"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). Declared in provenance.WRITERS and ingest_run's already-widened
# `writer` CHECK (db/031) -- a pair. db/031 built the schema half of that pair
# (it is schema-only, per its own docstring); this task is the first to
# actually CALL provenance.open_run(writer=WRITER), so it is where the
# Python-side half gets completed.
WRITER = "onchigh_run"

# The worklist table this orchestrator owns BEYOND class_contraindication.
# interactions.clear_source_contraindications already owns that one for any
# source -- db/006 put `source` into class_contraindication's own primary key
# precisely so a second authority could share the table safely, and the
# candidate tier needs no new contraindication writer for it (task-5 brief).
# ingest_unresolved_onc_endpoint is different: it is ONCHIGH's OWN worklist
# (db/031), so clearing and writing it is this module's job, not
# interactions.py's -- interactions.py's docstring lists the four tables IT
# writes, and this is not one of them.
#
# Restated as its own module constant, mirroring every other writer's
# declared-ownership convention (classes.CLASS_EDGE_TABLES,
# local.LOCAL_PRODUCT_TABLES, interactions.MESH_CONTRAINDICATION_TABLES, ...)
# so test_source_clear_contract.py can pin it independently of this module's
# own code: dropping a table from a writer's tuple with nothing failing is
# precisely the defect that module's docstring exists to catch, and here the
# consequence is concrete and was named directly by the task brief -- a stale
# unresolved-endpoint row would keep answering a question the file no longer
# asks, exactly as a stale class_contraindication row would keep asserting a
# rule the file no longer makes.
UNRESOLVED_ENDPOINT_TABLES = ("ingest_unresolved_onc_endpoint",)


@dataclass(frozen=True)
class OncSummary:
    """What one ONC high-priority ingest did (design spec §11) -- returned so
    a caller (or test) can assert on it, mirroring every other orchestrator's
    summary dataclass.

    `salt_forms_expanded` is the number the design's cost trade-off (this
    module's own top-of-file docstring, point 2) obliges this task to report:
    one file entry becomes several `class_contraindication` rows, one per
    resolved subject salt form, so the row count genuinely grows on ingest,
    and that growth must be visible rather than folded silently into a single
    opaque "rules written" figure.

    `rules_written` counts only rows that were actually NEW
    (`interactions.add_contraindication`'s ON CONFLICT DO NOTHING return
    value) -- it can be LOWER than `salt_forms_expanded` only when two salt
    forms of the same file collide on the same (subject, object, relationship,
    source) key within ONE run, since the clear step (below) already removed
    every row from any earlier run before this run writes a single one.
    """
    entries_read: int
    rules_written: int
    salt_forms_expanded: int
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
      3. CLEAR this source's previous projection -- BOTH tables a re-ingest
         must replace rather than accumulate: `class_contraindication` (via
         interactions.clear_source_contraindications, scoped to SOURCE so
         MED-RT's rows are untouched) and `ingest_unresolved_onc_endpoint`
         (this module's own worklist, via UNRESOLVED_ENDPOINT_TABLES above).
         Clearing the worklist in this SAME step, alongside the candidate
         rows, is deliberate (task-5 brief): a stale unresolved-endpoint row
         would keep answering a question the file no longer asks, exactly as
         a stale class_contraindication row would keep asserting a rule the
         file no longer makes.
      4. Per entry, resolve_entry returns either a ResolvedEndpoint (one
         class_contraindication row per resolved salt form, via
         subject_forms already expanded onto it -- see ResolvedEndpoint's own
         docstring) or a list[UnresolvedEndpoint] (queued and written once,
         batched, after the loop).
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
    approved.
    """
    path = pathlib.Path(path)
    try:
        run_id = provenance.open_run(
            conn, source=SOURCE, upstream_release=upstream_release,
            source_checksum=checksum(path), writer=WRITER)

        entries = onchigh.parse(path)

        interactions.clear_source_contraindications(conn, SOURCE)
        db.clear_source_tables(conn, UNRESOLVED_ENDPOINT_TABLES, SOURCE)

        rules_written = salt_forms_expanded = endpoints_unresolved = 0
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
            else:
                endpoints_unresolved += len(result)
                unresolved_batch.extend(result)

        _record_unresolved(conn, run_id, unresolved_batch)

        # Re-derive the open-question register (Plan A), last and for the
        # same reason mesh_run/gsrs_run do: this run rewrote
        # class_contraindication and ingest_unresolved_onc_endpoint, both of
        # which gap views read, so every currently-open gap (not only this
        # run's own gap kind) is refreshed here.
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
                         endpoints_unresolved=endpoints_unresolved)
    log.info("ONC high-priority ingest %s complete: %s", upstream_release, summary)
    return summary
