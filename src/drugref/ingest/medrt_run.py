"""Orchestrate one MED-RT ingest: parse -> upsert classes -> rebuild edges.

The shape mirrors ingest/run.py (the slice-1 UNII orchestrator): open an
ingest_run for provenance, do the work, stamp finished_at, commit. The one
structural difference is the REBUILD step -- MED-RT is a rebuildable projection,
so a new release replaces the previous release's edges wholesale rather than
merging into them (see classes.clear_source_edges for why that is necessary).

Order matters here:
  1. classes first, because every edge references a class row;
  2. then clear the old edges, so a class that lost a parent upstream loses it
     here too -- the clear happens before any of this run's edges are written,
     so it only ever removes the previous release's rows;
  3. then insert the new edges.
"""
import hashlib
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref.ingest import medrt

SOURCE = "MED-RT"


@dataclass(frozen=True)
class MedrtSummary:
    """What one run did -- returned so a caller (or a test) can assert on it.

    `unmatched_rxcuis` is the worklist number: MED-RT classified an ingredient we
    do not carry, usually because the slice-1 moiety gate excluded it. That is
    reported rather than silently swallowed, matching the gate's own posture that
    an unmatched substance is a worklist item, never an invisible drop.
    """
    classes: int
    parent_edges: int
    memberships: int
    unmatched_rxcuis: int


def _checksum(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def ingest_medrt(conn: psycopg.Connection, *, medrt_path,
                 upstream_release: str) -> MedrtSummary:
    """Ingest one MED-RT release file.

    Idempotent: re-running rebuilds to the same state, with the same class UUIDs.
    """
    parsed = medrt.parse(medrt_path)

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release, _checksum(medrt_path))).fetchone()[0]

    # 1. Classes. Their UUIDs are derived, so this both registers new classes and
    #    builds the lookup every edge below needs.
    uuid_by_nui = {c.nui: class_writer.upsert_class(conn, c, run_id) for c in parsed.classes}

    # 2. Drop the previous release's edges before writing this one's.
    class_writer.clear_source_edges(conn, SOURCE)

    # 3. The DAG. The parser guaranteed both endpoints are classes we ingested.
    parent_edges = sum(
        class_writer.add_parent_edge(conn, uuid_by_nui[e.child_nui],
                                     uuid_by_nui[e.parent_nui], run_id)
        for e in parsed.parents)

    # 4. Membership, joined through the RXNORM_IN claims slice 1 recorded.
    memberships = 0
    unmatched: set[str] = set()
    for assertion in parsed.memberships:
        moiety_uuid = class_writer.resolve_moiety_by_rxcui(conn, assertion.rxcui)
        if moiety_uuid is None:
            # Not an error: MED-RT classifies far more ingredients than pass our
            # moiety gate. Counted by DISTINCT RxCUI so the yield is auditable.
            unmatched.add(assertion.rxcui)
            continue
        if class_writer.add_membership(conn, moiety_uuid, uuid_by_nui[assertion.class_nui],
                                       assertion.relationship, run_id):
            memberships += 1

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s",
                 (run_id,))
    conn.commit()
    return MedrtSummary(classes=len(uuid_by_nui), parent_edges=parent_edges,
                        memberships=memberships, unmatched_rxcuis=len(unmatched))
