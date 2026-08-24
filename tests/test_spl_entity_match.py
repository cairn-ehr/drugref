"""Tests for the SPL entity matcher -- the 5c.3 measurement spike's riskiest part.

Every published yield figure for slice 5c.3 comes out of ``find_matches``. This
project's most-recorded failure mode is a *partially-working parser handing back a
plausible value that then gets written down as a measurement* (PROJECT-NOTES
§ "Slice 5c.2g", seven wrong figures, every one found by implementation). So the
matcher's rules are pinned here as tests BEFORE the corpus is measured, not after
a number looks surprising.
"""
from __future__ import annotations

import pytest

from tools.spl_entity_match import (
    Entry,
    build_vocabulary,
    find_matches,
    fold,
    tokenise,
)


def _vocab(*names: tuple[str, str, str]) -> object:
    """Build a vocabulary from (kind, key, display) triples."""
    return build_vocabulary(
        Entry(kind=kind, key=key, display=display) for kind, key, display in names
    )


# --------------------------------------------------------------------------
# folding and tokenisation
# --------------------------------------------------------------------------

def test_fold_lowercases_and_strips_punctuation_but_keeps_alphanumerics():
    assert fold("CYP1A2") == "cyp1a2"
    assert fold("P-gp") == "p gp"


def test_tokenise_reports_character_offsets_into_the_ORIGINAL_text():
    # Offsets must index the original string, because the spike stores them as
    # provenance -- a consumer has to be able to cut the quoted span back out.
    text = "Avoid warfarin therapy"
    tokens = tokenise(text)
    assert [t.text for t in tokens] == ["avoid", "warfarin", "therapy"]
    warfarin = tokens[1]
    assert text[warfarin.char_start:warfarin.char_end] == "warfarin"


def test_tokenise_splits_hyphenated_names_into_separate_tokens():
    # 'P-gp' folds to two tokens, so the vocabulary key for it must too --
    # otherwise the class never matches and its yield silently reads zero.
    assert [t.text for t in tokenise("P-gp inhibitor")] == ["p", "gp", "inhibitor"]


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

def test_finds_a_single_word_moiety_by_exact_fold():
    vocab = _vocab(("moiety", "warfarin", "warfarin"))
    (match,) = find_matches("Concomitant WARFARIN increases bleeding.", vocab)
    assert match.entry.display == "warfarin"
    assert match.char_start == 12
    assert match.char_end == 20


def test_matches_only_on_whole_tokens_never_inside_a_longer_word():
    # 'iron' must not fire inside 'environment'. Substring matching over a
    # 19,438-name registry would manufacture yield out of ordinary English.
    vocab = _vocab(("moiety", "iron", "iron"))
    assert find_matches("environmental exposure", vocab) == ()


def test_matches_a_multi_word_class_name():
    vocab = _vocab(("class", "cytochrome p450 1a2 inhibitors",
                    "Cytochrome P450 1A2 Inhibitors [MoA]"))
    (match,) = find_matches("avoid cytochrome P450 1A2 inhibitors", vocab)
    assert match.entries[0].display == "Cytochrome P450 1A2 Inhibitors [MoA]"


def test_a_parenthetical_interrupting_a_class_phrase_defeats_the_match():
    # The real tizanidine label reads 'strong cytochrome P450 1A2 (CYP1A2)
    # inhibitors'. The matcher is CONTIGUOUS, so this does not match -- and
    # asserting the negative is the point. A matcher that "helpfully" skipped
    # the parenthetical would inflate class yield with spans it cannot quote
    # back, and the measurement must report this miss rather than paper over it.
    vocab = _vocab(("class", "cytochrome p450 1a2 inhibitors",
                    "Cytochrome P450 1A2 Inhibitors [MoA]"))
    text = "with strong cytochrome P450 1A2 (CYP1A2) inhibitors is contraindicated"
    assert find_matches(text, vocab) == ()


