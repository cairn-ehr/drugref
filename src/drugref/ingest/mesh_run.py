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
PA class it belongs to.
"""
import hashlib
import uuid
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref.classes import ClassConcept
from drugref.ingest import mesh

SOURCE = "MeSH"
RELATIONSHIP = "has_PA"


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
    """
    classes_in_release: int
    classes_added: int
    parent_edges: int
    memberships: int
    members_no_key: int
    members_key_not_in_registry: int


def _checksum(*paths) -> str:
    """One checksum over all three release files, in a fixed order, so the
    ingest_run's provenance changes if ANY of the three inputs changes.

    Read in chunks rather than read_bytes(): supp2026.xml is ~750 MB, and slurping
    it whole would spike peak RSS far above the streaming parser's measured 32.7 MB
    (spec §F) -- the checksum has no reason to undo that. Chunked hashing keeps the
    whole run's memory footprint bounded regardless of file size.
    """
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


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
    """
    parsed = mesh.parse(pa_path=pa_path, desc_path=desc_path, supp_path=supp_path)

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release, _checksum(pa_path, desc_path, supp_path))).fetchone()[0]

    # 1. Classes. A PA class hands upsert_class the same source-neutral shape a
    #    MED-RT concept does; descriptor_ui is both its identity key and its
    #    published code (MeSH keys on the UI it publishes).
    uuid_by_ui: dict[str, uuid.UUID] = {}
    classes_added = 0
    for pa in parsed.classes:
        concept = ClassConcept(nui=pa.descriptor_ui, code=pa.descriptor_ui,
                               name=pa.name, concept_type=pa.concept_type)
        class_uuid, is_new = class_writer.upsert_class(conn, concept, run_id, SOURCE)
        uuid_by_ui[pa.descriptor_ui] = class_uuid
        classes_added += is_new

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

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s",
                 (run_id,))
    conn.commit()
    return MeshSummary(classes_in_release=len(uuid_by_ui), classes_added=classes_added,
                       parent_edges=parent_edges, memberships=memberships,
                       members_no_key=members_no_key,
                       members_key_not_in_registry=members_key_not_in_registry)
