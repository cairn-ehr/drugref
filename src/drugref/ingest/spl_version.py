# src/drugref/ingest/spl_version.py
"""SPL's `<versionNumber>`, as one value with exactly three states (issue #170).

PURE: no I/O, no DB. Split out of `spl_dailymed` (which reads one label's XML)
under CLAUDE.md rule 4, and because a version is a value of its own: the ingest
reads it, `dedupe_by_set_id` sorts by it, `scan_release` refuses a JUNK one, and
`tools/spl_skip_census.py` counts it -- all through this one type.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum


class VersionState(Enum):
    """The three states of SPL's `<versionNumber>` (issue #170)."""

    #: Present and an integer: the only state that can win a tie-break.
    PARSED = "parsed"
    #: No `<versionNumber>` element at all. The label keeps its place, merely
    #: unversioned -- a benign context line, never a drop.
    ABSENT = "absent"
    #: Present and NOT an integer -- issue #162 case 2. The label's tie-break is
    #: destroyed, so `scan_release` refuses the run over it.
    JUNK = "junk"


@dataclass(frozen=True)
class Version:
    """One label's `<versionNumber>`, as exactly one of its three states.

    ⇒ WHY A TYPE (issue #170). This used to be `version: int | None` plus a
    `version_was_unreadable: bool` -- four combinations for three states, so
    "version 5, unreadable" was constructible and meant nothing. The census
    meanwhile re-derived the same states its own way, and the two once DISAGREED
    on `<versionNumber/>`. Now `version_of` is the one reader, and
    `__post_init__` makes the meaningless combinations unbuildable.
    """

    state: VersionState
    #: The parsed integer. Carried by PARSED and by nothing else.
    number: int | None = None
    #: The `value` attribute exactly as written (`""` when the element has none).
    #: Kept for JUNK because a bare count cannot tell a typo from a broken
    #: document. `None` exactly when the element is ABSENT.
    raw: str | None = None

    def __post_init__(self) -> None:
        if (self.number is not None) != (self.state is VersionState.PARSED):
            raise ValueError(f"only a PARSED version carries a number: {self!r}")
        if (self.raw is None) != (self.state is VersionState.ABSENT):
            raise ValueError(f"only an ABSENT version has no raw text: {self!r}")

    @property
    def sort_key(self) -> int:
        """The de-duplication key: an unparsed version ranks below every version.

        `-1` rather than `0`, because SPL version numbers are non-negative and a
        label at version 0 must still outrank one carrying no version at all.
        """
        return self.number if self.number is not None else -1


#: The one ABSENT value; it carries nothing, so every label can share it.
ABSENT_VERSION = Version(VersionState.ABSENT)


def version_from_element(element: ET.Element | None) -> Version:
    """The `Version` a `<versionNumber>` element (or its absence) carries.

    ⇒ IT KEYS ON THE ELEMENT, NOT ON THE ATTRIBUTE: `<versionNumber/>` with no
    `value` is JUNK, not ABSENT. The census once keyed on the attribute and so
    reported zero for a condition the ingest aborts the whole run over. Callers
    pass the element `spl_dailymed.version_of` finds, so the namespace lives in
    one place.
    """
    if element is None:
        return ABSENT_VERSION
    raw = element.get("value") or ""
    try:
        return Version(VersionState.PARSED, number=int(raw), raw=raw)
    except ValueError:
        return Version(VersionState.JUNK, raw=raw)
