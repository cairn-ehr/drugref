"""Orchestrate one allergy-group ingest: allow-list + MeSH -> groups, members,
placement.

An allergy group (spec 2026-11-08) is a MeSH descriptor subtree named by the reviewed
allow-list (data/allergy_groups.toml). allergy_groups.parse (pure) decides which MeSH
records are in each group; this module is the ONLY writer, and owns the transaction.

ITS OWN SOURCE, 'DRUGREF-ALLERGY', NOT 'MeSH'. mesh_run clears every 'MeSH' row of
class_membership/class_parent on each rebuild; sharing that source would let either
writer wipe the other (#39). With a source of its own, each rebuild deletes only its
own rows, in either order.

NO class_membership, NO class_parent. The group is registered in substance_class (so
it has an immortal class_uuid cairn can store), but its members live in
allergy_group_member, which no existing class walk reads. That keeps an allergy group
from leaking into interaction/contraindication expansion (spec 2, "the sulfa trap").

Order:
  1. parse (pure; a bad allow-list fails here, before any run row exists);
  2. open the run; upsert each group's class row;
  3. clear this source's allergy tables (children first);
  4. write allergy_group, then members (MeSH-bridged, then allow-list includes),
     refusing a group left with no member at all, then placement (every moiety
     MeSH knows at all, plus every member);
  5. finish the run and commit -- or roll back and re-raise.

WORKLIST NUMBERS, NOT SILENT DROPS (mesh_run's posture): a group record that becomes
no member row is counted as members_no_key or members_key_not_in_registry, and an
include whose UNII no moiety carries is named in includes_unresolved.
"""
import logging
import uuid
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref import db, provenance
from drugref.classes import ClassConcept
from drugref.ingest import allergy_groups
from drugref.ingest.checksum import checksum
from drugref.ingest.mesh_run import resolve_moieties

SOURCE = "DRUGREF-ALLERGY"
WRITER = "allergy_group_run"
CONCEPT_TYPE = "AG"
# Children first: allergy_group_member references allergy_group (db/067).
ALLERGY_TABLES = ("allergy_group_member", "allergy_group", "allergy_mesh_placement")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AllergyGroupSummary:
    """What one run wrote, plus its worklist.

    * groups / members / placed_moieties -- rows written to allergy_group,
      allergy_group_member and allergy_mesh_placement
    * members_no_key -- group records with neither UNII nor CAS (e.g. combinations)
    * members_key_not_in_registry -- group records whose keys no moiety carries
    * desc_records / scr_records -- MeSH records parsed (a worklist figure: a run
      that read far fewer than last time read a truncated file)
    * includes_unresolved -- allow-list include UNIIs no moiety carries (reported,
      not fatal: the allow-list may name a moiety this registry has not admitted)
    """
    groups: int
    members: int
    placed_moieties: int
    members_no_key: int
    members_key_not_in_registry: int
    includes_unresolved: tuple[str, ...]
    desc_records: int
    scr_records: int


