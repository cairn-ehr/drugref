# tests/test_signing_payload.py
"""The canonical payload format (spec 4.1-4.5). PURE -- no database.

EVERY TEST HERE IS A PROPERTY OF THE FORMAT ITSELF, not of drugref's tables. The format
has to be reproducible from a stored row years from now and reimplementable in another
language from the spec, so what is pinned is the bytes -- not that two calls agree with
each other, which any broken encoder also manages.
"""
import datetime as dt
import json
import pathlib

import pytest

from drugref import signing

VECTORS = pathlib.Path(__file__).parent / "fixtures" / "signing_vectors.json"


def test_the_prologue_and_context_open_the_payload():
    payload = signing.canonical_payload("curated_condition/v1", (("x", "1"),))
    assert payload.startswith(b"drugref-sig-v1\ncurated_condition/v1\n1\n")


def test_the_field_count_is_stated():
    payload = signing.canonical_payload("t/v1", (("a", "1"), ("b", "2")))
    assert payload.split(b"\n")[2] == b"2"


def test_null_and_the_empty_string_are_different_bytes():
    """`mechanism IS NULL` means 'no mechanism recorded' and `mechanism = ''` means a
    curator wrote an empty one. 5c.1 already rests on that distinction elsewhere --
    a NULL question_uuid MEANS 'this grade rests on nothing recorded'. A format that
    folded them would let either be substituted for the other under a valid signature.
    """
    assert (signing.canonical_payload("t/v1", (("mechanism", None),))
            != signing.canonical_payload("t/v1", (("mechanism", ""),)))


def test_a_null_field_is_tagged_N_with_zero_length():
    payload = signing.canonical_payload("t/v1", (("mechanism", None),))
    assert b"9:mechanism:N:0:\n" in payload


def test_a_value_containing_a_newline_is_length_delimited_not_line_delimited():
    """Length prefixes rather than delimiters is the whole reason this format is not
    CSV-shaped: `management` is free text a curator writes, and a newline in it must not
    be able to forge a field boundary."""
    payload = signing.canonical_payload("t/v1", (("mechanism", "a\nb"),))
    assert b"9:mechanism:S:3:a\nb\n" in payload


def test_a_value_that_imitates_the_encoding_cannot_forge_a_field():
    """The adversarial case the length prefix exists for: a curator (or an attacker
    writing an unsigned row) puts something shaped like an encoded field INSIDE a value.
    Two payloads that would collide under a delimiter-scanning parser must differ."""
    a = signing.canonical_payload("t/v1", (("x", "1:y:S:1:z"), ("y", "")))
    b = signing.canonical_payload("t/v1", (("x", ""), ("y", "1:y:S:1:z")))
    assert a != b


def test_lengths_are_utf8_BYTE_counts_not_character_counts():
    """A character count would disagree between a Python implementation and one in a
    language counting UTF-16 code units, which is exactly the interoperability the
    format exists to provide."""
    payload = signing.canonical_payload("t/v1", (("x", "é"),))
    assert b"1:x:S:2:\xc3\xa9\n" in payload


def test_the_context_separates_domains():
    """Spec 4.4: a condition ruling's bytes must never verify as an interaction
    judgement. The context line is what makes that structural rather than hoped for."""
    fields = (("subject_moiety_uuid", "3f7a1c22-0b64-5e9d-9a11-8c4f2e6b0d13"),)
    assert (signing.canonical_payload("curated_interaction/v1", fields)
            != signing.canonical_payload("curated_condition/v1", fields))


def test_group_members_are_sorted_so_row_order_cannot_change_the_payload():
    """A manifest is built from a SELECT, and a SELECT without ORDER BY may return rows
    in any order. If member order reached the bytes, the same database would publish two
    different manifests."""
    one = (("target_id", "1"),)
    two = (("target_id", "2"),)
    assert (signing.canonical_payload("t/v1", (), (("entries", [one, two]),))
            == signing.canonical_payload("t/v1", (), (("entries", [two, one]),)))


def test_a_group_header_names_the_group():
    """The header carries a length-prefixed name and a member count (spec 4.2):
    `--<len(group)>:<group>:<member-count>--`. Length-prefixed, not bare, for the same
    reason field names are -- see test_a_group_named_like_two_groups_is_not_two_groups
    below for the collision a bare name allowed."""
    payload = signing.canonical_payload("t/v1", (), (("entries", [(("id", "1"),)]),))
    assert b"--7:entries:1--\n" in payload


def test_two_groups_stay_distinct():
    """Members must not migrate between groups without changing the bytes."""
    m = (("id", "1"),)
    assert (signing.canonical_payload("t/v1", (), (("entries", [m]), ("upstream", [])))
            != signing.canonical_payload("t/v1", (), (("entries", []), ("upstream", [m]))))


