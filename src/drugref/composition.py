# src/drugref/composition.py
"""The ONLY module that writes drugref.substance_composition (slice 3).

Mirrors conditions.py and classes.py: a rebuildable projection, so the writer owns
a per-source clear as well as an insert. `substance_composition` is REBUILT
wholesale on re-ingest -- a salt whose composition upstream corrects has to be able
to lose a component, which an insert-only merge could never express.

WHAT THIS MODULE DOES NOT DO: mint identity. The composite side of every row is a
UNII from the source, not a drugref UUID, because 4,425 of 7,377 composites are not
moieties and slice 3 deliberately creates no second registry.
"""
import uuid

import psycopg

from drugref import db

# Restated independently in tests/test_source_clear_contract.py, so that dropping a
# table here fails loudly rather than leaving a projection that grows on every
# ingest (#43).
COMPOSITION_TABLES = ("substance_composition",)


def clear_source_composition(conn: psycopg.Connection, source: str) -> None:
    """Drop every composition row contributed by `source`.

    Called at the start of a re-ingest so a new upstream release fully REPLACES the
    previous one, scoped by source so no other feed's rows are touched.
    """
    db.clear_source_tables(conn, COMPOSITION_TABLES, source)


def moiety_uuid_by_unii(conn: psycopg.Connection) -> dict[str, uuid.UUID]:
    """Every live UNII claim, as a UNII -> moiety_uuid map.

    Loaded once per run rather than queried per edge: the registry is ~19,438 rows
    against ~15,200 candidate edges, so one scan beats 15,200 round trips. This is
    the same shape the row-at-a-time ingests filed as #7/#29 got wrong.

    Superseded claims are excluded: a corrected claim's OLD value must not resolve.
    """
    rows = conn.execute(
        "SELECT value, moiety_uuid FROM drugref.identity_claim "
        "WHERE scheme = 'UNII' AND superseded_by IS NULL").fetchall()
    return {value: moiety_uuid for value, moiety_uuid in rows}


def add_composition(conn: psycopg.Connection, *, substance_unii: str,
                    component_moiety: uuid.UUID, relation: str,
                    is_active_component: bool | None,
                    ingest_run_id: int) -> bool:
    """Record that `substance_unii` is composed of `component_moiety`.

    Returns True if a new row was written. ON CONFLICT DO NOTHING keeps a release
    that states one edge from BOTH ends harmless -- GSRS stores ~15,039 of its
    ~15,100 salt edges twice, and the parser normalises both encodings to one edge.

    `is_active_component` is passed through unchanged, INCLUDING None. None means
    the release ruled on nothing, and turning it into False here would manufacture
    an answer no authority gave.
    """
    cur = conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (substance_unii, component_moiety, relation, is_active_component,
         ingest_run_id))
    return cur.rowcount == 1
