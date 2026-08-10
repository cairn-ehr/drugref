# src/drugref/keys.py
"""The signing-key registry: who drugref trusts to sign a curated judgement (db/030).

THE PRIVATE HALF NEVER ENTERS THIS DATABASE, or any drugref infrastructure. That is the
entire value of the row layer: an insider with total write access can still type any
name into curated_*.reviewed_by, but cannot produce a signature over the row. Store a
private key here and the layer proves exactly what the unauthenticated text column
already claimed.

THE TRUST ROOT IS AN OPERATOR. A public key is trusted because somebody with database
access ran `drugref keys register`. There is no enrolment protocol, no web of trust
and no certificate chain -- a real limitation, recorded in the spec rather than
papered over.

REVOCATION IS A CORRECTION, not an edit: INSERT the new status, then point the live row
at it through overlay.supersede -- the same sequence every curated table uses, and the
reason a key's status history survives at all. Without that history, "was this key
already revoked when that signature was made?" has no answer, and the whole time-scoped
half of the revocation model collapses.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules,
and the single-live check is DEFERRED -- so registering a second live row for one
fingerprint surfaces at the caller's COMMIT, not here.
"""
import datetime as dt
from dataclasses import dataclass

import psycopg

from drugref import overlay, signing


class NoLiveKeyError(RuntimeError):
    """Revoking a key with no live row. Raised rather than no-op'ing.

    interactions.NoLiveDecisionError's precedent, and the argument is the same one
    turned up a notch: an operator who mistypes a fingerprint and is told nothing walks
    away believing a compromised key has been revoked. Silence is the worst answer a
    revocation command can give.
    """


# THE ONE COLUMN LIST, generating the SELECT and binding the record BY KEYWORD --
# curation._UNRESOLVED_COLUMNS' shape and its reason. Positionally, `key_fingerprint`,
# `algorithm`, `holder`, `status` and `registered_by` are all text and the two
# timestamps are interchangeable, so a transposition builds a WELL-TYPED WRONG record
# that no annotation and no arity check can see. Binding by name removes the failure
# mode instead of testing for it; strict=True catches a column gained or lost.
_COLUMNS = ("signing_key_id", "key_fingerprint", "public_key", "algorithm", "holder",
            "status", "status_from", "registered_by", "registered_at", "superseded_by")


@dataclass(frozen=True)
class KeyRecord:
    """One row of signing_key -- a key's state at one point in its history."""
    signing_key_id: int
    key_fingerprint: str
    public_key: bytes
    algorithm: str
    holder: str
    status: str
    status_from: dt.datetime
    registered_by: str
    registered_at: dt.datetime
    superseded_by: int | None


def _record(row) -> KeyRecord:
    values = dict(zip(_COLUMNS, row, strict=True))
    # psycopg returns bytea as `memoryview`; the caller compares it against the bytes it
    # generated, and memoryview(b"a") != b"a" is False but `==` is True only after a
    # cast. Normalise once, here, rather than at every call site.
    values["public_key"] = bytes(values["public_key"])
    return KeyRecord(**values)


_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM drugref.signing_key "


def register(conn: psycopg.Connection, *, public_key: bytes, holder: str,
             registered_by: str, algorithm: str = signing.ED25519,
             status: str = "active",
             status_from: dt.datetime | None = None) -> int:
    """Register a public key. Returns the new signing_key_id.

    TAKES THE KEY, DERIVES THE FINGERPRINT. Accepting both would let a caller store a
    fingerprint that does not match its key -- a row that matches no signature, reports
    UNKNOWN_KEY forever, and looks perfectly healthy in `drugref keys list`.

    `status_from` defaults to the database's `now()` rather than to a Python clock, so a
    registration cannot be dated by a machine whose time is wrong relative to the server
    that stamps `registered_at` beside it. It is settable so a test can pin an instant.
    """
    return conn.execute(
        "INSERT INTO drugref.signing_key (key_fingerprint, public_key, algorithm, "
        "holder, status, status_from, registered_by) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s) "
        "RETURNING signing_key_id",
        (signing.fingerprint(public_key), public_key, algorithm, holder, status,
         status_from, registered_by)).fetchone()[0]