# ---- regressions for the four collisions the review caught -----------------
#
# The first draft of this format applied its length-prefix principle to VALUES but not
# to the format's OWN structure: a group header carried a bare name and no member count,
# and a member carried no field count of its own. Nothing in this codebase could exploit
# that -- member arity is fixed by the code building each group, and contexts are
# constants -- but a canonical format whose canonicity depends on its callers behaving
# is not canonical, and this is a published reference third parties implement against.
# Each test below was run against the PRE-FIX encoder (commit c0ba811) before being
# written here, to confirm it really did collide there and is not merely asserting a
# property the old code already had.


def test_a_member_states_its_own_field_count():
    """Without a per-member field count, two members' fields ran together
    indistinguishably from one member holding all of them:
    g=[{a:1,b:9},{a:2,b:8}] collided with g=[{a:1},{b:9,a:2,b:8}] under the pre-fix
    encoder (verified independently before this test was written)."""
    two_two = signing.canonical_payload(
        "t/v1", (), (("g", [(("a", "1"), ("b", "9")), (("a", "2"), ("b", "8"))]),))
    one_three = signing.canonical_payload(
        "t/v1", (), (("g", [(("a", "1"),), (("b", "9"), ("a", "2"), ("b", "8"))]),))
    assert two_two != one_three


def test_a_group_states_its_member_count():
    """Without a member count, two one-field members and one two-field member were the
    same concatenation: g=[{a:1},{b:2}] collided with g=[{a:1,b:2}] under the pre-fix
    encoder (verified independently before this test was written)."""
    two_members = signing.canonical_payload(
        "t/v1", (), (("g", [(("a", "1"),), (("b", "2"),)]),))
    one_member = signing.canonical_payload(
        "t/v1", (), (("g", [(("a", "1"), ("b", "2"))]),))
    assert two_members != one_member


def test_a_group_named_like_two_groups_is_not_two_groups():
    """A bare group name made a group's own delimiter forgeable: a single empty group
    named "x--\\n--y" collided with two separate empty groups "x" and "y" under the
    pre-fix encoder (verified independently before this test was written), because
    "--" + "x--\\n--y" + "--\\n" is byte-identical to "--x--\\n" + "--y--\\n". The
    group name is now length-prefixed in the header, closing this the same way a
    field's name and value are."""
    one_group = signing.canonical_payload("t/v1", (), (("x--\n--y", []),))
    two_groups = signing.canonical_payload("t/v1", (), (("x", []), ("y", [])))
    assert one_group != two_groups


def test_a_malformed_context_is_refused():
    """The context occupies a whole, unbounded line of the payload (spec 4.2). Without
    validation, `canonical_payload("evil/v1\\n99", (("z", "x"),))` did not raise under
    the pre-fix encoder and instead produced a payload whose third line read b"99" --
    an attacker-chosen value sitting where a naive reader would expect the field-count
    line, not the real one (which was pushed one line further down). Verified
    independently against the pre-fix encoder before this test was written. Fixed by
    validating the context's *shape* outright (`^[a-z_]+/v[0-9]+\\Z`), narrower than
    merely 'no newline' on purpose -- it closes the whole class of separator
    characters, not just the one exploited here."""
    with pytest.raises(ValueError, match="context"):
        signing.canonical_payload("evil/v1\n99", (("z", "x"),))


def test_a_well_formed_context_is_not_refused():
    """Negative control for the check above: the validation must not reject any of the
    contexts the format actually uses, or the ad hoc "t/v1" this test file uses as a
    stand-in for them."""
    for context in ("curated_interaction/v1", "curated_condition/v1",
                     "release_manifest/v1", "t/v1"):
        signing.canonical_payload(context, (("x", "1"),))  # must not raise


def test_a_trailing_newline_context_is_refused():
    """Regression for a second-round review finding: `$` in a Python regex matches at
    end-of-string OR immediately before a single trailing newline, so a first-draft
    `^[a-z_]+/v[0-9]+$` validator (checked with `.match()`) let
    "curated_interaction/v1\\n" through -- the exact character this validator exists to
    keep out of the context line, on the FIRST context anyone would try. Confirmed
    independently against that `$`-anchored version before this test was written: it
    accepted the trailing-newline context and produced
    b"drugref-sig-v1\\ncurated_interaction/v1\\n\\n1\\n...\\n" rather than raising. Not
    reachable as a collision here -- the field-count line that follows is always a bare
    digit run, which a blank line injected by this trap can never equal -- but the
    validator's own docstring claims it closes "the whole class of separator
    characters", and a version that lets the one character it was built for through is
    exactly the kind of overclaim this slice keeps finding. Fixed with `\\Z`, which has
    no trailing-newline exception."""
    with pytest.raises(ValueError, match="context"):
        signing.canonical_payload("curated_interaction/v1\n", (("x", "1"),))


# ---- value rendering -------------------------------------------------------


def test_a_timestamp_renders_as_utc_with_exactly_six_fractional_digits():
    """Six digits always, including when the microseconds are zero: a variable-length
    rendering means the same instant has two spellings, and only one of them verifies."""
    aest = dt.timezone(dt.timedelta(hours=10))
    assert (signing.render(dt.datetime(2026, 8, 9, 14, 31, 7, 123456, tzinfo=aest))
            == "2026-08-09T04:31:07.123456Z")
    assert (signing.render(dt.datetime(2026, 8, 9, 4, 31, 7, 0, tzinfo=dt.timezone.utc))
            == "2026-08-09T04:31:07.000000Z")


