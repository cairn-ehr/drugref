# tests/test_gate.py
import pathlib
from drugref.ingest import gate, unii

DATA = pathlib.Path("src/drugref/data")


def _cand(name, has_inn):
    return unii.MoietyCandidate(unii="X", preferred_name=name, has_inn=has_inn)


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


def test_legacy_allowlist_drug_is_a_moiety_despite_no_inn():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_cand("MAGNESIUM SULFATE", False), allow) is True


def test_excipient_without_inn_is_excluded():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_cand("MICROCRYSTALLINE CELLULOSE", False), allow) is False


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
