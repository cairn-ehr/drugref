# tests/test_gate.py
import pathlib
from drugref.ingest import gate, unii

DATA = pathlib.Path("src/drugref/data")


def _cand(name, has_inn):
    return unii.MoietyCandidate(unii="X", preferred_name=name, has_inn=has_inn)


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