def test_a_naive_timestamp_is_refused():
    """psycopg returns timestamptz as aware, so a naive value means somebody built it in
    Python -- and rendering it would silently assume a zone. Refuse rather than guess."""
    with pytest.raises(ValueError, match="naive"):
        signing.render(dt.datetime(2026, 8, 9))


def test_a_boolean_renders_before_the_integer_branch():
    """isinstance(True, int) is True in Python, so a bool tested after int renders as
    '1' -- which is also how the integer 1 renders. Two different values, one spelling."""
    assert signing.render(True) == "true"
    assert signing.render(False) == "false"
    assert signing.render(1) == "1"


def test_a_uuid_renders_lowercase_canonical():
    import uuid
    value = uuid.UUID("3F7A1C22-0B64-5E9D-9A11-8C4F2E6B0D13")
    assert signing.render(value) == "3f7a1c22-0b64-5e9d-9a11-8c4f2e6b0d13"


def test_bytes_render_as_lowercase_hex():
    assert signing.render(b"\xde\xad\xbe\xef") == "deadbeef"


def test_none_renders_as_none_not_as_a_string():
    assert signing.render(None) is None


def test_an_unrenderable_type_is_refused_rather_than_stringified():
    """str() on anything is what makes a format silently wrong: a Decimal, a memoryview
    or a dict would each get SOME spelling, and none of them a specified one."""
    with pytest.raises(TypeError):
        signing.render({"a": 1})


def test_text_is_not_unicode_normalised():
    """The signature commits to the bytes Postgres stored, not to a normalised shadow of
    them. NFC-normalising here would make two distinct stored strings sign identically.
    """
    decomposed = "é"          # e + combining acute
    composed = "é"             # precomposed e-acute
    assert signing.render(decomposed) != signing.render(composed)


# ---- the frozen field lists ------------------------------------------------


def test_every_field_list_ends_with_the_attestation_pair():
    """Spec 4.4: the signer and the moment are INSIDE the signed bytes, so a signature
    cannot be re-attributed to another key by editing a column, nor walked across a
    revocation boundary by editing a timestamp."""
    for context, fields in signing.FIELD_LISTS.items():
        assert fields[-2:] == signing.ATTESTATION_FIELDS, context


def test_no_field_list_names_a_surrogate_key_or_superseded_by():
    """target_id is a POINTER, not content. GENERATED ALWAYS AS IDENTITY values are
    local to one database, so signing them would break a signature carried into another;
    superseded_by is the one column the floor lets change, so signing it would invalidate
    every signature the moment its row is corrected."""
    for context, fields in signing.FIELD_LISTS.items():
        assert "superseded_by" not in fields, context
        assert not any(f.endswith("_id") for f in fields), context


def test_the_field_lists_have_no_duplicates():
    for context, fields in signing.FIELD_LISTS.items():
        assert len(fields) == len(set(fields)), context


# ---- the published test vectors --------------------------------------------


def test_the_committed_vectors_reproduce():
    """REGRESSION DETECTION, and it is worth being exact about what this does and does
    not prove. The vectors are GENERATED by the same encoder they check, so they cannot
    establish that the format is correct -- only that it has not DRIFTED. Correctness
    rests on the property tests above plus review of the format itself.

    What the fixture adds beyond that is independent checkability: it stores the payload
    bytes as an escaped literal beside their digest, so a reviewer can read the payload
    by eye and confirm the digest with `sha256sum` without running any drugref code --
    which is also what a third party reimplementing the format needs.
    """
    vectors = json.loads(VECTORS.read_text())
    assert vectors["format"] == "drugref-sig-v1"
    for case in vectors["cases"]:
        fields = [(name, value) for name, value in case["fields"]]
        groups = [(g["name"], [[(n, v) for n, v in m] for m in g["members"]])
                  for g in case.get("groups", [])]
        payload = signing.canonical_payload(case["context"], fields, groups)
        assert payload == case["payload"].encode("utf-8"), case["name"]
        assert signing.digest(payload).hex() == case["digest"], case["name"]


def test_the_vector_signatures_verify_under_the_committed_test_key():
    """The test key is 32 bytes of 00..1f -- obviously a test key, never registered
    anywhere real. Ed25519 is deterministic, so a signature is reproducible and can be
    committed."""
    vectors = json.loads(VECTORS.read_text())
    private = bytes.fromhex(vectors["test_private_key"])
    public = bytes.fromhex(vectors["test_public_key"])
    assert signing.fingerprint(public) == vectors["test_key_fingerprint"]
    for case in vectors["cases"]:
        payload = case["payload"].encode("utf-8")
        assert signing.sign(private, payload).hex() == case["signature"], case["name"]
        assert signing.verify(public, payload, bytes.fromhex(case["signature"]))
