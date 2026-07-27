# tests/test_gate.py
import pathlib
from drugref.ingest import gate, unii

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
