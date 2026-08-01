"""Attach ChEBI identifiers to already-registered moieties.

ChEBI (CC BY 4.0) is joined to the moiety registry by InChIKey -- a structural
key both UNII and ChEBI carry -- so no re-gating is needed: every moiety that
carries an INCHIKEY claim matching a ChEBI entry gets that ChEBI id attached as
another cross-reference claim. This is the cheap public-cross-walk value the user
asked for; it does not mint or gate moieties.
"""
import hashlib
import pathlib
import csv

import psycopg

from drugref import claims

SOURCE = "CHEBI"
# WHICH orchestrator this is, as distinct from the authority it reads (db/025). One
# source can have two writers -- MED-RT does -- so a release is only unambiguous per
# (source, writer).
WRITER = "chebi"


def enrich_from_chebi(conn: psycopg.Connection, *, chebi_path, upstream_release: str) -> int:
    """Add a CHEBI claim to every moiety whose INCHIKEY matches a ChEBI row.
    Returns the number of CHEBI claims newly added (idempotent on re-run)."""
    checksum = hashlib.sha256(pathlib.Path(chebi_path).read_bytes()).hexdigest()
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, %s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release, checksum, WRITER)).fetchone()[0]

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

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return added
