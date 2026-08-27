# tests/test_spl_match.py
"""The matcher: which known moieties does a section name, and exactly where.

**It recognises entities and asserts no relation.** Deciding that a sentence
means *contraindicated* rather than *monitor* is a clinical reading of prose, and
the standing invariant is *ingest preserves evidence; curation creates clinical
judgement*. So this module answers only *which known things does this section
name, and where* -- never what the label says about them.

**The rule is the SHIPPED resolver's, not a more generous variant**: exact,
case-insensitive, contiguous, whole-token, longest-match-wins. The measured
29,258-pair floor rests on exactly that rule, and a matcher that skipped words
would produce spans it cannot quote back to a reader.

Each rule below exists to stop a specific way of inflating the yield, and each
has a test named after it.
"""
import pytest

from drugref.ingest import spl_match


def _vocab(*names: str, suppress: tuple[str, ...] = ()) -> spl_match.Vocabulary:
    entries = [
        spl_match.Entry(kind="moiety", key=name, display=name, moiety_uuid=f"U-{name}")
        for name in names
    ]
    entries += [
        spl_match.Entry(kind="suppress", key=term, display=term, moiety_uuid=None)
        for term in suppress
    ]
    return spl_match.build_vocabulary(entries)


def _displays(matches) -> list[str]:
    return [entry.display for match in matches for entry in match.entries]


# --------------------------------------------------------------------------
# Rule 1: whole tokens only
# --------------------------------------------------------------------------

def test_a_name_inside_a_longer_word_does_NOT_match():
    """Substring matching over a 19,438-name registry manufactures yield.

    'iron' inside 'environmental' is the canonical case.
    """
    matches = spl_match.find_matches("environmental exposure", _vocab("iron"))
    assert matches == ()


def test_matching_is_case_insensitive_in_both_directions():
    vocab = _vocab("Warfarin")
    assert _displays(spl_match.find_matches("WARFARIN sodium", vocab)) == ["Warfarin"]
    assert _displays(spl_match.find_matches("warfarin", vocab)) == ["Warfarin"]


def test_punctuation_separates_tokens_so_a_hyphenated_name_still_matches():
    """'P-gp' is two tokens and 'CYP1A2' is one, and the vocabulary key is folded
    the same way -- so the two sides agree by construction, not by coincidence."""
    assert _displays(spl_match.find_matches("P-gp inhibitors", _vocab("P gp"))) == ["P gp"]


# --------------------------------------------------------------------------
# Rule 2: contiguous only -- the miss is REPORTED, never papered over
# --------------------------------------------------------------------------

def test_the_matcher_does_not_skip_intervening_words():
    """The real tizanidine label reads 'strong cytochrome P450 1A2 (CYP1A2)
    inhibitors', and the parenthetical defeats the phrase.

    That miss is reported rather than papered over: a matcher that skipped words
    produces spans it cannot quote back to a reader, and the 25% quote budget is
    computed over exactly those spans.
    """
    text = "cytochrome P450 1A2 (CYP1A2) inhibitors"
    assert spl_match.find_matches(text, _vocab("cytochrome P450 1A2 inhibitors")) == ()


# --------------------------------------------------------------------------
# Rule 3: longest match wins, and matches never overlap
# --------------------------------------------------------------------------

def test_the_longest_name_wins_so_a_nested_name_is_not_counted_twice():
    matches = spl_match.find_matches(
        "ferrous sulfate lowers it", _vocab("iron", "ferrous sulfate"))
    assert _displays(matches) == ["ferrous sulfate"]


def test_a_shorter_name_still_fires_where_the_longer_one_does_not_apply():
    matches = spl_match.find_matches(
        "ferrous sulfate and ferrous", _vocab("ferrous", "ferrous sulfate"))
    assert _displays(matches) == ["ferrous sulfate", "ferrous"]


def test_matches_are_returned_in_document_order():
    """Document order is what the quote rule spends its budget in, so the
    matcher owes it -- a rule whose result moved with the plan's row order
    would not be a licensing constraint."""
    matches = spl_match.find_matches(
        "rifampin then warfarin", _vocab("warfarin", "rifampin"))
    assert _displays(matches) == ["rifampin", "warfarin"]


# --------------------------------------------------------------------------
# Offsets index the text the wording key was minted from
# --------------------------------------------------------------------------

def test_offsets_cut_the_matched_span_back_out_of_the_text():
    text = "Concomitant warfarin raises INR."
    (match,) = spl_match.find_matches(text, _vocab("warfarin"))
    assert text[match.char_start:match.char_end] == "warfarin"


def test_offsets_survive_a_multi_token_name():
    text = "Give ferrous sulfate apart."
    (match,) = spl_match.find_matches(text, _vocab("ferrous sulfate"))
    assert text[match.char_start:match.char_end] == "ferrous sulfate"


# --------------------------------------------------------------------------
# Ambiguity is UNRESOLVED, never "pick the first"
# --------------------------------------------------------------------------

