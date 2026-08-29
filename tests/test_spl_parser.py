# tests/test_spl_parser.py
"""The pure openFDA reader: what a label offers, and what it does not.

`ingest/spl.py` is a PARSER under the architecture invariant -- no database, no
resolution, no judgement. It answers one question per record: does this label
carry section 34073-7, and if so what is its identity, its subject UNIIs and its
wording?

Every fixture here is shaped like a real openFDA record. The traps pinned below
are the ones the 5c.3 measurement rounds actually hit:

* `drug_interactions` is a LIST of strings, and taking `[0]` drops the second
  interaction statement;
* the normalising `openfda` block is PRESENT and EMPTY on 100% of unkeyed
  records, so a presence check reports full coverage;
* reformatting is not a new statement, so the wording key normalises whitespace.
"""
import json

import zipfile

import pytest

from drugref.ingest import spl


def _record(**overrides) -> dict:
    """One openFDA record with the fields the parser reads, overridable."""
    record = {
        "set_id": "9f1b2c3d-0000-4000-8000-000000000001",
        "version": "3",
        "effective_time": "20260101",
        "openfda": {"unii": ["GEB06NHM23"], "product_type": ["HUMAN PRESCRIPTION DRUG"]},
        "drug_interactions": ["7 DRUG INTERACTIONS Warfarin increases the effect."],
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------------------
# Which records carry a section at all
# --------------------------------------------------------------------------

def test_a_record_with_no_interactions_field_carries_no_section():
    assert spl.extract_section(_record(drug_interactions=None)) is None


def test_a_record_whose_interactions_field_is_an_empty_list_carries_no_section():
    assert spl.extract_section(_record(drug_interactions=[])) is None


def test_a_whitespace_only_section_is_ABSENT_not_a_zero_entity_section():
    """Blank-but-present folds into absent, deliberately.

    A zero-entity row left in the yield denominator depresses every rate derived
    from it, which is how a corpus census lies without any figure being wrong.
    """
    assert spl.extract_section(_record(drug_interactions=["   ", "\n"])) is None


def test_every_part_of_the_interactions_list_is_read_not_just_the_first():
    """The trap that drops a whole interaction statement.

    openFDA splits one label's section into several strings. `[0]` is a plausible
    read and silently loses everything after it.
    """
    section = spl.extract_section(
        _record(drug_interactions=["Warfarin increases.", "Rifampin decreases."]))
    assert "Warfarin" in section.text
    assert "Rifampin" in section.text


# --------------------------------------------------------------------------
# Identity -- the columns db/051's primary key rests on
# --------------------------------------------------------------------------

def test_the_identity_fields_are_carried_verbatim():
    section = spl.extract_section(_record())
    assert section.set_id == "9f1b2c3d-0000-4000-8000-000000000001"
    assert section.version == "3"
    assert section.effective_time == "20260101"
    assert section.product_type == "HUMAN PRESCRIPTION DRUG"


def test_a_label_with_no_openfda_block_has_no_uniis_and_no_product_type():
    section = spl.extract_section(_record(openfda=None))
    assert section.uniis == ()
    assert section.product_type is None


def test_an_openfda_block_PRESENT_AND_EMPTY_yields_no_subject():
    """100% of unkeyed records carry this block and it is simply empty.

    A presence check on `openfda` reports full coverage of a population where
    nothing is keyed -- measured, and the reason the recovery round exists.
    """
    section = spl.extract_section(_record(openfda={}))
    assert section.uniis == ()


def test_uniis_are_deduplicated_and_ordered_so_two_reads_agree():
    section = spl.extract_section(
        _record(openfda={"unii": ["ZZZ", "AAA", "ZZZ"]}))
    assert section.uniis == ("AAA", "ZZZ")


def test_a_record_missing_set_id_falls_back_to_id():
    record = _record()
    del record["set_id"]
    record["id"] = "fallback-id"
    assert spl.extract_section(record).set_id == "fallback-id"


# --------------------------------------------------------------------------
# The wording key -- the de-duplication identity the whole slice counts in
# --------------------------------------------------------------------------

def test_reformatting_is_not_a_new_statement():
    """Two labels wrapping one wording differently are ONE wording.

    Without this the corpus overstates itself: 68,550 labels carry 27,406
    wordings, and the ratio between them is the de-duplication factor every rate
    in this slice is divided by.
    """
    one = spl.extract_section(_record(drug_interactions=["Warfarin\n  increases."]))
    two = spl.extract_section(_record(drug_interactions=["Warfarin increases."]))
    assert one.text_key == two.text_key


def test_two_different_wordings_do_not_share_a_key():
    one = spl.extract_section(_record(drug_interactions=["Warfarin increases."]))
    two = spl.extract_section(_record(drug_interactions=["Rifampin decreases."]))
    assert one.text_key != two.text_key


def test_the_wording_key_is_a_sha256_hex_digest():
    """db/051 puts a CHECK on this shape, so the producer has to honour it."""
    section = spl.extract_section(_record())
    assert len(section.text_key) == 64
    assert set(section.text_key) <= set("0123456789abcdef")


def test_normalised_text_is_what_the_offsets_index():
    """Offsets and quotes both index the NORMALISED text, never the raw one.

    They have to be the same string, or a stored `char_start` cuts the wrong
    characters out of the wording a reader looks it up in.
    """
    section = spl.extract_section(_record(drug_interactions=["  A   B  "]))
    assert section.normalised_text == "A B"
    assert section.char_length == 3


def test_char_length_is_the_denominator_the_quote_budget_is_spent_against():
    section = spl.extract_section(_record(drug_interactions=["abcdefghij"]))
    assert section.char_length == len(section.normalised_text) == 10


# --------------------------------------------------------------------------
# The floor check -- a reader that finds nothing must say so
# --------------------------------------------------------------------------

def test_a_corpus_carrying_no_section_is_REFUSED_not_ingested_as_empty():
    """db/050's lesson: an ingest that would publish nothing must not clear.

    Silently rebuilding an empty projection over a good one is the failure this
    guard exists for, and it has to be shown it can fire.
    """
    with pytest.raises(ValueError, match="no label carries section"):
        spl.check_something_was_read([], records=1_000)


def test_the_floor_check_passes_on_a_corpus_that_did_carry_sections():
    spl.check_something_was_read([spl.extract_section(_record())], records=10)


def test_a_partition_with_no_results_key_is_REFUSED_not_read_as_empty(tmp_path):
    """⇒ THE ONE QUIET BRANCH IN AN OTHERWISE LOUD READER.

    `(member,) = archive.namelist()` raises on a repacked zip and `json.load`
    raises on a truncated one, but `document.get("results", [])` turned a
    changed export shape -- or a half-written download -- into zero records and
    no complaint. `--openfda` is a glob, so a partial directory is accepted, and
    `check_something_was_read` only fires when EVERY partition yields nothing.
    The missing records would land in the denominator and lower every rate.
    """
    path = tmp_path / "drug-label-0001-of-0001.json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("drug-label-0001-of-0001.json",
                         json.dumps({"meta": {"results": {"total": 0}}}))
    with pytest.raises(ValueError, match="carries no 'results' key"):
        list(spl.iter_partition_records(path))


def test_a_partition_with_an_EMPTY_results_list_is_read_as_empty(tmp_path):
    """The control, and the distinction that matters: openFDA saying "no records"
    is a fact about the export; a missing key is a fact about the download."""
    path = tmp_path / "drug-label-0001-of-0001.json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("drug-label-0001-of-0001.json",
                         json.dumps({"results": []}))
    assert list(spl.iter_partition_records(path)) == []
