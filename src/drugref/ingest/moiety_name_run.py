"""Orchestrate one moiety-names ingest: UNII_Names -> moiety_official_name (#231).

WHY. Cairn's allergy pick searches drugref for the name a clinician types. The
registry names each moiety once (`display_name`, mostly the INN), so typing the
US name 'acetaminophen' found nothing: the registry calls it 'paracetamol'.
This run stores each moiety's UNII official names, and the `drugref.moiety_name`
view (db/070) puts them beside the display names for searching.

ONE RULE, SHARED WITH THE SPL DRUG PASS. Which official names a moiety may take
is decided by `spl_official_names.for_run` (#227), unchanged: a name another
moiety displays, a name two moieties claim, a name of a UNII two moieties claim
(#230) and the shipped exclusions all stay out. So a name a clinician can pick
is exactly a name a label is matched by.

THE SOURCE IS 'UNII', THE WRITER 'moiety_name_run'. The names are part of the
UNII release; a release is unambiguous per (source, writer). The per-source
clear touches only this module's table, so `ingest unii` and this run can never
delete each other's rows.

A SNAPSHOT OF THE REGISTRY. Which names are adopted depends on the display
names and UNII claims at the moment this runs. A later `ingest unii` can rename a
moiety into one of these names, contest a UNII, or add moieties, and nothing here
changes until this is re-run: re-run it after every `ingest unii`. `drugref
status` says so when `unii_run` finished after the last `moiety_name_run`.

Order:
  1. read the registry and refuse an empty one (a rebuild over it would delete
     every name and report success);
  2. refuse a `--release` other than the registry's own `unii_run` release: the
     names must describe the same UNIIs the registry was built from;
  3. read UNII_Names (refuses a UNII_Records file, or one yielding no official
     name) and decide with `for_run`; refuse a decision adopting nothing (again
     a rebuild that would delete every name) -- all before the run opens;
  4. open the run, clear this source's names, write the adopted ones;
  5. finish the run and commit -- or roll back and re-raise.
"""
import logging
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import db, provenance, registry_read, spl_class_evidence
from drugref.ingest import spl_official_names
from drugref.ingest.checksum import checksum

SOURCE = "UNII"
WRITER = "moiety_name_run"
MOIETY_NAME_TABLES = ("moiety_official_name",)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MoietyNameSummary:
    """What one run wrote.

    * stored -- rows written to moiety_official_name
    * adoption -- the adoption line `ingest spl` also prints: adopted, shadowed,
      ambiguous and excluded names listed (shadowed and ambiguous: first 12),
      already-named and unfoldable as counts, plus any unused-exclusion WARNING
    """
    stored: int
    adoption: str


def ingest_moiety_names(conn: psycopg.Connection, *,
                        unii_names_path: str | pathlib.Path,
                        upstream_release: str) -> MoietyNameSummary:
    """Rebuild moiety_official_name from one UNII_Names file.

    TRANSACTION OWNERSHIP as every orchestrator here: `open_run` commits the run
    row, this function commits the work, or rolls it back before re-raising.
    Callers must commit their own pending work first.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    log.info("moiety-names ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest(conn, unii_names_path, upstream_release, clock)
    except Exception:
        conn.rollback()
        log.exception("moiety-names ingest failed (release=%s); rolled back",
                      upstream_release)
        raise
    log.info("moiety-names ingest finished (release=%s): %s",
             upstream_release, summary)
    return summary


def _ingest(conn, unii_names_path, upstream_release, clock) -> MoietyNameSummary:
    """The body of one run (see ingest_moiety_names for the transaction contract)."""
    registry = registry_read.load_registry(conn)
    if not registry.by_name or not registry.by_unii:
        raise ValueError(
            f"moiety-names: the registry holds {len(registry.by_name)} moiety "
            f"name(s) and {len(registry.by_unii)} live UNII claim(s); rebuilding "
            "over it would delete every stored name. Run `ingest unii` first.")
    registry_release = _registry_release(conn)
    if registry_release != upstream_release:
        raise ValueError(
            f"moiety-names: --release {upstream_release!r}, but the registry was "
            f"built from UNII release {registry_release!r} (unii_run). Use the "
            "UNII_Names file of the registry's own release.")
    rows = spl_class_evidence.read_official_name_rows(unii_names_path,
                                                      registry.by_unii)
    adopted, counts = spl_official_names.for_run(registry, rows)
    if not counts.adopted:
        raise ValueError(
            f"moiety-names: 0 official names adopted ({counts}); rebuilding would "
            "delete every stored name. Is this the right file and registry?")

    run_id = provenance.open_run(
        conn, source=SOURCE, upstream_release=upstream_release,
        source_checksum=checksum(unii_names_path), writer=WRITER, clock=clock)
    db.clear_source_tables(conn, MOIETY_NAME_TABLES, SOURCE)
    # `adopted` is display names plus the adopted official names; only the
    # latter are stored (the view reads display names from substance_moiety).
    for name in counts.adopted:
        conn.execute("INSERT INTO drugref.moiety_official_name "
                     "(moiety_uuid, name, ingest_run) VALUES (%s, %s, %s)",
                     (adopted[name], name, run_id))
    provenance.finish_run(conn, run_id)
    conn.commit()
    return MoietyNameSummary(stored=len(counts.adopted), adoption=str(counts))


def _registry_release(conn) -> str | None:
    """The release the registry's last finished `ingest unii` landed, or None."""
    row = conn.execute(
        "SELECT upstream_release FROM drugref.loaded_release "
        "WHERE source = %s AND writer = 'unii_run'", (SOURCE,)).fetchone()
    return row[0] if row else None
