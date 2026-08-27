# src/drugref/ingest/spl_quote.py
"""The bounded quoted window -- the only prose drugref stores from this corpus.

PURE, and **separately testable on purpose**. This is the one module implementing
a licensing determination, and a determination that can only be tested through a
database is a determination nobody re-checks.

THE DETERMINATION (issue #154, answered by the owner on 2026-08-24): **bundle a
quoted window only** -- neither reference-only nor the full prose. The two
publishers of this corpus take opposite positions on it: NLM disclaims (*"cannot
guarantee the copyright status for any item"*) over labeling *"submitted to the
FDA by companies"*, while **openFDA dedicates the same bytes CC0 1.0**. So the
unit of clearance is the column, and this module owns the one column that needs
clearing.

**THE RULE, MEASURED**: +/-60 characters around the FIRST occurrence of each
distinct moiety, kept in DOCUMENT order, until 25% of the section's characters
are spent. Measured over all 26,721 wordings naming a moiety: **20.4% of a
section stored on average**, median 22.7%, 5.1 merged windows per wording,
covering 71.6% of the distinct moieties named.

**WHY NOT THE OBVIOUS RULES** -- because a per-occurrence window is not a quote,
it is the section reassembled. Over the same corpus:

| per-occurrence rule    | mean % stored | median | >=90% of section |
|------------------------|---------------|--------|------------------|
| the containing sentence| **82.7%**     | 87.2%  | 41.4%            |
| +/-120 characters      | 89.0%         | 94.0%  | 64.4%            |
| +/-60 characters       | 74.9%         | 77.9%  | 15.6%            |

**COVERAGE IS MERGED, NEVER SUMMED.** 48 windows of 120 characters is 5,760
characters against a mean section of 3,809. A rule that added window lengths
would report more prose stored than exists; one that ignored overlap entirely
would report a fiction in the other direction. Every figure here is the count of
DISTINCT characters covered, which is the only thing a licensing determination
can be argued from.

**THE BUDGET IS ENFORCED IN THE SCHEMA, NOT INTENDED HERE.** `db/051` carries a
deferred constraint trigger that re-computes `sum(char_end - char_start)` per
`text_key` at commit and refuses a wording exceeding `ceil(0.25 * char_length)`.
This module is what a correct writer uses; the trigger is what makes a wrong one
impossible. The failure mode is silent, additive and visible only in aggregate --
exactly the shape that survives a test suite.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Characters of context either side of the matched span. The owner's rule.
QUOTE_RADIUS = 60

#: Share of a section's characters the stored windows may occupy. The owner's
#: rule, and `db/051`'s trigger enforces exactly this constant -- pinned by a test
#: that reads the Python value and the catalog's, because a budget spelled twice
#: is two budgets that can disagree.
QUOTE_SHARE = 0.25

#: A sentence ends at . ! or ? followed by whitespace. Deliberately naive: SPL
#: interaction prose is full of "e.g." and "vs." and a smarter splitter would be a
#: second thing to validate. It is used ONLY to show that the SENTENCE rule loses,
#: and a splitter that under-segments makes that rule look BETTER than it is --
#: the conservative direction for the conclusion drawn from it.
_SENTENCE_END = re.compile(r"[.!?]\s")


@dataclass(frozen=True, kw_only=True)
class Window:
    """One span of a section that would be stored, as half-open characters."""

    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError(
                f"window {self.char_start}:{self.char_end} is not a span")

    @property
    def length(self) -> int:
        return self.char_end - self.char_start


def quote_budget(text_length: int) -> int:
    """How many characters of one wording may be stored. `ceil`, not `floor`.

    Rounding up rather than down so a very short section is not silently denied
    the whole determination by integer truncation -- and stated as a function
    because `db/051`'s trigger computes the identical expression, and a test
    compares the two.
    """
    return math.ceil(QUOTE_SHARE * text_length)


def merge_windows(windows: Iterable[Window]) -> tuple[Window, ...]:
    """Overlapping or touching windows folded into disjoint spans.

    This is what makes "percent of the section stored" mean anything.
    """
    ordered = sorted(windows, key=lambda w: (w.char_start, w.char_end))
    merged: list[Window] = []
    for window in ordered:
        if merged and window.char_start <= merged[-1].char_end:
            last = merged[-1]
            if window.char_end > last.char_end:
                merged[-1] = Window(char_start=last.char_start,
                                    char_end=window.char_end)
            continue
        merged.append(window)
    return tuple(merged)


def stored_chars(windows: Iterable[Window]) -> int:
    """Distinct characters covered. Merges first, so it cannot double-count."""
    return sum(window.length for window in merge_windows(windows))


def fixed_window(text_length: int, char_start: int, char_end: int, *,
                 radius: int = QUOTE_RADIUS) -> Window:
    """The match plus `radius` characters either side, clamped to the text."""
    return Window(char_start=max(0, char_start - radius),
                  char_end=min(text_length, char_end + radius))


def sentence_window(text: str, char_start: int, char_end: int) -> Window:
    """The sentence containing the match. **A RULE THAT LOSES**, kept as evidence.

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
    radius: int = QUOTE_RADIUS,
    share: float = QUOTE_SHARE,
) -> tuple[Window, ...]:
    """**THE SHIPPED RULE.** First occurrence per moiety, +/-radius, to a budget.

    `occurrences` are `(moiety, char_start, char_end)` in document order.

    Only the FIRST occurrence of each distinct moiety earns a window: a section
    names its own subject dozens of times, and windowing each one is exactly what
    reassembles the prose.

    Windows are then taken **in document order of that first occurrence** until
    the next would push merged coverage past the budget. Document order because
    it is the only ordering reproducible from the section alone -- a
    "pair-priority" ordering would depend on which pairs the registry happens to
    resolve, and a licensing constraint whose result moves with the vocabulary is
    not a constraint.

    **A window that would exceed the budget is SKIPPED, never truncated.**
    Truncating would cut a quote mid-word; skipping keeps every stored window a
    readable span. Skipping is also not a stop signal: a later, smaller window
    that still fits is taken, because a rule that halted at the first over-budget
    candidate would store less than the determination allows for no reason
    anybody chose.
    """
    budget = math.ceil(share * text_length)
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


def quotes_for(
    text: str,
    occurrences: Sequence[tuple[str, int, int]],
    *,
    radius: int = QUOTE_RADIUS,
    share: float = QUOTE_SHARE,
) -> tuple[tuple[int, Window, str], ...]:
    """`(ordinal, window, quote_text)` for one wording -- the writable rows.

    The ordinal is document order, which is also the order the budget was spent
    in, so a reader reassembling the stored windows gets them in the order the
    label wrote them.

    `text` MUST be the normalised text the occurrences were matched against.
    Passing the raw text here would cut characters the offsets do not name -- by a
    variable amount nobody can reconstruct after the fact.
    """
    windows = budgeted_windows(len(text), occurrences, radius=radius, share=share)
    return tuple(
        (ordinal, window, text[window.char_start:window.char_end])
        for ordinal, window in enumerate(windows))


def per_occurrence_windows(
    text: str,
    occurrences: Sequence[tuple[str, int, int]],
    *,
    rule: str,
    radius: int = QUOTE_RADIUS,
) -> tuple[Window, ...]:
    """Every occurrence gets a window -- **the family of rules that LOSE**.

    Kept because the measurement IS the argument: "we store a quoted window" and
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
