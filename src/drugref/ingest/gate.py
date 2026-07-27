# src/drugref/ingest/gate.py
"""The moiety-membership gate and INN display-name resolution (design §6.1).

Gate: a substance is an active drug moiety iff it HAS a WHO INN (UNII's INN_ID
signal) OR its UNII is on the small, closed legacy allow-list of pre-INN drugs
(magnesium sulfate, ...). Everything else (excipients, foods) is excluded.

INN display name: for harmonized drugs the UNII preferred term IS the INN once
case-folded; for the closed historical USAN<->INN divergences
(acetaminophen -> paracetamol) the hand-curated crosswalk overrides. This is
why slice 1 needs no WHO INN bulk-list: the gate signal comes from UNII, and
the display name from (UNII's Display Name, overridden by the crosswalk).

NAMES ARE LABELS HERE, NOT KEYS -- with exactly one deliberate exception. The
allow-list moved to UNII keys after #27 measured that its flagship entry matched
nothing upstream. The USAN<->INN crosswalk stays name-keyed because it is a
NAME-TO-NAME map: its whole subject matter is that two vocabularies spell the
same drug differently, and all five shipped entries were verified to match a real
Display Name exactly in the 26Feb2026 release.
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


def load_allowlist_entries(path: str | pathlib.Path) -> dict[str, str]:
    """Load the closed legacy-drug allow-list as {UNII: human-readable name}.

    KEYED ON UNII, NOT ON A NAME (issue #17, measured by #27). The list used to
    hold normalised display names, and against the real 26Feb2026 release its
    flagship entry matched nothing: magnesium sulfate is published as "MAGNESIUM
    SULFATE, UNSPECIFIED FORM", so the pre-INN drug the list exists to admit was
    silently excluded from the registry with no error anywhere. A display name is
    upstream's editorial choice and may be restyled in any release; the UNII is
    the immortal key drugref already mints moiety_uuid from. Keying on it is the
    ROADMAP's "own immortal UUIDs, never key on a name" applied to curator data.

    The `name` column is carried for HUMANS: a bare column of UNII codes is
    undiffable, and a reviewer must be able to see whether a changed line added
    magnesium sulfate or removed it. It is never a lookup key -- same posture as
    Plan B's class_expansion_policy, which is curator policy held as data a
    pharmacist can read.
    """
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        # QUOTE_NONE for the same reason as unii.parse: tab-delimited files with
        # no quoting convention, where csv's default lets a stray double quote
        # swallow the following lines.
        for row in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            code = (row.get("unii") or "").strip()
            if code:                                  # skip blank/padding lines
                out[code] = (row.get("name") or "").strip()
    return out


def load_allowlist(path: str | pathlib.Path) -> set[str]:
    """The allow-list as the bare set of admitted UNIIs, for is_moiety()."""
    return set(load_allowlist_entries(path))


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


#: UNII SUBSTANCE_TYPE values a *weak* signal may be trusted on. Not a definition
#: of "drug" -- see is_moiety for why it qualifies only the weak signal.
DRUG_LIKE_SUBSTANCE_TYPES = frozenset({"chemical", "protein", "nucleicAcid"})


def admission_signals(cand: MoietyCandidate, allowlist: set[str]) -> list[str]:
    """Every reason this candidate is admitted, or [] if it is not (#26).

    Returned in strength order, strongest first, and named after the UNII column
    the signal is read from so a stored row can be traced back to the release.
    The gate is `bool()` of this list -- the two cannot disagree, which is what
    makes drugref.moiety_admission an audit of the decision rather than a second,
    parallel opinion about it.

    * INN_ID / USAN_ID -- STRONG. An INN or a USAN is an act of naming by WHO or
      the USAN Council: a positive assertion that the substance is a medicine.
      A strong signal admits OUTRIGHT, whatever the substance type. That
      asymmetry is the design: 571 records carry an INN_ID with a non-drug-like
      type, and they include heparin, enoxaparin, protamine sulfate, iron sucrose
      and 346 gene/cell therapies. Losing heparin from a drug-interaction service
      would be far worse than admitting a botanical.
    * RXCUI -- WEAK, and therefore type-constrained. RxNorm covers US-marketed
      content broadly, including excipients (microcrystalline cellulose has an
      RxCUI) and homeopathic botanicals. The type test rejects ~5k of those.
    * LEGACY_ALLOWLIST -- the curated escape hatch, keyed on UNII. Still needed:
      magnesium sulfate (`mixture`), activated charcoal (`polymer`) and
      pancrelipase (`mixture`) are real drugs with neither an INN nor a USAN.

    MONOTONICITY. Every substance the old INN_ID-only gate admitted is still
    admitted, because has_inn alone suffices. moiety_uuid is immortal and
    consumers cite it, so this gate may widen but must never silently narrow.
    """
    signals = []
    if cand.has_inn:
        signals.append("INN_ID")
    if cand.has_usan:
        signals.append("USAN_ID")
    if cand.has_rxcui and cand.substance_type in DRUG_LIKE_SUBSTANCE_TYPES:
        signals.append("RXCUI")
    if cand.unii.strip() in allowlist:
        signals.append("LEGACY_ALLOWLIST")
    return signals


def is_moiety(cand: MoietyCandidate, allowlist: set[str]) -> bool:
    """True iff the candidate is an active drug moiety (design §6.1, #26).

    `allowlist` is a set of UNIIs (load_allowlist), not of names -- see that
    function for why. Matching on the immortal key also TIGHTENS the gate: a
    different substance that merely happens to share a listed drug's name no
    longer inherits its admission.
    """
    return bool(admission_signals(cand, allowlist))


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
