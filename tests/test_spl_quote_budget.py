"""Tests for the stored-prose budget measurement (slice 5c.3 design round).

**Throwaway spike code.** It measures a licensing determination.

Issue #154 was answered "bundle a quoted window only", and that answer needs a
window RULE. The rules are measured here rather than chosen, because the obvious
per-occurrence ones do not survive contact with the corpus: a section averages
~48 moiety occurrences, so a window around each one reassembles the section.

**Overlap merging is the whole measurement.** 48 windows of 120 characters is
5,760 characters against a mean section of 3,663 -- so a rule that summed window
lengths would report >100% and a rule that ignored overlap would report a
fiction. Every figure here is merged coverage of distinct characters, and that
is pinned first.
"""
from __future__ import annotations

from tools.spl_quote_budget import (
    Window,
    budgeted_windows,
    fixed_window,
    merge_windows,
    sentence_window,
    stored_chars,
)


def test_overlapping_windows_are_MERGED_not_summed():
    # Two 10-character windows overlapping by 5 cover 15 characters, not 20.
    # Summing would let a per-occurrence rule report more stored prose than the
    # section contains, and would make every rule look worse than it is.
    merged = merge_windows([Window(char_start=0, char_end=10),
                            Window(char_start=5, char_end=15)])
    assert merged == (Window(char_start=0, char_end=15),)
    assert stored_chars(merged) == 15


def test_windows_that_only_touch_are_merged_into_one_span():
    merged = merge_windows([Window(char_start=0, char_end=10),
                            Window(char_start=10, char_end=20)])
    assert merged == (Window(char_start=0, char_end=20),)


def test_disjoint_windows_stay_separate_and_their_lengths_add():
    merged = merge_windows([Window(char_start=30, char_end=40),
                            Window(char_start=0, char_end=10)])
    assert merged == (Window(char_start=0, char_end=10),
                      Window(char_start=30, char_end=40))
    assert stored_chars(merged) == 20


def test_a_fixed_window_is_clamped_to_the_text_and_never_runs_off_either_end():
    # An occurrence at character 2 of a 20-character section cannot take 60
    # characters of context to its left. Unclamped, the arithmetic would report
    # negative starts and inflate coverage.
    assert fixed_window(20, 2, 5, radius=60) == Window(char_start=0, char_end=20)


def test_the_containing_sentence_stops_at_the_sentence_boundary():
    text = "First sentence here. Second names WARFARIN plainly. Third one."
    start = text.index("WARFARIN")
    window = sentence_window(text, start, start + len("WARFARIN"))
    assert text[window.char_start:window.char_end] == "Second names WARFARIN plainly."


def test_a_sentence_with_no_terminator_is_the_whole_text_not_an_error():
    text = "no terminator here WARFARIN at all"
    start = text.index("WARFARIN")
    window = sentence_window(text, start, start + 8)
    assert window == Window(char_start=0, char_end=len(text))


# --- the shipped rule ---------------------------------------------------------


def test_the_budget_stops_spending_before_it_exceeds_the_share():
    # 200 characters at 25% is a 50-character budget. Each window is at most
    # 2*radius + span, so the third cannot be afforded.
    text = "x" * 200
    occurrences = [("a", 10, 11), ("b", 60, 61), ("c", 110, 111)]
    windows = budgeted_windows(len(text), occurrences, radius=10, share=0.25)
    assert stored_chars(windows) <= 50
    assert len(windows) == 2


def test_only_the_FIRST_occurrence_of_each_moiety_earns_a_window():
    # A section names its subject dozens of times. Windowing every occurrence is
    # what reassembles the prose; one window per distinct drug is what makes the
    # stored fraction a fraction.
    text = "x" * 4000
    occurrences = [("warfarin", 10, 18), ("warfarin", 500, 508),
                   ("warfarin", 900, 908), ("aspirin", 1500, 1507)]
    windows = budgeted_windows(len(text), occurrences, radius=60, share=0.25)
    assert len(windows) == 2


def test_a_budget_of_zero_stores_nothing_rather_than_one_window():
    assert budgeted_windows(100, [("a", 10, 11)], radius=10, share=0.0) == ()


def test_the_stored_share_never_exceeds_the_budget_it_was_given():
    # The failure mode this rule exists to prevent is silent, additive and
    # visible only in aggregate, so the property is asserted directly rather
    # than sampled.
    text_length = 3663
    occurrences = [(f"m{i}", i * 70, i * 70 + 9) for i in range(48)]
    windows = budgeted_windows(text_length, occurrences, radius=60, share=0.25)
    assert stored_chars(windows) <= int(0.25 * text_length) + 1
