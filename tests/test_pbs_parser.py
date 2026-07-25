"""Pure tests for the PBS parser: no database, no network.

Every expectation here is drawn from the real 2026-07 release (spec 5.3), not
from the PBS data dictionary and not from intuition. Where a case looks odd, it
is odd because the upstream data is.
"""
import pathlib
import textwrap

from drugref.ingest import pbs


def test_splits_on_with():
    """' with ' is PBS's primary combination separator: 208 distinct names."""
    assert pbs.split_components("Abacavir with lamivudine") == ["abacavir", "lamivudine"]


def test_splits_on_and():
    """' and ' is the second: 88 distinct names."""
    assert pbs.split_components("Abiraterone and methylprednisolone") == [
        "abiraterone", "methylprednisolone"]


def test_does_not_split_on_plus():
    """' + ' appears in ZERO of the 1,086 distinct names upstream. A plus sign is
    therefore part of a name, never a separator, and splitting on it would shred
    real names for no gain."""
    assert pbs.split_components("Vitamin B+C complex") == ["vitamin b+c complex"]


def test_splits_multi_component_chains():
    """Real names chain commas and ' and ': 'Allantoin with sulfur, phenol, coal
    tar solution and menthol'."""
    assert pbs.split_components(
        "Allantoin with sulfur, phenol, coal tar solution and menthol") == [
        "allantoin", "sulfur", "phenol", "coal tar solution", "menthol"]


def test_strips_parenthetical_annotations():
    """'Acetic Acid (33 per cent)' must match the INN 'acetic acid'."""
    assert pbs.split_components("Acetic Acid (33 per cent)") == ["acetic acid"]
    assert pbs.split_components("Acetone (use as additive only)") == ["acetone"]


def test_folds_case():
    """PBS is Title-case; INN claims are lower-case."""
    assert pbs.split_components("Rifaximin") == ["rifaximin"]


def test_strip_salt_removes_a_trailing_salt_token():
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("alfuzosin hydrochloride", suffixes) == "alfuzosin"
    assert pbs.strip_salt("metoprolol succinate", suffixes) == "metoprolol"


def test_strip_salt_never_strips_acid():
    """THE TRAP. 'acid' is the last word of real INNs -- alendronic acid, folic
    acid, folinic acid. Stripping it destroys correct matches, so it is not on
    the list and this test pins that."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert "acid" not in suffixes
    assert pbs.strip_salt("alendronic acid", suffixes) is None
    assert pbs.strip_salt("folic acid", suffixes) is None


def test_strip_salt_returns_none_when_nothing_to_strip():
    """None means 'no fallback to try', distinct from a stripped empty string."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("rifaximin", suffixes) is None


def test_strip_salt_never_strips_the_whole_name():
    """'Docusate sodium' strips fine, but a name that IS only a salt token would
    otherwise strip to nothing and then match everything."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("sodium", suffixes) is None


def test_dimethyl_fumarate_is_a_regression_case():
    """'Dimethyl fumarate' and 'Diroximel fumarate' are INNs IN THEIR OWN RIGHT,
    even though 'fumarate' is a genuine salt token elsewhere ('Ferrous
    fumarate'). This is why the caller must try the UNSTRIPPED name FIRST and
    only fall back to the stripped one -- strip_salt itself is deliberately
    dumb, so the ordering is the safeguard (spec 5.3)."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("dimethyl fumarate", suffixes) == "dimethyl"


def _write_csv(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """Write a minimal items.csv. The BOM is deliberate: the real files have one."""
    path = tmp_path / "items.csv"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8-sig")
    return path


def test_parse_items_reads_the_allow_listed_columns(tmp_path):
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        10001J_14023,10001J,Xifaxan,Rifaximin,Rifaximin,Tablet 550 mg,GE,A
        """)
    items = list(pbs.parse_items(path))
    assert len(items) == 1
    assert items[0].source_code == "10001J_14023"
    assert items[0].drug_name == "Rifaximin"
    assert items[0].benefit_type_code == "A"


def test_parse_items_falls_back_to_drug_name_when_li_drug_name_is_null(tmp_path):
    """159 rows upstream carry li_drug_name='null' -- every one with a usable
    drug_name. Without this fallback they would all be lost."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        X_1,X,null,null,Aspirin,null,GE,U
        """)
    items = list(pbs.parse_items(path))
    assert items[0].drug_name == "Aspirin"


def test_parse_items_maps_the_null_sentinel_to_none(tmp_path):
    """The literal string 'null' must never reach the database as a value."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        X_1,X,null,Aspirin,Aspirin,null,GE,U
        """)
    items = list(pbs.parse_items(path))
    assert items[0].brand_name is None
    assert items[0].form_strength is None


def test_parse_items_ignores_encumbered_columns(tmp_path):
    """QUARANTINE (spec 6). ATC and AMT are absent from items.csv upstream; if a
    future release adds them, the fixed allow-list must still refuse to read
    them. PbsItem has nowhere to put such a value."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code,atc_code,amt_code
        X_1,X,B,Aspirin,Aspirin,Tab,GE,U,N02BA01,12345678
        """)
    item = next(pbs.parse_items(path))
    assert "N02BA01" not in str(item)
    assert "12345678" not in str(item)


def test_parse_items_skips_rows_with_no_identity(tmp_path):
    """A row with no li_item_id cannot be keyed, so it is refused rather than
    given a degenerate UUID -- the same discipline gate.has_identity_key applies
    to the identity spine."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        ,X,B,Aspirin,Aspirin,Tab,GE,U
        X_2,Y,B,Ibuprofen,Ibuprofen,Tab,GE,U
        """)
    items = list(pbs.parse_items(path))
    assert [i.source_code for i in items] == ["X_2"]
