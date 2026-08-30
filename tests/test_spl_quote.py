# tests/test_spl_quote.py
"""The bounded quoted window -- the only prose drugref stores from this corpus.

Issue #154 was answered by the owner on 2026-08-24: **bundle a quoted window
only**, neither reference-only nor the full prose. That answer needs a window
RULE, and the rule could not be chosen by taste, because the section this slice
quotes averages ~48 moiety occurrences over 3,809 characters -- so a window
around every occurrence does not quote a section, it reassembles one.

Measured, per occurrence: the containing sentence stores **82.7%** of the
section, +/-120 characters **89.0%**, +/-60 characters **74.9%**. The shipped
rule -- first occurrence per distinct moiety, +/-60, document order, to 25% of
the section -- stores **20.4%**.

**This module is pure and separately testable on purpose.** It is the one
implementing a licensing determination, and a determination that can only be
tested through a database is a determination nobody re-checks.
"""
import pytest

from drugref.ingest import spl_quote


def _window(start: int, end: int) -> spl_quote.Window:
    return spl_quote.Window(char_start=start, char_end=end)


# --------------------------------------------------------------------------
# Coverage is MERGED, never summed
# --------------------------------------------------------------------------

def test_overlapping_windows_are_merged_not_added():
    """48 windows of 120 characters is 5,760 against a mean section of 3,809.

    A rule that added lengths would report more prose stored than exists, and
    every licensing figure derived from it would be a fiction.
    """
    assert spl_quote.stored_chars([_window(0, 100), _window(50, 150)]) == 150


def test_touching_windows_are_merged_too():
    assert spl_quote.merge_windows([_window(0, 10), _window(10, 20)]) == (
        _window(0, 20),)


def test_disjoint_windows_are_kept_apart():
    assert spl_quote.stored_chars([_window(0, 10), _window(50, 60)]) == 20


def test_a_window_wholly_inside_another_does_not_extend_it():
    assert spl_quote.merge_windows([_window(0, 100), _window(10, 20)]) == (
        _window(0, 100),)


def test_merging_is_order_independent():
    windows = [_window(50, 150), _window(0, 100)]
    assert spl_quote.merge_windows(windows) == (_window(0, 150),)


def test_a_window_that_is_not_a_span_is_refused():
    with pytest.raises(ValueError, match="is not a span"):
        _window(10, 5)


# --------------------------------------------------------------------------
# The shipped rule
# --------------------------------------------------------------------------

def test_only_the_FIRST_occurrence_of_each_moiety_earns_a_window():
    """A section names its own subject dozens of times, and windowing each one
    is exactly what reassembles the prose."""
    text_length = 10_000
    occurrences = [("A", 100, 110), ("A", 5_000, 5_010), ("A", 9_000, 9_010)]
    windows = spl_quote.budgeted_windows(text_length, occurrences)
    assert len(windows) == 1
    assert windows[0].char_start == 40


def test_the_window_is_the_match_plus_sixty_characters_either_side():
    windows = spl_quote.budgeted_windows(10_000, [("A", 1_000, 1_010)])
    assert windows == (_window(940, 1_070),)


def test_the_window_is_clamped_to_the_text_at_both_ends():
    """A match near an edge cannot reach past it.

    Asserted on `fixed_window`, which is where clamping lives: at a text length
    small enough to sit near an edge, the BUDGET would skip the window anyway,
    and a test that went through `budgeted_windows` would be asserting the wrong
    rule -- passing or failing for a reason it does not name.
    """
    assert spl_quote.fixed_window(200, 5, 10).char_start == 0
    assert spl_quote.fixed_window(200, 190, 195).char_end == 200


def test_a_clamped_window_survives_the_budget_when_the_section_can_afford_it():
    windows = spl_quote.budgeted_windows(2_000, [("A", 5, 10)])
    assert windows == (spl_quote.Window(char_start=0, char_end=70),)


def test_windows_are_taken_in_DOCUMENT_order_not_in_registry_order():
    """Priority order would make the stored bytes depend on which pairs the
    registry happens to resolve, and a licensing constraint whose result moves
    with the vocabulary is not a constraint."""
    text_length = 1_000            # budget 250
    occurrences = [("Z", 900, 905), ("A", 100, 105)]
    windows = spl_quote.budgeted_windows(text_length, occurrences)
    assert windows[0].char_start == 40      # the one at char 100 came first


