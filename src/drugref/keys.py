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


class NoLiveKeyError(signing.SigningError):
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

# THE ONE PYTHON SPELLING OF THE REGISTRATION STATUS, on signing.ED25519's precedent and
# for its reason. `signing_key_status_kind` is this vocabulary's home and `signing_key.
# status` is a FOREIGN KEY into it -- but that column has no SQL DEFAULT, so Python must
# write SOME value when registering. This constant exists because of that, not as a
# second list to disagree with the first: an unrecognised value raises
# ForeignKeyViolation from the database, which is the intended behaviour.
ACTIVE = "active"


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
    # NORMALISE bytea TO `bytes` SO THE ANNOTATION ABOVE IS TRUE, and note carefully
    # what this is NOT for. An earlier draft of this comment claimed psycopg returns
    # bytea as `memoryview` and that equality needs the cast. Both halves are false,
    # measured on this project's pinned psycopg 3.3.4: bytea decodes to `bytes` in
    # text AND binary mode, and `memoryview(b"a") == b"a"` is True regardless. What
    # the cast actually buys is that `isinstance(record.public_key, bytes)` holds
    # whatever a future adapter or driver version returns -- equality would survive a
    # memoryview, `isinstance` and anything type-dispatching on it would not. Pinned
    # by a test asserting the type.
    values["public_key"] = bytes(values["public_key"])
    return KeyRecord(**values)


_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM drugref.signing_key "

# `for_verification`'s one query (issue 87), assembled from _COLUMNS rather than from
# a second hand-written column list, so `_record` keeps unpacking positionally and a
# column added to _COLUMNS reaches this read for free. The four status columns are
# appended AFTER the record's, which is what lets the caller split the row on
# `len(_COLUMNS)`.
#
# THE LATERAL IS `key_status`'s QUERY, unchanged in every clause that matters --
# history-wide (`superseded_by IS NULL OR invalidates_all_signatures`), blanket-first,
# earliest-compromise on the tiebreak -- correlated to the live row's fingerprint
# instead of taking its own parameter. See `for_verification` for why it must not be
# flattened into the outer row read, and why CROSS rather than LEFT is right here.
_FOR_VERIFICATION = (
    f"SELECT {', '.join('live.' + c for c in _COLUMNS)}, "
    "st.status, st.is_revocation, st.invalidates_all_signatures, st.status_from "
    "FROM drugref.signing_key live "
    "CROSS JOIN LATERAL ("
    "  SELECT k.status, t.is_revocation, t.invalidates_all_signatures, k.status_from "
    "  FROM drugref.signing_key k "
    "  JOIN drugref.signing_key_status_kind t ON t.status = k.status "
    "  WHERE k.key_fingerprint = live.key_fingerprint "
    "    AND (k.superseded_by IS NULL OR t.invalidates_all_signatures) "
    "  ORDER BY t.invalidates_all_signatures DESC, k.signing_key_id "
    "  LIMIT 1"
    ") st "
    "WHERE live.key_fingerprint = %s AND live.superseded_by IS NULL")


