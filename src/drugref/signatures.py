# src/drugref/signatures.py
"""Sign one curated row: build its payload from the database, record, verify (db/030).

WHAT THIS MODULE OWNS, and what it deliberately does not. `signing.py` is the PURE half
-- bytes in, bytes out, no connection -- and everything here is the half that touches
one: reading a target row's live content, writing an `assertion_signature` row over it,
and re-deriving that content to check a signature years later. Nothing here decides what
a signature MEANS; that precedence lives in `signing.verdict` and is called, not
repeated.

THE TARGET VOCABULARY LIVES IN `drugref.signature_target_kind`, NEVER IN A PYTHON DICT.
db/006's lesson, applied again: a mapping from `target_kind` to its table, primary-key
column and canonical context is exactly the kind of thing that drifts the moment it
exists in two places, and a fourth target kind (a future `release_manifest` sibling, or
whatever comes after it) must be one INSERT in db/030 rather than an edit here as well.
`payload_fields` composes its SELECT with `psycopg.sql.Identifier` over names read from
that catalog -- they are drugref's own seed values, so there was never an injection, but
composition makes that visible at a glance instead of requiring the reader to trace
where the names came from.

RECORDING AND VERIFYING ARE SEPARATE ACTS. `record` stores whatever it is given and
asserts nothing about validity, deliberately: a recorder that refused an invalid
signature could not store the evidence that an invalid one exists, and reporting that
evidence -- "this row carries a signature that does NOT verify" -- is precisely what a
node needs to be able to do.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules,
and the two append-only triggers on `assertion_signature` mean any attempt to correct a
mistake here is a new row, not an edit -- there is nothing to roll back to, only
something new to add.
"""
import datetime as dt
from dataclasses import dataclass

import psycopg
from psycopg import sql

from drugref import keys, signing


class UnknownTargetError(RuntimeError):
    """Either `target_kind` names no row in `signature_target_kind`, or the target row
    itself does not exist. Raised rather than silently building a payload of NULLs --
    see `payload_fields` for why that would be worse than an exception.
    """


def payload_fields(conn: psycopg.Connection, target_kind: str, target_id: int, *,
                    key_fingerprint: str, signed_at: dt.datetime
                    ) -> tuple[str, list[signing.Field]]:
    """The (name, rendered-value) pairs for one target row, in FROZEN order.

    Returns `(payload_context, fields)`. PUBLIC rather than a private helper: the
    per-field mutation gate in tests/test_signatures_writer.py calls this directly so
    the test exercises the production code path that builds a payload, rather than a
    parallel reimplementation of it that could silently disagree.

    THE TABLE, KEY COLUMN AND CONTEXT COME FROM signature_target_kind -- never from a
    dict in Python. A fourth target kind is then one INSERT in db/030 rather than an
    edit here, in the migration and in a CHECK, which is db/006's lesson.

    The SELECT is composed with psycopg.sql.Identifier over names read from the
    catalogue. They are drugref's own seed values rather than user input, so there was
    never an injection here -- but composition makes that visible at a glance instead of
    requiring the reader to trace where the names came from.
    """
    kind = conn.execute(
        "SELECT target_table, pk_column, payload_context "
        "FROM drugref.signature_target_kind WHERE target_kind = %s",
        (target_kind,)).fetchone()
    if kind is None:
        raise UnknownTargetError(
            f"{target_kind!r} is not a signature target kind. The vocabulary lives in "
            "drugref.signature_target_kind; adding one is an INSERT there.")
    table, pk_column, context = kind
    # The frozen field list includes the two attestation fields (signer_key_fingerprint,
    # signed_at) at its tail, but those are supplied by the CALLER, not read from the
    # target table -- no such columns exist on curated_interaction or curated_condition.
    # Excluding them here is what makes the SELECT below name only real columns.
    field_names = signing.FIELD_LISTS[context]
    row_columns = [f for f in field_names if f not in signing.ATTESTATION_FIELDS]
    row = conn.execute(
        sql.SQL("SELECT {cols} FROM drugref.{table} WHERE {pk} = %s").format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in row_columns),
            table=sql.Identifier(table), pk=sql.Identifier(pk_column)),
        (target_id,)).fetchone()
    if row is None:
        raise UnknownTargetError(
            f"drugref.{table} has no row with {pk_column} = {target_id}. Signing a row "
            "that does not exist would produce a payload of NULLs -- a valid signature "
            "over nothing, which looks like a real one.")
    # strict=True catches row_columns and the fetched row disagreeing in length -- a
    # column the catalog's context names but the live table lost (or vice versa) would
    # otherwise zip silently short and mis-pair every field after the gap.
    fields = [(name, signing.render(value))
              for name, value in zip(row_columns, row, strict=True)]
    # THE ATTESTATION FIELDS, appended last, matching FIELD_LISTS' own tail order. Both
    # are supplied by the caller rather than read from any table: `signed_at` is the
    # instant the curator is attesting AT, which is chosen by whoever is signing, not
    # stored anywhere until `record` writes it; `key_fingerprint` names the key about to
    # sign, which no target row could possibly already contain.
    fields.append(("signer_key_fingerprint", key_fingerprint))
    fields.append(("signed_at", signing.render(signed_at)))
    return context, fields


