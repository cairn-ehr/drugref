# src/drugref/signing.py
"""The signing subsystem's PURE half: bytes in, bytes out, no database (slice 5c.4).

WHAT LIVES HERE AND WHY IT IS SEPARATE. Three things: the Ed25519 primitives, the
CANONICAL PAYLOAD FORMAT (the artefact everything else rests on), and the verdict rule
that says what a signature means. None of them touches a connection, so all of them are
testable without one -- which matters most for the canonical format, whose entire job is
to be reproducible from a stored row years from now.

THE VERDICT RULE IS HERE, NOT IN A DB MODULE, on accumulation.fires' precedent: drugref
publishes facts rather than verdicts and hands out the rules as code, so "why did this
verify?" has one answer everywhere rather than one per caller.

ALGORITHM: Ed25519. 32-byte keys, 64-byte signatures, no parameter choices to get
wrong, and deterministic -- the nonce is derived from key and message, so there is no
per-signature randomness and therefore no RNG failure mode of the kind that leaks an
ECDSA private key. The name is stored per key and per signature (db/030) so a second
algorithm is an additive migration rather than a rewrite.
"""
import datetime as dt
import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

# The one Python spelling of the algorithm name. db/030's CHECK is its vocabulary home;
# this constant exists because Python must write the value into a row, not as a second
# list to disagree with the first -- an unrecognised value raises CheckViolation from
# the database, which is the intended behaviour.
ED25519 = "Ed25519"


def generate_keypair() -> tuple[bytes, bytes]:
    """A fresh keypair as (private_32, public_32) RAW bytes.

    Raw rather than PEM or DER deliberately: the private half is written to a file the
    curator holds and the public half into a bytea column, and both want the smallest
    unambiguous encoding. A container format would add a parser -- and a second way for
    two 32-byte keys to compare unequal.
    """
    private = Ed25519PrivateKey.generate()
    return (private.private_bytes_raw(), private.public_key().public_bytes_raw())


def fingerprint(public_key: bytes) -> str:
    """SHA-256 over the raw public key, lowercase hex. THE IDENTITY A SIGNATURE NAMES.

    Changing this derivation orphans every signature ever recorded, because
    assertion_signature.key_fingerprint is how a signature finds its key. It is pinned
    by a test that recomputes it independently rather than by calling this function.
    """
    return hashlib.sha256(public_key).hexdigest()


def digest(payload: bytes) -> bytes:
    """SHA-256 of the canonical payload -- what a manifest entry stores.

    The manifest records digests rather than whole payloads so that a manifest over
    thousands of rows stays a manifest. Verification recomputes the payload from the
    live row and re-digests it, so the digest is a comparison key, never the thing
    signed: Ed25519 signs the payload itself (see `sign`).
    """
    return hashlib.sha256(payload).digest()


def sign(private_key: bytes, payload: bytes) -> bytes:
    """A 64-byte Ed25519 signature over the payload ITSELF, not over its digest.

    Ed25519 hashes internally, so pre-hashing would be both redundant and a different
    scheme (Ed25519ph) that a third-party verifier following this project's published
    format would not reproduce.
    """
    return Ed25519PrivateKey.from_private_bytes(private_key).sign(payload)


def verify(public_key: bytes, payload: bytes, signature: bytes) -> bool:
    """True if `signature` is a valid Ed25519 signature by `public_key` over `payload`.

    RETURNS FALSE RATHER THAN RAISING on malformed input -- a wrong-length key, a
    truncated signature, rubbish in either column. Both values come out of a table an
    attacker can INSERT into, so garbage there is an ordinary finding rather than an
    exceptional one, and a verifier that raises is one every caller must wrap -- and one
    a caller will eventually wrap too widely, swallowing a real error beside it.
    """
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (InvalidSignature, ValueError):
        return False
    return True


