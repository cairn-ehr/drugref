# src/drugref/overlay.py
"""The append-only curated overlay's one correction primitive.

WHAT THE OVERLAY TIER IS. drugref stores two kinds of thing. Ingested feeds are
REBUILDABLE PROJECTIONS -- dropped and rebuilt per release, because a fact upstream
retracts has to be able to disappear. Curated knowledge is an APPEND-ONLY OVERLAY:
nothing is edited in place and nothing is deleted, because "what did we last say about
this, against which release, and why did we change our mind" has to be answerable from
the database. db/020 built the floor that enforces it; db/027 put a fifth table on it.

THE ONE SEQUENCE THAT TIER ADMITS, and why it is a function rather than a paragraph of
documentation telling every writer to get it right:

    1. INSERT the new assertion, which becomes live.
    2. UPDATE whatever was live for the same natural key to point at it.

In that order, always. `superseded_by` is a foreign key to a row that must already
exist, so pointing first cannot work -- and getting the order backwards fails at
COMMIT, arbitrarily far from the call that caused it.

BOTH ROWS ARE BRIEFLY LIVE, between the INSERT and the UPDATE, and that is exactly why
single-live is a DEFERRED CONSTRAINT TRIGGER rather than a partial unique index: an
immediate check would reject the only sequence that can express a correction. Spec
5.0 proposed the index; db/007 met the problem first on `question_state` and db/020
generalised the trigger. Published as `decisions/correcting-a-curated-assertion.md`.

NOT EVERY APPEND-ONLY WRITE IS A SUPERSESSION. `claims.add_claim` uses ON CONFLICT DO
NOTHING scoped to live rows: re-asserting the same identity claim is idempotent, not a
correction, and routing it through here would write a supersession where db/005 wants
a no-op.
"""
import psycopg
from psycopg import sql


def supersede(conn: psycopg.Connection, table: str, pk_column: str, new_id: int,
              key_columns: tuple[str, ...], key_values: tuple) -> None:
    """Point whatever was live at `new_id`. Called AFTER the new row exists.

    Kept in one place because the ordering is the part that is easy to get wrong, and
    getting it wrong fails only at COMMIT -- long after the call that caused it.

    The natural key arrives as COLUMN NAMES rather than a pre-built SQL fragment, and
    the statement is composed with psycopg.sql. Every call site passes literals, so
    there was never an injection here -- but proving that took reading all of them, and
    composition makes it visible at a glance instead. It also puts the columns in the
    same shape db/020's triggers take them, which is what they are.

    `{pk} <> %s` keeps the row just written out of its own supersession.

    NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these
    modules, and the single-live check is DEFERRED -- so a mistake surfaces at the
    caller's COMMIT, not here.
    """
    where = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(col)) for col in key_columns)
    conn.execute(
        sql.SQL("UPDATE drugref.{table} SET superseded_by = %s "
                "WHERE {where} AND superseded_by IS NULL AND {pk} <> %s").format(
            table=sql.Identifier(table), where=where, pk=sql.Identifier(pk_column)),
        (new_id, *key_values, new_id))
