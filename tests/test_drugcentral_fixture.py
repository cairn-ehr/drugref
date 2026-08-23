# tests/test_drugcentral_fixture.py
"""The committed fixture's licence and coverage properties.

The redaction is a LICENCE requirement, not tidiness: ddi_ref_id 1 and 3 cite a
copyrighted book and a commercial compendium, and their description text may not
sit in an AGPL repository. tests/fixtures/medrt_subset.xml carries the same kind
of test for the same kind of reason.
"""
import functools
import gzip
import pathlib

from drugref.ingest import drugcentral
from drugref.ingest.drugcentral_dump import decode_copy_field
from tests.fixtures.make_drugcentral_subset import encode_copy_field

FIXTURE = pathlib.Path("tests/fixtures/drugcentral_ddi_subset.sql.gz")

REDACTED = "[redacted: cites a reference CLAUDE.md rule 6 excludes]"


@functools.cache
def _tables():
    """Decode the fixture ONCE per session.

    Every test here calls this, and one of them calls it four times, so the gzip
    was being re-read and re-parsed on each. Cheap at 8 rows and an invitation to
    stop being cheap; `DumpTables` is frozen and holds tuples and a plain mapping,
    so a shared instance is exactly as safe as a fresh one.
    """
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        return drugcentral.read_tables(handle)


def test_the_excluded_references_are_present_so_the_filter_is_exercised():
    """A fixture holding only ref 2 would let the rule-6 filter be deleted and
    still pass every test -- the shape of issues 74, 66 and 76.

    Anchored to `ddi.ddi_ref_id`, the column `drugcentral.bundleable_rows`
    actually filters on -- NOT the `reference` metadata table. A fixture can
    carry all three bibliographic `reference` rows while its `ddi` table holds
    only ref-2 rows, which gives the rule-6 exclusion nothing to exclude and
    every other test in this file passes vacuously; asserting against
    `reference` alone cannot catch that. This is exactly the mutation task-10's
    review constructed (all three `reference` rows present, `ddi` narrowed to
    ref 2 only) and confirmed the old `set(_tables().reference) >= {...}`
    assertion missed.
    """
    ddi_ref_ids = {row["ddi_ref_id"] for row in _tables().ddi}
    assert ddi_ref_ids >= {"1", "2", "3"}


def test_no_excluded_description_text_is_committed():
    for row in _tables().ddi:
        if row["ddi_ref_id"] != "2":
            assert row["description"] == REDACTED, (
                f"row {row['id']} cites reference {row['ddi_ref_id']} and its "
                f"description must be redacted before it is committed")


def test_the_fixture_carries_a_pair_published_in_both_orders():
    """The view's collapse rule needs a case, and 33 real pairs have one.

    Also asserts the two orientations' `ddi_risk` DIFFER, not merely that both
    orderings exist. most-severe-wins has nothing to prove on a pair that
    agrees with itself -- 29 of the 33 real both-order pairs DO agree -- so a
    future regeneration that swapped in one of those would pass a "both
    orderings exist" check while silently losing all coverage of the
    tie-breaking rule.
    """
    rows = [r for r in _tables().ddi if r["ddi_ref_id"] == "2"]
    risk_by_pair = {(r["drug_class1"].lower(), r["drug_class2"].lower()): r["ddi_risk"]
                    for r in rows}
    pairs = set(risk_by_pair)
    both_orders = [(a, b) for a, b in pairs if (b, a) in pairs]
    assert both_orders, (
        "no endpoint pair appears in both orders; the collapse is untested")
    assert any(risk_by_pair[(a, b)] != risk_by_pair[(b, a)] for a, b in both_orders), (
        "every both-order pair agrees on ddi_risk; most-severe-wins is untested")


def test_the_fixture_carries_an_unresolvable_endpoint():
    """gap_unresolved_ddi_endpoint needs a case too.

    Subtracts BOTH `structures` and `synonyms` names. The resolution cascade in
    drugcentral_resolve tries structures' primary name, then synonyms, before
    giving up, so an endpoint absent from `structures.name` alone (e.g.
    'acetaminophen', this fixture's synonym-only case) still resolves fine and
    must not be mistaken for a gap; subtracting only `structures`, as an
    earlier version of this test did, is satisfied by that case even with the
    genuinely-unresolvable row removed entirely.
    """
    rows = [r for r in _tables().ddi if r["ddi_ref_id"] == "2"]
    names = {r["drug_class1"].lower() for r in rows} | \
            {r["drug_class2"].lower() for r in rows}
    structures = {(r["name"] or "").lower() for r in _tables().structures}
    synonyms = {(r["name"] or "").lower() for r in _tables().synonyms}
    assert names - structures - synonyms, (
        "every endpoint resolves via structures or synonyms; nothing is a gap")


def test_encode_copy_field_is_the_exact_inverse_of_decode_copy_field():
    """The escaper is the one piece of this task the brief does not hand over --
    `decode_copy_field` is its specification. Proven directly, on strings no
    selected row happens to contain, rather than trusting that real DrugCentral
    prose never exercises a backslash, a tab or a NULL. Covers all seven
    entries in `_ENCODE_ESCAPES`, including backspace/form-feed/vertical-tab,
    which no fixture row's text exercises on its own.
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
        "x\by",                 # backspace -- the other three named escapes
        "x\fy",                 # form feed  -- decode_copy_field understands
        "x\vy",                 # vertical tab -- but no selected row contains
    ]
    for value in cases:
        encoded = encode_copy_field(value)
        assert decode_copy_field(encoded) == value, (
            f"round trip failed for {value!r}: encoded to {encoded!r}, "
            f"decoded back to {decode_copy_field(encoded)!r}")
    # And the NULL sentinel itself: only `None` may encode to the bare `\N`.
    assert encode_copy_field(None) == "\\N"
    assert decode_copy_field("\\N") is None