# ---- the canonical payload (spec 4.1-4.5) ----------------------------------
#
# THE FORMAT, in full, so it is reimplementable from this comment alone:
#
#     drugref-sig-v1\n
#     <context>\n                                     * ^[a-z_]+/v[0-9]+$, validated
#     <field-count>\n
#     <len(name)>:<name>:<tag>:<len(value)>:<value>\n   * field-count, FROZEN order
#     --<len(group)>:<group>:<member-count>--\n         * zero or more groups
#     <member-field-count>\n                            * one block per member; members
#     <len(name)>:<name>:<tag>:<len(value)>:<value>\n     sorted by their own COMPLETE
#                                                        encoding, count line included
#
# `tag` is S for a present value or N for SQL NULL (length 0, empty value). Lengths
# are UTF-8 BYTE counts. The trailing newlines are readability only -- the lengths
# and counts are what delimit, which is exactly why a newline inside a value cannot
# forge a boundary.
#
# EVERY STRUCTURAL LINE IS SELF-DELIMITING, and that was a correction. The first
# draft applied the length-prefix principle to VALUES but not to the format's own
# structure, and three collisions followed -- each demonstrated against the shipped
# encoder:
#
#   g=[{a:1,b:9},{a:2,b:8}] == g=[{a:1},{b:9,a:2,b:8}]   no per-member field count
#   g=[{a:1},{b:2}]         == g=[{a:1,b:2}]             no member count
#   group named "x--\n--y"  == two empty groups          group name not length-prefixed
#   context "evil/v1\n99"                                context forged the count line
#
# No forgery followed in this codebase -- member arity is fixed by the code building
# each group, and contexts are constants -- but a canonical format whose canonicity
# depends on its callers behaving is not canonical, and this is a published reference
# third parties implement against. Fixed while three test vectors existed and nothing
# had been signed; after the first real signature the format can never change again.
#
# THE PAYLOAD IS GENERATE-AND-COMPARE. IT IS NEVER PARSED, by drugref or by anyone.
# Verification re-derives the bytes from the stored row and compares. The format is
# documented so a third party can REPRODUCE the bytes from their own copy of the
# data, which is all a verifier needs -- not so anyone can write a parser and then
# depend on guarantees a generator does not owe them.
#
# WHY NOT JSON/RFC 8785. JCS is a published standard and its genuinely hard part is
# NUMBER canonicalisation, which this format sidesteps entirely by rendering every
# value as a string. At that point JCS contributes JSON's familiarity and an
# escaping surface to implement wrong. What it would have bought -- independent
# checkability -- is bought instead by tests/fixtures/signing_vectors.json, which
# stores each payload beside its digest so both can be checked without running
# drugref.
PROLOGUE = b"drugref-sig-v1"


def render(value) -> str | None:
    """One Python value -> its canonical string form (or None for SQL NULL).

    EVERY VALUE BECOMES A STRING, which is what removes number canonicalisation -- the
    part of RFC 8785 that is hard to reimplement correctly -- from the problem entirely.

    BOOL IS TESTED BEFORE INT ON PURPOSE: isinstance(True, int) is True in Python, so
    the other order renders True as '1', which is also how the integer 1 renders. Two
    different values, one spelling, under a valid signature.

    An unrecognised type RAISES rather than falling back to str(). A fallback is what
    makes a format silently wrong: a Decimal, a memoryview or a dict would each get
    SOME spelling and none of them a specified one, so a second implementation would
    disagree.

    TEXT IS NOT NORMALISED. The signature commits to the bytes Postgres stored; NFC here
    would make two distinct stored strings sign identically.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, uuid.UUID):
        return str(value)                      # lowercase canonical 8-4-4-4-12
    if isinstance(value, dt.datetime):
        return _render_timestamp(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise TypeError(
        f"{type(value).__name__} has no canonical rendering. Add one deliberately -- a "
        "str() fallback would give it a spelling nothing specifies, and a second "
        "implementation of this format would choose differently.")


def _render_timestamp(value: dt.datetime) -> str:
    """RFC 3339, UTC, EXACTLY six fractional digits.

    Six always, including when the microseconds are zero: a variable-length rendering
    gives one instant two spellings and only one of them verifies. Postgres timestamptz
    has microsecond resolution, so six is lossless.

    A NAIVE datetime RAISES. psycopg returns timestamptz as aware, so a naive value
    means somebody constructed it in Python, and rendering it would silently assume a
    zone -- which would produce a valid signature over the wrong instant.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "a naive datetime has no canonical rendering: it names no instant, and "
            "assuming a zone would sign the wrong one")
    utc = value.astimezone(dt.timezone.utc)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond:06d}Z"


def _encode_field(name: str, value: str | None) -> bytes:
    """One `<len>:<name>:<tag>:<len>:<value>\\n` record."""
    name_b = name.encode("utf-8")
    if value is None:
        return b"%d:%s:N:0:\n" % (len(name_b), name_b)
    value_b = value.encode("utf-8")
    return b"%d:%s:S:%d:%s\n" % (len(name_b), name_b, len(value_b), value_b)