def ingest_allergy_groups(conn: psycopg.Connection, *, desc_path, supp_path,
                          allow_list_path,
                          upstream_release: str) -> AllergyGroupSummary:
    """Rebuild every allergy group from one MeSH release and the allow-list.

    Idempotent: a re-run rebuilds the same rows, and the class_uuid never changes.
    TRANSACTION OWNERSHIP is mesh_run.ingest_mesh's: open_run commits the run row,
    this function commits the work, or rolls it back before re-raising. Callers must
    commit their own pending work first.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    log.info("allergy-group ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest(conn, desc_path, supp_path, allow_list_path,
                          upstream_release, clock)
    except Exception:
        conn.rollback()
        log.exception("allergy-group ingest failed (release=%s); rolled back",
                      upstream_release)
        raise
    log.info("allergy-group ingest finished (release=%s): %s",
             upstream_release, summary)
    return summary


def _insert_member(conn, class_uuid, moiety_uuid, via, reason, run_id) -> int:
    """Insert one member row: 1 if new, 0 if (group, moiety, via) was already in."""
    return conn.execute(
        "INSERT INTO drugref.allergy_group_member "
        "(class_uuid, moiety_uuid, via, reason, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (class_uuid, moiety_uuid, via, reason, run_id)).rowcount


def _ingest(conn, desc_path, supp_path, allow_list_path, upstream_release,
            clock) -> AllergyGroupSummary:
    """The body of one run (see ingest_allergy_groups for the transaction contract)."""
    groups = allergy_groups.load_allow_list(allow_list_path)
    parsed = allergy_groups.parse(desc_path=desc_path, supp_path=supp_path,
                                  groups=groups)
    # SHRINK GUARD: an empty file would rebuild every group from nothing, deleting
    # real members (a missed allergy warning). parse() refuses an empty desc file.
    if parsed.scr_records == 0:
        raise ValueError(f"MeSH supplemental file {supp_path} holds no "
                         "SupplementalRecord (truncated or wrong file?)")

    run_id = provenance.open_run(
        conn, source=SOURCE, upstream_release=upstream_release,
        source_checksum=checksum(allow_list_path, desc_path, supp_path),
        writer=WRITER, clock=clock)

    # Step 2. Class rows first: allergy_group references substance_class. The MeSH root
    #    UI is the identity key, so the class_uuid is stable across releases.
    uuid_by_root: dict[str, uuid.UUID] = {}
    for g in groups:
        concept = ClassConcept(
            nui=g.root_ui, code=g.root_ui, concept_type=CONCEPT_TYPE,
            name=f"{parsed.root_names[g.root_ui]} (allergy group)")
        uuid_by_root[g.root_ui], _ = class_writer.upsert_class(
            conn, concept, run_id, SOURCE)

    # Step 3. Drop this source's previous rows before writing this run's.
    db.clear_source_tables(conn, ALLERGY_TABLES, SOURCE)

    for g in groups:
        conn.execute(
            "INSERT INTO drugref.allergy_group (class_uuid, mesh_root_ui, "
            "review_status, reviewed_by, reviewed_against, ingest_run) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (uuid_by_root[g.root_ui], g.root_ui, g.review_status, g.reviewed_by,
             g.reviewed_against, run_id))

    # Step 4a. Members, via mesh_run's two-key bridge (UNII-primary, CAS-fallback).
    unii_index = class_writer.moieties_by_scheme(conn, "UNII")
    cas_index = class_writer.moieties_by_scheme(conn, "CAS")
    members = members_no_key = members_key_not_in_registry = 0
    member_moieties: set[uuid.UUID] = set()
    grouped: set[str] = set()                  # roots that got at least one member
    for m in parsed.members:
        if not m.keys.unii and not m.keys.cas:
            members_no_key += 1
            continue
        resolved = resolve_moieties(m.keys, unii_index, cas_index)
        if not resolved:
            members_key_not_in_registry += 1
            continue
        for moiety_uuid in resolved:
            members += _insert_member(conn, uuid_by_root[m.root_ui], moiety_uuid,
                                      m.via, None, run_id)
            member_moieties.add(moiety_uuid)
            grouped.add(m.root_ui)

    # Includes resolve by UNII only: the allow-list names a moiety by its own UNII.
    unresolved: list[str] = []
    for g in groups:
        for inc in g.include:
            resolved = unii_index.get(inc.ui_or_unii, [])
            if not resolved:
                unresolved.append(inc.ui_or_unii)
            for moiety_uuid in resolved:
                members += _insert_member(conn, uuid_by_root[g.root_ui], moiety_uuid,
                                          f"include:{inc.ui_or_unii}", inc.reason,
                                          run_id)
                member_moieties.add(moiety_uuid)
                grouped.add(g.root_ui)

    # An EMPTY group answers no-match for every placed drug (a missed allergy
    # warning), so a group that bridged to no moiety at all is refused; the
    # rollback keeps the previous build.
    empty = sorted({g.root_ui for g in groups} - grouped)
    if empty:
        raise ValueError(f"allergy group(s) {', '.join(empty)} would have no member "
                         "moiety (wrong root, or registry not yet seeded?)")

    # Step 4b. Placement: "does MeSH know this drug at all?" -- every moiety a keyed
    #    MeSH record bridges to, record by record through the SAME two-key rule as
    #    membership (a CAS is tried only when the record's UNIIs resolve nothing).
    placed = set(member_moieties)
    for keys in parsed.placement_keys:
        placed.update(resolve_moieties(keys, unii_index, cas_index))
    for moiety_uuid in sorted(placed):
        conn.execute("INSERT INTO drugref.allergy_mesh_placement "
                     "(moiety_uuid, ingest_run) VALUES (%s, %s)", (moiety_uuid, run_id))

    provenance.finish_run(conn, run_id)
    conn.commit()
    return AllergyGroupSummary(
        groups=len(groups), members=members, placed_moieties=len(placed),
        members_no_key=members_no_key,
        members_key_not_in_registry=members_key_not_in_registry,
        includes_unresolved=tuple(unresolved),
        desc_records=parsed.desc_records, scr_records=parsed.scr_records)
