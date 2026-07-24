# src/drugref/ingest/gate.py
"""The moiety-membership gate and INN display-name resolution (design §6.1).

Gate: a substance is an active drug moiety iff it HAS a WHO INN (UNII's INN_ID
signal) OR it is on the small, closed legacy allow-list of pre-INN drugs
(magnesium sulfate, ...). Everything else (excipients, foods) is excluded.

INN display name: for harmonized drugs the UNII preferred term IS the INN once
case-folded; for the closed historical USAN<->INN divergences
(acetaminophen -> paracetamol) the hand-curated crosswalk overrides. This is
why slice 1 needs no WHO INN bulk-list: the gate signal comes from UNII, and
the display name from (UNII PT, overridden by the divergence crosswalk).
"""
import csv
import pathlib

from drugref.ingest.unii import MoietyCandidate


def _norm(name: str) -> str:
    """Case/space-fold a name for lookup and comparison."""
    return " ".join(name.strip().lower().split())


def load_crosswalk(path: str | pathlib.Path) -> dict[str, str]:
    """Load the closed USAN->INN divergence map, keyed on the normalized US name."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        # QUOTE_NONE for the same reason as unii.parse: these are tab-delimited
        # files with no quoting convention, and csv's default would let a stray
        # double quote swallow following lines.
        for row in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            out[_norm(row["us_name"])] = row["inn"].strip()
    return out


def load_allowlist(path: str | pathlib.Path) -> set[str]:
    """Load the closed legacy-drug allow-list (normalized names)."""
    with open(path, encoding="utf-8") as fh:
        return {_norm(line) for line in fh if line.strip()}


def has_identity_key(cand: MoietyCandidate) -> bool:
    """True iff the candidate carries the UNII its immortal UUID derives from.

    This is an ADMISSION test, not the membership gate: moiety_uuid is a pure
    function of the UNII (ids.mint_moiety_uuid), so a row with a blank UNII would
    mint UUIDv5(namespace, "UNII:") -- one shared UUID that every such row
    collapses onto, merging unrelated drugs into a single registry entry that
    carries all of their INNs, CAS numbers and RxCUIs at once. Because
    moiety_uuid is immortal and the floor forbids DELETE, that merge cannot be
    undone. medrt._parse_concepts refuses identifier-less concepts for exactly
    the same reason; the identity spine refuses them here.
    """
    return bool(cand.unii.strip())


def is_moiety(cand: MoietyCandidate, allowlist: set[str]) -> bool:
    """True iff the candidate is an active drug moiety (design §6.1 gate)."""
    return cand.has_inn or _norm(cand.preferred_name) in allowlist


def inn_display_name(cand: MoietyCandidate, crosswalk: dict[str, str]) -> str:
    """The INN-preferred display label: crosswalk override, else the folded PT.

    The fallback uses _norm() (same fold as the lookup key) so an upstream PT with
    stray internal whitespace collapses to a clean single-spaced label rather than
    passing through as-is.
    """
    return crosswalk.get(_norm(cand.preferred_name), _norm(cand.preferred_name))
