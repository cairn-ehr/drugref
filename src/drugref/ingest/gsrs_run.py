# src/drugref/ingest/gsrs_run.py
"""Orchestrate one GSRS composition ingest: parse -> clear -> insert -> rebuild.

The ONLY writer of drugref.substance_composition's transaction, per the
architecture invariant: parsers are pure, orchestrators own the transaction.

ORDER MATTERS, as for MED-RT and MeSH:
  1. parse and checksum BEFORE opening the run, so a crash during the 2.05 GB pass
     leaves no half-written run row;
  2. clear this source's old rows, so a re-ingest REPLACES rather than accumulates;
  3. insert, then rebuild the question register, then finish and commit.

WORKLIST NUMBERS, NOT SILENT DROPS -- the slice-1/2a posture. An edge whose
component is not a gated-in moiety is COUNTED (`components_not_in_registry`), never
quietly discarded: on the real release only 4,433 of GSRS's 11,209 distinct
components (across both relations) are drugref moieties, and a number that
vanishes is a number nobody fixes.
"""
import dataclasses
import logging

import psycopg

from drugref import composition, provenance, questions
from drugref.ingest import gsrs
from drugref.ingest.checksum import checksum
from drugref.ingest.gsrs import StrPath

SOURCE = "GSRS"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). Declared in provenance.WRITERS and db/028's CHECK -- a pair.
WRITER = "gsrs_run"

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class GsrsSummary:
    """What one GSRS run did -- returned so a caller (or test) can assert on it.

    EVERY FIELD IS NAMED FOR WHAT IT ACTUALLY COUNTS, which the first two were not:
    they were `records_in_release` and `edges_in_release`, and both counted something
    narrower than "in the release". A summary line printed by the CLI is the number a
    reader quotes later, so a name that overstates its scope is a wrong number with a
    plausible source.

    * records_with_unii -- records carrying an approvalID, NOT records in the file.
      iter_records skips the 5,078 of 173,080 that carry none, so on the real dump
      this reads ~168,002 and never the release total.
    * edge_statements_read -- composition RELATIONSHIP STATEMENTS seen, not distinct
      edges. GSRS stores most edges from both ends and both encodings are counted
      here, so this roughly doubles the edge count; the deduplicated total is
      rows_written + components_not_in_registry.

    The two worklist numbers are reported, never swallowed:

    * components_not_in_registry -- edges naming a component no gated-in moiety
      carries. The moiety gate is the binding constraint, exactly as for the MeSH
      bridge (#26), and this counts the shortfall rather than hiding it.
    * unruled_composites -- composites written with no active-component ruling,
      which become gap kind 12. NOT a failure: it is the release declining to say.
      Pinned against the gap view itself in test_gsrs_run.py, because this Python
      count and the view's `bool_and` are two implementations of one rule.
    """
    records_with_unii: int
    edge_statements_read: int
    rows_written: int
    composites_written: int
    components_not_in_registry: int
    unruled_composites: int


def ingest_gsrs(conn: psycopg.Connection, *, dump_path: StrPath,
                upstream_release: str) -> GsrsSummary:
    """Ingest one GSRS public dump into drugref.substance_composition."""
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    # 1. PARSE FIRST, before any run row exists. The pass is ~8 s over 2.05 GB and
    #    touches no database; a crash here must leave no trace to explain.
    edges: dict[tuple[str, str, str], bool | None] = {}
    records_with_unii = 0
    edge_statements_read = 0
    for record in gsrs.iter_records(dump_path):
        records_with_unii += 1
        if not record.edges:
            continue
        for edge in record.edges:
            edge_statements_read += 1
            # NULL unless the COMPOSITE's own record rules on it; otherwise whether
            # THIS component is the active one. Keyed by the composite's own
            # record, so the mirror encoding on the component's record cannot
            # overwrite a ruling with a NULL -- but also cannot SUPPLY one: for 27
            # in-registry edges GSRS states ACTIVE MOIETY only on the component's
            # record, and this code records NULL for them even though the release
            # does rule, just not from the end this code consults (issue 69).
            if edge.substance_unii == record.unii:
                activity = (edge.component_unii in record.active_moieties
                            if record.active_moieties else None)
            else:
                activity = None
            key = (edge.substance_unii, edge.component_unii, edge.relation)
            # A ruling beats a None, whichever end the edge arrived from.
            if key not in edges or edges[key] is None:
                edges[key] = activity

    source_checksum = checksum(dump_path)

    # 2. Open the run. This COMMITS in its own transaction (provenance.open_run),
    #    so everything after it is the work and rolls back together on failure.
    run_id = provenance.open_run(conn, source=SOURCE,
                                 upstream_release=upstream_release,
                                 source_checksum=source_checksum, writer=WRITER,
                                 clock=clock)

    # 3. The work, and it MUST roll back as one. This was the only orchestrator of the
    #    eleven with no failure handling at all -- the other ten wrap exactly this span
    #    -- and db/053 made the gap newly reachable: `finish_run` used to be a bare
    #    `UPDATE ... = now()` that could not fail, and now writes a stamp a CHECK can
    #    refuse. Without this, a programmatic caller was handed back a connection in an
    #    aborted transaction with nothing logged, and the operator got a psycopg
    #    traceback naming neither GSRS nor the release -- the cost fda_cyp_run's own
    #    comment records paying, as the last orchestrator to be fixed before this one.
    try:
        by_unii = composition.moiety_uuid_by_unii(conn)
        composition.clear_source_composition(conn, SOURCE)

        rows_written = 0
        composites: set[str] = set()
        unresolved = 0
        activity_by_composite: dict[str, set[bool | None]] = {}
        for (substance_unii, component_unii, relation), activity in edges.items():
            component_moiety = by_unii.get(component_unii)
            if component_moiety is None:
                unresolved += 1
                continue
            if composition.add_composition(
                    conn, substance_unii=substance_unii,
                    component_moiety=component_moiety, relation=relation,
                    is_active_component=activity, ingest_run_id=run_id):
                rows_written += 1
            composites.add(substance_unii)
            activity_by_composite.setdefault(substance_unii, set()).add(activity)

        unruled = sum(1 for values in activity_by_composite.values()
                      if values == {None})

        questions.register_from_gaps(conn, run_id)
        provenance.finish_run(conn, run_id)
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("GSRS ingest failed for release %s; rolled back",
                      upstream_release)
        raise

    summary = GsrsSummary(records_with_unii=records_with_unii,
                          edge_statements_read=edge_statements_read,
                          rows_written=rows_written,
                          composites_written=len(composites),
                          components_not_in_registry=unresolved,
                          unruled_composites=unruled)
    log.info("GSRS ingest: %s", summary)
    return summary
