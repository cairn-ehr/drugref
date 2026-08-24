"""Tests for the 5c.3 spike's figure computations.

Each accounting dataclass here refuses to exist unless its parts add up. The
DrugCentral round's own summary needed three rounds of correction before its
rows, pair-rows and distinct-pairs reconciled; encoding the reconciliation as a
constructor invariant is cheaper than discovering it in review.
"""
from __future__ import annotations

import pytest

from tools.spl_ddi_measure import (
    BandTally,
    PairCount,
    Yield,
    band_for,
    count_pairs,
    frequency_profile,
    moiety_pairs,
    summarise_yield,
    tally_bands,
)
from tools.spl_entity_match import Entry, Match


def _match(kind: str, display: str, char_start: int = 0) -> Match:
    return Match(
        entries=(Entry(kind=kind, key=display, display=display),),
        char_start=char_start,
        char_end=char_start + len(display),
        token_start=0,
        token_end=1,
    )


# --------------------------------------------------------------------------
# yield
# --------------------------------------------------------------------------

def test_yield_counts_wordings_and_occurrences_as_different_units():
    matches = {
        "k1": [_match("moiety", "warfarin"), _match("moiety", "warfarin")],
        "k2": [_match("class", "CYP3A Inhibitors [MoA]")],
        "k3": [],
    }
    result = summarise_yield(matches)
    assert result.wordings == 3
    assert result.with_any_entity == 2
    assert result.with_moiety == 1
    assert result.with_class == 1
    # two occurrences of ONE distinct moiety -- the distinction the evaluation
    # lost when it quoted rows as pairs
    assert result.moiety_occurrences == 2
    assert result.distinct_moieties == 1


def test_yield_REFUSES_a_subtotal_larger_than_its_denominator():
    with pytest.raises(ValueError, match="exceeds"):
        Yield(
            wordings=2, with_any_entity=3, with_moiety=1, with_class=1,
            moiety_occurrences=1, class_occurrences=1,
            distinct_moieties=1, distinct_classes=1,
        )


def test_yield_REFUSES_an_any_entity_count_below_one_of_its_parts():
    with pytest.raises(ValueError, match="at least as large"):
        Yield(
            wordings=5, with_any_entity=1, with_moiety=3, with_class=0,
            moiety_occurrences=3, class_occurrences=0,
            distinct_moieties=1, distinct_classes=0,
        )


# --------------------------------------------------------------------------
# pairs
# --------------------------------------------------------------------------

def test_pairs_are_orientation_normalised_so_one_couple_counts_once():
    # DrugCentral's measurement found the same pair asserted in both orders, 4
    # of them disagreeing with themselves. Normalising here means the SPL figure
    # is directly comparable with that one.
    assert moiety_pairs("warfarin", [_match("moiety", "aspirin")]) == {
        ("aspirin", "warfarin")
    }
    assert moiety_pairs("aspirin", [_match("moiety", "warfarin")]) == {
        ("aspirin", "warfarin")
    }


def test_a_label_naming_its_own_drug_forms_no_pair():
    assert moiety_pairs("warfarin", [_match("moiety", "warfarin")]) == set()


def test_class_matches_do_not_become_moiety_pairs():
    assert moiety_pairs("warfarin", [_match("class", "CYP3A Inhibitors")]) == set()


def test_count_pairs_partitions_held_and_novel_exactly():
    candidate = {("a", "b"), ("c", "d"), ("e", "f")}
    held = {("c", "d")}
    result = count_pairs(candidate, held, self_pairs_excluded=2)
    assert (result.distinct, result.held, result.novel) == (3, 1, 2)
    assert round(result.novel_share, 4) == round(2 / 3, 4)
    assert result.self_pairs_excluded == 2


def test_PairCount_REFUSES_parts_that_do_not_sum():
    with pytest.raises(ValueError, match="!="):
        PairCount(distinct=10, held=3, novel=5, self_pairs_excluded=0)


def test_novel_share_of_an_empty_candidate_set_is_zero_not_a_crash():
    assert count_pairs(set(), set(), self_pairs_excluded=0).novel_share == 0.0


# --------------------------------------------------------------------------
# potency bands
# --------------------------------------------------------------------------

def test_band_for_reads_the_qualifier_immediately_before_a_mention():
    text = "with strong CYP1A2 inhibitors is contraindicated"
    assert band_for(text, text.index("CYP1A2")) == "strong"


def test_band_for_takes_the_NEAREST_band_when_a_phrase_names_two():
    # 'moderate or weak CYP1A2 inhibitors' -- taking the first would report
    # 'moderate' for a phrase whose nearest qualifier is 'weak'.
    text = "moderate or weak CYP1A2 inhibitors should be avoided"
    assert band_for(text, text.index("CYP1A2")) == "weak"


def test_band_for_does_not_reach_beyond_its_window():
    text = "strong" + " " * 100 + "CYP1A2 inhibitors"
    assert band_for(text, text.index("CYP1A2")) is None


def test_band_for_returns_None_when_nothing_qualifies_the_mention():
    text = "Concomitant use with CYP1A2 inhibitors"
    assert band_for(text, text.index("CYP1A2")) is None


def test_tally_bands_counts_banded_and_unbanded_class_mentions():
    texts = {
        "k1": "with strong CYP1A2 inhibitors here",
        "k2": "with CYP1A2 inhibitors here",
    }
    matches = {
        "k1": [_match("class", "CYP1A2 inhibitors", texts["k1"].index("CYP1A2"))],
        "k2": [_match("class", "CYP1A2 inhibitors", texts["k2"].index("CYP1A2"))],
    }
    tally = tally_bands(texts, matches)
    assert tally.class_occurrences == 2
    assert tally.banded == 1
    assert tally.by_band == {"strong": 1}
    assert tally.banded_share == 0.5


def test_BandTally_REFUSES_a_per_band_tally_that_does_not_sum():
    with pytest.raises(ValueError, match="per-band tally"):
        BandTally(class_occurrences=5, banded=3, by_band={"strong": 1})


# --------------------------------------------------------------------------
# false-positive profiling
# --------------------------------------------------------------------------

def test_frequency_profile_ranks_names_so_common_word_matches_are_visible():
    matches = {
        "k1": [_match("moiety", "iron"), _match("moiety", "warfarin")],
        "k2": [_match("moiety", "iron")],
    }
    profile = frequency_profile(matches, "moiety")
    assert profile.most_common(1) == [("iron", 2)]
    assert profile["warfarin"] == 1