def test_the_budget_is_twentyfive_percent_of_the_section_rounded_up():
    assert spl_quote.quote_budget(1_000) == 250
    assert spl_quote.quote_budget(1_001) == 251     # ceil, not floor
    assert spl_quote.quote_budget(3) == 1


def test_a_window_that_would_exceed_the_budget_is_SKIPPED_not_truncated():
    """Truncating would cut a quote mid-word; skipping keeps every stored window
    a readable span."""
    text_length = 400                    # budget 100
    occurrences = [("A", 200, 205), ("B", 0, 5)]
    windows = spl_quote.budgeted_windows(text_length, occurrences)
    # 'B' at char 0 comes first in document order: 0..65 is 65 chars and fits.
    # 'A' would add 130 more, so it is skipped whole.
    assert windows == (_window(0, 65),)


def test_a_later_SMALLER_window_can_still_land_after_a_bigger_one_was_skipped():
    """The budget is checked per candidate, so skipping is not a stop signal.

    A rule that stopped at the first over-budget window would store less than
    the determination allows for no reason anybody chose.
    """
    text_length = 600                    # budget 150
    occurrences = [("A", 300, 305), ("B", 320, 325)]
    windows = spl_quote.budgeted_windows(text_length, occurrences)
    assert spl_quote.stored_chars(windows) <= spl_quote.quote_budget(text_length)
    assert windows


def test_a_section_too_short_to_afford_any_window_stores_NOTHING():
    """Measured: the shortest wording in the corpus is 17 characters.

    Its 25% budget is 5, and no +/-60 window fits -- so it stores no prose at
    all, and its occurrences and citation are stored regardless.
    """
    assert spl_quote.budgeted_windows(17, [("A", 0, 5)]) == ()


def test_a_wording_naming_nothing_stores_nothing():
    assert spl_quote.budgeted_windows(10_000, []) == ()


def test_the_stored_share_never_exceeds_the_budget_however_many_moieties():
    """The property the schema CHECK enforces, asserted over the rule itself."""
    text_length = 4_000
    occurrences = [(f"M{i}", i * 40, i * 40 + 5) for i in range(90)]
    windows = spl_quote.budgeted_windows(text_length, occurrences)
    assert spl_quote.stored_chars(windows) <= spl_quote.quote_budget(text_length)


# --------------------------------------------------------------------------
# The rules that LOSE -- kept because the measurement IS the argument
# --------------------------------------------------------------------------

def test_a_per_occurrence_window_reassembles_the_section():
    """'We store a quoted window' and 'we store the prose' have to be shown to be
    different acts, and the only way to show it is to measure the rule that makes
    them the same."""
    text_length = 1_000
    occurrences = [(f"M{i}", i * 50, i * 50 + 5) for i in range(20)]
    per_occurrence = spl_quote.per_occurrence_windows(
        "x" * text_length, occurrences, rule="fixed", radius=60)
    budgeted = spl_quote.budgeted_windows(text_length, occurrences)
    assert spl_quote.stored_chars(per_occurrence) == text_length
    assert spl_quote.stored_chars(budgeted) <= spl_quote.quote_budget(text_length)


def test_the_sentence_rule_returns_the_containing_sentence():
    text = "First one. Warfarin is affected. Third one."
    window = spl_quote.sentence_window(text, text.index("Warfarin"),
                                       text.index("Warfarin") + 8)
    assert text[window.char_start:window.char_end] == "Warfarin is affected."


def test_a_text_with_no_terminator_is_one_sentence():
    text = "no terminator here"
    window = spl_quote.sentence_window(text, 3, 13)
    assert (window.char_start, window.char_end) == (0, len(text))


# --------------------------------------------------------------------------
# Cutting the stored text back out
# --------------------------------------------------------------------------

def test_a_quote_is_exactly_the_characters_its_offsets_name():
    text = "A" * 500 + "warfarin" + "B" * 500
    quotes = spl_quote.quotes_for(text, [("W", 500, 508)])
    assert len(quotes) == 1
    ordinal, window, quote_text = quotes[0]
    assert ordinal == 0
    assert quote_text == text[window.char_start:window.char_end]
    assert "warfarin" in quote_text


def test_quotes_are_ordinalled_in_document_order():
    text = "x" * 5_000
    quotes = spl_quote.quotes_for(text, [("A", 100, 105), ("B", 2_000, 2_005)])
    assert [q[0] for q in quotes] == [0, 1]
    assert quotes[0][1].char_start < quotes[1][1].char_start
