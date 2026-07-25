# src/drugref/ingest/pbs_run.py
"""Orchestrator for the Australian PBS ingest (slice 8a) -- the only writer path.

Owns the transaction, exactly like medrt_run.py and mesh_run.py: the parser is
pure, local.py holds the SQL, and this module decides what happens and when.

LICENCE (spec section 1): drugref ships this CODE. It never ships PBS DATA, and a
node operator supplies their own release. See issue #25 for the redistribution
gate that is still open.
"""
import hashlib
import logging
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import classes, local
from drugref.ingest import pbs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PbsSummary:
    """What one PBS ingest did -- the slice's actual deliverable (spec section 7).

    products_bridged / products_written is the MATCH RATE, and the split between
    exact and salt-stripped rows says how much of it the slice-3 stand-in is
    carrying. unmatched_components is the residual worklist.
    """
    items_read: int
    products_written: int
    products_bridged: int
    bridge_rows_exact: int
    bridge_rows_salt_stripped: int
    combination_products: int
    unmatched_components: int


def resolve(component: str, inn_index: dict[str, list], salt_suffixes: frozenset[str]):
    """Resolve one ingredient name to (moieties, match_method), or ([], None).

    ORDER IS THE SAFEGUARD, and it is the whole reason this is a function rather
    than an inline lookup: the UNSTRIPPED name is tried FIRST, and the salt strip
    is only a fallback. "Dimethyl fumarate" is an INN in its own right while
    "fumarate" is a genuine salt token elsewhere ("Ferrous fumarate"), so an
    eager strip would turn a correct exact match into a miss -- and would label
    it 'salt_stripped', hiding the damage behind a plausible-looking row.

    Returns EVERY claimant moiety, never an arbitrary first: identity_claim is
    unique per (moiety, scheme, value) but not across moieties, so picking one
    would drop a real link and could answer differently run to run
    (classes.moieties_by_scheme applies the same rule).
    """
    exact = inn_index.get(component)
    if exact:
        return exact, "exact"
    stripped = pbs.strip_salt(component, salt_suffixes)
    if stripped:
        fallback = inn_index.get(stripped)
        if fallback:
            return fallback, "salt_stripped"
    return [], None


def ingest_pbs(conn: psycopg.Connection, items_csv_path: str | pathlib.Path,
               upstream_release: str, source_checksum: str | None = None,
               jurisdiction: str = "AU", source: str = "PBS") -> PbsSummary:
    """Ingest one PBS release's items.csv. Owns the transaction end to end.

    Steps, in an order that matters: open the provenance row, CLEAR this source's
    previous projection (so a de-listed item disappears), read the INN index ONCE
    (a per-item query would re-ask an answered question thousands of times), then
    per item upsert the product and bridge or record each component.

    On failure the transaction is rolled back and the error re-raised, rather than
    left half-applied: a mid-run abort previously left the caller's connection in
    an aborted state, so the NEXT feed's first statement failed for reasons that
    had nothing to do with it.
    """
    path = pathlib.Path(items_csv_path)
    if source_checksum is None:
        source_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        run_id = conn.execute(
            "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
            "VALUES (%s, %s, %s) RETURNING ingest_run_id",
            (source, upstream_release, source_checksum)).fetchone()[0]

        local.clear_source_products(conn, source)
        inn_index = classes.moieties_by_scheme(conn, "INN")
        salt_suffixes = pbs.load_salt_suffixes()
        log.info("PBS ingest %s: %d INN claims indexed", upstream_release, len(inn_index))

        items_read = products_bridged = exact_rows = salt_rows = combinations = 0
        unmatched: list[tuple[str, str]] = []

        for item in pbs.parse_items(path):
            items_read += 1
            product_uuid = local.upsert_product(
                conn, item, run_id, jurisdiction=jurisdiction, source=source)
            components = pbs.split_components(item.drug_name or "")
            if len(components) > 1:
                combinations += 1
            bridged_here = False
            for component in components:
                moieties, method = resolve(component, inn_index, salt_suffixes)
                if not moieties:
                    unmatched.append((item.source_code, component))
                    continue
                bridged_here = True
                for moiety_uuid in moieties:
                    if local.add_product_moiety(
                            conn, product_uuid, moiety_uuid, component, method, run_id):
                        if method == "exact":
                            exact_rows += 1
                        else:
                            salt_rows += 1
            if bridged_here:
                products_bridged += 1

        local.add_unmatched_components(
            conn, unmatched, run_id, jurisdiction=jurisdiction, source=source)
        conn.execute(
            "UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s",
            (run_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("PBS ingest failed for release %s; rolled back", upstream_release)
        raise

    summary = PbsSummary(
        items_read=items_read, products_written=items_read,
        products_bridged=products_bridged, bridge_rows_exact=exact_rows,
        bridge_rows_salt_stripped=salt_rows, combination_products=combinations,
        unmatched_components=len(unmatched))
    log.info("PBS ingest %s complete: %s", upstream_release, summary)
    return summary
