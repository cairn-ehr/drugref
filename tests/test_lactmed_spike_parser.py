"""Pure LactMed parser tests for the pregnancy/lactation source spike."""
import io
import tarfile

from drugref.ingest import lactmed


RECORD = b"""<?xml version="1.0" encoding="UTF-8"?>
<book-part-wrapper id="LMTEST">
  <book-meta>
    <publisher><publisher-name>National Institute of Child Health and Human Development</publisher-name></publisher>
    <notes notes-type="disclaimer"><p><bold>Disclaimer:</bold> Clinical judgment remains required.</p></notes>
    <notes notes-type="rights"><p><bold>Attribution:</bold> LactMed is an HHS trademark.</p></notes>
  </book-meta>
  <book-part>
    <book-part-meta>
      <book-part-id book-part-id-type="pmcid">LMTEST</book-part-id>
      <title-group><title>Example Drug</title></title-group>
      <pub-history><date date-type="revised"><day>7</day><month>8</month><year>2026</year></date></pub-history>
      <kwd-group>
        <kwd>Example</kwd><kwd>123-45-6</kwd><kwd>UNII-ABC1234567</kwd>
      </kwd-group>
    </book-part-meta>
    <body>
      <p>CASRN: 123-45-6</p>
      <sec><title>Drug Levels and Effects</title>
        <sec><title>Summary of Use during Lactation</title>
          <p>Conditional summary.<xref ref-type="bibr" rid="LMTEST.REF.1">1</xref></p>
        </sec>
        <sec><title>Drug Levels</title>
          <p><italic>Maternal Levels.</italic> Milk measurement.</p>
          <p><italic>Infant Levels.</italic> Infant measurement.</p>
        </sec>
        <sec><title>Effects in Breastfed Infants</title><p>No effect report.</p></sec>
        <sec><title>Effects on Lactation and Breastmilk</title><p>Milk supply report.</p></sec>
        <sec><title>Alternate Drugs to Consider</title><p>Alternative A.</p></sec>
      </sec>
      <sec><title>Substance Identification</title>
        <sec><title>CAS Registry Number</title><p>123-45-6</p></sec>
      </sec>
    </body>
  </book-part>
</book-part-wrapper>
"""


def test_parses_identity_revision_rights_and_disclaimer():
    record = lactmed.parse_record(io.BytesIO(RECORD))

    assert record.record_id == "LMTEST"
    assert record.title == "Example Drug"
    assert record.revised == "2026-08-07"
    assert record.publisher == "National Institute of Child Health and Human Development"
    assert record.cas_numbers == ("123-45-6",)
    assert record.uniis == ("ABC1234567",)
    assert "HHS trademark" in record.rights
    assert "Clinical judgment" in record.disclaimer


def test_splits_lactmed_sections_without_interpreting_a_recommendation():
    record = lactmed.parse_record(io.BytesIO(RECORD))
    by_kind = {section.evidence_kind: section for section in record.sections}

    assert set(by_kind) == {
        "lactmed_summary",
        "maternal_level",
        "infant_level",
        "infant_effect",
        "lactation_effect",
        "alternative_medicine",
    }
    assert by_kind["lactmed_summary"].reference_ids == ("LMTEST.REF.1",)
    assert by_kind["maternal_level"].text == "Maternal Levels. Milk measurement."
    assert not hasattr(record, "recommendation")


def test_identifies_evidence_records_by_source_native_summary_section():
    record = lactmed.parse_record(io.BytesIO(RECORD))
    auxiliary = lactmed.parse_record(io.BytesIO(
        RECORD.replace(
            b"Summary of Use during Lactation",
            b"Collection Documentation",
        )
    ))

    assert lactmed.is_evidence_record(record)
    assert not lactmed.is_evidence_record(auxiliary)


def test_archive_iteration_reads_only_nxml_and_reports_member_name(tmp_path):
    archive = tmp_path / "lactmed.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("lactmed/LMTEST.nxml")
        info.size = len(RECORD)
        tf.addfile(info, io.BytesIO(RECORD))
        ignored = tarfile.TarInfo("lactmed/LMTEST.pdf")
        ignored.size = 3
        tf.addfile(ignored, io.BytesIO(b"pdf"))

    parsed = list(lactmed.iter_archive(archive))

    assert len(parsed) == 1
    assert parsed[0].member_name == "lactmed/LMTEST.nxml"
    assert parsed[0].record.record_id == "LMTEST"


def test_malformed_dates_are_retained_as_missing_not_invented():
    malformed = RECORD.replace(b"<month>8</month>", b"<month></month>")
    record = lactmed.parse_record(io.BytesIO(malformed))
    assert record.revised is None
