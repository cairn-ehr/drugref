# tests/test_drugcentral_fixture.py
"""The committed fixture's licence and coverage properties.

The redaction is a LICENCE requirement, not tidiness: ddi_ref_id 1 and 3 cite a
copyrighted book and a commercial compendium, and their description text may not
sit in an AGPL repository. tests/fixtures/medrt_subset.xml carries the same kind
of test for the same kind of reason.
"""
import gzip
import pathlib

from drugref.ingest import drugcentral
from drugref.ingest.drugcentral_dump import decode_copy_field
from tests.fixtures.make_drugcentral_subset import encode_copy_field

FIXTURE = pathlib.Path("tests/fixtures/drugcentral_ddi_subset.sql.gz")

REDACTED = "[redacted: cites a reference CLAUDE.md rule 6 excludes]"


def _tables():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        return drugcentral.read_tables(handle)


def test_the_excluded_references_are_present_so_the_filter_is_exercised():
    """A fixture holding only ref 2 would let the rule-6 filter be deleted and
    still pass every test -- the shape of issues 74, 66 and 76."""
    assert set(_tables().reference) >= {"1", "2", "3"}


def test_no_excluded_description_text_is_committed():
    for row in _tables().ddi:
        if row["ddi_ref_id"] != "2":
            assert row["description"] == REDACTED, (
                f"row {row['id']} cites reference {row['ddi_ref_id']} and its "
                f"description must be redacted before it is committed")


def test_the_fixture_carries_a_pair_published_in_both_orders():
    """The view's collapse rule needs a case, and 33 real pairs have one."""
    rows = [r for r in _tables().ddi if r["ddi_ref_id"] == "2"]
    pairs = {(r["drug_class1"].lower(), r["drug_class2"].lower()) for r in rows}
    assert any((b, a) in pairs for a, b in pairs), (
        "no endpoint pair appears in both orders; the collapse is untested")


def test_the_fixture_carries_an_unresolvable_endpoint():
    """gap_unresolved_ddi_endpoint needs a case too."""
    rows = [r for r in _tables().ddi if r["ddi_ref_id"] == "2"]
    names = {r["drug_class1"].lower() for r in rows} | \
            {r["drug_class2"].lower() for r in rows}
    structures = {(r["name"] or "").lower() for r in _tables().structures}
    assert names - structures, "every endpoint is a structures.name; nothing is a gap"


def test_encode_copy_field_is_the_exact_inverse_of_decode_copy_field():
    """The escaper is the one piece of this task the brief does not hand over --
    `decode_copy_field` is its specification. Proven directly, on strings no
    selected row happens to contain, rather than trusting that real DrugCentral
    prose never exercises a backslash, a tab or a NULL.
    """
    cases = [
        None,
        "",
        "plain text with no escapes",
        "a\tb",                 # a literal tab
        "a\nb",                 # a literal newline
        "a\\b",                 # a literal single backslash
        "a\\tb",                # a literal BACKSLASH followed by 't' (not a tab)
        "\\N",                  # looks like the NULL sentinel but is real text
        "trailing backslash test: \\",
        "mixed\t\n\\control\rchars",
    ]
    for value in cases:
        encoded = encode_copy_field(value)
        assert decode_copy_field(encoded) == value, (
            f"round trip failed for {value!r}: encoded to {encoded!r}, "
            f"decoded back to {decode_copy_field(encoded)!r}")
    # And the NULL sentinel itself: only `None` may encode to the bare `\N`.
    assert encode_copy_field(None) == "\\N"
    assert decode_copy_field("\\N") is None
