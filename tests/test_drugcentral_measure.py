"""Tests for the arithmetic that produces every published DrugCentral figure.

This module had no tests at all, which is the wrong half of the PR to leave
unpinned: the parser reads bytes, but `measure` is what turns them into 914/924,
7,501 pairs and 6,866 new ones. Its inputs are pure and its output is a value
object, so nothing here needs a database or the 1.4 GB dump.

Three units are counted and they are NOT interchangeable -- PROJECT-NOTES records
that the original evaluation quoted them as if they were:

* **rows** -- one `ddi` record;
* **pair rows** -- rows whose two endpoints resolved to two DIFFERENT moieties;
* **distinct pairs** -- those pairs, orientation-normalised and deduplicated.

`Measurement` refuses to exist unless ``rows`` accounts for itself exactly, so a
row can no longer vanish between the three.
"""
from __future__ import annotations

import pytest

from drugref.ingest.drugcentral_resolve import (
    ROUTE_CAS,
    ROUTE_DISPLAY_NAME,
    ROUTE_NOT_A_SUBSTANCE,
    Resolution,
)
from tools.drugcentral_ddi_measure import (
    Measurement,
    class_coverage,
    measure,
    mentions_qt,
    name_provenance,
)


def _row(left, right, description="", ddi_ref_id="2", ddi_risk="Significant"):
    return {
        "drug_class1": left,
        "drug_class2": right,
        "description": description,
        "ddi_ref_id": ddi_ref_id,
        "ddi_risk": ddi_risk,
    }


# A toy resolver: three names resolve, everything else is a class name.
RESOLVED = {
    "warfarin": Resolution("uuid-w", ROUTE_DISPLAY_NAME),
    "aspirin": Resolution("uuid-a", ROUTE_DISPLAY_NAME),
    "acetylsalicylic acid": Resolution("uuid-a", ROUTE_CAS),
}


def resolve(name: str) -> Resolution:
    return RESOLVED.get(name.strip().lower(), Resolution(None, ROUTE_NOT_A_SUBSTANCE))


# ---------------------------------------------------------------------------
# Measurement -- the row accounting closes, or the object does not exist
# ---------------------------------------------------------------------------

def test_a_measurement_refuses_to_lose_a_row():
    """Every row is unresolvable, a self-pair, or a pair row. There is no fourth bucket.

    The published results said 7,571 bundleable rows and 37 unresolvable, then
    7,501 distinct pairs, and gave the reader no way to attribute the difference --
    `self_pair_rows` was computed and never rendered. Making the arithmetic a
    constructor invariant means a future refactor cannot quietly reopen that gap.
    """
    with pytest.raises(ValueError):
        Measurement(
            rows=10, raw_names=2, names=2, names_resolved=2, routes={},
            unresolved_names=(), unresolvable_rows=1, self_pair_rows=1,
            pair_rows=1, pairs=1, held=0)


def test_new_pairs_are_derived_rather_than_stored():
    """`pairs`, `held` and `new` cannot disagree if only two of them are stored."""
    m = measure([_row("warfarin", "aspirin")], resolve, held=set())

    assert m.pairs == 1 and m.held == 0 and m.new == 1


# ---------------------------------------------------------------------------
# measure -- the three units, kept apart
# ---------------------------------------------------------------------------

def test_measure_counts_rows_pairs_and_distinct_pairs_separately():
    """Two rows naming one pair in both orientations are two rows and ONE pair."""
    rows = [
        _row("warfarin", "aspirin"),
        _row("aspirin", "warfarin"),                 # same pair, reversed
        _row("warfarin", "Strong CYP3A4 Inhibitors"),  # unresolvable endpoint
        _row("aspirin", "acetylsalicylic acid"),     # both endpoints -> uuid-a
    ]

    m = measure(rows, resolve, held=set())

    assert m.rows == 4
    assert m.unresolvable_rows == 1
    assert m.self_pair_rows == 1
    assert m.pair_rows == 2
    assert m.pairs == 1


def test_measure_reports_the_overlap_with_what_drugref_already_holds():
    held = {("uuid-a", "uuid-w")}
    rows = [_row("warfarin", "aspirin")]

    m = measure(rows, resolve, held=held)

    assert (m.pairs, m.held, m.new) == (1, 1, 0)


def test_measure_counts_a_route_once_per_distinct_name_not_once_per_spelling():
    """`Warfarin` and `warfarin ` are one name, and the denominator must say so.

    Names were built from the raw `varchar(500)` text while resolution folded
    case and whitespace, so the headline denominators counted spellings and the
    route table double-counted every variant.
    """
    rows = [_row("Warfarin", "aspirin"), _row("warfarin ", "aspirin")]

    m = measure(rows, resolve, held=set())

    assert m.raw_names == 3          # 'Warfarin', 'warfarin ', 'aspirin'
    assert m.names == 2              # folded: 'warfarin', 'aspirin'
    assert m.routes[ROUTE_DISPLAY_NAME] == 2
    assert m.names_resolved == 2


def test_measure_lists_the_names_it_could_not_resolve():
    rows = [_row("warfarin", "Strong CYP3A4 Inhibitors")]

    m = measure(rows, resolve, held=set())

    assert m.unresolved_names == (("strong cyp3a4 inhibitors", ROUTE_NOT_A_SUBSTANCE),)
    assert m.routes[ROUTE_NOT_A_SUBSTANCE] == 1


