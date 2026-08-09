# tests/test_signing_primitives.py
"""The Ed25519 primitives. PURE -- no database, so these run everywhere."""
import hashlib

from drugref import signing


def test_a_generated_keypair_has_the_expected_raw_sizes():
    private, public = signing.generate_keypair()
    assert len(private) == 32
    assert len(public) == 32


def test_a_signature_verifies_against_its_own_payload():
    private, public = signing.generate_keypair()
    signature = signing.sign(private, b"a canonical payload")
    assert len(signature) == 64
    assert signing.verify(public, b"a canonical payload", signature) is True


def test_a_tampered_payload_does_not_verify():
    """The whole point of the layer, in one assertion: one flipped byte breaks it."""
    private, public = signing.generate_keypair()
    signature = signing.sign(private, b"severity=major")
    assert signing.verify(public, b"severity=minor", signature) is False


def test_another_key_does_not_verify():
    private, _ = signing.generate_keypair()
    _, other_public = signing.generate_keypair()
    signature = signing.sign(private, b"payload")
    assert signing.verify(other_public, b"payload", signature) is False


def test_verify_returns_false_rather_than_raising_on_a_malformed_signature():
    """A verifier that RAISES on rubbish is a verifier every caller must wrap, and one
    that a caller will eventually wrap too widely. Garbage in the signature column is
    an ordinary thing to find in a table an attacker can INSERT into -- it is a `false`,
    not an exception."""
    _, public = signing.generate_keypair()
    assert signing.verify(public, b"payload", b"not a signature at all") is False


def test_verify_returns_false_rather_than_raising_on_a_malformed_public_key():
    """Same argument one column over: signing_key.public_key is bytea, and a 31-byte
    value is a row somebody can write."""
    private, _ = signing.generate_keypair()
    signature = signing.sign(private, b"payload")
    assert signing.verify(b"\x00" * 31, b"payload", signature) is False


def test_signing_is_deterministic():
    """Ed25519 derives its nonce from the key and message, so there is no per-signature
    randomness to get wrong -- the failure mode that leaks an ECDSA private key. Pinned
    because it is a property this project relies on when comparing signatures."""
    private, _ = signing.generate_keypair()
    assert signing.sign(private, b"x") == signing.sign(private, b"x")


def test_the_fingerprint_is_sha256_over_the_raw_public_key():
    """Stated here as an INDEPENDENT computation rather than by calling the function
    twice: the fingerprint is the identity a signature names, so a change to how it is
    derived orphans every signature ever recorded."""
    _, public = signing.generate_keypair()
    assert signing.fingerprint(public) == hashlib.sha256(public).hexdigest()
    assert len(signing.fingerprint(public)) == 64


def test_the_digest_is_sha256_of_the_payload():
    assert signing.digest(b"abc") == hashlib.sha256(b"abc").digest()


def test_generate_keypair_does_not_repeat_itself():
    assert signing.generate_keypair()[0] != signing.generate_keypair()[0]