def test_two_registry_entries_folding_onto_one_span_BOTH_survive():
    """FDA-CYP's rule, and it matters for DDI specifically.

    24 folded keys carry more than one registry name, covering 55 of 19,438
    (0.28%) -- mostly stereoisomers whose punctuation suffix the fold strips
    ('carvone, (+)-'). S- and R-warfarin take different CYP pathways, so nothing
    downstream may silently choose one.
    """
    entries = [
        spl_match.Entry(kind="moiety", key="carvone", display="carvone, (+)-",
                        moiety_uuid="U-1"),
        spl_match.Entry(kind="moiety", key="carvone", display="carvone, (-)-",
                        moiety_uuid="U-2"),
    ]
    (match,) = spl_match.find_matches("carvone", spl_match.build_vocabulary(entries))
    assert len(match.entries) == 2
    assert match.ambiguous is True


def test_an_unambiguous_span_is_not_flagged():
    (match,) = spl_match.find_matches("warfarin", _vocab("warfarin"))
    assert match.ambiguous is False


def test_reading_entry_on_an_ambiguous_span_RAISES_rather_than_choosing():
    entries = [
        spl_match.Entry(kind="moiety", key="x", display="a", moiety_uuid="U-1"),
        spl_match.Entry(kind="moiety", key="x", display="b", moiety_uuid="U-2"),
    ]
    (match,) = spl_match.find_matches("x", spl_match.build_vocabulary(entries))
    with pytest.raises(ValueError, match="carries 2 entries"):
        _ = match.entry


# --------------------------------------------------------------------------
# The negative vocabulary -- suppression, NOT a stop-list
# --------------------------------------------------------------------------

def test_a_suppressed_phrase_consumes_the_span_so_the_name_inside_never_fires():
    """'lead to' is a verb; 9,157 of 'lead''s 9,160 occurrences are followed by
    'to'."""
    matches = spl_match.find_matches(
        "may lead to bleeding", _vocab("lead", suppress=("lead to",)))
    assert _displays(matches) == ["lead to"]
    assert spl_match.moiety_occurrences(matches) == ()


def test_suppression_leaves_the_name_matchable_WHERE_IT_IS_REALLY_THE_DRUG():
    """This is the whole difference from a stop-list.

    'lead' the element (Pb) is a real moiety and a real interaction participant
    -- chelation therapy -- so a stop-list that deleted the name everywhere would
    delete a true positive to remove a false one.
    """
    matches = spl_match.find_matches(
        "chelation of lead is impaired", _vocab("lead", suppress=("lead to",)))
    assert _displays(matches) == ["lead"]


def test_a_suppressed_span_is_not_a_moiety_occurrence():
    matches = spl_match.find_matches(
        "serotonin syndrome may occur",
        _vocab("serotonin", suppress=("serotonin syndrome",)))
    assert spl_match.moiety_occurrences(matches) == ()


def test_the_suppression_file_ignores_comments_and_blank_lines():
    terms = spl_match.parse_suppression_terms(
        "# a comment\n\nlead to\n  prothrombin time  \n")
    assert terms == ("lead to", "prothrombin time")


def test_the_shipped_suppression_vocabulary_loads_and_is_not_empty():
    """It is SEED DATA, not a convention: it ships in the package."""
    terms = spl_match.shipped_suppression_terms()
    assert "lead to" in terms
    assert "serotonin syndrome" in terms


# --------------------------------------------------------------------------
# A vocabulary entry that cannot be matched safely is REFUSED
# --------------------------------------------------------------------------

def test_a_name_that_folds_to_no_tokens_is_refused_not_indexed():
    """A key of () would match at every position in every text."""
    with pytest.raises(ValueError, match="folds to no tokens"):
        spl_match.build_vocabulary(
            [spl_match.Entry(kind="moiety", key="---", display="---",
                             moiety_uuid="U-1")])


def test_moiety_occurrences_reports_one_row_per_colliding_entry():
    """Every colliding entry gets a row and the flag is set -- db/051's rule."""
    entries = [
        spl_match.Entry(kind="moiety", key="x", display="a", moiety_uuid="U-1"),
        spl_match.Entry(kind="moiety", key="x", display="b", moiety_uuid="U-2"),
    ]
    occurrences = spl_match.moiety_occurrences(
        spl_match.find_matches("x", spl_match.build_vocabulary(entries)))
    assert [o.moiety_uuid for o in occurrences] == ["U-1", "U-2"]
    assert all(o.ambiguous for o in occurrences)


def test_moiety_occurrences_are_in_document_order_with_their_offsets():
    text = "rifampin then warfarin"
    occurrences = spl_match.moiety_occurrences(
        spl_match.find_matches(text, _vocab("warfarin", "rifampin")))
    assert [(o.char_start, o.char_end) for o in occurrences] == [(0, 8), (14, 22)]
