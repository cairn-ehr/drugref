"""Tests for openFDA label extraction and the census that accounts for it.

The census refuses to exist unless its parts add up. That is deliberate: the
5c.3 source evaluation's second sampling attempt published a tally that
accounted for only 40 of its 50 labels, and nobody noticed until the numbers
were re-derived a round later. An accounting dataclass makes that failure
impossible to publish rather than merely unlikely.
"""
from __future__ import annotations

import pytest

from tools.spl_label_extract import (
    Census,
    extract_section,
    normalise_text,
    section_key,
)


def _record(**over) -> dict:
    """A minimal openFDA label record, shaped like the real export."""
    record = {
        "set_id": "abc-123",
        "version": "14",
        "effective_time": "20250311",
        "drug_interactions": ["7 DRUG INTERACTIONS Avoid warfarin."],
        "openfda": {
            "product_type": ["HUMAN PRESCRIPTION DRUG"],
            "unii": ["B53E3NMY5C"],
        },
    }
    record.update(over)
    return record


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def test_extracts_the_section_with_its_identity_and_provenance():
    section = extract_section(_record())
    assert section is not None
    assert section.set_id == "abc-123"
    assert section.version == "14"
    assert section.effective_time == "20250311"
    assert section.product_type == "HUMAN PRESCRIPTION DRUG"
    assert section.uniis == ("B53E3NMY5C",)
    assert "warfarin" in section.text


def test_a_record_without_the_section_extracts_to_None():
    assert extract_section(_record(drug_interactions=None)) is None
    record = _record()
    del record["drug_interactions"]
    assert extract_section(record) is None


def test_an_empty_or_blank_section_counts_as_absent():
    # openFDA does emit empty strings. Treating one as a present-but-empty
    # section would put a zero-entity row into the yield denominator and quietly
    # depress every rate derived from it.
    assert extract_section(_record(drug_interactions=[""])) is None
    assert extract_section(_record(drug_interactions=["   \n  "])) is None


def test_a_multi_element_section_is_joined_rather_than_truncated():
    # openFDA stores the section as a LIST. Taking [0] would silently drop text,
    # and the dropped part is exactly where a second interaction statement sits.
    section = extract_section(_record(drug_interactions=["first part", "second part"]))
    assert section is not None
    assert "first part" in section.text
    assert "second part" in section.text


def test_a_missing_openfda_block_yields_no_product_type_and_no_uniis():
    # 40,441 of the 68,595 section-carrying labels have no openfda block at all.
    # That is a measured population, not an error, so extraction must survive it.
    section = extract_section(_record(openfda={}))
    assert section is not None
    assert section.product_type is None
    assert section.uniis == ()


def test_uniis_are_deduplicated_and_ordered_so_the_key_is_stable():
    section = extract_section(_record(openfda={"unii": ["B", "A", "B"]}))
    assert section is not None
    assert section.uniis == ("A", "B")


# --------------------------------------------------------------------------
# text identity
# --------------------------------------------------------------------------

def test_normalise_text_collapses_whitespace_so_reformatting_is_not_a_new_text():
    assert normalise_text("a  b\n\tc ") == "a b c"


def test_two_labels_whose_section_differs_only_in_whitespace_share_one_key():
    # The corpus is dominated by generic labels repeating one manufacturer's
    # wording. If whitespace made them distinct, the de-duplication factor --
    # the figure every downstream rate depends on -- would be overstated.
    assert section_key("Avoid  warfarin.") == section_key("Avoid warfarin.")


def test_texts_that_actually_differ_get_different_keys():
    assert section_key("Avoid warfarin.") != section_key("Avoid warfarin!")


# --------------------------------------------------------------------------
# census accounting
# --------------------------------------------------------------------------

def test_census_accepts_a_tally_that_balances():
    census = Census(
        records=10,
        with_section=4,
        by_product_type={"HUMAN PRESCRIPTION DRUG": 3, None: 1},
        with_unii=3,
        distinct_text_keys=2,
    )
    assert census.without_section == 6


def test_census_REFUSES_a_tally_whose_product_types_do_not_sum_to_its_sections():
    # This is the exact shape of the evaluation's superseded 50-label tally.
    with pytest.raises(ValueError, match="product-type tally"):
        Census(
            records=10,
            with_section=4,
            by_product_type={"HUMAN PRESCRIPTION DRUG": 3},
            with_unii=3,
            distinct_text_keys=2,
        )


def test_census_REFUSES_more_sections_than_records():
    with pytest.raises(ValueError, match="records"):
        Census(
            records=2,
            with_section=4,
            by_product_type={None: 4},
            with_unii=0,
            distinct_text_keys=1,
        )


def test_census_REFUSES_more_distinct_texts_than_sections():
    with pytest.raises(ValueError, match="distinct"):
        Census(
            records=10,
            with_section=4,
            by_product_type={None: 4},
            with_unii=0,
            distinct_text_keys=5,
        )


def test_census_REFUSES_more_unii_carrying_labels_than_sections():
    with pytest.raises(ValueError, match="unii"):
        Census(
            records=10,
            with_section=4,
            by_product_type={None: 4},
            with_unii=5,
            distinct_text_keys=1,
        )
