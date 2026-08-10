# tests/test_signatures_writer.py
"""Building, recording and verifying a per-row signature (spec 4.4, 7.1). DB-gated."""
import datetime as dt

import pytest

from drugref import curation, keys, signatures, signing

SIGNED_AT = dt.datetime(2026, 8, 9, 4, 33, 52, 8117, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def signed_rule(conn, a_graded_rule):
    """One curated interaction judgement, signed by one registered curator key.

    Builds on conftest's `a_graded_rule`, which sets up the CI_MoA rule and its
    membership but deliberately does not grade it -- the grading is this fixture's job.
    """
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a curator",
                  registered_by="an operator")
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", mechanism="additive effect", management="monitor",
        evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", target_id,
        key_fingerprint=fingerprint, signed_at=SIGNED_AT)
    signature = signing.sign(private, payload)
    signatures.record(conn, target_kind="curated_interaction", target_id=target_id,
                      payload_context=context, payload=payload,
                      key_fingerprint=fingerprint, signature=signature,
                      signed_at=SIGNED_AT)
    return {"target_id": target_id, "private": private, "public": public,
            "fingerprint": fingerprint, "payload": payload, "signature": signature,
            **a_graded_rule}


def test_a_recorded_signature_verifies(conn, signed_rule):
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID]
    assert verdicts[0].holder == "a curator"


def test_an_unsigned_target_reports_no_signature(conn, a_graded_rule):
    """UNSIGNED IS AN ORDINARY STATE, not an error: signing is optional per row and the
    overlay ships empty. A verifier that raised here would make the normal case fail."""
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="minor", evidence_grade="theoretical", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    assert signatures.verify_target(conn, "curated_interaction", target_id) == []


def test_the_payload_context_comes_from_the_catalog(conn, signed_rule):
    """payload_for reads target_table, pk_column and payload_context from
    signature_target_kind -- never from a hardcoded mapping in Python. A fourth target
    kind must be one INSERT there, not an edit here and there and in a CHECK."""
    context, _ = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=SIGNED_AT)
    assert context == "curated_interaction/v1"


def test_a_missing_target_row_raises_rather_than_signing_nothing(conn):
    """Signing a row that does not exist would produce a payload of NULLs -- a valid
    signature over nothing, indistinguishable from a real one at a glance."""
    with pytest.raises(signatures.UnknownTargetError):
        signatures.payload_for(conn, "curated_interaction", 999_999,
                               key_fingerprint="a" * 64, signed_at=SIGNED_AT)


def test_the_signature_survives_the_row_being_superseded(conn, signed_rule):
    """A correction inserts a NEW row and points this one at it. The old signature is
    still a true statement about what the curator attested on that date -- and for a row
    that fired alerts for six months, that is the record that matters most."""
    curation.record_interaction_judgement(
        conn, signed_rule["subject"], signed_rule["class"], "CI_MoA", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID]


def test_two_curators_may_counter_sign_one_judgement(conn, signed_rule):
    """Several signatures per row is the point of detaching them -- a second reviewer
    attesting the same judgement is ordinary clinical governance, and a signature COLUMN
    could not represent it at all."""
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a second curator",
                  registered_by="an operator")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=fingerprint, signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=LATER)
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert sorted(v.holder for v in verdicts) == ["a curator", "a second curator"]
    assert {v.verdict for v in verdicts} == {signing.VALID}


def test_each_signature_is_checked_against_its_OWN_signed_at(conn, signed_rule):
    """Two signatures over one row cover DIFFERENT bytes, because signed_at is inside
    the payload. Rebuilding one payload and reusing it for every signature would fail
    all but the last -- the defect this test exists to catch, since the symptom looks
    exactly like a forgery."""
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a second curator",
                  registered_by="an operator")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=fingerprint, signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=LATER)
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert all(v.verdict == signing.VALID for v in verdicts)


def test_a_signature_by_an_unregistered_key_reports_unknown_key(conn, signed_rule):
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)          # deliberately NOT registered
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=fingerprint, signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=LATER)
    verdicts = {v.key_fingerprint: v.verdict for v in signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])}
    assert verdicts[fingerprint] == signing.UNKNOWN_KEY


def test_a_compromised_key_flags_every_signature_it_made(conn, signed_rule):
    keys.revoke(conn, key_fingerprint=signed_rule["fingerprint"],
                status="compromised", revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.KEY_REVOKED_COMPROMISED]


def test_a_rotated_key_leaves_an_earlier_signature_valid(conn, signed_rule):
    keys.revoke(conn, key_fingerprint=signed_rule["fingerprint"], status="rotated",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID]


def test_a_condition_payload_does_not_verify_as_an_interaction(conn, a_contradicted_pair):
    """Spec 4.4's domain separation, exercised end to end rather than on synthetic
    field lists: the two contexts differ, so the bytes differ, so the signature does
    not carry across."""
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a curator",
                  registered_by="an operator")
    condition_id = curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    _, condition_payload = signatures.payload_for(
        conn, "curated_condition", condition_id,
        key_fingerprint=fingerprint, signed_at=SIGNED_AT)
    assert b"curated_condition/v1" in condition_payload
    assert b"curated_interaction/v1" not in condition_payload


@pytest.mark.parametrize("field,mutated", [
    ("relationship", "CI_PE"), ("applies", "false"), ("severity", "minor"),
    ("mechanism", "something else"), ("management", "do nothing"),
    ("evidence_grade", "theoretical"), ("source", "SOMEBODY-ELSE"),
    ("reviewed_by", "somebody else"), ("reviewed_against", "MED-RT 2020.01.01"),
    ("subject_moiety_uuid", "00000000-0000-5000-8000-000000000000"),
    ("object_class_uuid", "00000000-0000-5000-8000-000000000001"),
    ("question_uuid", "00000000-0000-5000-8000-000000000002"),
    ("reviewed_at", "2000-01-01T00:00:00.000000Z"),
    ("signer_key_fingerprint", "b" * 64),
    ("signed_at", "2000-01-01T00:00:00.000000Z"),
])
def test_changing_any_signed_field_breaks_the_signature(conn, signed_rule,
                                                        field, mutated):
    """ONE TEST PER SIGNED FIELD, per the standing rule slice 5c.1's PR review produced:
    for every clause in a multi-field guard, name the test that kills its removal.

    A field silently missing from signing.CURATED_INTERACTION_V1 is the ONE defect this
    layer cannot survive -- the signature would keep verifying while the unsigned field
    was free to be anything -- and no aggregate test can see it.

    HOW THE MUTATION IS APPLIED, and why it is not an UPDATE: the curated row is
    append-only, so the row itself cannot be edited. Instead the payload is REBUILT with
    one field replaced and the recorded signature checked against those bytes. That is a
    genuine coverage test rather than a proxy for one: if the field is absent from the
    frozen list, the 'mutated' payload is byte-identical to the original and the
    signature VERIFIES -- failing this test, which is exactly what should happen.
    """
    _, fields = signatures.payload_fields(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=SIGNED_AT)
    assert field in dict(fields), (
        f"{field} is not in the payload at all -- either the frozen field list dropped "
        f"it, or this parametrisation names a column that no longer exists")
    rebuilt = signing.canonical_payload(
        "curated_interaction/v1",
        [(name, mutated if name == field else value) for name, value in fields])
    assert rebuilt != signed_rule["payload"], (
        f"changing {field} did not change the payload -- it is not covered by "
        f"signing.CURATED_INTERACTION_V1, so a signature says nothing about it")
    assert signing.verify(signed_rule["public"], rebuilt,
                          signed_rule["signature"]) is False
