"""Attach ChEBI identifiers to already-registered moieties.

ChEBI (CC BY 4.0) is joined to the moiety registry by InChIKey -- a structural
key both UNII and ChEBI carry -- so no re-gating is needed: every moiety that
carries an INCHIKEY claim matching a ChEBI entry gets that ChEBI id attached as
another cross-reference claim. This is the cheap public-cross-walk value the user
asked for; it does not mint or gate moieties.
"""
import csv
import logging

import psycopg

from drugref import claims, provenance
from drugref.ingest.checksum import checksum

log = logging.getLogger(__name__)

SOURCE = "CHEBI"
# WHICH orchestrator this is, as distinct from the authority it reads (db/025). One
# source can have two writers -- MED-RT does -- so a release is only unambiguous per
# (source, writer).
WRITER = "chebi"


def enrich_from_chebi(conn: psycopg.Connection, *, chebi_path,
                      upstream_release: str) -> int:
    """Add a CHEBI claim to every moiety whose INCHIKEY matches a ChEBI row.

    Returns the number of CHEBI claims newly added (idempotent on re-run).

    TRANSACTION OWNERSHIP: TWO transactions on one connection. provenance.open_run
    commits the run record before the WRITES, so a crash during them leaves it standing
    with finished_at NULL (ingest_run_incomplete reports it); everything after it is
    the work, which this function owns, commits on success, and rolls back before
    re-raising. A caller with pending work has it committed at the provenance boundary,
    so callers must commit their own work before calling.

    THE WINDOW OPENS EARLY HERE: the parse streams AFTER open_run, unlike MOST of the
    other writers -- medrt_run, mesh_run, mesh_rel_run, gsrs_run, fda_cyp_run,
    drugcentral_run and spl_run all do substantial work before opening a run, and so
    leave no trace of a crash during it. (Stated structurally rather than as a tally:
    this sentence named three when there were six writers and seven when there are
    eleven, which is the hand-listed-count defect db/053 removes from db/025.)
    Everything but the checksum read is covered.
    The orchestrators are not uniform in this, and ingest_run_incomplete says so.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    log.info("ChEBI enrichment starting (release=%s)", upstream_release)
    try:
        added = _enrich_from_chebi(conn, chebi_path, upstream_release, clock)
    except Exception:
        conn.rollback()
        log.exception("ChEBI enrichment failed (release=%s); transaction rolled back",
                      upstream_release)
        raise
    log.info("ChEBI enrichment finished (release=%s): %d claims added",
             upstream_release, added)
    return added


def _enrich_from_chebi(conn: psycopg.Connection, chebi_path, upstream_release: str,
                       clock: provenance.RunClock) -> int:
    """The body of one ChEBI enrichment (see enrich_from_chebi for the contract)."""
    run_id = provenance.open_run(conn, source=SOURCE, upstream_release=upstream_release,
                                 source_checksum=checksum(chebi_path), writer=WRITER,
                                 clock=clock)

    added = 0
    with open(chebi_path, newline="", encoding="utf-8") as fh:
        # QUOTE_NONE: tab-delimited text with no quoting convention (see
        # unii.parse for why csv's default would silently swallow rows).
        for row in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            inchikey = row["INCHIKEY"].strip()
            chebi_id = row["CHEBI_ID"].strip()
            # Find every moiety carrying this InChIKey (structural identity join).
            # An InChIKey is not guaranteed unique across moieties, so attach to
            # ALL matches, not just the first. Superseded claims are excluded so a
            # corrected-away InChIKey never drags a stale ChEBI id back in.
            hits = conn.execute(
                "SELECT moiety_uuid FROM drugref.identity_claim "
                "WHERE scheme = 'INCHIKEY' AND value = %s AND superseded_by IS NULL",
                (inchikey,)).fetchall()
            for (moiety_uuid,) in hits:
                # add_claim reports whether the row was genuinely new (ON CONFLICT
                # no-op returns False), so we count without a separate probe query.
                if claims.add_claim(conn, moiety_uuid, "CHEBI", chebi_id, run_id):
                    added += 1

    provenance.finish_run(conn, run_id)
    conn.commit()
    return added