def revoke(conn: psycopg.Connection, *, key_fingerprint: str, status: str,
           revoked_by: str, status_from: dt.datetime | None = None) -> int:
    """Change a key's status by CORRECTION. Returns the new signing_key_id.

    Raises NoLiveKeyError if nothing is live for that fingerprint.

    THE KEY MATERIAL AND HOLDER ARE CARRIED FORWARD from the live row, never re-supplied
    by the caller -- withdraw_expansion_decision carries class_name forward for exactly
    this reason. Taking them as arguments would let a revocation quietly re-attribute a
    key to a different holder, under the one command an operator runs when they are
    already alarmed.

    `revoked_by` lands in `registered_by`: the column records WHO PUT THIS ROW HERE, and
    for a revocation that is the revoker. A separate `revoked_by` column would be a
    second name for one fact, NULL on every other row.
    """
    current = live(conn, key_fingerprint)
    if current is None:
        raise NoLiveKeyError(
            f"no live signing key for fingerprint {key_fingerprint}. Nothing was "
            "changed. Check `drugref keys list` -- a mistyped fingerprint here would "
            "otherwise leave you believing a key had been revoked.")
    new_id = conn.execute(
        "INSERT INTO drugref.signing_key (key_fingerprint, public_key, algorithm, "
        "holder, status, status_from, registered_by) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s) "
        "RETURNING signing_key_id",
        (current.key_fingerprint, current.public_key, current.algorithm,
         current.holder, status, status_from, revoked_by)).fetchone()[0]
    overlay.supersede(conn, "signing_key", "signing_key_id", new_id,
                      ("key_fingerprint",), (key_fingerprint,))
    return new_id


def live(conn: psycopg.Connection, key_fingerprint: str) -> KeyRecord | None:
    """The key's current row, or None if no key with that fingerprint is registered.

    NONE IS NOT AN ERROR: a signature naming an unregistered key is the UNKNOWN_KEY
    verdict, which is an ordinary thing for a verifier to report.
    """
    row = conn.execute(
        _SELECT + "WHERE key_fingerprint = %s AND superseded_by IS NULL",
        (key_fingerprint,)).fetchone()
    return _record(row) if row else None


def key_status(conn: psycopg.Connection,
               key_fingerprint: str) -> signing.KeyStatus | None:
    """What the verdict rule needs to know about a key. None if unregistered.

    THE TWO BOOLEANS COME FROM signing_key_status_kind, never from a Python mapping over
    `status`. That table is where the revocation rule lives (db/030 section 1), and a
    second copy here is the defect db/006 named and four rounds have paid for.
    """
    row = conn.execute(
        "SELECT k.status, t.is_revocation, t.invalidates_all_signatures, k.status_from "
        "FROM drugref.signing_key k "
        "JOIN drugref.signing_key_status_kind t ON t.status = k.status "
        "WHERE k.key_fingerprint = %s AND k.superseded_by IS NULL",
        (key_fingerprint,)).fetchone()
    if row is None:
        return None
    status, is_revocation, invalidates, status_from = row
    return signing.KeyStatus(status, is_revocation=is_revocation,
                             invalidates_all_signatures=invalidates,
                             status_from=status_from)


def all_live(conn: psycopg.Connection) -> list[KeyRecord]:
    """Every currently-registered key, ordered by holder then fingerprint.

    TOTALLY ORDERED, on curation.unresolved_targets' precedent: several keys for one
    holder is the ordinary state during a rotation, and those tie on `holder` alone --
    which would leave `drugref keys list` printing a different order run to run and any
    multi-row test flaking.
    """
    return [_record(row) for row in conn.execute(
        _SELECT + "WHERE superseded_by IS NULL ORDER BY holder, key_fingerprint"
        ).fetchall()]


def history(conn: psycopg.Connection, key_fingerprint: str) -> list[KeyRecord]:
    """One key's whole status history, OLDEST FIRST.

    Ordered on the surrogate key rather than on `status_from`, which an operator may
    supply out of order (dating a revocation to when the laptop was actually stolen is
    the realistic case). The surrogate key is the order the rows were WRITTEN, which is
    the one the supersession chain follows.
    """
    return [_record(row) for row in conn.execute(
        _SELECT + "WHERE key_fingerprint = %s ORDER BY signing_key_id",
        (key_fingerprint,)).fetchall()]
