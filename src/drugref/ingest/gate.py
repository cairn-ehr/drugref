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

from drugref import ids
from drugref.ingest.unii import MoietyCandidate


def _norm(name: str) -> str:
    """Case/space-fold a name for lookup and comparison.

    Delegates to ids.normalise_name, which slice 8a promoted to the shared module
    when the local-tier bridge became a second consumer. Kept as a private alias
    so this module's existing call sites read unchanged.
    """
    return ids.normalise_name(name)


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

    Both branches are folded through _norm() (review round, finding 7): the
    fallback needs it so an upstream PT with stray internal whitespace collapses
    to a clean single-spaced label rather than passing through as-is, and the
    crosswalk HIT needs it for the identical reason -- ids.normalise_name's own
    docstring states that lower-casing is the fact the whole local-tier bridge
    rests on (PBS names fold to meet the stored INN claim). Returning a crosswalk
    value un-normalised made that only CONTINGENTLY true: every entry in the
    shipped usan_inn_crosswalk.tsv happens to be lower-case today, but a future
    Title-case entry would store an INN claim the bridge's fold-based lookup can
    never match, silently killing that drug's bridge with no error anywhere.
    """
    folded_pt = _norm(cand.preferred_name)
    return _norm(crosswalk.get(folded_pt, folded_pt))
