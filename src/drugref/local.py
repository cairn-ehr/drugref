# src/drugref/local.py
"""The ONLY module that writes the local (jurisdiction-specific) tier.

It mirrors classes.py, not claims.py, and the difference matters:

* claims.py guards an APPEND-ONLY spine -- substance identity is immortal and the
  database floor rejects UPDATE/DELETE outright.
* This module manages a REBUILDABLE PROJECTION. PBS republishes monthly, and an
  item it DE-LISTS must disappear here too -- which an insert-only merge can never
  express. So clear_source_products deliberately DELETEs.

What survives a rebuild unchanged is product IDENTITY: local_product_uuid is a
pure function of (jurisdiction, source, code), so every surviving product comes
back with exactly the UUID it had before (src/drugref/ids.py).
"""
import uuid
from collections.abc import Iterable

import psycopg

from drugref import db, ids
from drugref.ingest.pbs import PbsItem


def upsert_product(conn: psycopg.Connection, item: PbsItem, ingest_run_id: int,
                   jurisdiction: str = "AU", source: str = "PBS") -> uuid.UUID:
    """Register a local product, or refresh its cached attributes on re-ingest.

    The UUID is DERIVED, never looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the descriptive columns -- brands are renamed and items
    move between programs -- while the identity columns are, by construction, the
    same values the UUID was minted from and so cannot change.
    """
    product_uuid = ids.mint_local_product_uuid(jurisdiction, source, item.source_code)
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, pbs_code, brand_name, drug_name, form_strength, program_code, "
        "benefit_type_code, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (local_product_uuid) DO UPDATE SET "
        "  pbs_code = EXCLUDED.pbs_code, brand_name = EXCLUDED.brand_name, "
        "  drug_name = EXCLUDED.drug_name, form_strength = EXCLUDED.form_strength, "
        "  program_code = EXCLUDED.program_code, "
        "  benefit_type_code = EXCLUDED.benefit_type_code, "
        "  ingest_run = EXCLUDED.ingest_run",
        (product_uuid, jurisdiction, source, item.source_code, item.pbs_code,
         item.brand_name, item.drug_name, item.form_strength, item.program_code,
         item.benefit_type_code, ingest_run_id))
    return product_uuid


# ORDER IS LOAD-BEARING, not cosmetic: both of the first two reference
# local_product, so listing the parent first makes the foreign key refuse the
# delete. This is the one table tuple in the codebase whose order can fail.
LOCAL_PRODUCT_TABLES = ("local_product_moiety", "local_unmatched_ingredient",
                        "local_product")


def clear_source_products(conn: psycopg.Connection, source: str) -> None:
    """Drop every product, bridge row and unmatched note contributed by `source`.

    Called at the start of a re-ingest so a new monthly release fully REPLACES the
    previous one. Order matters: the bridge and the unmatched list are deleted
    BEFORE the products they reference, or the foreign key refuses the delete --
    and db.clear_source_tables preserves the order it is given for exactly this.

    Scoped by source (via ingest_run) so another jurisdiction's or authority's
    rows survive -- the same per-source discipline classes.clear_source_edges uses.
    """
    db.clear_source_tables(conn, LOCAL_PRODUCT_TABLES, source)


def add_product_moiety(conn: psycopg.Connection, product_uuid: uuid.UUID,
                       moiety_uuid: uuid.UUID, component_name: str,
                       match_method: str, ingest_run_id: int) -> bool:
    """Link a local product to a moiety. Returns True if newly inserted.

    `match_method` ('exact' | 'salt_stripped') is stored per row so a consumer can
    discard the salt-strip heuristic wholesale rather than having to trust it --
    the heuristic stands in for slice 3 and should not masquerade as certainty.
    """
    cur = conn.execute(
        "INSERT INTO drugref.local_product_moiety (local_product_uuid, moiety_uuid, "
        "component_name, match_method, ingest_run) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (product_uuid, moiety_uuid, component_name, match_method, ingest_run_id))
    return cur.rowcount == 1


def add_unmatched_components(conn: psycopg.Connection,
                             rows: Iterable[tuple[str, str]], ingest_run_id: int,
                             jurisdiction: str = "AU", source: str = "PBS") -> int:
    """Record ingredient names that resolved to no moiety. Returns rows written.

    Not an error and not a silent drop. PBS lists foods, dressings and
    extemporaneous chemicals that slice 1's gate excludes BY DESIGN, so a healthy
    ingest still produces these -- and persisting them is what makes coverage a
    query instead of an impression (spec section 7).

    Batched via executemany: this is thousands of rows on a real release, and
    unlike add_product_moiety no caller needs the per-row insert-vs-conflict answer.
    """
    batch = [(ingest_run_id, jurisdiction, source, source_code, component_name)
             for source_code, component_name in rows]
    if not batch:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO drugref.local_unmatched_ingredient "
            "(ingest_run, jurisdiction, source, source_code, component_name) "
            "VALUES (%s, %s, %s, %s, %s)", batch)
    return len(batch)
