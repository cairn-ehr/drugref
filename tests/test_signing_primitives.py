# tests/test_signing_primitives.py
"""The Ed25519 primitives. PURE -- no database, so these run everywhere."""
import hashlib

import pytest

from drugref import signing


def test_a_generated_keypair_has_the_expected_raw_sizes():
    _kp = signing.generate_keypair()
    private, public = _kp.private_key, _kp.public_key
    assert len(private) == 32
    assert len(public) == 32


def test_a_signature_verifies_against_its_own_payload():
    _kp = signing.generate_keypair()
    private, public = _kp.private_key, _kp.public_key
    signature = signing.sign(private, b"a canonical payload")
    assert len(signature) == 64
    assert signing.verify(public, b"a canonical payload", signature) is True


def test_a_tampered_payload_does_not_verify():
    """The whole point of the layer, in one assertion: one flipped byte breaks it."""
    _kp = signing.generate_keypair()
    private, public = _kp.private_key, _kp.public_key
    signature = signing.sign(private, b"severity=major")
    assert signing.verify(public, b"severity=minor", signature) is False


def test_another_key_does_not_verify():
    private = signing.generate_keypair().private_key
    other_public = signing.generate_keypair().public_key
    signature = signing.sign(private, b"payload")
    assert signing.verify(other_public, b"payload", signature) is False


def test_verify_returns_false_rather_than_raising_on_a_malformed_signature():
    """A verifier that RAISES on rubbish is a verifier every caller must wrap, and one
    that a caller will eventually wrap too widely. Garbage in the signature column is
    an ordinary thing to find in a table an attacker can INSERT into -- it is a `false`,
    not an exception."""
    public = signing.generate_keypair().public_key
    assert signing.verify(public, b"payload", b"not a signature at all") is False


def test_verify_returns_false_rather_than_raising_on_a_malformed_public_key():
    """Same argument one column over: signing_key.public_key is bytea, and a 31-byte
    value is a row somebody can write."""
    private = signing.generate_keypair().private_key
    signature = signing.sign(private, b"payload")
    assert signing.verify(b"\x00" * 31, b"payload", signature) is False


def test_signing_is_deterministic():
    """Ed25519 derives its nonce from the key and message, so there is no per-signature
    randomness to get wrong -- the failure mode that leaks an ECDSA private key. Pinned
    because it is a property this project relies on when comparing signatures."""
    private = signing.generate_keypair().private_key
    assert signing.sign(private, b"x") == signing.sign(private, b"x")


def test_the_fingerprint_is_sha256_over_the_raw_public_key():
    """Stated here as an INDEPENDENT computation rather than by calling the function
    twice: the fingerprint is the identity a signature names, so a change to how it is
    derived orphans every signature ever recorded."""
    public = signing.generate_keypair().public_key
    assert signing.fingerprint(public) == hashlib.sha256(public).hexdigest()
    assert len(signing.fingerprint(public)) == 64


def test_the_digest_is_sha256_of_the_payload():
    assert signing.digest(b"abc") == hashlib.sha256(b"abc").digest()


def test_generate_keypair_does_not_repeat_itself():
    assert signing.generate_keypair().private_key != signing.generate_keypair().private_key


def test_a_keypair_cannot_be_unpacked_like_a_tuple():
    """THE POINT OF THE TYPE, pinned so nobody "simplifies" it back to a tuple.

    While `generate_keypair` returned `tuple[bytes, bytes]`, `public, private = ...`
    type-checked, ran, and wrote the PRIVATE key into `signing_key.public_key` -- which
    passes the 32-byte CHECK, because both halves are 32 bytes, onto a table the overlay
    floor forbids DELETE and UPDATE on. No runtime check can catch it either: any 32
    bytes is a valid Ed25519 private seed. Making the value UNPACKABLE is the whole
    defence, so this asserts the defence rather than the defect."""
    with pytest.raises(TypeError):
        _first, _second = signing.generate_keypair()


def test_the_two_halves_of_a_keypair_are_named_and_distinct():
    """The anti-vacuity control for the test above: a type that raised TypeError on
    everything would pass it. Both fields must be readable, 32 bytes, and different --
    and the public half must be the one the private half actually derives."""
    keypair = signing.generate_keypair()
    assert len(keypair.private_key) == 32
    assert len(keypair.public_key) == 32
    assert keypair.private_key != keypair.public_key
    # The named fields are not merely two arbitrary blobs in a fixed order: signing with
    # the private half must verify under the public one.
    payload = b"drugref-sig-v1\ncurated_interaction/v1\n0\n"
    assert signing.verify(keypair.public_key, payload,
                          signing.sign(keypair.private_key, payload))
