"""Orchestrate one MeSH-synonyms ingest: desc + supp -> moiety_mesh_synonym (#231).

WHY. db/070 made the UNII official names searchable; the BAN/AAN names clinicians
type (lignocaine, frusemide, adrenaline) are not among them. MeSH carries them as
entry terms; `mesh_synonyms` explains which terms and how they link. Cairn's
allergy pick finds them through the `drugref.moiety_name` view (db/071).

SEARCH ONLY. The SPL drug pass never reads this table.

THE SOURCE IS 'MeSH', THE WRITER 'mesh_synonym_run'. The per-source clear touches
only this module's table, so mesh_run's PA rows, the only other 'MeSH' writer, are
unaffected.

A SNAPSHOT OF THE REGISTRY'S UNII CLAIMS, like `moiety_name_run`: re-run it after
every `ingest unii`. `drugref status` says so when `unii_run` finished later.

Order:
  1. read the registry's UNII claims and refuse an empty registry (a rebuild over
     it would delete every synonym and report success);
  2. read desc then supp (descriptors first, so a descriptor's spelling wins) and
     link; refuse a file holding none of its records (the wrong file) and a result
     with no synonyms, for the same reason -- before the run opens;
  3. open the run, clear this source's synonyms, write the new ones;
  4. finish the run and commit -- or roll back and re-raise.
"""
import itertools
import logging
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import classes, db, provenance
from drugref.ingest import mesh_synonyms, spl_official_names
from drugref.ingest.checksum import checksum

SOURCE = "MeSH"
WRITER = "mesh_synonym_run"
MESH_SYNONYM_TABLES = ("moiety_mesh_synonym",)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeshSynonymSummary:
    """What one run wrote: `stored` rows, and the linking line (`SynonymLinks`)."""
    stored: int
    linking: str


def ingest_mesh_synonyms(conn: psycopg.Connection, *,
                         desc_path: str | pathlib.Path,
                         supp_path: str | pathlib.Path,
                         upstream_release: str) -> MeshSynonymSummary:
    """Rebuild moiety_mesh_synonym from one MeSH release.

    TRANSACTION OWNERSHIP as every orchestrator here: `open_run` commits the run
    row, this function commits the work, or rolls it back before re-raising.
    Callers must commit their own pending work first.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    log.info("mesh-synonyms ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest(conn, desc_path, supp_path, upstream_release, clock)
    except Exception:
        conn.rollback()
        log.exception("mesh-synonyms ingest failed (release=%s); rolled back",
                      upstream_release)
        raise
    log.info("mesh-synonyms ingest finished (release=%s): %s",
             upstream_release, summary)
    return summary


def _ingest(conn, desc_path, supp_path, upstream_release,
            clock) -> MeshSynonymSummary:
    """The body of one run (see ingest_mesh_synonyms for the transaction contract)."""
    unii_index = classes.moieties_by_scheme(conn, "UNII")
    if not unii_index:
        raise ValueError(
            "mesh-synonyms: the registry holds no live UNII claim; rebuilding over "
            "it would delete every stored synonym. Run `ingest unii` first.")
    records = itertools.chain(
        mesh_synonyms.synonym_records(desc_path, "DescriptorRecord"),
        mesh_synonyms.synonym_records(supp_path, "SupplementalRecord"))
    links = mesh_synonyms.link(records, unii_index,
                               spl_official_names.shipped_exclusions())
    if not links.rows:
        raise ValueError(
            f"mesh-synonyms: 0 synonyms linked ({links}); rebuilding would delete "
            "every stored synonym. Is this the right release and registry?")

    run_id = provenance.open_run(
        conn, source=SOURCE, upstream_release=upstream_release,
        source_checksum=checksum(desc_path, supp_path), writer=WRITER, clock=clock)
    db.clear_source_tables(conn, MESH_SYNONYM_TABLES, SOURCE)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO drugref.moiety_mesh_synonym "
                        "(moiety_uuid, name, mesh_ui, ingest_run) "
                        "VALUES (%s, %s, %s, %s)",
                        [(*row, run_id) for row in links.rows])
    provenance.finish_run(conn, run_id)
    conn.commit()
    return MeshSynonymSummary(stored=len(links.rows), linking=str(links))
