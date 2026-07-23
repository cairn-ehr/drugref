"""Attach ChEBI identifiers to already-registered moieties.

ChEBI (CC BY 4.0) is joined to the moiety registry by InChIKey -- a structural
key both UNII and ChEBI carry -- so no re-gating is needed: if a moiety already
has an INCHIKEY claim matching a ChEBI entry, we attach that ChEBI id as another
cross-reference claim. This is the cheap public-cross-walk value the user asked
for; it does not mint or gate moieties.
"""
import hashlib
import pathlib
import csv

import psycopg

from drugref import claims


def enrich_from_chebi(conn: psycopg.Connection, *, chebi_path, upstream_release: str) -> int:
    """Add a CHEBI claim to each moiety whose INCHIKEY matches a ChEBI row.
    Returns the number of CHEBI claims added (idempotent on re-run)."""
    checksum = hashlib.sha256(pathlib.Path(chebi_path).read_bytes()).hexdigest()
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('CHEBI', %s, %s) RETURNING ingest_run_id",
        (upstream_release, checksum)).fetchone()[0]

    added = 0
    with open(chebi_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            inchikey = row["INCHIKEY"].strip()
            chebi_id = row["CHEBI_ID"].strip()
            # Find the moiety carrying this InChIKey (structural identity join).
            hit = conn.execute(
                "SELECT moiety_uuid FROM drugref.identity_claim "
                "WHERE scheme = 'INCHIKEY' AND value = %s", (inchikey,)).fetchone()
            if hit is None:
                continue
            before = conn.execute(
                "SELECT count(*) FROM drugref.identity_claim "
                "WHERE moiety_uuid = %s AND scheme = 'CHEBI' AND value = %s",
                (hit[0], chebi_id)).fetchone()[0]
            claims.add_claim(conn, hit[0], "CHEBI", chebi_id, run_id)
            if before == 0:
                added += 1

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return added
