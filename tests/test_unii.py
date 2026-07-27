import pathlib

import pytest

from drugref.ingest import unii

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"

# The real release columns drugref reads, used by the tests that build their own
# file. Kept as one literal so a future column rename is a single edit here plus a
# fixture regeneration -- never a quietly divergent second opinion about upstream
# shape. Column ORDER is irrelevant (DictReader keys by name); presence is not.
REAL_HEADER = ("UNII\tDisplay Name\tRN\tRXCUI\tPUBCHEM\tINN_ID\tUSAN_ID"
               "\tINCHIKEY\tSUBSTANCE_TYPE\n")


def _row(unii, name, *, rn="", rxcui="", pubchem="", inn="", usan="",
         inchikey="", stype="chemical"):
    """One tab-delimited line matching REAL_HEADER, so tests state only what matters."""
    return "\t".join((unii, name, rn, rxcui, pubchem, inn, usan, inchikey, stype)) + "\n"


def _by_name() -> dict[str, unii.MoietyCandidate]:
    return {c.preferred_name: c for c in unii.parse(FIX)}


def test_parse_yields_all_rows():
    assert len(list(unii.parse(FIX))) == 11


def test_preferred_name_comes_from_the_display_name_column():
    """Regression for #27: the real UNII release has no `PT` column.

    The parser read `row.get("PT")`, and the hand-written fixture supplied one,
    so the suite was green while every real row produced preferred_name == "".
    That is not a cosmetic loss: the name becomes the moiety's display_name AND
    its INN claim value, and both the legacy allow-list and the USAN<->INN
    crosswalk are keyed on it -- so a production run would have completed
    "successfully" with an entirely unlabelled registry and a dead allow-list.
    """
    names = {c.preferred_name for c in unii.parse(FIX)}
    assert "ACETAMINOPHEN" in names
    assert "" not in names


def test_a_header_without_a_preferred_name_column_is_refused(tmp_path):
    """A missing name column must RAISE, not silently yield blank names.

    This is the actual lesson of #27. The defect was not that the column name
    was wrong -- names drift, that is normal -- it was that `row.get(...) or ""`
    turned a structural mismatch into a plausible-looking empty string. Failing
    loudly is what makes the next rename a five-minute fix instead of a silently
    corrupt registry.
    """
    path = tmp_path / "no_name_column.tsv"
    path.write_text("UNII\tRN\tINN_ID\n362O9ITL9D\t103-90-2\t626\n")
    with pytest.raises(ValueError, match="Display Name"):
        list(unii.parse(path))


def test_a_header_without_the_membership_signal_is_refused(tmp_path):
    """INN_ID absent must raise too: silently, it empties the whole registry.

    has_inn is the membership gate (design 6.1). If the column disappeared,
    `bool("")` would be False for every row, every substance would be gated out,
    and the ingest would report a successful run over an empty registry.
    """
    path = tmp_path / "no_inn_column.tsv"
    path.write_text("UNII\tDisplay Name\tRN\n362O9ITL9D\tACETAMINOPHEN\t103-90-2\n")
    with pytest.raises(ValueError, match="INN_ID"):
        list(unii.parse(path))


def test_a_missing_cross_ref_column_is_tolerated(tmp_path):
    """Cross-refs are enrichment, not identity: absence degrades, it does not break.

    The contrast with the tests above is the whole point of the header contract --
    REQUIRED columns are the ones whose absence corrupts identity, labelling or
    membership. A release that stopped shipping PUBCHEM should cost drugref a
    cross-walk scheme, not an ingest.
    """
    path = tmp_path / "no_pubchem.tsv"
    path.write_text("UNII\tDisplay Name\tINN_ID\tUSAN_ID\tRXCUI\tSUBSTANCE_TYPE\n"
                    "362O9ITL9D\tACETAMINOPHEN\t626\t\t\tchemical\n")
    cand = list(unii.parse(path))[0]
    assert cand.preferred_name == "ACETAMINOPHEN"
    assert cand.cross_refs == {}


def test_a_header_without_a_gate_signal_column_is_refused(tmp_path):
    """USAN_ID, RXCUI and SUBSTANCE_TYPE became gate-critical with #26.

    RXCUI in particular used to be an OPTIONAL cross-ref, tolerated when absent.
    Once the gate reads it, that tolerance would silently shrink the registry by
    every substance admitted on the weak signal -- so it moves to the required
    set. #27's lesson applied forward rather than relearned.
    """
    for missing in ("USAN_ID", "RXCUI", "SUBSTANCE_TYPE"):
        cols = [c for c in ("UNII", "Display Name", "INN_ID", "USAN_ID", "RXCUI",
                            "SUBSTANCE_TYPE") if c != missing]
        path = tmp_path / f"no_{missing}.tsv"
        path.write_text("\t".join(cols) + "\n" + "\t".join(["x"] * len(cols)) + "\n")
        with pytest.raises(ValueError, match=missing):
            list(unii.parse(path))


def test_has_inn_flag_from_inn_id_column():
    by_name = _by_name()
    assert by_name["ACETAMINOPHEN"].has_inn is True
    assert by_name["MAGNESIUM SULFATE, UNSPECIFIED FORM"].has_inn is False
    assert by_name["MICROCRYSTALLINE CELLULOSE"].has_inn is False


def test_a_stray_double_quote_does_not_swallow_following_rows(tmp_path):
    # A UNII preferred term may legitimately contain a double-prime. TSV has no
    # quoting convention, but csv's default QUOTE_MINIMAL treats a leading quote
    # as opening a quoted field and then consumes every following line until it
    # finds a closing one -- silently merging an unbounded run of substances into
    # a single mangled record. The parser must read TSV as pure delimited text.
    path = tmp_path / "quoted.tsv"
    path.write_text(
        REAL_HEADER
        + _row("1ABC000001", '"ALPHA FORM OF SOMETHING', rn="50-00-1", rxcui="1",
               pubchem="1", inn="1", inchikey="AAAAAAAAAAAAAA-A")
        + _row("2DEF000002", "PLAIN NAME", rn="60-00-2", rxcui="2", pubchem="2",
               inn="2", inchikey="BBBBBBBBBBBBBB-B")
        + _row("3GHI000003", "ANOTHER NAME", rn="70-00-3", rxcui="3", pubchem="3",
               inn="3", inchikey="CCCCCCCCCCCCCC-C"))
    cands = list(unii.parse(path))
    assert [c.unii for c in cands] == ["1ABC000001", "2DEF000002", "3GHI000003"]


def test_cross_refs_captured_when_present():
    by_name = _by_name()
    acet = by_name["ACETAMINOPHEN"]
    assert acet.unii == "362O9ITL9D"
    assert acet.cross_refs["CAS"] == "103-90-2"
    assert acet.cross_refs["RXNORM_IN"] == "161"
    assert acet.cross_refs["PUBCHEM_CID"] == "1983"
    assert acet.cross_refs["INCHIKEY"] == "RZVAJINKPMORJF-UHFFFAOYSA-N"


def test_an_empty_cross_ref_cell_is_omitted_not_stored_blank():
    # DE08037SAB carries no RN upstream (verified against the 26Feb2026 release,
    # which is why the extractor picks it). An empty cell must not become a CAS
    # claim of "" -- that would be an assertion drugref never had grounds to make.
    mag = _by_name()["MAGNESIUM SULFATE, UNSPECIFIED FORM"]
    assert "CAS" not in mag.cross_refs
    assert mag.cross_refs["RXNORM_IN"] == "6585"
