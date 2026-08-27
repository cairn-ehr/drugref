# tests/test_spl_suppress_derive.py
"""Deriving the negative vocabulary from measured next-word distributions.

The design spec puts this in scope for the slice: *"nine terms is a starting
point that was measured, not a finished list"*. What it derives is a RANKED
CANDIDATE LIST for a human to rule on, never an automatic vocabulary -- and that
restraint is the finding, not a hedge.

**WHY IT CANNOT DECIDE BY ITSELF.** `lead` is followed by `to` in 9,157 of 9,160
occurrences and the bigram is a verb; `warfarin` is followed by `sodium` on many
of its occurrences and the bigram is still the drug. **Dominance is identical in
both cases**; what differs is whether the longer phrase names an entity, which is
a reading. The round's first pass asserted such a cause without checking it
(*"lead is a verb"*) and got another backwards -- `alcohol` was called a false
positive when 13,530 of its occurrences are ethanol as a genuine interactant.

So this module measures, ranks, and stops.
"""
import pytest

from drugref.ingest import spl_match


def _vocab(*names: str) -> spl_match.Vocabulary:
    return spl_match.build_vocabulary([
        spl_match.Entry(kind="moiety", key=name, display=name,
                        moiety_uuid=f"U-{name}")
        for name in names])


def test_the_following_word_of_every_occurrence_is_tallied():
    texts = ["may lead to bleeding", "can lead to nausea", "chelation of lead is"]
    (profile,) = spl_match.next_word_profiles(
        texts, _vocab("lead"), min_occurrences=1)
    assert profile.occurrences == 3
    assert profile.following == {"to": 2, "is": 1}


def test_a_name_at_the_very_end_of_a_text_has_no_following_word():
    """Counted in `occurrences` and absent from `following`, so the shares are
    computed against the right denominator -- an occurrence with no next word is
    evidence AGAINST the bigram, not missing data."""
    (profile,) = spl_match.next_word_profiles(
        ["it may cause lead"], _vocab("lead"), min_occurrences=1)
    assert profile.occurrences == 1
    assert profile.following == {}
    assert profile.dominance == 0.0


def test_only_SINGLE_TOKEN_names_are_profiled():
    """A multi-token name is already the longer phrase; suppressing a phrase
    inside another phrase is not what this vocabulary is for."""
    profiles = spl_match.next_word_profiles(
        ["give ferrous sulfate now"], _vocab("ferrous sulfate"),
        min_occurrences=1)
    assert profiles == ()


def test_profiles_come_back_ranked_by_how_much_evidence_they_carry():
    texts = ["lead to x lead to y lead to z", "gold is inert"]
    profiles = spl_match.next_word_profiles(
        texts, _vocab("lead", "gold"), min_occurrences=1)
    assert [p.name for p in profiles] == ["lead", "gold"]


def test_a_name_below_the_occurrence_floor_is_not_profiled_at_all():
    """A distribution over three occurrences is not a measurement."""
    profiles = spl_match.next_word_profiles(
        ["lead to x"], _vocab("lead"), min_occurrences=100)
    assert profiles == ()


def test_dominance_is_the_share_of_ALL_occurrences_not_of_the_followed_ones():
    profiles = spl_match.next_word_profiles(
        ["lead to x", "lead to y", "lead"], _vocab("lead"), min_occurrences=1)
    assert profiles[0].dominance == pytest.approx(2 / 3)


# --------------------------------------------------------------------------
# The candidate list, and what it deliberately will not do
# --------------------------------------------------------------------------

def test_a_dominated_name_becomes_a_CANDIDATE_bigram():
    profiles = spl_match.next_word_profiles(
        ["lead to a", "lead to b", "lead to c", "lead is d"],
        _vocab("lead"), min_occurrences=1)
    (candidate,) = spl_match.suppression_candidates(
        profiles, min_dominance=0.5)
    assert candidate.term == "lead to"
    assert candidate.occurrences == 4
    assert candidate.share == pytest.approx(0.75)


def test_an_UNDOMINATED_name_is_not_offered_as_a_candidate():
    profiles = spl_match.next_word_profiles(
        ["x sodium", "x tablets", "x injection", "x is"],
        _vocab("x"), min_occurrences=1)
    assert spl_match.suppression_candidates(profiles, min_dominance=0.5) == ()


def test_a_term_ALREADY_in_the_vocabulary_is_not_offered_again():
    """The shipped list is an input, so a second run does not re-propose what a
    human already ruled on."""
    profiles = spl_match.next_word_profiles(
        ["lead to a", "lead to b"], _vocab("lead"), min_occurrences=1)
    assert spl_match.suppression_candidates(
        profiles, min_dominance=0.5, already=("lead to",)) == ()


def test_candidates_are_ranked_by_occurrences_so_the_biggest_read_first():
    profiles = spl_match.next_word_profiles(
        ["a to x"] * 2 + ["b to y"] * 5, _vocab("a", "b"), min_occurrences=1)
    candidates = spl_match.suppression_candidates(profiles, min_dominance=0.5)
    assert [c.term for c in candidates] == ["b to", "a to"]


def test_the_derivation_never_returns_a_vocabulary_only_candidates():
    """⇒ THE RESTRAINT IS THE FINDING.

    `lead`/`to` and `warfarin`/`sodium` have the SAME shape: one dominant next
    word. One bigram is a verb and the other is still the drug, and no
    distribution can tell them apart -- only a reading can. So the type is named
    `SuppressionCandidate`, it carries the measurement a human needs, and nothing
    here writes the shipped file.
    """
    assert not hasattr(spl_match, "derive_suppression_terms")
    profiles = spl_match.next_word_profiles(
        ["lead to a", "lead to b"], _vocab("lead"), min_occurrences=1)
    (candidate,) = spl_match.suppression_candidates(profiles, min_dominance=0.5)
    assert candidate.__class__.__name__ == "SuppressionCandidate"
    # Everything a reviewer needs to rule on it, and no verdict.
    assert candidate.evidence.startswith("lead: 2 occurrences")
    assert not hasattr(candidate, "suppress")


def test_the_shipped_terms_all_survive_their_own_derivation_rule():
    """Every line in the shipped file is a bigram-or-longer over a single-token
    name -- the shape this derivation produces. A term that could never be
    derived would be one nobody could re-check."""
    for term in spl_match.shipped_suppression_terms():
        assert len(term.split()) >= 2, term
