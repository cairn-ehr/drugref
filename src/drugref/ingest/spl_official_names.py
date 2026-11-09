# src/drugref/ingest/spl_official_names.py
"""UNII official names as extra moiety match keys for the SPL drug pass (#213).

WHY: the moiety matcher keys on `substance_moiety.display_name`, which is the
INN / registry name. US labels write the USAN. Measured on the 2026-08-22 corpus
(27,406 distinct interaction wordings), the drug pass never saw 'rifampin'
(13,693 spans; registry: rifampicin), 'acetaminophen' (6,588; paracetamol),
'glyburide' (2,440; glibenclamide), 'albuterol' (1,135; salbutamol) and ~80
more. UNII_Names (FDA, public domain) carries those spellings as TYPE 'of'
(official) names of the same UNII the registry already holds.

THE RULES, each one tested in tests/test_spl_official_names.py:

* A name folding onto the substance's OWN display name adds nothing (a second
  entry on one key would make every span of it ambiguous).
* A name folding onto ANOTHER moiety's display name is dropped: the registry
  name stays the sole owner of its key. ('epinephrine' is an official name of
  the racemate's UNII; the registry names the L-isomer 'epinephrine'.)
* A name two moieties claim is dropped -- ambiguity is never resolved by
  "first wins" (`spl_match.Match.ambiguous`'s rule).
* A name in the shipped exclusion file is not adopted. Every line there carries
  the measurement that justified it, as the suppression file does.

PURE: the caller reads UNII_Names (`spl_class_evidence.read_official_name_rows`)
and the registry; this module only decides.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import resources
from typing import NamedTuple

from drugref.ingest.spl_match import fold
from drugref.ingest.spl_suppress import parse_suppression_terms

#: The shipped exclusion list, a package data file (same format as suppression).
EXCLUSIONS_DATA = "spl_official_name_exclusions.txt"


def shipped_exclusions() -> tuple[str, ...]:
    """The official names deliberately NOT adopted (see the file's comments)."""
    text = resources.files("drugref.data").joinpath(EXCLUSIONS_DATA).read_text()
    return parse_suppression_terms(text)


class OfficialNameCounts(NamedTuple):
    """What became of each official name, by NAME, for the run's progress line."""

    adopted: list[str]
    #: Names folding onto the substance's own display name (a count: ~15,000).
    already_named: int
    shadowed: list[str]
    ambiguous: list[str]
    excluded: list[str]
    #: True when no UNII names file was given, so nothing was adopted at all.
    skipped: bool = False

    def __str__(self) -> str:
        if self.skipped:
            return ("WARNING: no UNII names file (--unii-names), so no official "
                    "name was adopted as a moiety key (#213 skipped)")
        return (f"{len(self.adopted):,} official names adopted as moiety keys "
                f"({self.already_named:,} already the display name; "
                f"{len(self.shadowed):,} shadowed by another moiety's name: "
                f"{sorted(self.shadowed)[:12]}; {len(self.ambiguous):,} claimed "
                f"by several moieties: {sorted(self.ambiguous)[:12]}; "
                f"{len(self.excluded):,} excluded: {sorted(self.excluded)})")


def skipped() -> OfficialNameCounts:
    """The counts of a run given no UNII names file."""
    return OfficialNameCounts([], 0, [], [], [], skipped=True)


def for_run(
    names: Mapping[str, str], rows: Iterable[tuple[str, str]] | None, *,
    by_unii: Mapping[str, str],
) -> tuple[dict[str, str], OfficialNameCounts]:
    """`adopt` with the shipped exclusions; `rows` None (no names file): nothing."""
    if rows is None:
        return dict(names), skipped()
    return adopt(names, rows, by_unii=by_unii, excluded=shipped_exclusions())


def adopt(
    names: Mapping[str, str], rows: Iterable[tuple[str, str]], *,
    by_unii: Mapping[str, str], excluded: Iterable[str],
) -> tuple[dict[str, str], OfficialNameCounts]:
    """`names` (display_name -> moiety_uuid) plus the official names it may take.

    `rows` are (official name, UNII) pairs for UNIIs the registry holds; `by_unii`
    maps those UNIIs to moieties. Returns the merged mapping (the input is not
    modified) and what happened to every name.
    """
    owner_of_key = {fold(name): uuid for name, uuid in names.items()}
    excluded_keys = {fold(term) for term in excluded}
    # Group by folded key first, so "two moieties claim it" is seen whole rather
    # than depending on which row came first.
    claims: dict[str, tuple[str, set[str]]] = {}
    for name, unii in rows:
        key = fold(name)
        if not key:
            continue
        _, moieties = claims.setdefault(key, (name, set()))
        moieties.add(by_unii[unii])

    merged = dict(names)
    adopted, shadowed, ambiguous, dropped = [], [], [], []
    already_named = 0
    for key, (name, moieties) in claims.items():
        owner = owner_of_key.get(key)
        if owner is not None and moieties == {owner}:
            already_named += 1
        elif owner is not None:
            shadowed.append(name)
        elif key in excluded_keys:
            dropped.append(name)
        elif len(moieties) > 1:
            ambiguous.append(name)
        else:
            merged[name] = next(iter(moieties))
            adopted.append(name)
    return merged, OfficialNameCounts(adopted, already_named, shadowed,
                                      ambiguous, dropped)
