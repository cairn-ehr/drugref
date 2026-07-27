# tests/test_gate.py
import pathlib
from drugref.ingest import gate, unii
from drugref.ingest import unii as unii_mod

DATA = pathlib.Path("src/drugref/data")


def _cand(name, has_inn):
    return unii.MoietyCandidate(unii="X", preferred_name=name, has_inn=has_inn)


def _unii_cand(code):
    """A no-INN candidate identified only by its UNII -- the allow-list's key."""
    return unii.MoietyCandidate(unii=code, preferred_name="", has_inn=False)


def test_row_carrying_a_unii_has_a_usable_identity():
    assert gate.has_identity_key(_cand("ACETAMINOPHEN", True)) is True


def test_row_without_a_unii_has_no_usable_identity():
    # The moiety UUID is a pure function of the UNII, so a row with none would
    # mint UUIDv5(namespace, "UNII:") -- one shared UUID that EVERY such row
    # collapses onto, merging unrelated drugs into a single immortal registry
    # entry. medrt.py already refuses concepts with no identifier for exactly
    # this reason; the identity spine must refuse them too.
    for blank in ("", "   ", "\t"):
        cand = unii.MoietyCandidate(unii=blank, preferred_name="SOMETHING", has_inn=True)
        assert gate.has_identity_key(cand) is False


def test_has_inn_is_a_moiety():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_cand("ACETAMINOPHEN", True), allow) is True


# ---- the gate rule (#26) ----------------------------------------------------
#
# INN | USAN | (RXCUI & drug-like type) | allow-list. Real values from the
# 26Feb2026 release, so each case is a substance that actually looks like this.

def _real(unii, name, *, inn=False, usan=False, rxcui=None, stype=""):
    return unii_mod.MoietyCandidate(
        unii=unii, preferred_name=name, has_inn=inn, has_usan=usan,
        substance_type=stype,
        cross_refs={"RXNORM_IN": rxcui} if rxcui else {})


AMOXICILLIN = _real("804826J2HU", "AMOXICILLIN", rxcui="133008", stype="chemical")
MORPHINE = _real("76I7G6D29C", "MORPHINE", rxcui="7052", stype="chemical")
HEPARIN = _real("ZZ45AB24CA", "HEPARIN SODIUM", inn=True, rxcui="9877", stype="polymer")
IRON_SUCROSE = _real("FZ7NYF5N8L", "IRON SUCROSE", usan=True, rxcui="24909", stype="polymer")
THUJA = _real("0T0DQN8786", "THUJA OCCIDENTALIS LEAF", rxcui="1309810",
              stype="structurallyDiverse")
POLYSORBATE = _real("6OZP39ZG8H", "POLYSORBATE 80", rxcui="8560", stype="polymer")


def test_an_rxcui_on_a_drug_like_substance_is_a_moiety():
    """The #26 headline: amoxicillin and morphine carry no INN_ID at all.

    UNII's INN_ID turns out to be a sparse cross-reference, not a has-INN flag --
    it is empty for amoxicillin, morphine, codeine, doxycycline, tacrolimus,
    dasatinib and aspirin. A registry missing those is not fit for purpose.
    """
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(AMOXICILLIN, allow) is True
    assert gate.is_moiety(MORPHINE, allow) is True


def test_a_strong_identifier_admits_regardless_of_substance_type():
    """Heparin is a `polymer`; iron sucrose is a `polymer` with only a USAN.

    Applying the substance-type filter uniformly would reject both -- along with
    enoxaparin, protamine sulfate and 345 gene/cell therapies (571 records carry
    an INN_ID with a non-drug-like type). Excluding heparin from a drug-
    interaction service would be a far worse error than admitting a botanical.

    An INN or a USAN is an act of NAMING by WHO or the USAN Council: a positive
    assertion that the substance is a medicine. SUBSTANCE_TYPE is a chemistry
    classification that was never meant to answer that question, so it may only
    ever qualify the weak signal.
    """
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(HEPARIN, allow) is True
    assert gate.is_moiety(IRON_SUCROSE, allow) is True


def test_an_rxcui_on_a_non_drug_like_substance_is_not_a_moiety():
    """RxNorm covers US-marketed content broadly, including things that are not
    drugs: homeopathic botanicals (Thuja leaf) and excipients (polysorbate 80,
    microcrystalline cellulose) all carry an RXCUI. The type constraint on the
    weak signal is what keeps ~5k of them out of the registry."""
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(THUJA, allow) is False
    assert gate.is_moiety(POLYSORBATE, allow) is False


def test_the_gate_never_narrows_every_inn_holder_is_still_admitted():
    """Monotonicity is a design property (#26 spec §3), not a coincidence.

    moiety_uuid is immortal and consumers cite it, so the gate may WIDEN but must
    never silently narrow -- a narrowing would remove drugs already published.
    An INN holder must therefore be admitted whatever its type or other signals.
    """
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    for stype in ("chemical", "polymer", "mixture", "structurallyDiverse", ""):
        cand = _real("TESTUNII01", "SOMETHING", inn=True, stype=stype)
        assert gate.is_moiety(cand, allow) is True, stype


