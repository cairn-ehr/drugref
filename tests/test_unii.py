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


def test_cross_refs_captured_when_present():
    by_name = {c.preferred_name: c for c in unii.parse(FIX)}
    acet = by_name["ACETAMINOPHEN"]
    assert acet.unii == "362O9ITL9D"
    assert acet.cross_refs["CAS"] == "103-90-2"
    assert acet.cross_refs["RXNORM_IN"] == "161"
    assert acet.cross_refs["PUBCHEM_CID"] == "1983"
    assert acet.cross_refs["INCHIKEY"] == "RZVAJINKPMORJF-UHFFFAOYSA-N"
    # Empty upstream cells are omitted, not stored as empty strings.
    assert "CAS" not in by_name["MICROCRYSTALLINE CELLULOSE"].cross_refs or \
        by_name["MICROCRYSTALLINE CELLULOSE"].cross_refs.get("RXNORM_IN") is None
