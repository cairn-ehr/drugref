"""Measure what a stored-prose window rule would actually store.

**Throwaway spike code for the slice 5c.3 design round.** Pure functions over a
text length and a list of occurrences; no DB, no corpus, no I/O.

Issue #154 was answered by the owner on 2026-08-24 -- **bundle a quoted window
only**, the matched span plus bounded context, with the rest referenced by
citation. That answer needs a window RULE, and the rule cannot be chosen by
taste: the section this slice quotes averages **~48 moiety occurrences over
3,663 characters**, so a window around every occurrence does not quote a section,
it reassembles one. Which rules do that and which do not is a measurement, and
this module is what performs it.

**Coverage is MERGED, never summed.** 48 windows of 120 characters is 5,760
characters against a mean section of 3,663. A rule that added window lengths
would report more prose stored than exists; a rule that ignored overlap entirely
would report a fiction in the other direction. Every figure here is the count of
DISTINCT characters covered, which is the only thing a licensing determination
can be argued from.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: A sentence ends at . ! or ? followed by whitespace. Deliberately naive: SPL
#: interaction prose is full of "e.g." and "vs." and a smarter splitter would be
#: a second thing to validate. It is used only to show that the SENTENCE rule
#: loses, and a splitter that under-segments makes that rule look BETTER than it
#: is -- so the conservative direction for the conclusion drawn from it.
_SENTENCE_END = re.compile(r"[.!?]\s")


@dataclass(frozen=True, kw_only=True)
class Window:
    """One span of a section that a rule would store, as half-open characters."""

    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError(
                f"window {self.char_start}:{self.char_end} is not a span"
            )

    @property
    def length(self) -> int:
        return self.char_end - self.char_start


def merge_windows(windows: Iterable[Window]) -> tuple[Window, ...]:
    """Overlapping or touching windows folded into disjoint spans.

    This is what makes "percent of the section stored" mean anything. Without
    it the per-occurrence rules would report several hundred percent, and the
    comparison between rules -- which is the whole point -- would be noise.
    """
    ordered = sorted(windows, key=lambda w: (w.char_start, w.char_end))
    merged: list[Window] = []
    for window in ordered:
        if merged and window.char_start <= merged[-1].char_end:
            last = merged[-1]
            if window.char_end > last.char_end:
                merged[-1] = Window(
                    char_start=last.char_start, char_end=window.char_end
                )
            continue
        merged.append(window)
    return tuple(merged)


def stored_chars(windows: Iterable[Window]) -> int:
    """Distinct characters covered. Merges first, so it cannot double-count."""
    return sum(window.length for window in merge_windows(windows))


def fixed_window(
    text_length: int, char_start: int, char_end: int, *, radius: int
) -> Window:
    """The match plus ``radius`` characters either side, clamped to the text."""
    return Window(
        char_start=max(0, char_start - radius),
        char_end=min(text_length, char_end + radius),
    )


def sentence_window(text: str, char_start: int, char_end: int) -> Window:
    """The sentence containing the match.

    A text with no terminator is one sentence -- returned whole rather than
    treated as an error, because that is what it is.
    """
    start = 0
    for match in _SENTENCE_END.finditer(text, 0, char_start):
        start = match.end()
    end_match = _SENTENCE_END.search(text, char_end)
    end = end_match.start() + 1 if end_match else len(text)
    return Window(char_start=start, char_end=max(end, char_end))


def budgeted_windows(
    text_length: int,
    occurrences: Sequence[tuple[str, int, int]],
    *,
    radius: int,
    share: float,
    hard_cap: int | None = None,
) -> tuple[Window, ...]:
    """**The shipped rule.** First occurrence per moiety, ±radius, to a budget.

    ``occurrences`` are ``(moiety, char_start, char_end)`` in document order.

    Only the FIRST occurrence of each distinct moiety earns a window: a section
    names its own subject dozens of times, and windowing each one is exactly
    what reassembles the prose.

    Windows are then taken **in document order of that first occurrence** until
    the next one would push merged coverage past ``ceil(share * text_length)``.
    Document order is used because it is the only ordering reproducible from the
    section alone -- a "pair-priority" ordering would depend on which pairs the
    registry happens to resolve, and a licensing constraint whose result moves
    with the vocabulary is not a constraint.

    **The budget is checked against MERGED coverage, and a window that would
    exceed it is skipped rather than truncated.** Truncating would cut a quote
    mid-word; skipping keeps every stored window a readable span.
    """
    budget = math.ceil(share * text_length)
    if hard_cap is not None:
        budget = min(budget, hard_cap)
    if budget <= 0:
        return ()

    first_seen: dict[str, tuple[int, int]] = {}
    for moiety, start, end in occurrences:
        first_seen.setdefault(moiety, (start, end))

    kept: list[Window] = []
    for start, end in sorted(first_seen.values()):
        candidate = fixed_window(text_length, start, end, radius=radius)
        if stored_chars([*kept, candidate]) > budget:
            continue
        kept.append(candidate)
    return merge_windows(kept)


def per_occurrence_windows(
    text: str,
    occurrences: Sequence[tuple[str, int, int]],
    *,
    rule: str,
    radius: int = 60,
) -> tuple[Window, ...]:
    """Every occurrence gets a window -- the family of rules that LOSE.

    Kept because the measurement is the argument: "we store a quoted window" and
    "we store the prose" have to be shown to be different acts, and the only way
    to show it is to measure the rule that makes them the same.
    """
    windows = []
    for _moiety, start, end in occurrences:
        if rule == "sentence":
            windows.append(sentence_window(text, start, end))
        else:
            windows.append(fixed_window(len(text), start, end, radius=radius))
    return merge_windows(windows)
