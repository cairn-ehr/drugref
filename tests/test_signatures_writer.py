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


def test_an_unsigned_target_reports_no_signature(conn, signed_rule):
    """UNSIGNED IS AN ORDINARY STATE, not an error: signing is optional per row and the
    overlay ships empty. A verifier that raised here would make the normal case fail.

    I1 (review round 2): this now runs IN THE PRESENCE OF `signed_rule` rather than on
    a bare `a_graded_rule` -- with no other signed row anywhere, `assertion_signature`
    was empty for the WHOLE TEST and `[]` was the only possible answer regardless of
    whether verify_target's WHERE clause even looked at target_id. Proved by the
    reviewer: replacing the WHERE clause with `WHERE target_kind = %s AND %s IS NOT
    NULL` -- deleting the target filter outright -- left the old version of this test
    (and all 26 others) passing. A SECOND, real curated_interaction row -- same subject
    and class as signed_rule's, a different relationship so it is a genuinely different
    natural key -- makes a broken filter return signed_rule's signature instead of [],
    which this assertion would then catch.
    """
    target_id = curation.record_interaction_judgement(
        conn, signed_rule["subject"], signed_rule["class"], "CI_PE", True,
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
    signature over nothing, indistinguishable from a real one at a glance.

    M3 (review round 2): `match=` pins this to the ROW-missing branch specifically --
    UnknownTargetError has a second raise site, for an unrecognised `target_kind`, that
    means something different, and a bare pytest.raises could not tell the two apart.
    """
    with pytest.raises(signatures.UnknownTargetError, match="has no row with"):
        signatures.payload_for(conn, "curated_interaction", 999_999,
                               key_fingerprint="a" * 64, signed_at=SIGNED_AT)


def test_an_unrecognised_target_kind_raises(conn):
    """M3 (review round 2): the OTHER of UnknownTargetError's two raise sites -- no
    row in signature_target_kind at all, as opposed to no row in the target table.
    Previously untested; the missing-row case above already existed but nothing pinned
    which branch either test was actually exercising."""
    with pytest.raises(signatures.UnknownTargetError,
                       match="is not a signature target kind"):
        signatures.payload_for(conn, "not_a_real_target_kind", 1,
                               key_fingerprint="a" * 64, signed_at=SIGNED_AT)


def test_verify_target_raises_on_a_target_row_that_never_existed(conn):
    """M4 (review round 2): verify_target used to report [] for a target_id that never
    existed at all -- the same answer as an ordinary unsigned row -- because zero
    matching assertion_signature rows looks identical either way. A mistyped id must
    raise, exactly as payload_for already does for the same id, rather than silently
    read as "nobody has signed this yet"."""
    with pytest.raises(signatures.UnknownTargetError, match="has no row with"):
        signatures.verify_target(conn, "curated_interaction", 999_999)


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


def test_a_forged_signature_reports_bad_signature(conn, signed_rule):
    """I3 (review round 2): BAD_SIGNATURE is verify_target's headline capability, and
    the stated justification for record() not verifying -- until this test, that rested
    entirely on prose plus coverage of the PURE verdict function, and never on
    verify_target actually reaching this branch. A signature made by a DIFFERENT
    private key but recorded under a REGISTERED key's fingerprint is what an attempted
    forgery against a real curator's identity looks like: the key is known, so this
    must be told apart from UNKNOWN_KEY, and the maths must actually fail."""
    forger_private, _ = signing.generate_keypair()
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=LATER)
    forged_signature = signing.sign(forger_private, payload)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload,
        key_fingerprint=signed_rule["fingerprint"], signature=forged_signature,
        signed_at=LATER)
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID, signing.BAD_SIGNATURE]


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


def test_a_signature_at_or_after_the_rotation_boundary_is_expired(conn, signed_rule):
    """I2 (review round 2): the OTHER direction of a time-scoped revocation.
    test_a_rotated_key_leaves_an_earlier_signature_valid only exercises signed_at <
    status_from, which ANY early value satisfies -- proved by the reviewer: hardcoding
    signed_at=1970-01-01 into the signing.verdict(...) call inside verify_target left
    all 26 tests (including that one) green, because an always-ancient date can never
    reach the `signed_at >= status_from` branch and KEY_EXPIRED was consequently
    unreachable through verify_target entirely. Spec 12 item 8 asks for both
    directions; this is the missing one, signed exactly AT the boundary
    (signed_at == status_from), which is the sharpest case of ">=".
    """
    keys.revoke(conn, key_fingerprint=signed_rule["fingerprint"], status="rotated",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload,
        key_fingerprint=signed_rule["fingerprint"],
        signature=signing.sign(signed_rule["private"], payload), signed_at=LATER)
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    # signature_id order: the original (SIGNED_AT, before the boundary) first, then
    # this one (LATER, exactly at the boundary).
    assert [v.verdict for v in verdicts] == [signing.VALID, signing.KEY_EXPIRED]


def test_verification_reconstructs_the_past_context_not_the_present(conn, signed_rule):
    """C1 (review round 2, Critical): a signature recorded under
    curated_interaction/v1 must still verify after the CATALOG's current context for
    curated_interaction moves on -- verification must rebuild the payload the signature
    was actually made over, never whatever signature_target_kind says today.

    signature_target_kind carries no floor (no append-only trigger), so mutating it
    inside this test's transaction and letting `conn` roll back afterwards is a faithful
    stand-in for a real /v2 having been registered elsewhere, without needing one to
    exist in signing.FIELD_LISTS for this test to prove the point: the signed row's own
    `payload_context` column -- 'curated_interaction/v1' -- must be what gets used, and
    that context's field list is real and present regardless of what the catalog says.
    """
    conn.execute(
        "UPDATE drugref.signature_target_kind "
        "SET payload_context = 'curated_interaction/v2' "
        "WHERE target_kind = 'curated_interaction'")
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID]


def test_an_unsupported_algorithm_raises_rather_than_assuming_ed25519(conn, signed_rule):
    """C2 (review round 2, Critical): assertion_signature.algorithm is recorded per row
    and must be read back and checked, not assumed to be Ed25519 -- signing.verify only
    implements Ed25519, so a row naming anything else must raise rather than be
    silently checked against the wrong scheme (which -- since signing.verify would just
    run its one algorithm against bytes signed under a different one -- would look
    exactly like an ordinary BAD_SIGNATURE and hide that verification itself is not
    equipped to answer the question).

    UNREACHABLE THROUGH THE CHECK CONSTRAINT TODAY: assertion_signature_algorithm
    admits exactly one value, which is exactly what makes shipping this gap easy. The
    CHECK is dropped inside this test's transaction (the conn fixture rolls back, so
    nothing survives) -- 5c.1's technique for reaching pair_count's second-authority
    case, applied here to reach a value the schema does not otherwise allow in.
    """
    conn.execute("ALTER TABLE drugref.assertion_signature "
                 "DROP CONSTRAINT assertion_signature_algorithm")
    # A FRESH payload (signed_at=LATER, not SIGNED_AT) is required rather than reusing
    # signed_rule["payload"]: the unique constraint keys on (target_kind, target_id,
    # key_fingerprint, payload_digest), and re-recording the exact same digest already
    # stored by the fixture would raise UniqueViolation before algorithm is ever
    # examined -- a different signed_at gives this row its own digest.
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload,
        key_fingerprint=signed_rule["fingerprint"],
        signature=signing.sign(signed_rule["private"], payload),
        signed_at=LATER, algorithm="RSA-4096")
    with pytest.raises(signatures.UnsupportedAlgorithmError, match="RSA-4096"):
        signatures.verify_target(conn, "curated_interaction", signed_rule["target_id"])


def test_record_refuses_a_payload_context_that_contradicts_the_payload(conn,
                                                                        signed_rule):
    """C3 (review round 2, Critical): record() must not store a payload_context that
    disagrees with the payload it digests. Proved by the reviewer: recording a
    curated_interaction/v1 payload under payload_context='curated_condition/v1' stored
    the lie with no error raised anywhere. Harmless ONLY because of C1 -- verification
    used to ignore this column entirely -- and once C1 makes verify_target read it back,
    a wrong context here is how a row permanently stops verifying, on an insert-only
    table with no correction path.

    A FRESH payload (signed_at=LATER) is used rather than signed_rule["payload"]:
    payload_digest is computed from the payload BYTES alone, which do not depend on the
    (correct or lied-about) payload_context string, so reusing the fixture's exact
    payload would collide with assertion_signature_unique before this check is ever
    reached -- a real failure, but the wrong one, and not the one this test exists to
    catch.
    """
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=LATER)
    assert context == "curated_interaction/v1"          # the TRUE context of `payload`
    with pytest.raises(ValueError, match="does not match"):
        signatures.record(
            conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
            payload_context="curated_condition/v1",      # THE LIE
            payload=payload, key_fingerprint=signed_rule["fingerprint"],
            signature=signing.sign(signed_rule["private"], payload), signed_at=LATER)


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


# ---- the mutation gate -------------------------------------------------------------
# ONE PARAMETRIZE LIST, checked against signing.CURATED_INTERACTION_V1 by the test
# immediately below rather than trusted to stay in step by hand (M1, review round 2):
# without that check, a field ADDED to the frozen list with no matching case here would
# be invisible -- "one rule in two places" inside the one test the brief calls most
# important in this task.
_MUTATION_CASES = [
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
]


def test_the_mutation_gate_covers_every_frozen_field():
    """M1 (review round 2): guards the gate itself against becoming a second,
    unenforced copy of signing.CURATED_INTERACTION_V1. Complete today at 15/15, but
    nothing previously asserted that the two lists agree -- a field added to the frozen
    list with no matching mutation case below would otherwise go uncovered silently."""
    assert {field for field, _ in _MUTATION_CASES} == set(signing.CURATED_INTERACTION_V1)


@pytest.mark.parametrize("field,mutated", _MUTATION_CASES)
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

    M2 (review round 2): the UNMUTATED rebuild is now asserted equal to the recorded
    payload BEFORE the mutated one is checked. Without that baseline, this test's real
    claim -- "the recorded payload and this rebuild are the SAME construction, so a
    difference after mutating one field is caused by that field alone" -- was asserted
    nowhere; had the rebuild and the fixture's payload diverged for an unrelated reason,
    every case below would have passed vacuously (both payloads merely `!=` for reasons
    having nothing to do with `field`).
    """
    _, fields = signatures.payload_fields(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=SIGNED_AT)
    assert field in dict(fields), (
        f"{field} is not in the payload at all -- either the frozen field list dropped "
        f"it, or this parametrisation names a column that no longer exists")
    baseline = signing.canonical_payload("curated_interaction/v1", fields)
    assert baseline == signed_rule["payload"], (
        "the unmutated rebuild does not match the recorded payload -- this test's "
        "premise (that a difference below is caused by the mutated field alone) does "
        "not hold, and every case would pass vacuously")
    rebuilt = signing.canonical_payload(
        "curated_interaction/v1",
        [(name, mutated if name == field else value) for name, value in fields])
    assert rebuilt != signed_rule["payload"], (
        f"changing {field} did not change the payload -- it is not covered by "
        f"signing.CURATED_INTERACTION_V1, so a signature says nothing about it")
    assert signing.verify(signed_rule["public"], rebuilt,
                          signed_rule["signature"]) is False
