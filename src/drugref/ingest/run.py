# src/drugref/ingest/run.py
"""Orchestrate one slice-1 ingest run: gate -> mint -> claim.

For each UNII row that passes the moiety gate: mint (or recognise) the immortal
moiety_uuid, refresh its display-name cache, and append its identity claims
(UNII, INN, and the cheap cross-references). Idempotent and immortal: re-running
adds nothing new for unchanged data, and upstream churn attaches new claims
without ever re-keying an existing moiety.
"""
import hashlib
import pathlib

import psycopg

from drugref import claims, ids
from drugref.ingest import gate, unii


def _checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def ingest_unii(conn: psycopg.Connection, *, unii_path, crosswalk_path,
                allowlist_path, upstream_release: str) -> int:
    """Ingest one UNII file. Returns the number of moieties registered/seen."""
    crosswalk = gate.load_crosswalk(crosswalk_path)
    allowlist = gate.load_allowlist(allowlist_path)

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII', %s, %s) RETURNING ingest_run_id",
        (upstream_release, _checksum(unii_path))).fetchone()[0]

    count = 0
    for cand in unii.parse(unii_path):
        if not gate.is_moiety(cand, allowlist):
            continue
        count += 1
        moiety_uuid = ids.mint_moiety_uuid(cand.unii)          # deterministic at seed
        display_name = gate.inn_display_name(cand, crosswalk)
        claims.upsert_moiety(conn, moiety_uuid, display_name, run_id)
        claims.add_claim(conn, moiety_uuid, "UNII", cand.unii, run_id)
        if cand.has_inn:
            claims.add_claim(conn, moiety_uuid, "INN", display_name, run_id)
        for scheme, value in cand.cross_refs.items():
            claims.add_claim(conn, moiety_uuid, scheme, value, run_id)

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return count
