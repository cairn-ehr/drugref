# src/drugref/ingest/onchigh_run.py
"""Orchestrator for the ONC high-priority DDI list (slice 5c.2) -- RESOLUTION HALF.

Task 3's onchigh.py is a pure, DB-free parser: it turns the TOML file into
frozen dataclasses but never looks anything up. This module is where those
raw identifiers meet the registry: `resolve_entry` turns one OncEntry's
`subject_unii` / `object_medrt_code` into a `moiety_uuid` / `class_uuid` pair
(or reports that one or both could not be found), and `subject_forms` expands
a resolved subject to every salt form of it drugref actually holds.

THE WRITE HALF -- rebuilding `class_contraindication` rows, opening the
`ingest_run`, recording unresolved endpoints into
`ingest_unresolved_onc_endpoint` -- is a later task and is deliberately not
built here. This module opens no transaction and writes no row; it only
reads, mirroring the read side of every other orchestrator's split (pbs_run,
medrt_run) one level earlier than usual, because slice 5c.2 splits ingest
into resolution and write as two separate, independently testable steps.

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
import uuid
from dataclasses import dataclass

import psycopg

from drugref import ids
from drugref.ingest import onchigh

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
