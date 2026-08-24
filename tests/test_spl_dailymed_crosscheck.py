"""Tests for the DailyMed cross-check.

This module's whole job is to catch openFDA's section field being wrong. If the
cross-check itself is wrong it produces false reassurance, which is worse than
no check at all -- so its two extraction paths are pinned here against a
miniature SPL document shaped like the real thing.
"""
from __future__ import annotations

from tools.spl_dailymed_crosscheck import (
    extract_interactions_text,
    scan_label,
    token_containment,
    token_overlap,
)

SPL = b"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3">
  <id root="doc-1"/>
  <code code="34391-3" displayName="HUMAN PRESCRIPTION DRUG LABEL"/>
  <setId root="set-abc"/>
  <component><structuredBody>
    <component><section>
      <code code="34067-9" displayName="DOSAGE"/>
      <text>Take one tablet.</text>
    </section></component>
    <component><section>
      <code code="34073-7" displayName="DRUG INTERACTIONS SECTION"/>
      <title>7 DRUG INTERACTIONS</title>
      <text>Avoid concomitant warfarin.</text>
      <component><section>
        <code code="34073-7"/>
        <title>7.1 Strong CYP1A2 Inhibitors</title>
        <text>Use with fluvoxamine is contraindicated.</text>
      </section></component>
    </section></component>
  </structuredBody></component>
</document>"""


def test_scan_reads_the_set_id_and_the_document_type_code():
    scan = scan_label(SPL, "doc-1")
    assert scan.set_id == "set-abc"
    assert scan.doc_code == "34391-3"
    assert scan.has_interactions is True


def test_scan_reports_a_label_without_the_section():
    without = SPL.replace(b'code="34073-7"', b'code="43685-7"')
    assert scan_label(without, "doc-1").has_interactions is False


def test_extract_pulls_the_section_INCLUDING_its_subsections():
    # The tizanidine label puts its entire strong-versus-moderate distinction in
    # subsections 7.1/7.2. Extracting only the parent's own <text> would drop
    # exactly the content issue #102 is about.
    text = extract_interactions_text(SPL)
    assert text is not None
    assert "Avoid concomitant warfarin." in text
    assert "fluvoxamine is contraindicated" in text
    assert "Strong CYP1A2 Inhibitors" in text


def test_extract_does_not_leak_other_sections():
    text = extract_interactions_text(SPL)
    assert text is not None
    assert "Take one tablet" not in text


def test_extract_returns_None_when_the_section_is_absent():
    without = SPL.replace(b'code="34073-7"', b'code="43685-7"')
    assert extract_interactions_text(without) is None


def test_extract_survives_malformed_xml_rather_than_raising():
    # A release of 50,000 files will contain something unparseable, and one bad
    # document must not abort a whole-corpus pass.
    assert extract_interactions_text(b"<document><unclosed>") is None


def test_token_overlap_is_1_for_identical_text_and_ignores_case_and_order():
    assert token_overlap("Avoid warfarin", "warfarin avoid") == 1.0


def test_containment_is_1_when_openfda_carries_the_whole_section():
    # openFDA's field starts '7 DRUG INTERACTIONS ...', so a faithful
    # reproduction is never byte-identical. Containment is the fidelity metric
    # BECAUSE it is asymmetric: extra text on openFDA's side is a formatting
    # difference, missing text is the defect.
    source = "Avoid concomitant warfarin."
    openfda = "7 DRUG INTERACTIONS Avoid concomitant warfarin."
    assert token_containment(source, openfda) == 1.0


def test_jaccard_would_have_scored_that_same_perfect_case_at_only_half():
    # Pinned so nobody "simplifies" the fidelity check back to a symmetric
    # measure: on a short section Jaccard reports a perfect reproduction as 0.5.
    source = "Avoid concomitant warfarin."
    openfda = "7 DRUG INTERACTIONS Avoid concomitant warfarin."
    assert token_overlap(source, openfda) == 0.5


def test_containment_falls_when_openfda_actually_drops_content():
    source = "Avoid warfarin and fluvoxamine and ciprofloxacin"
    openfda = "7 DRUG INTERACTIONS Avoid warfarin"
    assert token_containment(source, openfda) < 0.6


def test_containment_with_an_empty_source_is_zero_rather_than_a_crash():
    assert token_containment("", "anything") == 0.0


def test_token_overlap_of_unrelated_text_is_low():
    assert token_overlap("Avoid warfarin", "Store below 25 degrees") == 0.0


def test_token_overlap_with_an_empty_side_is_zero_rather_than_a_crash():
    assert token_overlap("", "anything") == 0.0
