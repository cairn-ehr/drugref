# src/drugref/ingest/run.py
"""Orchestrate one slice-1 ingest run: gate -> mint -> claim.

For each UNII row that passes the moiety gate: mint (or recognise) the immortal
moiety_uuid, refresh its display-name cache, and append its identity claims
(UNII, INN, and the cheap cross-references). Idempotent and immortal: re-running
adds nothing new for unchanged data, and upstream churn attaches new claims
without ever re-keying an existing moiety.
"""
import logging
from dataclasses import dataclass

import psycopg

from drugref import claims, ids, provenance, questions
from drugref.ingest import gate, unii
from drugref.ingest.checksum import checksum

log = logging.getLogger(__name__)

SOURCE = "UNII"
# WHICH orchestrator this is, as distinct from the authority it reads (db/025). One
# source can have two writers -- MED-RT does -- so a release is only unambiguous per
# (source, writer).
WRITER = "unii_run"


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


def ingest_unii(conn: psycopg.Connection, *, unii_path, crosswalk_path,
                allowlist_path, upstream_release: str) -> UniiSummary:
    """Ingest one UNII file. Returns a UniiSummary of what was and was not carried.

    TRANSACTION OWNERSHIP: TWO transactions on one connection. provenance.open_run
    commits the run record before the WRITES, so a crash during them leaves it standing
    with finished_at NULL (ingest_run_incomplete reports it); everything after it is
    the work, which this function owns, commits on success, and rolls back before
    re-raising. A caller with pending work has it committed at the provenance boundary,
    so callers must commit their own work before calling.

    THE WINDOW OPENS EARLY HERE: the parse streams AFTER open_run, unlike medrt_run,
    mesh_run and mesh_rel_run, which parse their whole release before opening a run and
    so leave no trace of a crash during it. Everything but the checksum read is covered.
    The six orchestrators are not uniform in this, and ingest_run_incomplete says so.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    log.info("UNII ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest_unii(conn, unii_path, crosswalk_path, allowlist_path,
                               upstream_release, clock)
    except Exception:
        conn.rollback()
        log.exception("UNII ingest failed (release=%s); transaction rolled back",
                      upstream_release)
        raise
    log.info("UNII ingest finished (release=%s): %s", upstream_release, summary)
    return summary


def _ingest_unii(conn: psycopg.Connection, unii_path, crosswalk_path,
                 allowlist_path, upstream_release: str,
                 clock: provenance.RunClock) -> UniiSummary:
    """The body of one UNII ingest (see ingest_unii for the transaction contract)."""
    crosswalk = gate.load_crosswalk(crosswalk_path)
    allowlist = gate.load_allowlist(allowlist_path)

    run_id = provenance.open_run(conn, source=SOURCE, upstream_release=upstream_release,
                                 source_checksum=checksum(unii_path), writer=WRITER,
                                 clock=clock)

    # The admission projection is rebuilt, not appended to (db/011): clear it
    # before the loop so a signal upstream has stopped asserting disappears with
    # this release rather than lingering as evidence nothing supports.
    claims.clear_admissions(conn)

    count = gated_out = rows_without_unii = 0
    for cand in unii.parse(unii_path):
        # Identity first: a row with no UNII has no derivable moiety_uuid, and
        # admitting it would collapse every such row onto one shared identity.
        if not gate.has_identity_key(cand):
            rows_without_unii += 1
            continue
        # One call answers both "is it admitted" and "on what evidence", so the
        # stored reason can never drift from the decision (#26).
        signals = gate.admission_signals(cand, allowlist)
        if not signals:
            gated_out += 1
            continue
        count += 1
        moiety_uuid = ids.mint_moiety_uuid(cand.unii)          # deterministic at seed
        display_name = gate.inn_display_name(cand, crosswalk)
        claims.upsert_moiety(conn, moiety_uuid, display_name, run_id)
        claims.record_admission(conn, moiety_uuid, signals, run_id)
        claims.add_claim(conn, moiety_uuid, "UNII", cand.unii, run_id)
        if cand.has_inn:
            claims.add_claim(conn, moiety_uuid, "INN", display_name, run_id)
        for scheme, value in cand.cross_refs.items():
            claims.add_claim(conn, moiety_uuid, scheme, value, run_id)

    # Re-derive the open-question register (Plan A). This run registers moieties, and
    # a moiety with no has_PE membership IS a gap_unclassified_moiety -- so on a
    # fresh database this is the ingest that first fills the register, and every
    # moiety a later MED-RT run classifies leaves it again. Rebuilding here rather
    # than only in medrt_run is what makes "the register reflects the database" true
    # after ANY ingest, instead of only after the one that happens to run last.
    questions.register_from_gaps(conn, run_id)

    provenance.finish_run(conn, run_id)
    conn.commit()
    return UniiSummary(moieties=count, gated_out=gated_out,
                       rows_without_unii=rows_without_unii)