def test_longest_match_wins_and_matches_do_not_overlap():
    # 'aspirin' is a moiety; 'aspirin and dipyridamole' could be a product name.
    # If both matched, one span would be counted twice and the pair yield would
    # double-count. Longest wins, and the shorter one inside it is suppressed.
    vocab = _vocab(
        ("moiety", "aspirin", "aspirin"),
        ("moiety", "aspirin and dipyridamole", "aspirin and dipyridamole"),
    )
    (match,) = find_matches("Give aspirin and dipyridamole daily", vocab)
    assert match.entry.display == "aspirin and dipyridamole"


def test_a_name_recurring_in_the_text_is_reported_once_per_occurrence():
    # Occurrence counts and distinct-entity counts are different units, and the
    # 5c.3 evaluation was already burned once by quoting one as the other.
    vocab = _vocab(("moiety", "warfarin", "warfarin"))
    matches = find_matches("warfarin ... warfarin again", vocab)
    assert len(matches) == 2


def test_two_entries_sharing_one_folded_key_are_both_reported():
    # A moiety and a class can fold onto the same string. Dropping one silently
    # would decide a grain question by accident; both are returned so the
    # measurement can count the collision instead of hiding it.
    vocab = _vocab(
        ("moiety", "digoxin", "digoxin"),
        ("class", "digoxin", "Digoxin [EPC]"),
    )
    (match,) = find_matches("digoxin toxicity", vocab)
    assert {e.kind for e in match.entries} == {"moiety", "class"}


def test_empty_and_blank_text_yield_nothing_rather_than_raising():
    vocab = _vocab(("moiety", "warfarin", "warfarin"))
    assert find_matches("", vocab) == ()
    assert find_matches("   \n  ", vocab) == ()


def test_vocabulary_refuses_an_entry_whose_key_folds_to_nothing():
    # A name that is pure punctuation would key on the empty tuple and then
    # match at every position. Refuse it at build time.
    with pytest.raises(ValueError, match="folds to no tokens"):
        _vocab(("moiety", "---", "---"))


# --------------------------------------------------------------------------
# class-name normalisation
# --------------------------------------------------------------------------

def test_strip_axis_tag_removes_the_stored_axis_suffix():
    # 'Cytochrome P450 1A2 Inhibitors [MoA]' is how drugref stores it; no label
    # ever writes the tag. Matching the stored form verbatim would score a
    # near-zero class yield and invite the wrong conclusion.
    from tools.spl_entity_match import strip_axis_tag
    assert strip_axis_tag("Cytochrome P450 1A2 Inhibitors [MoA]") == (
        "Cytochrome P450 1A2 Inhibitors"
    )
    assert strip_axis_tag("CYP1A2 strong inhibitor [FDA-CYP]") == (
        "CYP1A2 strong inhibitor"
    )


def test_strip_axis_tag_leaves_an_untagged_name_alone():
    from tools.spl_entity_match import strip_axis_tag
    assert strip_axis_tag("Warfarin") == "Warfarin"


def test_name_variants_offers_singular_and_plural_and_nothing_else():
    # The variant set is deliberately minimal. Word-order rewrites and
    # CYP-abbreviation expansion are REAL gaps between drugref's vocabulary and
    # label prose; the measurement exists to size them, not to hide them.
    from tools.spl_entity_match import name_variants
    assert name_variants("CYP3A Inhibitors [MoA]") == (
        "CYP3A Inhibitors", "CYP3A Inhibitor",
    )
    assert name_variants("CYP1A2 strong inhibitor [FDA-CYP]") == (
        "CYP1A2 strong inhibitor", "CYP1A2 strong inhibitors",
    )


def test_name_variants_deduplicates_rather_than_returning_one_name_twice():
    from tools.spl_entity_match import name_variants
    assert len(set(name_variants("Antacids"))) == len(name_variants("Antacids"))