# \Z, NOT $ -- $ matches at end-of-string OR immediately before a single trailing
# newline, so "curated_interaction/v1\n" would pass a $-anchored check and then supply
# the exact newline this whole validator exists to keep out of the context line. \Z has
# no such exception: it matches only the true end of the string. (.fullmatch() with a
# $-anchored pattern would work too, since fullmatch requires consuming the whole
# string regardless of $'s newline exception -- \Z is used here because it keeps the
# existing .match() call below unchanged, so the fix is visible in the pattern alone.)
_CONTEXT = re.compile(r"^[a-z_]+/v[0-9]+\Z")

Field = tuple[str, str | None]
Group = tuple[str, Sequence[Sequence[Field]]]


def canonical_payload(context: str,
                      fields: Sequence[Field] = (),
                      groups: Sequence[Group] = ()) -> bytes:
    """The bytes a signature is made over. THE LOAD-BEARING ARTEFACT OF THIS SLICE.

    `context` is the domain separator (spec 4.4) -- `curated_interaction/v1`,
    `curated_condition/v1`, `release_manifest/v1`. It is inside the payload, so bytes
    signed as one kind of statement can never verify as another. VALIDATED against
    `^[a-z_]+/v[0-9]+$` before use: the context occupies a whole line of the payload,
    so an unvalidated one could embed its own newline and inject a forged line below
    it -- the field-count line, most dangerously. Narrower than "no newline" on
    purpose, closing the whole class of separator characters rather than the one
    exploited in review.

    `fields` is a sequence of (name, rendered-value) pairs IN THE FROZEN ORDER for that
    context. Order is part of the format: two orderings of one row are two different
    payloads, which is why FIELD_LISTS is a frozen tuple and not a dict.

    `groups` is a sequence of (group_name, members), each member itself a field-pair
    sequence. MEMBERS ARE SORTED BY THEIR OWN COMPLETE ENCODING -- the per-member field
    count included -- because a manifest is built from a SELECT and a SELECT without
    ORDER BY may return rows in any order; if that order reached the bytes, one
    database would publish two different manifests. Sorting the ENCODED member (rather
    than by some key) means the rule needs no knowledge of what a member contains.
    """
    if not _CONTEXT.match(context):
        raise ValueError(
            f"{context!r} is not a valid context. It occupies a whole line of the "
            "payload, so a newline in it forges the field-count line below; the "
            "pattern is deliberately narrower than 'anything without a newline'.")
    out = [PROLOGUE, b"\n", context.encode("utf-8"), b"\n",
           str(len(fields)).encode("ascii"), b"\n"]
    out.extend(_encode_field(name, value) for name, value in fields)
    for group_name, members in groups:
        name_b = group_name.encode("utf-8")
        out.append(b"--%d:%s:%d--\n" % (len(name_b), name_b, len(members)))
        # Each member carries its OWN field count, and members sort by their complete
        # encoding -- that count line included. Without the count, two members' fields
        # ran together indistinguishably from one member holding all of them; without
        # sorting on the complete encoding, two members could look like one merged
        # member whose fields happened to total the same bytes.
        out.extend(sorted(
            b"%d\n%s" % (len(member),
                         b"".join(_encode_field(n, v) for n, v in member))
            for member in members))
    return b"".join(out)


# ---- the frozen field lists (spec 4.5) -------------------------------------
#
# FROZEN CONSTANTS, AND THIS DELIBERATELY INVERTS A STANDING RULE. The gates round's
# rule reads "derive the covered set from the catalog, never from a list you maintain",
# and here the opposite is required: deriving the payload from information_schema means
# a later ALTER TABLE ADD COLUMN silently changes every payload and INVALIDATES EVERY
# SIGNATURE EVER MADE.
#
# The alarm the rule exists for is rebuilt rather than abandoned --
# tests/test_signing_payload_coverage.py compares these lists against the live catalog
# and FAILS on a new column, forcing an explicit choice: bump to /v2, or exclude the
# column with a stated reason. Frozen bytes, catalog-driven alarm.
#
# Adding a field to a list below without bumping the context is a BREAKING change to
# every signature already recorded. There is no way to make that safe; there is only a
# test that makes it deliberate.
ATTESTATION_FIELDS = ("signer_key_fingerprint", "signed_at")

CURATED_INTERACTION_V1 = (
    "subject_moiety_uuid", "object_class_uuid", "relationship", "applies",
    "severity", "mechanism", "management", "evidence_grade", "question_uuid",
    "source", "reviewed_by", "reviewed_against", "reviewed_at",
    *ATTESTATION_FIELDS)