def test_a_substance_with_no_signal_at_all_is_not_a_moiety():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_real("NOSIGNAL01", "MYSTERY POWDER", stype="chemical"),
                          allow) is False


def test_legacy_allowlist_drug_is_a_moiety_despite_no_inn():
    # DE08037SAB is magnesium sulfate's real UNII in the 26Feb2026 release.
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_unii_cand("DE08037SAB"), allow) is True


def test_allowlist_admits_a_drug_whose_upstream_display_name_drifted():
    """The allow-list is keyed on UNII, not on a display name (issue #17).

    Measured against the real 26Feb2026 release: magnesium sulfate -- the list's
    flagship entry, the substance the design cites when explaining why the list
    exists at all -- is published as "MAGNESIUM SULFATE, UNSPECIFIED FORM". The
    name-keyed list therefore matched NOTHING, and drugref silently excluded it.
    Nothing raised; the drug simply was not in the registry.

    A name is upstream's editorial choice and may be restyled in any release. The
    UNII is the immortal identity key drugref already mints moiety_uuid from, so
    keying the list on it makes this class of silent drop structurally impossible
    -- the same principle as ROADMAP's "own immortal UUIDs, never key on a name".
    """
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    drifted = unii.MoietyCandidate(unii="DE08037SAB", has_inn=False,
                                   preferred_name="MAGNESIUM SULFATE, UNSPECIFIED FORM")
    assert gate.is_moiety(drifted, allow) is True
    # And the name genuinely plays no part: an unrecognisable one still admits...
    renamed = unii.MoietyCandidate(unii="DE08037SAB", has_inn=False,
                                   preferred_name="ANYTHING UPSTREAM DECIDES TOMORROW")
    assert gate.is_moiety(renamed, allow) is True


def test_an_unlisted_substance_is_not_admitted_by_wearing_a_listed_name():
    # The converse of the test above, and the reason keying on UNII is a
    # tightening rather than a loosening: a DIFFERENT substance that happens to
    # be named like a listed one must not inherit its admission.
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    impostor = unii.MoietyCandidate(unii="ZZZZZZZZZZ", has_inn=False,
                                    preferred_name="MAGNESIUM SULFATE")
    assert gate.is_moiety(impostor, allow) is False


def test_excipient_without_inn_is_excluded():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    # OP1R32D61U is microcrystalline cellulose in the real release.
    assert gate.is_moiety(_unii_cand("OP1R32D61U"), allow) is False


def test_every_allowlist_entry_names_the_substance_it_admits():
    """The list must stay reviewable by a human, not just parseable.

    A bare column of UNII codes is undiffable -- a reviewer cannot tell whether
    a changed line added magnesium sulfate or removed it. Same argument as Plan
    B's class_expansion_policy: curator policy is data a pharmacist reads.
    """
    entries = gate.load_allowlist_entries(DATA / "legacy_allowlist.tsv")
    assert entries
    assert all(name.strip() for name in entries.values())
    assert entries["DE08037SAB"] == "magnesium sulfate"


def test_inn_display_name_uses_crosswalk_for_divergent_us_name():
    xw = gate.load_crosswalk(DATA / "usan_inn_crosswalk.tsv")
    assert gate.inn_display_name(_cand("ACETAMINOPHEN", True), xw) == "paracetamol"


def test_inn_display_name_lowercases_harmonized_name():
    xw = gate.load_crosswalk(DATA / "usan_inn_crosswalk.tsv")
    assert gate.inn_display_name(_cand("AMLODIPINE", True), xw) == "amlodipine"


def test_inn_display_name_collapses_internal_whitespace():
    # A PT with stray internal whitespace must fold to a clean single-spaced
    # label via _norm(), not pass through as-is.
    xw = gate.load_crosswalk(DATA / "usan_inn_crosswalk.tsv")
    assert gate.inn_display_name(_cand("MAGNESIUM   SULFATE", True), xw) == "magnesium sulfate"


def test_inn_display_name_normalises_a_title_case_crosswalk_hit():
    """A crosswalk HIT must fold too, not just the fallback (review round,
    finding 7). Nothing stops a future crosswalk entry from being Title-case --
    every shipped entry happens to be lower-case today, but that was only ever
    contingent, not guaranteed. ids.normalise_name's docstring is explicit that
    lower-casing is the fact the whole local-tier bridge rests on: an
    un-normalised hit would store an INN claim the fold-based PBS bridge lookup
    could never match, silently killing that drug's bridge with no error."""
    xw = {"acetaminophen": "Paracetamol"}
    assert gate.inn_display_name(_cand("ACETAMINOPHEN", True), xw) == "paracetamol"