def test_measure_counts_a_row_once_even_when_BOTH_endpoints_are_unresolvable():
    """`unresolvable_rows` is a row count, not an endpoint count."""
    rows = [_row("Class A", "Class B")]

    m = measure(rows, resolve, held=set())

    assert m.unresolvable_rows == 1 and m.rows == 1


def test_measure_of_no_rows_is_empty_rather_than_an_error():
    """Zero is a legitimate answer here; refusing it is the CALLER's job.

    The spike asserts non-empty inputs before rendering, because a report full of
    confident zeros is the failure mode. `measure` itself stays total.
    """
    m = measure([], resolve, held=set())

    assert (m.rows, m.names, m.pairs) == (0, 0, 0)


# ---------------------------------------------------------------------------
# mentions_qt -- issue 93's row selection
# ---------------------------------------------------------------------------

def test_mentions_qt_matches_qt_in_an_endpoint_or_in_the_prose():
    """The class-named QT populations sit in the endpoints; one row says it in prose."""
    assert mentions_qt(_row("High Risk QT Prolonging Agents", "x"))
    assert mentions_qt(_row("cisapride", "x", description="QT prolongation reported"))
    assert mentions_qt(_row("a", "b", description="risk of torsades de pointes"))


def test_mentions_qt_does_not_match_qt_inside_a_longer_word():
    """`Qtern` is a real marketed dapagliflozin/saxagliptin product.

    A substring test put it in a verbatim listing that a licensing and safety
    narrative is written from, where three rows is a small enough number that one
    false positive changes the finding.
    """
    assert not mentions_qt(_row("Qtern", "warfarin"))
    assert not mentions_qt(_row("a", "b", description="see doc QTX-1 for details"))


def test_mentions_qt_ignores_case_and_the_qtc_spelling():
    assert mentions_qt(_row("a", "b", description="may prolong the qtc interval"))
    assert mentions_qt(_row("a", "b", description="QTc-Prolonging Agents"))


# ---------------------------------------------------------------------------
# class_coverage -- the figures issue #101 got wrong, made re-derivable
# ---------------------------------------------------------------------------

CLASS_SOURCES = {
    "monoamine oxidase inhibitors": "MeSH",
    "strong cyp3a4 inhibitors": "MED-RT",
}


def test_class_coverage_splits_the_residue_by_the_authority_that_defines_it():
    """The correction the re-measurement made by hand, now computed.

    Issue #101 said "8 match a MED-RT class name". It was 4, and they were MeSH --
    wrong in its number AND its authority. The instrument could not re-derive
    either figure, so PROJECT-NOTES filed hand-measured numbers under
    "RE-DERIVABLE". This closes that.
    """
    rows = [
        _row("warfarin", "Monoamine Oxidase Inhibitors"),
        _row("aspirin", "Strong CYP3A4 Inhibitors"),
        _row("warfarin", "something nobody defines"),
    ]

    coverage = class_coverage(rows, resolve, CLASS_SOURCES)

    assert coverage.names == 5
    assert coverage.names_matching_a_class == 2
    assert coverage.by_source == {"MeSH": 1, "MED-RT": 1}
    assert coverage.names_matching_nothing == 1


def test_keyable_rows_and_moiety_by_moiety_rows_are_different_denominators():
    """`7,621 - 7,000 = 621` does not equal the unresolvable count, and this is why.

    *Keyable* counts moiety-OR-class at both ends; *moiety x moiety* is the subset
    with two moiety endpoints. The difference is the rows with exactly one class
    endpoint, and PROJECT-NOTES records the two being quoted interchangeably.
    """
    rows = [
        _row("warfarin", "aspirin"),                      # moiety x moiety
        _row("warfarin", "Strong CYP3A4 Inhibitors"),     # moiety x class
        _row("warfarin", "something nobody defines"),     # neither
    ]

    coverage = class_coverage(rows, resolve, CLASS_SOURCES)

    assert coverage.moiety_by_moiety_rows == 1
    assert coverage.keyable_rows == 2


def test_a_name_that_is_both_a_moiety_and_a_class_counts_as_a_moiety():
    """The cascade runs first, so a resolved endpoint is never also class residue."""
    rows = [_row("warfarin", "aspirin")]

    coverage = class_coverage(rows, resolve, {"warfarin": "MeSH"})

    assert coverage.names_matching_a_class == 0


# ---------------------------------------------------------------------------
# name_provenance -- which of DrugCentral's own tables knows the endpoint
# ---------------------------------------------------------------------------

def test_name_provenance_separates_a_primary_name_from_a_synonym_only_one():
    """"905 a structures.name, 17 more a synonyms.name, leaving 2" -- computed now."""
    provenance = name_provenance(
        ["warfarin", "acetylsalicylic acid", "Strong CYP3A4 Inhibitors"],
        structures=[{"id": "1", "name": "warfarin", "inchikey": "K",
                     "cas_reg_no": "C"}],
        synonyms=[{"id": "1", "name": "acetylsalicylic acid"}],
    )

    assert provenance.in_structures == 1
    assert provenance.in_synonyms_only == 1
    assert provenance.in_neither == 1
