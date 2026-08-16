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

VERIFICATION RECONSTRUCTS THE PAST, NOT THE PRESENT -- a correction made after review
found the first draft re-deriving `payload_context` from the catalog's CURRENT row on
every check, which is right for SIGNING (a new signature is always made under today's
context) and silently wrong for VERIFYING: the day a second context version is minted,
every historical signature would be rebuilt under the new one, produce different bytes,
and be reported as a forgery. `payload_context` is therefore an OVERRIDE on
`payload_fields`/`payload_for` -- `None` reads the catalog's current value, an explicit
value pins one -- and `verify_target` always passes the value STORED on the signature
row it is checking, never the catalog's idea of "current". `signing.FIELD_LISTS` keeps
every retired context version for exactly this reason: a version is stopped being
minted, never deleted. `algorithm` carries the identical hazard and the identical fix --
recorded per row, read back rather than assumed, because `signing.verify` only
implements one scheme.

RECORDING AND VERIFYING ARE SEPARATE ACTS. `record` stores whatever it is given and
asserts nothing about the SIGNATURE's validity, deliberately: a recorder that refused an
invalid signature could not store the evidence that an invalid one exists, and reporting
that evidence -- "this row carries a signature that does NOT verify" -- is precisely
what a node needs to be able to do. It DOES check that `payload_context` truthfully
names the bytes it is being stored beside (see `record`'s docstring) -- that is not a
check on the signature, only on `record`'s own inputs agreeing with each other, and it
guards a mistake this insert-only table gives no later step a chance to correct.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules,
and `assertion_signature_insert_only` -- ONE trigger, covering both UPDATE and DELETE
through `forbid_any_rewrite` -- means any attempt to correct a mistake here is a new
row, not an edit: there is nothing to roll back to, only something new to add.
"""
import datetime as dt
from dataclasses import dataclass

import psycopg
from psycopg import sql

from drugref import keys, signing


class UnknownTargetError(signing.SigningError):
    """Either `target_kind` names no row in `signature_target_kind`, or the target row
    itself does not exist. Raised rather than silently building a payload of NULLs --
    see `_row_content_fields` for why that would be worse than an exception, and
    `verify_target` for why a target row that never existed must raise here too rather
    than reading identically to an ordinary unsigned one.
    """


class DeclaredContextMismatchError(signing.SigningError):
    """A payload whose first bytes disagree with the `payload_context` recorded
    beside it. Refused rather than stored: `assertion_signature` is insert-only, so
    a signature whose declared context can never reproduce its own bytes would be a
    permanently unverifiable row."""


class UnsupportedAlgorithmError(signing.SigningError):
    """A signature names an algorithm this module cannot mathematically check.
    `signing.verify` implements Ed25519 only, so a row naming anything else must be
    reported as unverifiable rather than silently checked against the wrong scheme --
    the same failure mode `payload_context` re-derivation risked for the canonical
    format, one column over. Unreachable today because `assertion_signature`'s CHECK
    admits exactly one value, which is exactly what makes shipping this gap easy: db/030
    itself says "a second algorithm is stored per key and per signature so it is
    additive rather than a rewrite" -- this is the module's half of honouring that.
    """


def _target_kind_catalog(conn: psycopg.Connection,
                          target_kind: str) -> tuple[str, str, str]:
    """`(target_table, pk_column, current payload_context)` for one `target_kind`.

    Split out so `verify_target` can look this up ONCE per call rather than once per
    signature: all three values depend only on `target_kind`, which does not vary
    across the signatures checked in one call. It is THE one function that reads
    `drugref.signature_target_kind` -- db/006's lesson, and the point of the split.

    IT HAS CALLERS OUTSIDE THIS MODULE, despite the leading underscore, and that is
    stated here so a future "this is private, inline it" pass gets a signal from the
    code rather than from a failing test: `releases.enumerate_live`, `releases.publish`,
    `releases.natural_key_of` and `release_verification._published_content_is_history`
    all call it. An earlier version of this docstring claimed "exactly one place reads
    signature_target_kind" while `releases.py` ran its own near-duplicate SELECT
    (`_target_table`) justified on the grounds that this function was private to
    signatures.py -- two false statements propping each other up. The duplicate is
    deleted; those callers now come here.
    """
    kind = conn.execute(
        "SELECT target_table, pk_column, payload_context "
        "FROM drugref.signature_target_kind WHERE target_kind = %s",
        (target_kind,)).fetchone()
    if kind is None:
        raise UnknownTargetError(
            f"{target_kind!r} is not a signature target kind. The vocabulary lives in "
            "drugref.signature_target_kind; adding one is an INSERT there.")
    return kind


def _row_content_fields(conn: psycopg.Connection, table: str, pk_column: str,
                         target_id: int, context: str) -> list[signing.Field]:
    """One target row's own columns, rendered, in the order `context`'s frozen field
    list names -- everything EXCEPT the two attestation fields, which are supplied per
    signature rather than read from any table (see `payload_fields`).

    `context` picks which frozen field list -- and therefore which columns -- to read,
    which is exactly why this is safe to CACHE keyed by context but never to share
    across different ones: `signing.FIELD_LISTS` keeps every retired version, and a
    future `/v2` field list may name a different column set than `/v1` for the same
    table.

    IT HAS CALLERS OUTSIDE THIS MODULE, despite the leading underscore, for the same
    reason `_target_kind_catalog` does and recorded here for the same purpose (a static
    signal against a future "inline this private helper" pass):
    `releases.enumerate_live` and
    `release_verification._published_content_is_history` both call it, deliberately,
    rather than paying `payload_for`'s per-row catalog round trip -- see
    `enumerate_live`'s own docstring for the measurement that made that the intended
    path.
    """
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
    # column the frozen list names that the live table lost (or vice versa) would
    # otherwise zip silently short and mis-pair every field after the gap.
    return [(name, signing.render(value))
            for name, value in zip(row_columns, row, strict=True)]


def payload_fields(conn: psycopg.Connection, target_kind: str, target_id: int, *,
                    key_fingerprint: str, signed_at: dt.datetime,
                    payload_context: str | None = None
                    ) -> tuple[str, list[signing.Field]]:
    """The (name, rendered-value) pairs for one target row, in FROZEN order.

    Returns `(payload_context, fields)`. PUBLIC rather than a private helper: the
    per-field mutation gate in tests/test_signatures_writer.py calls this directly so
    the test exercises the production code path that builds a payload, rather than a
    parallel reimplementation of it that could silently disagree.

    `payload_context` IS AN OVERRIDE, and it is what lets verification reconstruct the
    PAST rather than the PRESENT. `None` (the default) means "read today's context from
    signature_target_kind" -- correct when SIGNING, since a new signature is always made
    under whatever context is current right now. An explicit value means "use exactly
    this one", which is REQUIRED when verifying: a signature recorded under
    `curated_interaction/v1` must still verify after `signature_target_kind`'s row for
    `curated_interaction` moves on to `/v2`, and re-deriving from the catalog would
    rebuild the payload under the new context, change the bytes, and report every
    historical signature as a forgery. `signing.FIELD_LISTS` therefore keeps every
    retired context version forever -- a version is stopped being minted, never deleted.

    THE TABLE AND KEY COLUMN COME FROM signature_target_kind -- never from a dict in
    Python -- regardless of whether `payload_context` overrides the context: one target
    kind names one table for its whole life, only which FIELD LIST applies to it can
    change between versions. A fourth target kind is then one INSERT in db/030 rather
    than an edit here, in the migration and in a CHECK, which is db/006's lesson.

    The SELECT (inside `_row_content_fields`) is composed with psycopg.sql.Identifier
    over names read from the catalogue. They are drugref's own seed values rather than
    user input, so there was never an injection here -- but composition makes that
    visible at a glance instead of requiring the reader to trace where the names came
    from.
    """
    table, pk_column, current_context = _target_kind_catalog(conn, target_kind)
    context = payload_context if payload_context is not None else current_context
    fields = _row_content_fields(conn, table, pk_column, target_id, context)
    # THE ATTESTATION FIELDS, appended last, matching FIELD_LISTS' own tail order. Both
    # are supplied by the caller rather than read from any table: `signed_at` is the
    # instant being attested AT (chosen by whoever is signing, or read back from a
    # recorded row when re-verifying); `key_fingerprint` names the key -- no target row
    # could possibly already contain either.
    return context, fields + [("signer_key_fingerprint", key_fingerprint),
                              ("signed_at", signing.render(signed_at))]


def payload_for(conn: psycopg.Connection, target_kind: str, target_id: int, *,
                 key_fingerprint: str, signed_at: dt.datetime,
                 payload_context: str | None = None) -> tuple[str, bytes]:
    """The canonical payload bytes for one target row, ready to sign or to verify
    against. A thin wrapper over `payload_fields` -> `signing.canonical_payload`; kept
    separate from `payload_fields` only because the mutation gate needs the
    (name, value) pairs themselves, not the encoded bytes, to build a mutated payload.

    See `payload_fields` for what `payload_context` means -- `None` to sign under
    today's context, the row's own stored value to verify against what was actually
    signed.
    """
    context, fields = payload_fields(
        conn, target_kind, target_id, key_fingerprint=key_fingerprint,
        signed_at=signed_at, payload_context=payload_context)
    return context, signing.canonical_payload(context, fields)


def record(conn: psycopg.Connection, *, target_kind: str, target_id: int,
           payload_context: str, payload: bytes, key_fingerprint: str,
           signature: bytes, signed_at: dt.datetime,
           algorithm: str = signing.ED25519) -> int:
    """Store one signature. Returns the new `signature_id`.

    RECORDING IS NOT VERIFYING, and that is deliberate rather than an oversight: a
    recorder that refused an invalid SIGNATURE could not store the evidence that an
    invalid one exists, which is precisely what a node needs to be able to report (a
    forged or corrupted signature is itself a finding, not something to discard).
    Whether `signature` is valid over `payload` is `verify_target`'s question, asked
    fresh every time rather than cached -- spec 7.3 is explicit that no verification
    result is ever stored in a column.

    ONE THING IS CHECKED, and it is not the signature: that `payload_context` actually
    NAMES the context `payload` was built under. Without this, `record` could be called
    with a `curated_interaction/v1` payload and `payload_context='curated_condition/v1'`
    and would store the lie -- harmless only by accident today, because nothing used to
    read the column back. Now that `verify_target` rebuilds against the STORED context
    (see `payload_fields`), a wrong `payload_context` here is how a row permanently
    stops verifying, on an insert-only table with no correction path: there is no UPDATE
    to fix it with afterwards.

    THIS IS A PREFIX CHECK, NOT A PARSE, and the distinction matters because the format
    is generate-and-compare and must never be parsed by anyone (signing.py's own
    comment). `canonical_payload` always writes PROLOGUE, a newline, the context, and a
    newline, in that fixed order, before a single field -- so confirming `payload`
    BEGINS WITH exactly those bytes for the declared context checks that the two agree
    without interpreting a length, a field, or anything past that fixed header, which is
    the one part of the format simple enough to check by inspection rather than by
    reproducing the whole encoder.
    """
    declared_header = signing.PROLOGUE + b"\n" + payload_context.encode("utf-8") + b"\n"
    if not payload.startswith(declared_header):
        # SigningError, NOT ValueError: `cli.main` catches the RuntimeError family
        # and renders one sentence, so a bare ValueError here is a traceback in an
        # operator's terminal. Unreachable from the CLI today (`_handle_sign` passes
        # `payload_for`'s own returned context, so the two always agree) -- typed
        # correctly anyway, because "unreachable from today's callers" is exactly
        # what was said about the KeyError that later escaped.
        raise DeclaredContextMismatchError(
            f"payload_context={payload_context!r} does not match the context this "
            "payload was actually built under (its first bytes disagree). Recording it "
            "anyway would store a signature whose declared context can never reproduce "
            "the bytes it claims to describe -- and there is no UPDATE to fix that "
            "afterwards.")
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

    UNSIGNED IS NOT AN ERROR: a target that EXISTS but carries no recorded signature
    returns `[]`, because signing is optional per row and the overlay ships with most
    rows unsigned. A target_id that never existed at all is a DIFFERENT thing and raises
    `UnknownTargetError` -- exactly what `payload_for` raises for the same id -- so a
    mistyped id cannot read identically to an ordinary unsigned row (`keys.revoke`'s
    precedent: silence is the worst answer a lookup can give).

    EACH SIGNATURE IS REBUILT AGAINST ITS OWN signed_at, key_fingerprint AND
    payload_context -- never one shared payload for the whole row. `signed_at` and
    `key_fingerprint` are inside the signed bytes (spec 4.4), so two signatures over one
    row -- a counter-signature, or a re-sign after a key rotation -- cover genuinely
    DIFFERENT bytes. `payload_context` is read back from THIS SIGNATURE ROW rather than
    re-derived from the catalog, so a later `/v2` context cannot rebuild an old
    signature under new bytes and report it as a forgery (see `payload_fields`).
    Sharing any of the three across signatures would make some subset look forged,
    which is the worst possible way for this to fail: silent, and indistinguishable
    from an actual attack.

    ALGORITHM IS CHECKED TOO: `signing.verify` only implements Ed25519, so a row naming
    anything else raises `UnsupportedAlgorithmError` rather than being silently checked
    against the wrong scheme.

    ORDERED BY signature_id, i.e. the order signatures were recorded in -- the surrogate
    key, not `signed_at`, which an operator may supply out of order (a late-recorded
    signature can honestly claim an earlier instant); `keys.history`'s precedent.

    THE CATALOG LOOKUP AND THE ROW READ ARE HOISTED OUT OF THE PER-SIGNATURE LOOP.
    Both depend only on `target_kind`/`target_id` (the row read also on
    `payload_context`, cached by that key), which are constant across every signature
    checked here -- not on which signature is being checked. The naive per-signature
    version paid the `signature_target_kind` lookup and the target-row SELECT once EACH
    per signature (several queries each -- the figure this comment used to quote was
    never recorded anywhere, so it is stated qualitatively rather than as a number
    nothing can check);
    this version pays the catalog lookup once total and the row read once per DISTINCT
    `payload_context` seen (once, in the common case where nothing has moved to a `/v2`
    yet). `keys.for_verification` is cached by `key_fingerprint` for the same reason:
    two signatures by the same key -- one curator re-signing, or the KEY_EXPIRED pair --
    do not repeat the same registry lookup. This matters now because Task 8's release
    verifier runs this across the whole overlay. Issue 87 then halved what an uncached
    fingerprint costs: `keys.live` and `keys.key_status` were two queries about one key,
    and are now one.
    """
    table, pk_column, _ = _target_kind_catalog(conn, target_kind)
    rows = conn.execute(
        "SELECT signature_id, key_fingerprint, algorithm, signature, signed_at, "
        "payload_context "
        "FROM drugref.assertion_signature "
        "WHERE target_kind = %s AND target_id = %s "
        "ORDER BY signature_id",
        (target_kind, target_id)).fetchall()
    if not rows:
        # UNSIGNED (return []) and NEVER EXISTED (raise) are indistinguishable up to
        # this point -- both have zero assertion_signature rows -- so the target row
        # itself must be checked before handing back the empty, ordinary-case answer.
        exists = conn.execute(
            sql.SQL("SELECT 1 FROM drugref.{table} WHERE {pk} = %s").format(
                table=sql.Identifier(table), pk=sql.Identifier(pk_column)),
            (target_id,)).fetchone()
        if exists is None:
            raise UnknownTargetError(
                f"drugref.{table} has no row with {pk_column} = {target_id}. A "
                "mistyped id would otherwise read identically to an ordinary unsigned "
                "row, which is the one place this layer must not stay silent.")
        return []

    row_fields_by_context = {}
    key_cache = {}
    verdicts = []
    for row in rows:
        (signature_id, key_fingerprint, algorithm, signature, signed_at,
         payload_context) = row
        if algorithm != signing.ED25519:
            raise UnsupportedAlgorithmError(
                f"assertion_signature {signature_id} was signed with {algorithm!r}, "
                "which this module cannot verify -- signing.verify only implements "
                f"{signing.ED25519}. A second algorithm needs a second verify path "
                "before any row naming it can be checked, not an assumption here.")
        if key_fingerprint not in key_cache:
            key_cache[key_fingerprint] = keys.for_verification(conn, key_fingerprint)
        # ONE REGISTRY READ, ONE `None` (issue 87). `keys.live` and `keys.key_status`
        # used to be asked separately here, and the docstring's claim that `holder is
        # None` exactly when the verdict is UNKNOWN_KEY held only because those two
        # queries happened to agree about which keys exist. `RegisteredKey` is
        # all-or-nothing, so the two halves below cannot come from different answers.
        registered = key_cache[key_fingerprint]
        key = registered.record if registered else None
        status = registered.status if registered else None
        # AN UNUSABLE payload_context IS A VERDICT, NEVER A RAISE (review C3). This
        # column carries only a regex CHECK and no FK, so `bogus/v9` or another kind's
        # context is one INSERT away -- and rebuilding under it raised `KeyError` or
        # psycopg's `UndefinedColumn`, neither caught by `cli.main`. Because the table
        # is INSERT-ONLY the offending row could never be deleted, so one planted row
        # denied verification of that curated row FOREVER, including the honest
        # signatures on it: verify_target raised inside this loop, so nothing was
        # reported at all.
        #
        # `signature_ok = False` rather than a seventh verdict constant: the six are
        # published and spec 7.1 ranks them. Setting the flag and letting
        # `signing.verdict` decide also keeps the precedence intact -- an unusable
        # context under an UNREGISTERED key must still report UNKNOWN_KEY, because
        # filing a registry gap as a forgery is what step 1 exists to prevent.
        if not signing.context_is_usable_for(payload_context, target_kind):
            signature_ok = False
        else:
            if payload_context not in row_fields_by_context:
                row_fields_by_context[payload_context] = _row_content_fields(
                    conn, table, pk_column, target_id, payload_context)
            fields = row_fields_by_context[payload_context] + [
                ("signer_key_fingerprint", key_fingerprint),
                ("signed_at", signing.render(signed_at))]
            payload = signing.canonical_payload(payload_context, fields)
            # WITH NO REGISTERED KEY THERE IS NO MATHEMATICS TO CHECK --
            # `signing.verdict` ignores `signature_ok` entirely when `key_status` is
            # None (UNKNOWN_KEY outranks it), so `False` here is never read as "this
            # signature is bad" rather than "this key is unknown".
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


# EXPORTED for `cli.py`'s migration guard (issue 122), for the reason
# `curation.UNRESOLVED_VIEW` states: one home per relation name, so a guard cannot end
# up probing a view the read no longer uses.
BACKDATED_VIEW = "drugref.signature_backdated"


@dataclass(frozen=True)
class BackdatedSignature:
    """One row of `signature_backdated` -- a signature claiming a `signed_at` more
    than a day before this database recorded it."""
    signature_id: int
    target_kind: str
    target_id: int
    key_fingerprint: str
    signed_at: dt.datetime
    recorded_at: dt.datetime
    lag: dt.timedelta


def backdated(conn: psycopg.Connection) -> list[BackdatedSignature]:
    """Every signature `signature_backdated` flags, oldest claim first.

    A DETECTOR NEEDS A CALLER (review I7). `signature_backdated` shipped with none:
    nothing in `src/` read it, so the one residual signal against a stolen key
    backdating its way past a TIME-SCOPED revocation was reachable only by an operator
    who wrote their own SQL. `curated_target_unresolved` -- the view this one's design
    was modelled on -- was given a `drugref status` block by issue 76 for exactly this
    reason, and spec 12's standing rule about detectors nobody calls says the rest.

    WHY IT MATTERS CONCRETELY: `signed_at` is inside the signed payload, so it cannot be
    forged WITHOUT the key -- but it is chosen by whoever HOLDS the key. After a
    `rotated` or `retired` revocation (time-scoped, by design), anyone holding the
    private half can mint signatures dated before `status_from`; those verify `valid`,
    read `signed`, and exit 0. The gap between the claimed `signed_at` and this
    database's own `recorded_at` is the only thing left that notices.

    NOT A GAP KIND, and reported as an OPERATOR SIGNAL rather than a failure: a curator
    with an air-gapped signing flow legitimately submits late. See the view's own
    COMMENT.

    Ordered by `signed_at` so the oldest claim -- the one least likely to be an ordinary
    late submission -- is read first; `signature_id` breaks ties so the output is
    totally ordered and a multi-row test cannot flake.
    """
    return [BackdatedSignature(*row) for row in conn.execute(
        "SELECT signature_id, target_kind, target_id, key_fingerprint, signed_at, "
        f"recorded_at, lag FROM {BACKDATED_VIEW} "
        "ORDER BY signed_at, signature_id").fetchall()]
