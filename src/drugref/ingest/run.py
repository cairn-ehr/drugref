# src/drugref/ingest/run.py
"""Orchestrate one slice-1 ingest run: gate -> mint -> claim.

For each UNII row that passes the moiety gate: mint (or recognise) the immortal
moiety_uuid, refresh its display-name cache, and append its identity claims
(UNII, INN, and the cheap cross-references). Idempotent and immortal: re-running
adds nothing new for unchanged data, and upstream churn attaches new claims
without ever re-keying an existing moiety.
"""
import hashlib
import logging
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import claims, ids
from drugref.ingest import gate, unii

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UniiSummary:
    """What one UNII run did -- returned so a caller (or a test) can assert on it.

    The two refusal counts are worklist numbers, not errors, and exist for the
    same reason MedrtSummary.unmatched_rxcuis and MeshSummary.members_no_key do:
    anything the ingest declines to carry must be visible, never an invisible
    drop. A bare "moieties registered" count cannot distinguish "upstream shrank"
    from "our gate silently stopped matching".

    * moieties          -- rows that passed the gate and were registered/refreshed
    * gated_out         -- rows the moiety gate excluded (excipients, foods, and
                           any legacy drug whose preferred term the closed
                           allow-list narrowly missed -- which is why this is
                           worth watching rather than assuming)
    * rows_without_unii -- rows carrying no UNII at all. Refused because the
                           moiety UUID derives from the UNII, so admitting them
                           would merge unrelated drugs onto one immortal
                           identity (see gate.has_identity_key).
    """
    moieties: int
    gated_out: int
    rows_without_unii: int


def _checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def ingest_unii(conn: psycopg.Connection, *, unii_path, crosswalk_path,
                allowlist_path, upstream_release: str) -> UniiSummary:
    """Ingest one UNII file. Returns a UniiSummary of what was and was not carried.

    TRANSACTION OWNERSHIP: as for the MED-RT and MeSH orchestrators -- this owns
    `conn`'s transaction, commits on success, and rolls back before re-raising so a
    failure never leaves the caller with an aborted transaction.
    """
    log.info("UNII ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest_unii(conn, unii_path, crosswalk_path, allowlist_path,
                               upstream_release)
    except Exception:
        conn.rollback()
        log.exception("UNII ingest failed (release=%s); transaction rolled back",
                      upstream_release)
        raise
    log.info("UNII ingest finished (release=%s): %s", upstream_release, summary)
    return summary


def _ingest_unii(conn: psycopg.Connection, unii_path, crosswalk_path,
                 allowlist_path, upstream_release: str) -> UniiSummary:
    """The body of one UNII ingest (see ingest_unii for the transaction contract)."""
    crosswalk = gate.load_crosswalk(crosswalk_path)
    allowlist = gate.load_allowlist(allowlist_path)

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII', %s, %s) RETURNING ingest_run_id",
        (upstream_release, _checksum(unii_path))).fetchone()[0]

    count = gated_out = rows_without_unii = 0
    for cand in unii.parse(unii_path):
        # Identity first: a row with no UNII has no derivable moiety_uuid, and
        # admitting it would collapse every such row onto one shared identity.
        if not gate.has_identity_key(cand):
            rows_without_unii += 1
            continue
        if not gate.is_moiety(cand, allowlist):
            gated_out += 1
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
    return UniiSummary(moieties=count, gated_out=gated_out,
                       rows_without_unii=rows_without_unii)
