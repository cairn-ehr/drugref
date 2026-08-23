"""Tests for rendering the DrugCentral `ddi` measurement.

The renderer had no tests, and it is where the licensing verdict is printed. Two
things it used to do are pinned against here:

* the rule-6 verdict was a hard-coded ``ref_id == "2"`` literal in this file while
  `BUNDLEABLE_REF_IDS` in the spike decided which rows were actually measured, so
  changing one left the other blessing a reference it had not counted;
* the conclusions were fixed prose interpolated around live figures, so a run in
  which nothing resolved still printed "**Bundle `ddi_ref_id = 2` only.**" in bold
  above an empty evidence table.
"""
from __future__ import annotations

import pytest

from tools.drugcentral_ddi_measure import (
    ClassCoverage,
    Measurement,
    NameProvenance,
)
from tools.drugcentral_ddi_report import (
    ReportContext,
    RegistryTotals,
    render_report,
)


def _measurement(**overrides):
    base = dict(
        rows=10, raw_names=4, names=4, names_resolved=3,
        routes={"display_name": 3, "not_a_substance": 1},
        unresolved_names=(("strong cyp3a4 inhibitors", "not_a_substance"),),
        unresolvable_rows=2, self_pair_rows=1, pair_rows=7, pairs=5, held=2)
    base.update(overrides)
    return Measurement(**base)


def _context(**overrides):
    base = dict(
        generated="2026-08-23",
        dump="downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz",
        dump_bytes=1_400_714_190,
        dump_sha256="0559" + "0" * 60,
        release="11012023",
        dump_lines=13_570_317,
        decompressed_bytes=4_980_000_000,
        table_counts={"ddi": 10, "reference": 3},
        references={
            "2": {"title": "NDF-RT", "authors": "VHA", "isbn10": "", "dp_year": ""},
            "3": {"title": "Lexicomp Online", "authors": "Wolters Kluwer",
                  "isbn10": "", "dp_year": ""},
        },
        ref_distribution={"2": 8, "3": 2},
        bundleable_ref_ids=frozenset({"2"}),
        risk_vocabulary=[{"id": "1", "name": "Significant"},
                         {"id": "2", "name": "Never used"}],
        risk_whole={"Significant": 10},
        risk_bundleable={"Significant": 8},
        registry_totals=RegistryTotals(
            moieties=19_438, classes=4_202, migration="048_x.sql",
            display_names=19_438, inchikeys=16_046, cas=19_010,
            duplicate_display_names=0, duplicate_inchikeys=0, duplicate_cas=0),
        candidate_rows=21_664,
        candidate_pairs=20_238,
        whole_name_only=_measurement(),
        whole_cascade=_measurement(names_resolved=4, held=3),
        bundleable_name_only=_measurement(rows=8, unresolvable_rows=1, pair_rows=6),
        bundleable_cascade=_measurement(
            rows=8, unresolvable_rows=1, pair_rows=6, names_resolved=4, held=3),
        whole_class_coverage=ClassCoverage(
            names=4, names_resolved=3, names_matching_a_class=1,
            by_source={"MeSH": 1}, names_matching_nothing=0,
            keyable_rows=9, moiety_by_moiety_rows=7),
        whole_class_coverage_name_only=ClassCoverage(
            names=4, names_resolved=2, names_matching_a_class=1,
            by_source={"MeSH": 1}, names_matching_nothing=1,
            keyable_rows=8, moiety_by_moiety_rows=6),
        name_provenance=NameProvenance(
            names=4, in_structures=3, in_synonyms_only=1, in_neither=0),
        qt_rows=[],
        pharma_class_rows=25_687,
        pharma_class_named=25_687,
        pharma_class_qt=0,
    )
    base.update(overrides)
    return ReportContext(**base)


# ---------------------------------------------------------------------------
# Rule 6 -- one home for the verdict
# ---------------------------------------------------------------------------

def test_the_bundling_verdict_follows_the_set_that_actually_filtered_the_rows():
    """Change the set and the table must change with it.

    While the verdict was a literal `ref_id == "2"` in this module, the report
    could bless a reference the measurement had excluded, or exclude one it had
    counted -- a licensing determination silently contradicting the code that
    produced the figures beside it.
    """
    report = render_report(_context(bundleable_ref_ids=frozenset({"3"})))

    lines = [ln for ln in report.splitlines() if ln.startswith("| `2`")]
    assert lines and "out" in lines[0]
    lines = [ln for ln in report.splitlines() if ln.startswith("| `3`")]
    assert lines and "bundle" in lines[0]


def test_the_bundling_sentence_names_the_set_rather_than_a_hard_coded_two():
    assert "Bundle `ddi_ref_id` 3 only" in render_report(
        _context(bundleable_ref_ids=frozenset({"3"})))


def test_the_reference_count_in_the_prose_is_counted_not_asserted():
    """"Every `ddi` row cites one of three references" was fixed text."""
    report = render_report(_context(
        ref_distribution={"1": 1, "2": 8, "3": 1},
        references={"1": {"title": "Stockley's", "authors": "", "isbn10": "",
                          "dp_year": ""}} | _context().references))

    assert "one of 3 references" in report


