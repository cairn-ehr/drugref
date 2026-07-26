"""Pure tests for the PBS parser: no database, no network.

Every expectation here is drawn from the real 2026-07 release (spec 5.3), not
from the PBS data dictionary and not from intuition. Where a case looks odd, it
is odd because the upstream data is.
"""
import pathlib
import textwrap

import pytest

from drugref.ingest import pbs


def test_splits_on_with():
    """' with ' is PBS's primary combination separator: 208 distinct names."""
    assert pbs.split_components("Abacavir with lamivudine") == ["abacavir", "lamivudine"]


def test_splits_on_and():
    """' and ' is the second: 88 distinct names."""
    assert pbs.split_components("Abiraterone and methylprednisolone") == [
        "abiraterone", "methylprednisolone"]


def test_does_not_split_on_an_unspaced_plus():
    """An UNSPACED '+' is part of a name, never a separator: 'Vitamin B+C complex'
    must survive whole. Only the SPACED ' + ' form separates (see below), which is
    what lets both facts hold at once."""
    assert pbs.split_components("Vitamin B+C complex") == ["vitamin b+c complex"]


def test_splits_on_the_spaced_plus_the_fallback_column_uses():
    """THE FALLBACK-VOCABULARY GAP (fix round, finding 2).

    The ' + ' measurement ("zero of the 1,086 distinct names") was taken on
    li_drug_name. But parse_items falls back to drug_name for the 159 rows whose
    li_drug_name is the 'null' sentinel, and drug_name is the Medicinal Product
    Pack name -- a DIFFERENT vocabulary that writes combinations with ' + ', as
    the committed fixture's own drug_name column shows ('Abacavir + lamivudine',
    'coal tar solution + phenol + precipitated sulfur'). Untreated, every
    combination among those rows collapsed into ONE pseudo-ingredient that no INN
    can ever match: lost from the bridge AND polluting the residual worklist with
    a name that is not a name."""
    assert pbs.split_components("Abacavir + lamivudine") == ["abacavir", "lamivudine"]
    assert pbs.split_components(
        "coal tar solution + phenol + precipitated sulfur") == [
        "coal tar solution", "phenol", "precipitated sulfur"]


def test_splits_on_the_ampersand_form_the_fallback_column_uses():
    """The other drug_name combination form: 'abiraterone (&) methylprednisolone'.

    This one was WORSE than a missed split (fix round, finding 2). '(&)' is
    parenthesised, so the annotation strip ate it and FUSED the two halves into
    'abiraterone methylprednisolone' -- a plausible-looking single ingredient
    rather than an obviously-broken one. It must separate, not be stripped."""
    assert pbs.split_components("abiraterone (&) methylprednisolone") == [
        "abiraterone", "methylprednisolone"]


def test_a_genuine_parenthetical_is_still_stripped_not_split():
    """The separator fix must not turn every '(...)' into a split. ONLY the exact
    '(&)' marker separates; anything else in parentheses is still an annotation
    and is still stripped -- including one that merely CONTAINS an ampersand."""
    assert pbs.split_components("Acetic Acid (33 per cent)") == ["acetic acid"]
    assert pbs.split_components("Sulfur (& related salts)") == ["sulfur"]


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


def test_parse_items_yields_rows_with_no_identity_as_none(tmp_path):
    """A row with no li_item_id cannot be keyed, so admitting it into the
    database would mint a degenerate UUID every such row collapses onto -- the
    same discipline gate.has_identity_key applies to the identity spine. But
    REFUSING it is an identity-gate decision, not a parsing one (review round,
    finding 1): this pure parser yields the row anyway, with source_code=None,
    and the orchestrator (pbs_run.ingest_pbs) is the one that skips it AND
    counts it as rows_without_identity -- mirroring how ingest/unii.py yields a
    blank-UNII row for ingest/run.py to refuse and count, rather than dropping
    it silently inside the parser where nothing could ever see it happen."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        ,X,B,Aspirin,Aspirin,Tab,GE,U
        X_2,Y,B,Ibuprofen,Ibuprofen,Tab,GE,U
        """)
    items = list(pbs.parse_items(path))
    assert [i.source_code for i in items] == [None, "X_2"]


def test_parse_items_raises_if_the_li_item_id_column_is_entirely_missing(tmp_path):
    """THE COLUMN-DRIFT GUARD (review round, finding 1). If a future release
    renames li_item_id, every row would otherwise be missing the key, and
    parse_items would silently yield a PbsItem with source_code=None for every
    single row -- which pbs_run.ingest_pbs would then count entirely as
    rows_without_identity and write NOTHING, but only after it had already
    cleared the previous release's projection. That is a silent, empty,
    "successful" re-ingest: the same failure mode filed as issue #27 against
    ingest/unii.py (a renamed column there quietly disabled matching with no
    exception). A missing COLUMN is a broken upstream contract, not a per-row
    data condition, so parsing must refuse immediately instead."""
    path = _write_csv(tmp_path, """
        pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        10001J,Xifaxan,Rifaximin,Rifaximin,Tablet 550 mg,GE,A
        """)
    with pytest.raises(ValueError, match="li_item_id"):
        list(pbs.parse_items(path))


def test_parse_items_raises_if_BOTH_name_columns_are_missing(tmp_path):
    """THE SAME GUARD, EXTENDED TO THE NAME (fix round, finding 4). The identity
    column was guarded; the name columns -- the OTHER thing the whole ingest
    depends on -- were not. With both renamed, every row parses cleanly with
    drug_name=None, every product is written, and every single one lands in the
    residual worklist under the '<no drug name>' sentinel: a 0% bridge reported
    as a successful run, discoverable only by someone reading the summary.

    That is the same broken-upstream-contract class as the li_item_id rename and
    issue #27's 'PT'-vs-'Display Name' drift, so it gets the same answer: refuse
    at the header, not one silent row at a time. ONE of the two suffices -- the
    fallback from li_drug_name to drug_name is a designed path, not drift."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_form,program_code,benefit_type_code
        10001J_14023,10001J,Xifaxan,Tablet 550 mg,GE,A
        """)
    with pytest.raises(ValueError, match="li_drug_name"):
        list(pbs.parse_items(path))


def test_parse_items_accepts_either_name_column_alone(tmp_path):
    """The guard demands ONE of the two name columns, never both: a release that
    dropped the redundant drug_name is degraded, not broken, and must still
    ingest. Pinned so the guard cannot be tightened into a false alarm."""
    only_li = _write_csv(tmp_path, """
        li_item_id,pbs_code,li_drug_name,program_code,benefit_type_code
        X_1,X,Rifaximin,GE,A
        """)
    assert next(pbs.parse_items(only_li)).drug_name == "Rifaximin"

    only_fallback = _write_csv(tmp_path, """
        li_item_id,pbs_code,drug_name,program_code,benefit_type_code
        X_1,X,Rifaximin,GE,A
        """)
    assert next(pbs.parse_items(only_fallback)).drug_name == "Rifaximin"