def register(conn: psycopg.Connection, *, public_key: bytes, holder: str,
             registered_by: str, algorithm: str = signing.ED25519,
             status: str = ACTIVE,
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

    THE WHOLE HISTORY IS READ, NOT THE LIVE ROW ALONE, and that is a security property
    rather than a nicety. A BLANKET revocation is PERMANENT: once any row for this
    fingerprint carries `invalidates_all_signatures`, no later row can take it back.

    Reading the live row alone was the defect. `revoke` writes whatever status it is
    handed and refuses no transition, so `drugref keys revoke --status active` on a
    compromised key -- one supported command, no raw SQL, no superuser, no dropped
    trigger -- silently returned every signature that key ever made to `valid`,
    INCLUDING every one the thief made with the stolen private half. Blanket revocation
    is the design's ONLY answer to a stolen key (spec 5.2, 7.4), and it was undoable by
    the same command that applied it.

    THIS IS WHAT db/030 SECTION 3 ALREADY PROMISED. Its comment justifies the whole
    insert-then-supersede shape on the grounds that "the full status history of a key is
    therefore readable, which is the only thing that makes 'was this key already revoked
    when that signature was made?' answerable" -- and until this query, nothing anywhere
    read that history. It was written every time and consulted never.

    ONLY BLANKET REVOCATIONS ARE PERMANENT. `rotated` and `retired` stay reversible,
    because a mistaken revocation must be correctable on an append-only floor and a new
    laptop must not unsound a curator's past work. Which statuses are permanent is read
    off `invalidates_all_signatures` -- still the vocabulary table's answer, never a
    status name spelled again here.

    `ORDER BY ... DESC` puts a blanket row ahead of the live one; the `signing_key_id`
    tiebreak picks the EARLIEST compromise, so `status_from` reports when the key was
    first declared lost rather than when it was last re-declared. `LIMIT 1` then yields
    the blanket row if the key has ever carried one, and the live row otherwise.
    """
    row = conn.execute(
        "SELECT k.status, t.is_revocation, t.invalidates_all_signatures, k.status_from "
        "FROM drugref.signing_key k "
        "JOIN drugref.signing_key_status_kind t ON t.status = k.status "
        "WHERE k.key_fingerprint = %s "
        "  AND (k.superseded_by IS NULL OR t.invalidates_all_signatures) "
        "ORDER BY t.invalidates_all_signatures DESC, k.signing_key_id "
        "LIMIT 1",
        (key_fingerprint,)).fetchone()
    if row is None:
        return None
    status, is_revocation, invalidates, status_from = row
    return signing.KeyStatus(status, is_revocation=is_revocation,
                             invalidates_all_signatures=invalidates,
                             status_from=status_from)


@dataclass(frozen=True)
class RegisteredKey:
    """Everything a verifier needs about one fingerprint, read in ONE query (issue 87).

    NEITHER FIELD IS OPTIONAL, and that is the whole point. `signatures.
    SignatureVerdict` documents that `holder is None` EXACTLY when the verdict is
    `signing.UNKNOWN_KEY` -- but that held only because `live` and `key_status` happened
    to run compatible predicates against the same table. Nothing enforced it: an edit to
    either predicate could have produced a holder with no status rule, or a status rule
    with no holder, and the verifier would have reported a verdict assembled from half a
    key. Here there is ONE `None` to test -- this record or nothing -- so the two halves
    cannot disagree about whether the key exists.

    THE TWO FIELDS ANSWER DIFFERENT QUESTIONS OVER DIFFERENT ROW SETS, which is why this
    is a record rather than a widened `KeyRecord`:

      * `record` is the key's LIVE row -- the material to check the signature against,
        and the holder to name in the verdict.
      * `status` is the rule that governs the key, taken over its WHOLE HISTORY,
        because a blanket revocation is PERMANENT: no later row can take it back.

    Collapsing them onto the live row would reinstate the defect `key_status` was
    written to fix, and would do so silently: `drugref keys revoke --status active` on
    a compromised key -- one supported command, no raw SQL -- would return every
    signature that key ever made to `valid`, the thief's included.
    """
    record: KeyRecord
    status: signing.KeyStatus


def for_verification(conn: psycopg.Connection,
                     key_fingerprint: str) -> RegisteredKey | None:
    """The key material AND the status rule, in one round trip. None if unregistered.

    WHY ONE QUERY (issue 87). `signatures.verify_target` and
    `release_verification._verify_manifest_signature` each asked the registry the same
    question twice about one fingerprint. `verify_target` is called once per curated
    row across the whole overlay by the release verifier -- its docstring already
    records hoisting the catalog lookup and the target-row read out of the
    per-signature loop for exactly this reason -- so the pair left behind was the last
    per-key duplication in the hot loop. `tests/test_keys_writer.py` counts the
    queries, with the old pair measured beside it as the control.

    HOW THE TWO HALVES STAY DIFFERENT. The outer query is `live`'s: the single row
    with `superseded_by IS NULL`, which the deferred single-live trigger guarantees is
    one row or none. The LATERAL is `key_status`'s, verbatim -- history-wide,
    blanket-first, earliest-compromise on the tiebreak -- correlated to that
    fingerprint. Merging them into one flat row read would take BOTH from the live row
    and undo the permanence of a compromise; the equivalence tests drive every history
    shape either function is documented to care about, and the material/status split
    has a test of its own.

    CROSS JOIN, NOT LEFT JOIN, and it cannot drop the row: the live row always
    satisfies the LATERAL's own `superseded_by IS NULL` arm, and `signing_key.status`
    is a FOREIGN KEY into `signing_key_status_kind`, so its join to the vocabulary
    table always finds a match. A LEFT JOIN here would be defensive against a state
    the schema forbids, and would hand back a `RegisteredKey` with a NULL status --
    reintroducing the half-a-key shape this function exists to make unrepresentable.

    `live` AND `key_status` REMAIN, deliberately. `revoke` and `cli_signing_release`
    need the live row alone, and a caller wanting only the status rule should not have
    to take the key material with it. This is a third read for the one caller that
    needs both, not a replacement for two that work.
    """
    row = conn.execute(_FOR_VERIFICATION, (key_fingerprint,)).fetchone()
    if row is None:
        return None
    record = _record(row[:len(_COLUMNS)])
    status, is_revocation, invalidates, status_from = row[len(_COLUMNS):]
    return RegisteredKey(
        record=record,
        status=signing.KeyStatus(status, is_revocation=is_revocation,
                                 invalidates_all_signatures=invalidates,
                                 status_from=status_from))


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