def test_a_reference_id_absent_from_the_reference_table_is_not_called_blank():
    """A missing citation and an empty title are different facts.

    `ref.get("title") or "(not present...)"` printed the same string for both, in
    the highest-stakes cell in the document.
    """
    report = render_report(_context(
        ref_distribution={"2": 8, "9": 2},
        references=_context().references | {
            "8": {"title": "", "authors": "", "isbn10": "", "dp_year": ""}}))

    assert "not cited in the `reference` table" in report


def test_a_bundleable_reference_that_the_dump_does_not_cite_is_refused(capsys):
    """Bundling rights asserted from a citation the dump does not contain.

    If ref 2 were absent from `reference`, the old renderer printed
    "(not present in the reference table)" AND stamped "clean -- bundle" beside it.
    """
    with pytest.raises(ValueError):
        render_report(_context(references={
            "3": {"title": "Lexicomp", "authors": "", "isbn10": "", "dp_year": ""}}))


# ---------------------------------------------------------------------------
# No confident report over absent evidence
# ---------------------------------------------------------------------------

def test_a_run_that_measured_nothing_refuses_to_render():
    """A table of zeros and a bold licensing verdict is the failure mode.

    No corruption is needed to reach it: a release that renumbered its references
    yields an empty bundleable subset, and the report rendered 2,851 characters of
    clean Markdown and exited 0.
    """
    with pytest.raises(ValueError):
        render_report(_context(bundleable_cascade=_measurement(
            rows=0, raw_names=0, names=0, names_resolved=0, routes={},
            unresolved_names=(), unresolvable_rows=0, self_pair_rows=0,
            pair_rows=0, pairs=0, held=0)))


def test_the_qt_conclusion_is_gated_on_the_figure_that_supports_it():
    """"defines them nowhere" is only true while the QT class count is zero."""
    assert "defines them nowhere" in render_report(_context(pharma_class_qt=0))
    assert "defines them nowhere" not in render_report(_context(pharma_class_qt=4))


def test_the_pharma_class_zero_is_reported_against_a_non_empty_denominator():
    """A zero from an absent column looks exactly like a zero from absence.

    The extract now refuses a stale projection, but the report states the
    denominator anyway so the reader never has to take the guard on trust.
    """
    assert "25,687 of 25,687 carry a name" in render_report(_context())


# ---------------------------------------------------------------------------
# Every measured figure is published
# ---------------------------------------------------------------------------

def test_the_row_accounting_is_printed_so_it_visibly_closes():
    """`self_pair_rows` was computed and rendered nowhere.

    A reader could see 8 rows and 5 distinct pairs and had no way to attribute the
    difference -- which is exactly what the ingest slice needs, since rows
    collapsing onto one pair carry different `ddi_risk` values.
    """
    report = render_report(_context())

    assert "rows resolving to a self-pair" in report
    assert "rows yielding a pair" in report


def test_the_risk_vocabulary_shows_a_label_no_row_uses():
    """The lookup table was loaded, threaded into the context, and never rendered.

    The section asserts the vocabulary is scoped per reference; a label at 0/0 is
    the evidence for that claim, and it was the one thing omitted.
    """
    report = render_report(_context())

    assert "Never used" in report


def test_a_non_bundleable_row_has_its_prose_withheld():
    """Rule 6 is a blocker, and all three QT rows are Lexicomp's.

    Reproducing a commercial compendium's sentences verbatim into a committed
    AGPL repo, on every run, should not be a side effect of the QT section having
    no reference filter. The endpoint strings are what issue 93 needed.
    """
    report = render_report(_context(qt_rows=[{
        "ddi_ref_id": "3", "ddi_risk": "Avoid combination",
        "drug_class1": "High Risk QT Prolonging Agents",
        "drug_class2": "cisapride",
        "description": "SOME LICENSED SENTENCE"}]))

    assert "High Risk QT Prolonging Agents" in report
    assert "SOME LICENSED SENTENCE" not in report
    assert "withheld" in report


def test_a_bundleable_row_keeps_its_prose():
    report = render_report(_context(qt_rows=[{
        "ddi_ref_id": "2", "ddi_risk": "Significant",
        "drug_class1": "a", "drug_class2": "b",
        "description": "PUBLIC DOMAIN SENTENCE"}]))

    assert "PUBLIC DOMAIN SENTENCE" in report


def test_the_registry_side_totals_are_published_so_the_join_can_be_audited():
    """The docstring promised duplicate reporting; nothing was returned or printed."""
    report = render_report(_context())

    assert "16,046" in report and "19,010" in report


def test_each_unresolved_name_is_printed_with_the_route_that_gave_up():
    """Four ways to fail, and they mean different things.

    A class name DrugCentral does not know is a correct miss; a `missing_keys_row`
    is a broken extract. A flat list of names could not tell a reader which.
    """
    report = render_report(_context())

    assert "- `strong cyp3a4 inhibitors` — `not_a_substance`" in report


def test_the_keyable_figure_is_shown_under_both_resolvers():
    """Issue #101's "7,000 keyable" was a NAME-MATCHING figure.

    Printing it beside a cascade number compares two different questions, so the
    report states both columns rather than letting a reader assume they are one
    series.
    """
    report = render_report(_context())

    assert "name matching (issue #101) | + cascade" in report
    assert "| 8 | 9 |" in report          # keyable: name-only 8, cascade 9
