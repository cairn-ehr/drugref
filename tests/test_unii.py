import pathlib
from drugref.ingest import unii

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"


def test_parse_yields_all_rows():
    cands = list(unii.parse(FIX))
    assert len(cands) == 4


def test_has_inn_flag_from_inn_id_column():
    by_name = {c.preferred_name: c for c in unii.parse(FIX)}
    assert by_name["ACETAMINOPHEN"].has_inn is True
    assert by_name["MAGNESIUM SULFATE"].has_inn is False
    assert by_name["MICROCRYSTALLINE CELLULOSE"].has_inn is False


def test_a_stray_double_quote_does_not_swallow_following_rows(tmp_path):
    # A UNII preferred term may legitimately contain a double-prime. TSV has no
    # quoting convention, but csv's default QUOTE_MINIMAL treats a leading quote
    # as opening a quoted field and then consumes every following line until it
    # finds a closing one -- silently merging an unbounded run of substances into
    # a single mangled record. The parser must read TSV as pure delimited text.
    path = tmp_path / "quoted.tsv"
    path.write_text(
        'UNII\tPT\tRN\tRXCUI\tPUBCHEM\tINN_ID\tINCHIKEY\n'
        '1ABC000001\t"ALPHA FORM OF SOMETHING\t50-00-1\t1\t1\t1\tAAAAAAAAAAAAAA-A\n'
        '2DEF000002\tPLAIN NAME\t60-00-2\t2\t2\t2\tBBBBBBBBBBBBBB-B\n'
        '3GHI000003\tANOTHER NAME\t70-00-3\t3\t3\t3\tCCCCCCCCCCCCCC-C\n')
    cands = list(unii.parse(path))
    assert [c.unii for c in cands] == ["1ABC000001", "2DEF000002", "3GHI000003"]


def test_cross_refs_captured_when_present():
    by_name = {c.preferred_name: c for c in unii.parse(FIX)}
    acet = by_name["ACETAMINOPHEN"]
    assert acet.unii == "362O9ITL9D"
    assert acet.cross_refs["CAS"] == "103-90-2"
    assert acet.cross_refs["RXNORM_IN"] == "161"
    assert acet.cross_refs["PUBCHEM_CID"] == "1983"
    assert acet.cross_refs["INCHIKEY"] == "RZVAJINKPMORJF-UHFFFAOYSA-N"
    # RN (CAS) is populated for this row -> present; RXCUI is empty -> omitted,
    # not stored as an empty string.
    cellulose = by_name["MICROCRYSTALLINE CELLULOSE"]
    assert cellulose.cross_refs["CAS"] == "9004-34-6"      # populated -> present
    assert "RXNORM_IN" not in cellulose.cross_refs          # empty RXCUI -> omitted
