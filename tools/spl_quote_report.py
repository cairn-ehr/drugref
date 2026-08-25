"""Print what each stored-prose window rule would store, over the real corpus.

**Throwaway spike code for the slice 5c.3 design round.** Split from
``tools/spl_recovery_probe.py`` under CLAUDE.md rule 4; the rules themselves are
pure functions in :mod:`tools.spl_quote_budget`, and this module only decides
what gets shown and against which denominator.

This is the measurement behind the owner's [#154] determination and behind the
25%-of-section CHECK the design spec puts in ``db/051``. The design round
published its table without a producer in the repository, which left a schema
constraint resting on a figure nobody could re-derive.
"""
from __future__ import annotations

import json
import pathlib

def report_quotes(
    cache: pathlib.Path, dsn: str, suppress_path: pathlib.Path | None
) -> None:
    """Stage 6: what each stored-prose window rule would actually store.

    This is the measurement behind the owner's #154 determination and behind
    the 25%-of-section CHECK the design spec puts in ``db/051``. It was
    published in the design round without a producer in the repository, which
    made a schema constraint rest on a figure nobody could re-derive.

    Every figure is MERGED coverage of distinct characters -- see
    :mod:`tools.spl_quote_budget` for why summing would be meaningless.
    """
    import statistics

    from tools.spl_entity_match import find_matches
    from tools.spl_quote_budget import (
        budgeted_windows,
        per_occurrence_windows,
        stored_chars,
    )
    from tools.spl_registry import load_registry, load_suppress_terms

    suppress_terms: tuple[str, ...] = ()
    if suppress_path is not None:
        suppress_terms = load_suppress_terms(suppress_path)
    print("loading drugref vocabularies ...", flush=True)
    registry = load_registry(dsn, suppress_terms=suppress_terms)

    texts = {}
    with (cache / "texts.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            texts[row["text_key"]] = row["text"]

    print(f"matching {len(texts):,} distinct wordings ...", flush=True)
    rules: dict[str, list[float]] = {
        "per occurrence, containing sentence": [],
        "per occurrence, +/-120 chars": [],
        "per occurrence, +/-60 chars": [],
        "first per moiety, +/-60": [],
        "  + cap at 25% of section": [],
        "  + hard cap at 600 chars": [],
    }
    windows_per_wording: list[int] = []
    moiety_share_kept: list[float] = []
    lengths: list[int] = []
    occurrence_counts: list[int] = []

    for text in texts.values():
        occurrences = [
            (entry.display, match.char_start, match.char_end)
            for match in find_matches(text, registry.vocabulary)
            for entry in match.entries
            if entry.kind == "moiety"
        ]
        if not occurrences:
            continue
        length = len(text)
        lengths.append(length)
        occurrence_counts.append(len(occurrences))
        distinct = len({moiety for moiety, _s, _e in occurrences})

        def pct(windows) -> float:
            return stored_chars(windows) / length if length else 0.0

        rules["per occurrence, containing sentence"].append(
            pct(per_occurrence_windows(text, occurrences, rule="sentence"))
        )
        rules["per occurrence, +/-120 chars"].append(
            pct(per_occurrence_windows(text, occurrences, rule="fixed", radius=120))
        )
        rules["per occurrence, +/-60 chars"].append(
            pct(per_occurrence_windows(text, occurrences, rule="fixed", radius=60))
        )
        rules["first per moiety, +/-60"].append(
            pct(budgeted_windows(length, occurrences, radius=60, share=1.0))
        )
        capped = budgeted_windows(length, occurrences, radius=60, share=0.25)
        rules["  + cap at 25% of section"].append(pct(capped))
        rules["  + hard cap at 600 chars"].append(
            pct(budgeted_windows(
                length, occurrences, radius=60, share=0.25, hard_cap=600
            ))
        )
        windows_per_wording.append(len(capped))
        kept = {
            moiety
            for moiety, start, _end in occurrences
            if any(w.char_start <= start < w.char_end for w in capped)
        }
        moiety_share_kept.append(len(kept) / distinct if distinct else 0.0)

    measured = len(lengths)
    print(f"\n=== THE STORED-PROSE BUDGET (over {measured:,} wordings "
          f"naming >= 1 moiety) ===")
    print(f"  mean section length               "
          f"{statistics.mean(lengths):>9,.0f} chars")
    print(f"  mean moiety occurrences/wording   "
          f"{statistics.mean(occurrence_counts):>9.1f}")
    print(f"  {'rule':<38} {'mean %':>8} {'median':>8} {'>=90%':>8}")
    for name, shares in rules.items():
        over90 = sum(1 for value in shares if value >= 0.90) / len(shares)
        print(f"  {name:<38} {statistics.mean(shares):>7.1%} "
              f"{statistics.median(shares):>8.1%} {over90:>8.1%}")
    print(f"  windows per wording (25% cap)     "
          f"{statistics.mean(windows_per_wording):>9.1f}")
    print(f"  distinct moieties keeping a window "
          f"{statistics.mean(moiety_share_kept):>8.1%}")
