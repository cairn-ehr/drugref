"""Orchestrate one MeSH PA ingest: parse -> upsert classes -> rebuild edges.

Mirrors medrt_run.py (open an ingest_run for provenance, do the work, stamp
finished_at, commit), with the one genuinely new piece being the MEMBERSHIP
BRIDGE. MED-RT joined to moieties through the RXNORM_IN claims slice 1 records;
MeSH PA has no RxCUI, so it joins through the UNII and CAS claims slice 1 also
records -- two-key, UNII-primary, CAS-fallback (spec §6). No new external source.

Order matters, exactly as for MED-RT:
  1. classes first, because every edge references a class row;
  2. then clear this source's old edges (scoped to 'MeSH', so a MeSH rebuild never
     touches MED-RT's edges), before any of this run's edges are written;
  3. then insert the DAG, then the memberships.

WORKLIST NUMBERS, NOT SILENT DROPS. A member that does not become a membership is
counted, split by CAUSE so the two stay legible (spec §5.3/§6, tension E):
  * members_no_key           -- the substance exposes neither UNII nor CAS (drug
                                combinations, novel research compounds): structurally
                                unjoinable, a MeSH-side property.
  * members_key_not_in_registry -- it has a key, but no gated-in moiety holds it
                                (the moiety gate is the binding constraint, §5.3).
Both are counted by DISTINCT member, since a member's keys are the same under every
PA class it belongs to. The class side has one refusal of its own, reported the same
way: a PA record naming no DescriptorUI (pa_records_without_descriptor, #17).
"""
import logging
import uuid
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref import provenance, questions
from drugref.classes import ClassConcept
from drugref.ingest import mesh
from drugref.ingest.checksum import checksum

SOURCE = "MeSH"
RELATIONSHIP = "has_PA"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). One source can have two writers -- MED-RT does -- so a release is only
# unambiguous per (source, writer).
WRITER = "mesh_run"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeshSummary:
    """What one MeSH run did -- returned so a caller (or test) can assert on it.

    As for MED-RT, classes ACCUMULATE while edges are REBUILT, so the two class
    numbers are reported separately rather than as one ambiguous count:

    * classes_in_release -- PA classes this release asserts (upserted, new or not)
    * classes_added      -- of those, the ones drugref had never seen before
    * parent_edges / memberships -- rows this run actually wrote

    The two worklist numbers (see the module docstring) are reported, never
    swallowed -- the slice-1/2a no-silent-exclude posture:

    * members_no_key               -- members exposing neither UNII nor CAS
    * members_key_not_in_registry  -- members whose key no gated moiety carries
    * pa_records_without_descriptor -- PA records naming no DescriptorUI, so there
      is no identity to key a class on. Zero against a well-formed release; the
      parser used to drop these invisibly, which is the last silent refusal #17
      names. It is a CLASS-side refusal, not a membership one, which is why it is
      not one of the two buckets above.
    """
    classes_in_release: int
    classes_added: int
    parent_edges: int
    memberships: int
    members_no_key: int
    members_key_not_in_registry: int
    pa_records_without_descriptor: int


def _resolve_moieties(keys: mesh.MemberKeys, unii_index, cas_index) -> list[uuid.UUID]:
    """Resolve a member's keys to moieties: UNII-primary, CAS-fallback (spec §6/§C).

    UNII is drugref's own identity key (the moiety UUID derives from it), so a UNII
    match is exact and is preferred. CAS is tried ONLY when no UNII resolved at all
    -- 'else any CAS', not 'also any CAS' -- which is why a member with a UNII that
    happens to be unregistered still falls through to its CAS. Keys are set-valued,
    so every key of the winning type is tried, and EVERY claimant is kept (a value
    may sit on more than one moiety). Order is deterministic (sorted keys, index
    order) so the ingest is reproducible.
    """
    matches: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in sorted(keys.unii):
        for moiety_uuid in unii_index.get(value, ()):
            if moiety_uuid not in seen:
                seen.add(moiety_uuid)
                matches.append(moiety_uuid)
    if matches:
        return matches                      # UNII-primary: a UNII match wins outright
    for value in sorted(keys.cas):
        for moiety_uuid in cas_index.get(value, ()):
            if moiety_uuid not in seen:
                seen.add(moiety_uuid)
                matches.append(moiety_uuid)
    return matches