def payload_for(conn: psycopg.Connection, target_kind: str, target_id: int, *,
                 key_fingerprint: str, signed_at: dt.datetime) -> tuple[str, bytes]:
    """The canonical payload bytes for one target row, ready to sign or to verify
    against. A thin wrapper over `payload_fields` -> `signing.canonical_payload`; kept
    separate from `payload_fields` only because the mutation gate needs the
    (name, value) pairs themselves, not the encoded bytes, to build a mutated payload.
    """
    context, fields = payload_fields(
        conn, target_kind, target_id,
        key_fingerprint=key_fingerprint, signed_at=signed_at)
    return context, signing.canonical_payload(context, fields)


def record(conn: psycopg.Connection, *, target_kind: str, target_id: int,
           payload_context: str, payload: bytes, key_fingerprint: str,
           signature: bytes, signed_at: dt.datetime,
           algorithm: str = signing.ED25519) -> int:
    """Store one signature. Returns the new `signature_id`.

    RECORDING IS NOT VERIFYING, and that is deliberate rather than an oversight: a
    recorder that refused an invalid signature could not store the evidence that an
    invalid one exists, which is precisely what a node needs to be able to report (a
    forged or corrupted signature is itself a finding, not something to discard).
    Whether `signature` is valid over `payload` is `verify_target`'s question, asked
    fresh every time rather than cached -- spec 7.3 is explicit that no verification
    result is ever stored in a column.

    `payload_digest` (not the payload itself) is what gets stored, alongside the
    fingerprint and instant the payload already commits to -- `signing.digest` is a
    comparison key for dedup and manifest lookups, never the thing signed.
    """
    return conn.execute(
        "INSERT INTO drugref.assertion_signature "
        "(target_kind, target_id, payload_context, payload_digest, key_fingerprint, "
        " algorithm, signature, signed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING signature_id",
        (target_kind, target_id, payload_context, signing.digest(payload),
         key_fingerprint, algorithm, signature, signed_at)).fetchone()[0]


@dataclass(frozen=True)
class SignatureVerdict:
    """What one recorded signature is worth, right now. Assembled fresh on every call
    to `verify_target` -- never stored, per spec 7.3: a cached "verified" flag is a
    claim nothing re-checks, which is the failure mode this whole layer exists to
    remove. `holder` is `None` exactly when `verdict` is `signing.UNKNOWN_KEY`: without
    a registered key there is no holder to report.
    """
    signature_id: int
    key_fingerprint: str
    holder: str | None
    signed_at: dt.datetime
    verdict: str


def verify_target(conn: psycopg.Connection, target_kind: str,
                   target_id: int) -> list[SignatureVerdict]:
    """Every signature recorded against one target row, each checked fresh.

    UNSIGNED IS NOT AN ERROR: a target with no recorded signature returns `[]`, because
    signing is optional per row and the overlay ships with most rows unsigned.

    EACH SIGNATURE IS REBUILT AGAINST ITS OWN signed_at AND key_fingerprint, not one
    shared payload for the whole row. Both values are INSIDE the signed bytes (spec
    4.4), so two signatures over one row -- a counter-signature, or a re-sign after a
    key rotation -- cover genuinely DIFFERENT bytes. Building one payload and reusing it
    for every row would make every signature but the one it happened to match look like
    a forgery, which is the worst possible way for this bug to present: silent, and
    indistinguishable from an actual attack.

    ORDERED BY signature_id, i.e. the order signatures were recorded in -- the surrogate
    key, not `signed_at`, which an operator may supply out of order (a late-recorded
    signature can honestly claim an earlier instant); `keys.history`'s precedent.
    """
    rows = conn.execute(
        "SELECT signature_id, key_fingerprint, signature, signed_at "
        "FROM drugref.assertion_signature "
        "WHERE target_kind = %s AND target_id = %s "
        "ORDER BY signature_id",
        (target_kind, target_id)).fetchall()
    verdicts = []
    for signature_id, key_fingerprint, signature, signed_at in rows:
        _, payload = payload_for(
            conn, target_kind, target_id,
            key_fingerprint=key_fingerprint, signed_at=signed_at)
        # THE TWO REGISTRY LOOKUPS, not one: `live` gives the public key material (and
        # the holder name to report), `key_status` gives the two booleans the verdict
        # rule branches on. Both come back None together for an unregistered
        # fingerprint -- there is no live signing_key row to join against either way.
        key = keys.live(conn, key_fingerprint)
        status = keys.key_status(conn, key_fingerprint)
        # WITH NO REGISTERED KEY THERE IS NO MATHEMATICS TO CHECK -- `signing.verdict`
        # ignores `signature_ok` entirely when `key_status` is None (UNKNOWN_KEY
        # outranks it), so `False` here is never read as "this signature is bad" rather
        # than "this key is unknown".
        signature_ok = key is not None and signing.verify(
            key.public_key, payload, signature)
        verdicts.append(SignatureVerdict(
            signature_id=signature_id,
            key_fingerprint=key_fingerprint,
            holder=key.holder if key else None,
            signed_at=signed_at,
            verdict=signing.verdict(
                status, signature_ok=signature_ok, signed_at=signed_at)))
    return verdicts
