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
import hashlib

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