def ingest_mesh(conn: psycopg.Connection, *, pa_path, desc_path, supp_path,
                upstream_release: str) -> MeshSummary:
    """Ingest one MeSH release (pa/desc/supp) into the PA classification axis.

    Idempotent: re-running rebuilds to the same state, with the same class UUIDs.

    TRANSACTION OWNERSHIP: TWO transactions on one connection. provenance.open_run
    commits the run record before the WRITES, so a crash during them leaves it standing
    with finished_at NULL (ingest_run_incomplete reports it); everything after it is
    the work, which this function owns, commits on success, and rolls back before
    re-raising. A caller with pending work has it committed at the provenance boundary,
    so callers must commit their own work before calling.

    "BEFORE THE WRITES" IS NOT "BEFORE THE COMMAND", and this orchestrator is one of
    the three where the gap is wide: the parse runs FIRST (it is pure and takes no
    connection), so a crash while parsing still leaves no row at all -- a view cannot
    report a run nobody opened. The orchestrators are not uniform in this, and
    ingest_run_incomplete's own comment says so.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    log.info("MeSH ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest_mesh(conn, pa_path, desc_path, supp_path,
                               upstream_release, clock)
    except Exception:
        conn.rollback()
        log.exception("MeSH ingest failed (release=%s); transaction rolled back",
                      upstream_release)
        raise
    log.info("MeSH ingest finished (release=%s): %s", upstream_release, summary)
    return summary


def _ingest_mesh(conn: psycopg.Connection, pa_path, desc_path, supp_path,
                 upstream_release: str,
                 clock: provenance.RunClock) -> MeshSummary:
    """The body of one MeSH ingest (see ingest_mesh for the transaction contract)."""
    parsed = mesh.parse(pa_path=pa_path, desc_path=desc_path, supp_path=supp_path)

    run_id = provenance.open_run(
        conn, source=SOURCE, upstream_release=upstream_release,
        source_checksum=checksum(pa_path, desc_path, supp_path), writer=WRITER,
        clock=clock)

    # 1. Classes. A PA class hands upsert_class the same source-neutral shape a
    #    MED-RT concept does; descriptor_ui is both its identity key and its
    #    published code (MeSH keys on the UI it publishes).
    uuid_by_ui: dict[str, uuid.UUID] = {}
    # By DISTINCT descriptor UI, for the reason medrt_run gives: a descriptor
    # repeated within one release would otherwise report is_new twice and make
    # classes_added exceed classes_in_release.
    new_uis: set[str] = set()
    for pa in parsed.classes:
        concept = ClassConcept(nui=pa.descriptor_ui, code=pa.descriptor_ui,
                               name=pa.name, concept_type=pa.concept_type)
        class_uuid, is_new = class_writer.upsert_class(conn, concept, run_id, SOURCE)
        uuid_by_ui[pa.descriptor_ui] = class_uuid
        if is_new:
            new_uis.add(pa.descriptor_ui)
    classes_added = len(new_uis)

    # 2. Drop this source's previous edges before writing this run's.
    class_writer.clear_source_edges(conn, SOURCE)

    # 3. The DAG. The parser guaranteed both endpoints are PA classes we ingested.
    parent_edges = sum(
        class_writer.add_parent_edge(conn, uuid_by_ui[e.child_ui],
                                     uuid_by_ui[e.parent_ui], run_id)
        for e in parsed.parents)

    # 4. Membership, via the two-key bridge. Read both claim indexes ONCE, not per
    #    member (a member belongs to several PA classes). Group memberships by
    #    member so each substance is resolved -- and counted, if unmatched -- once.
    unii_index = class_writer.moieties_by_scheme(conn, "UNII")
    cas_index = class_writer.moieties_by_scheme(conn, "CAS")

    classes_by_member: dict[str, list[str]] = {}    # insertion-ordered (Py3.7+)
    keys_by_member: dict[str, mesh.MemberKeys] = {}
    for m in parsed.memberships:
        classes_by_member.setdefault(m.record_ui, []).append(m.descriptor_ui)
        keys_by_member[m.record_ui] = m.keys

    memberships = members_no_key = members_key_not_in_registry = 0
    for record_ui, class_uis in classes_by_member.items():
        keys = keys_by_member[record_ui]
        if not keys.unii and not keys.cas:
            members_no_key += 1                     # structurally unjoinable (§5.3)
            continue
        moiety_uuids = _resolve_moieties(keys, unii_index, cas_index)
        if not moiety_uuids:
            members_key_not_in_registry += 1        # gated-out moiety (§5.3)
            continue
        for descriptor_ui in class_uis:
            for moiety_uuid in moiety_uuids:
                if class_writer.add_membership(conn, moiety_uuid,
                                               uuid_by_ui[descriptor_ui],
                                               RELATIONSHIP, run_id):
                    memberships += 1

    # Re-derive the open-question register (Plan A), last and for the same reason
    # medrt_run does: this run rewrote class_parent and class_membership, both of
    # which the gap views read. MeSH memberships are has_PA, so they never close a
    # gap_unclassified_moiety row (that asks for has_PE) -- but they do populate
    # classes, which is exactly what gap_unpopulated_contraindication tests against
    # once slice 5b keys contraindications on MeSH.
    questions.register_from_gaps(conn, run_id)

    provenance.finish_run(conn, run_id)
    conn.commit()
    return MeshSummary(
        classes_in_release=len(uuid_by_ui), classes_added=classes_added,
        parent_edges=parent_edges, memberships=memberships,
        members_no_key=members_no_key,
        members_key_not_in_registry=members_key_not_in_registry,
        pa_records_without_descriptor=parsed.pa_records_without_descriptor)
