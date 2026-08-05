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
quietly discarded: on the real release only 4,433 of GSRS's 10,090 parent bases are
drugref moieties, and a number that vanishes is a number nobody fixes.
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

    The two worklist numbers are reported, never swallowed:

    * components_not_in_registry -- edges naming a component no gated-in moiety
      carries. The moiety gate is the binding constraint, exactly as for the MeSH
      bridge (#26), and this counts the shortfall rather than hiding it.
    * unruled_composites -- composites written with no active-component ruling,
      which become gap kind 12. NOT a failure: it is the release declining to say.
    """
    records_in_release: int
    edges_in_release: int
    rows_written: int
    composites_written: int
    components_not_in_registry: int
    unruled_composites: int


def ingest_gsrs(conn: psycopg.Connection, *, dump_path: StrPath,
                upstream_release: str) -> GsrsSummary:
    """Ingest one GSRS public dump into drugref.substance_composition."""
    # 1. PARSE FIRST, before any run row exists. The pass is ~8 s over 2.05 GB and
    #    touches no database; a crash here must leave no trace to explain.
    edges: dict[tuple[str, str, str], bool | None] = {}
    records_in_release = 0
    edges_in_release = 0
    for record in gsrs.iter_records(dump_path):
        records_in_release += 1
        if not record.edges:
            continue
        for edge in record.edges:
            edges_in_release += 1
            # NULL when the release rules on nothing; otherwise whether THIS
            # component is the active one. Keyed by the composite's own record, so
            # the mirror encoding on the component's record cannot overwrite a
            # ruling with a NULL.
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
                                 source_checksum=source_checksum, writer=WRITER)

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

    unruled = sum(1 for values in activity_by_composite.values() if values == {None})

    questions.register_from_gaps(conn, run_id)
    provenance.finish_run(conn, run_id)
    conn.commit()

    summary = GsrsSummary(records_in_release=records_in_release,
                          edges_in_release=edges_in_release,
                          rows_written=rows_written,
                          composites_written=len(composites),
                          components_not_in_registry=unresolved,
                          unruled_composites=unruled)
    log.info("GSRS ingest: %s", summary)
    return summary
