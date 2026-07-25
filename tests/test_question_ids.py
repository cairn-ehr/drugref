# tests/test_question_ids.py
"""Immortal deterministic identity for open questions (Plan A).

A question's UUID is the ONE thing an external tool holds onto: it cannot notify
drugref about "that renal vasoconstriction thing", it needs a stable key. So the
derivation is pinned here the way test_ids.py pins the moiety and class
namespaces -- with FROZEN LITERALS, not formula-equals-formula, because both
sides of a re-derived formula drift together and the test stays green while every
externally-held reference silently breaks.

The `gap_key` format is frozen per gap_kind for the same reason: it is an input to
the UUID, so changing "CLASS:{uuid}" to a bare uuid re-mints every question.
"""
import uuid

import pytest

from drugref import ids


def test_question_namespace_matches_frozen_literal():
    # Every question_uuid in every database, and every reference an external tool
    # holds, derives from this. Pin the real value.
    assert str(ids.QUESTION_NAMESPACE) == "020f5a3b-4126-5142-a374-1e2fbfe55e0c"


def test_mint_question_uuid_is_deterministic():
    a = ids.mint_question_uuid("unclassified_moiety", "MOIETY:abc")
    assert a == ids.mint_question_uuid("unclassified_moiety", "MOIETY:abc")
    assert isinstance(a, uuid.UUID)


def test_question_uuid_matches_frozen_literal():
    """The end-to-end derivation, not just the namespace: gap_kind, the ':' joiner
    and the gap_key format are all inputs, so this pins the whole contract."""
    class_uuid = ids.mint_class_uuid("MED-RT", "N0000175722")
    assert str(ids.mint_question_uuid(
        "unpopulated_contraindication", f"CLASS:{class_uuid}"
    )) == "9b883857-3ad0-5d75-870f-4a5d5e9801c1"


def test_unmatched_ingredient_question_matches_frozen_literal():
    """The one gap_key that is not a UUID -- pinned separately because its scheme
    prefix (RXNORM_IN:) is a deliberate convention, not incidental formatting."""
    assert str(ids.mint_question_uuid(
        "unmatched_ingredient", "RXNORM_IN:5640"
    )) == "4eb8b1b9-489b-5318-b86c-1479247c4d5c"


def test_a_gap_kind_containing_the_joiner_is_rejected():
    """`f"{gap_kind}:{gap_key}"` is ambiguous if gap_kind may itself contain ':' --
    kind 'a:b' with key 'c' and kind 'a' with key 'b:c' both build "a:b:c" and mint
    the SAME question. gap_key legitimately contains colons (CLASS:, RXNORM_IN:), so
    the constraint belongs on gap_kind, which is drugref's own closed vocabulary and
    has no use for one. Enforced at mint time so it fails loudly rather than
    silently merging two unrelated questions."""
    with pytest.raises(ValueError, match="gap_kind"):
        ids.mint_question_uuid("a:b", "c")


def test_distinct_gaps_mint_distinct_questions():
    """The property the rejection above protects: with a colon-free gap_kind, the
    first ':' splits kind from key unambiguously, so no two distinct gaps collide."""
    minted = {
        ids.mint_question_uuid("a", "b:c"),
        ids.mint_question_uuid("a", "b"),
        ids.mint_question_uuid("b", "a:c"),
    }
    assert len(minted) == 3


def test_question_uuid_is_whitespace_insensitive():
    """gap_keys are assembled from text; incidental whitespace must not fork
    identity, exactly as it must not for a UNII or an NUI."""
    assert (ids.mint_question_uuid("  unclassified_moiety ", " MOIETY:abc  ")
            == ids.mint_question_uuid("unclassified_moiety", "MOIETY:abc"))


def test_question_namespace_cannot_collide_with_the_others():
    """Per-level namespaces: a moiety, a class and a question derived from the SAME
    source string must land on three different UUIDs."""
    assert ids.QUESTION_NAMESPACE not in (ids.MOIETY_NAMESPACE, ids.CLASS_NAMESPACE)
    minted = {
        ids.mint_question_uuid("X", "Y"),
        ids.mint_class_uuid("MED-RT", "X"),
        ids.mint_moiety_uuid("X"),
    }
    assert len(minted) == 3


@pytest.mark.parametrize("gap_kind,gap_key", [
    ("unpopulated_contraindication", "CLASS:84a81016-7abe-5716-bf37-2f949fcabf0b"),
    ("unclassified_moiety", "MOIETY:84a81016-7abe-5716-bf37-2f949fcabf0b"),
    ("unmatched_ingredient", "RXNORM_IN:5640"),
])
def test_every_shipped_gap_kind_mints(gap_kind, gap_key):
    """Plan A ships exactly these three kinds; each must mint without special-casing."""
    assert isinstance(ids.mint_question_uuid(gap_kind, gap_key), uuid.UUID)
