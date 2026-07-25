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
import logging
import pathlib
import uuid
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref import interactions
from drugref.ingest import medrt

SOURCE = "MED-RT"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MedrtSummary:
    """What one run did -- returned so a caller (or a test) can assert on it.

    Each count says exactly one thing, because the tables behave differently:
    classes ACCUMULATE (identity is immortal, nothing is ever deleted) while edges
    are REBUILT wholesale, so a single "classes" number would mean "seen in this
    release" and "written by this run" at the same time and be ambiguous on a
    re-ingest. They are therefore reported separately:

    * classes_in_release -- classes this release asserts (upserted, new or not)
    * classes_added      -- of those, the ones drugref had never seen before
    * parent_edges / memberships / contraindications -- rows this run actually wrote
      (contraindications are the slice-5a CI_MoA/CI_PE drug-drug rules)

    The remaining worklist numbers are not errors, reported rather than silently
    swallowed -- the slice-1 gate's posture, that anything we decline to carry is a
    work item and never an invisible drop:

    * unmatched_rxcuis      -- MED-RT classified an ingredient we do not carry,
                               usually because the moiety gate excluded it
    * unmatched_ci_rxcuis   -- the same, for a CI_MoA/CI_PE whose subject ingredient
                               our registry does not carry
    * inactive_concepts     -- upstream no longer marks the concept active
    * unidentified_concepts -- the concept carries neither a NUI nor a code
    * ambiguous_codes       -- one published code claimed by several concepts, so
                               every edge referencing it was refused rather than
                               resolved to an arbitrary one of them
    """
    classes_in_release: int
    classes_added: int
    parent_edges: int
    memberships: int
    contraindications: int
    unmatched_rxcuis: int
    unmatched_ci_rxcuis: int
    inactive_concepts: int
    unidentified_concepts: int
    ambiguous_codes: int


def _checksum(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def ingest_medrt(conn: psycopg.Connection, *, medrt_path,
                 upstream_release: str) -> MedrtSummary:
    """Ingest one MED-RT release file.

    Idempotent: re-running rebuilds to the same state, with the same class UUIDs.

    TRANSACTION OWNERSHIP: this function owns `conn`'s transaction for its whole
    body and commits at the end. On any failure it rolls back before re-raising, so
    the caller is handed back a usable connection rather than one stuck in
    Postgres's aborted-transaction state -- which would otherwise make the NEXT
    feed's first statement fail for reasons that have nothing to do with it.
    Callers must therefore commit their own pending work before calling.
    """
    log.info("MED-RT ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest_medrt(conn, medrt_path, upstream_release)
    except Exception:
        conn.rollback()
        log.exception("MED-RT ingest failed (release=%s); transaction rolled back",
                      upstream_release)
        raise
    log.info("MED-RT ingest finished (release=%s): %s", upstream_release, summary)
    return summary


def _ingest_medrt(conn: psycopg.Connection, medrt_path,
                  upstream_release: str) -> MedrtSummary:
    """The body of one MED-RT ingest. Separated so the public entry point above is
    just the transaction/logging boundary and this stays readable top to bottom."""
    parsed = medrt.parse(medrt_path)

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release, _checksum(medrt_path))).fetchone()[0]

    # 1. Classes. Their UUIDs are derived, so this both registers new classes and
    #    builds the lookup every edge below needs.
    uuid_by_nui: dict[str, uuid.UUID] = {}
    # Counted by DISTINCT NUI, not by summing the per-row flag: parsed.classes is a
    # list the parser does not deduplicate, and a concept repeated within one
    # release reports is_new on every occurrence (first_seen_ingest is already this
    # run's id by the second call). Summing gave classes_added > classes_in_release,
    # which the summary's own contract says cannot happen.
    new_nuis: set[str] = set()
    for concept in parsed.classes:
        class_uuid, is_new = class_writer.upsert_class(conn, concept, run_id, SOURCE)
        uuid_by_nui[concept.nui] = class_uuid
        if is_new:
            new_nuis.add(concept.nui)
    classes_added = len(new_nuis)

    # 2. Drop the previous release's edges AND contraindications before writing this
    #    run's -- both are rebuildable projections replaced wholesale per release.
    class_writer.clear_source_edges(conn, SOURCE)
    interactions.clear_source_contraindications(conn, SOURCE)
    class_writer.clear_source_unmatched_ingredients(conn, SOURCE)

    # 3. The DAG. The parser guaranteed both endpoints are classes we ingested.
    parent_edges = sum(
        class_writer.add_parent_edge(conn, uuid_by_nui[e.child_nui],
                                     uuid_by_nui[e.parent_nui], run_id)
        for e in parsed.parents)

    # 4. Membership, joined through the RXNORM_IN claims slice 1 recorded. The
    #    index is read once rather than per assertion: MED-RT states several
    #    memberships per ingredient, so a per-assertion lookup would re-ask the
    #    same question four times out of five.
    moieties = class_writer.moieties_by_rxcui(conn)
    memberships = 0
    unmatched: set[str] = set()
    for assertion in parsed.memberships:
        # Every moiety claiming this RxCUI, not just one -- see
        # classes.moieties_by_rxcui for why the multiplicity is real.
        matches = moieties.get(assertion.rxcui, ())
        if not matches:
            # Not an error: MED-RT classifies far more ingredients than pass our
            # moiety gate. Counted by DISTINCT RxCUI so the yield is auditable.
            unmatched.add(assertion.rxcui)
            continue
        for moiety_uuid in matches:
            if class_writer.add_membership(conn, moiety_uuid,
                                           uuid_by_nui[assertion.class_nui],
                                           assertion.relationship, run_id):
                memberships += 1

    # 4a. Persist WHICH ingredients went unmatched, not merely how many. The count
    #     answers "how much of the release can we not speak about"; only the
    #     identities answer "which drugs", and that is the question worth publishing
    #     (gap_unmatched_ingredient). Written once from the deduped set, after the
    #     loop, so a repeated assertion costs one row rather than many.
    for rxcui in sorted(unmatched):
        class_writer.add_unmatched_ingredient(conn, rxcui, run_id)

    # 5. Contraindications (slice 5a). The subject is joined by RxCUI through the
    #    same `moieties` index step 4 already built; the object is resolved through
    #    the class UUIDs step 1 built (the parser guaranteed the object is an
    #    ingested class, so uuid_by_nui always has it). A subject our registry does
    #    not carry is counted by DISTINCT RxCUI, exactly as membership's are.
    contraindications = 0
    unmatched_ci: set[str] = set()
    for ci in parsed.contraindications:
        matches = moieties.get(ci.rxcui, ())
        if not matches:
            unmatched_ci.add(ci.rxcui)
            continue
        for moiety_uuid in matches:
            if interactions.add_contraindication(conn, moiety_uuid,
                                                 uuid_by_nui[ci.class_nui],
                                                 ci.relationship, SOURCE, run_id):
                contraindications += 1

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s",
                 (run_id,))
    conn.commit()
    return MedrtSummary(classes_in_release=len(uuid_by_nui), classes_added=classes_added,
                        parent_edges=parent_edges, memberships=memberships,
                        contraindications=contraindications,
                        unmatched_rxcuis=len(unmatched),
                        unmatched_ci_rxcuis=len(unmatched_ci),
                        inactive_concepts=parsed.inactive_concepts,
                        unidentified_concepts=parsed.unidentified_concepts,
                        ambiguous_codes=parsed.ambiguous_codes)
