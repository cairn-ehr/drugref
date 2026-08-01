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


def test_unreviewed_expansion_root_question_matches_frozen_literal():
    """Plan B's kind. Pinned separately because the `gap_key` format is frozen PER
    KIND, not globally -- and this is the first question an external notifier could
    answer with a policy decision rather than with literature."""
    class_uuid = ids.mint_class_uuid("MED-RT", "N0000009065")  # Hematologic Act. Alt.
    assert str(ids.mint_question_uuid(
        "unreviewed_expansion_root", f"CLASS:{class_uuid}"
    )) == "a7db9f19-7a1b-56db-8f8d-b9d7b63d5d2e"


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
    ("unreviewed_expansion_root", "CLASS:84a81016-7abe-5716-bf37-2f949fcabf0b"),
])
def test_every_shipped_gap_kind_mints(gap_kind, gap_key):
    """Plan A shipped three kinds and Plan B adds a fourth; each must mint without
    special-casing."""
    assert isinstance(ids.mint_question_uuid(gap_kind, gap_key), uuid.UUID)


def test_two_kinds_about_the_same_class_are_two_questions():
    """`unpopulated_contraindication` and `unreviewed_expansion_root` share the
    CLASS:{uuid} gap_key format, so only gap_kind separates them. A class that is both
    unpopulated and unreviewed must raise two distinct, separately-citable questions."""
    key = f"CLASS:{ids.mint_class_uuid('MED-RT', 'N0000009065')}"
    assert (ids.mint_question_uuid("unpopulated_contraindication", key)
            != ids.mint_question_uuid("unreviewed_expansion_root", key))


# ---- Plan C: the four curation-dependent kinds ------------------------------
#
# Spec 10 requires one pinned literal PER gap_kind, because the gap_key format is
# frozen per kind rather than globally. An external tool that has cited one of these
# holds the UUID forever, so a change to the format is a change to a public
# identifier, not an implementation detail.

_EFFECT_CLASS = "N0000008836"
_CONTRIB_CLASS = "N0000175722"


def test_uncurated_additive_effect_question_matches_frozen_literal():
    cls = ids.mint_class_uuid("MED-RT", _EFFECT_CLASS)
    assert str(ids.mint_question_uuid(
        "uncurated_additive_effect", f"CLASS:{cls}"
    )) == "f141a451-1b8d-5780-a522-31e92b211cbe"


def test_uncurated_threshold_question_matches_frozen_literal():
    """Shares the CLASS:{uuid} gap_key with the kind above, so this pin is also the
    proof that gap_kind alone keeps two questions about ONE class apart."""
    cls = ids.mint_class_uuid("MED-RT", _EFFECT_CLASS)
    assert str(ids.mint_question_uuid(
        "uncurated_threshold", f"CLASS:{cls}"
    )) == "fbbce6a4-c5ef-5d19-a5c4-7251fd49dd73"


def test_ineffective_contribution_question_matches_frozen_literal():
    """The first COMPOUND gap_key -- two schemes joined by '/', per
    mint_question_uuid's documented convention. Pinned because the joiner and the
    order of the two halves are both frozen inputs."""
    eff = ids.mint_class_uuid("MED-RT", _EFFECT_CLASS)
    con = ids.mint_class_uuid("MED-RT", _CONTRIB_CLASS)
    assert str(ids.mint_question_uuid(
        "ineffective_contribution", f"CLASS:{eff}/CLASS:{con}"
    )) == "431834ca-00de-5c2f-b985-d82d1b74f3c6"


def test_ungraded_contribution_question_matches_frozen_literal():
    eff = ids.mint_class_uuid("MED-RT", _EFFECT_CLASS)
    con = ids.mint_class_uuid("MED-RT", _CONTRIB_CLASS)
    assert str(ids.mint_question_uuid(
        "ungraded_contribution", f"CLASS:{eff}/CLASS:{con}"
    )) == "4a5eab60-524e-5695-bc55-e395cbc4de61"


def test_a_compound_key_does_not_collide_with_its_halves():
    """The reason the compound key names BOTH classes: the same contributor class may
    be a sound promotion for one effect and a no-op for another, so folding the pair
    onto either half would hand two unrelated gaps one immortal question_uuid that
    append-only curator rows then attach to."""
    eff = ids.mint_class_uuid("MED-RT", _EFFECT_CLASS)
    con = ids.mint_class_uuid("MED-RT", _CONTRIB_CLASS)
    minted = {
        ids.mint_question_uuid("ungraded_contribution", f"CLASS:{eff}/CLASS:{con}"),
        ids.mint_question_uuid("ungraded_contribution", f"CLASS:{con}/CLASS:{eff}"),
        ids.mint_question_uuid("ungraded_contribution", f"CLASS:{eff}"),
    }
    assert len(minted) == 3