# NOTE THE ASYMMETRY, which mirrors db/029's own and is not an oversight: this table is
# keyed on the (drug, condition) PAIR and carries `ruling` where its sibling carries
# `relationship` + `applies`, because one pair genuinely holds both an indication and a
# contraindication in 168 cases. See spec 3 of the 5c.1 design.
CURATED_CONDITION_V1 = (
    "subject_moiety_uuid", "object_condition_uuid", "ruling",
    "severity", "mechanism", "management", "evidence_grade", "question_uuid",
    "source", "reviewed_by", "reviewed_against", "reviewed_at",
    *ATTESTATION_FIELDS)

# The manifest's scalars. The two group cardinalities are stated as scalars as well as
# being derivable from the groups themselves (spec 5.5): a group truncated at its END is
# otherwise detectable only by recomputing the whole digest, and a scalar count makes
# that specific failure nameable.
RELEASE_MANIFEST_V1 = (
    "release_tag", "published_by", "published_at", "entry_count", "upstream_count",
    *ATTESTATION_FIELDS)

FIELD_LISTS = {
    "curated_interaction/v1": CURATED_INTERACTION_V1,
    "curated_condition/v1": CURATED_CONDITION_V1,
    "release_manifest/v1": RELEASE_MANIFEST_V1,
}


# ---- what a signature MEANS (spec 7.1) -------------------------------------
#
# SIX VERDICTS, NOT A BOOLEAN, and the reason is the revocation model. A consumer needs
# to tell "this was forged" from "the curator's laptop was stolen last year", because
# the first is an attack and the second is a re-review queue. Collapsing them into
# pass/fail throws away the only information that decides what to do next.
NO_SIGNATURE = "no_signature"
UNKNOWN_KEY = "unknown_key"
BAD_SIGNATURE = "bad_signature"
KEY_REVOKED_COMPROMISED = "key_revoked_compromised"
KEY_EXPIRED = "key_expired"
VALID = "valid"

# THE PRECEDENCE ABOVE, AS DATA -- `verdict`'s own five-step order (see its docstring),
# restated here as an ordered tuple so a caller that combines SEVERAL verdicts into one
# (Task 8's release verifier folds several `assertion_signature` rows over one manifest
# into a single answer) has ONE place to rank them, worst first, rather than re-typing
# the order as a second hand-maintained dict -- exactly the "second home" db/006 found
# drifting. `NO_SIGNATURE` is deliberately absent: `verdict` never returns it (its own
# docstring: "with no signature there is nothing to pass in"), so a combiner only ever
# ranks among these five and reports `NO_SIGNATURE` itself, for the zero-signature case.
VERDICT_PRECEDENCE = (UNKNOWN_KEY, BAD_SIGNATURE, KEY_REVOKED_COMPROMISED, KEY_EXPIRED,
                      VALID)


@dataclass(frozen=True)
class KeyStatus:
    """What the registry currently says about the key a signature names.

    Assembled by keys.py from signing_key's LIVE row joined to
    signing_key_status_kind. The two booleans arrive as DATA rather than being derived
    from `status` here, because `status` is db/030's vocabulary and a Python-side test
    against one of its members is the second-home defect this project has paid for four
    times over.
    """
    status: str
    is_revocation: bool
    invalidates_all_signatures: bool
    status_from: dt.datetime


def verdict(key_status: KeyStatus | None, *, signature_ok: bool,
            signed_at: dt.datetime) -> str:
    """What one signature is worth. PURE, and the ONE place the precedence lives.

    ORDER IS LOAD-BEARING, and each step outranks the next for a stated reason:

    1. UNKNOWN_KEY -- without the public key the mathematics cannot be checked at all,
       so `signature_ok` is not evidence of anything here. Reporting this as
       BAD_SIGNATURE files a registry gap as an attack.
    2. BAD_SIGNATURE -- a forgery is a forgery first. Reporting a forged signature under
       a revoked key as "revoked" would file an attack as a key-management event.
    3. KEY_REVOKED_COMPROMISED -- blanket, ignoring signed_at, because after a
       compromise you cannot tell the curator's signatures from the attacker's.
    4. KEY_EXPIRED -- time-scoped: the key was rotated or retired and this signature is
       at or after that boundary. `is_revocation` is what makes this an END boundary; an
       active key's status_from is its registration time, so without that guard every
       signature ever made would land here.
    5. VALID.

    NO_SIGNATURE is not returned here -- with no signature there is nothing to pass in.
    The caller reports it; the constant lives beside its siblings so the six spellings
    have one home.
    """
    if key_status is None:
        return UNKNOWN_KEY
    if not signature_ok:
        return BAD_SIGNATURE
    if key_status.invalidates_all_signatures:
        return KEY_REVOKED_COMPROMISED
    if key_status.is_revocation and signed_at >= key_status.status_from:
        return KEY_EXPIRED
    return VALID
