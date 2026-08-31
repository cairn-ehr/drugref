# tests/test_spl_reader_skips.py
"""Issue #162: the three reader branches that declined a document uncounted.

Each had the same shape -- a document skipped for a reason nobody counted,
reappearing three stages later as `absent_from_dailymed`, a fact about the
READER sold as a fact about the RELEASE on the route whose population the design
spec turns into a commitment.

⇒ EACH COUNTER'S DROP-OR-REPORT VERDICT IS A MEASUREMENT, NOT A PREFERENCE.
`tools/spl_skip_census.py` read all 54,813 documents of the Human Rx release of
2026-08-21; the record is
`docs/superpowers/specs/2026-08-31-drugref-spl-reader-skip-census.md`.
Cases 1 and 2 are ZERO, so refusing over them cannot refuse a
legitimate release. **Case 3 is NOT zero** -- the release carries `COLR`, ten
times -- so folding case 3 into `total_dropped` as issue #162 proposed would
have aborted the ingest on the very release it was measured against. The guard
is therefore keyed on the condition that HARMS (an unknown classCode carrying a
UNII, which is the only way one could have contributed a subject) rather than on
the cause imagined, and all ten `COLR` ingredients carry no `<code>` element at
all.
"""
import pytest

from drugref.ingest import spl_checks, spl_dailymed as dm, spl_release as rel

UNII_SYSTEM = dm.UNII_CODE_SYSTEM


def _document(body: str = "", *, set_id: str = "SET-1", version: str | None = "4",
              before_set_id: str = "") -> bytes:
    version_element = (
        f'<versionNumber value="{version}"/>' if version is not None else "")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<document xmlns="urn:hl7-org:v3">'
        f'{before_set_id}<setId root="{set_id}"/>{version_element}{body}'
        "</document>"
    ).encode()


def _related_document(set_id: str) -> str:
    return (
        f'<relatedDocument typeCode="RPLC"><relatedDocument>'
        f'<setId root="{set_id}"/></relatedDocument></relatedDocument>')


def _ingredient(class_code: str, unii: str | None = "UNII-A") -> str:
    """One `<ingredient>`. `unii=None` is COLR's real shape: no `<code>` at all."""
    code = (f'<code code="{unii}" codeSystem="{UNII_SYSTEM}"/>' if unii else "")
    return (
        f'<ingredient classCode="{class_code}">'
        f"<ingredientSubstance>{code}</ingredientSubstance></ingredient>")


# --------------------------------------------------------------------------
# Case 1 -- a pre-filter that may have named the label being REPLACED
# --------------------------------------------------------------------------

def test_a_prefilter_with_no_relatedDocument_ahead_of_it_is_TRUSTWORTHY():
    assert dm.prefilter_is_trustworthy(_document()) is True


def test_a_relatedDocument_AHEAD_of_the_setId_makes_the_prefilter_untrustworthy():
    """`set_id_in_bytes` takes the FIRST `setId` in the bytes. When SPL's
    `<relatedDocument>` precedes the document's own, that first `setId` names the
    label being REPLACED -- so "not a target" may be a misreading, and the
    document is lost without any counter moving."""
    xml = _document(before_set_id=_related_document("SET-OLD"))
    assert dm.set_id_in_bytes(xml) == "SET-OLD"
    assert dm.prefilter_is_trustworthy(xml) is False


def test_a_relatedDocument_AFTER_the_setId_is_the_ordinary_case():
    """The normal SPL ordering, and the one the release actually uses: measured
    on all 54,813 documents, a `<relatedDocument>` never precedes the `<setId>`."""
    assert dm.prefilter_is_trustworthy(
        _document(body=_related_document("SET-OLD"))) is True


def test_an_untrustworthy_prefilter_on_a_NON_target_is_counted_as_a_drop(tmp_path):
    """The hole itself: the document is not a target BY A NAME THAT MAY NOT BE
    ITS OWN, so skipping it silently files a label drugref wanted as absent."""
    xml = _document(set_id="SET-MINE", before_set_id=_related_document("SET-OLD"))
    part = _part(tmp_path, {"m.zip": {"a.xml": xml}})

    scan = rel.scan_release([part], {"SET-WANTED"})

    assert scan.dropped_untrustworthy_prefilter == 1
    assert scan.total_dropped == 1


def test_a_TRUSTWORTHY_prefilter_on_a_non_target_is_skipped_silently_as_before(
        tmp_path):
    """The control. Most of a 54,813-document release is not a target, and
    counting those as drops would refuse every run."""
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(set_id="SET-OTHER")}})

    scan = rel.scan_release([part], {"SET-WANTED"})

    assert scan.dropped_untrustworthy_prefilter == 0
    assert scan.total_dropped == 0


# --------------------------------------------------------------------------
# Case 2 -- a junk <versionNumber>, which destroys the de-duplication rule
# --------------------------------------------------------------------------

def test_a_junk_version_is_REPORTED_by_the_reader_not_only_swallowed():
    """`dedupe_by_set_id` falls back to `(None or -1)`, i.e. to zip-member order
    -- the very thing the module argues at length is not a rule."""
    recovered = dm.extract_subject_uniis(_document(version="four"))
    assert recovered.version is None
    assert recovered.version_was_unreadable is True


def test_an_ABSENT_version_is_not_reported_as_unreadable():
    """`version is None` means BOTH "no element" and "broken element", and only
    the second is a label whose tie-break was destroyed."""
    recovered = dm.extract_subject_uniis(_document(version=None))
    assert recovered.version is None
    assert recovered.version_was_unreadable is False


def test_a_junk_version_is_a_DROP_because_it_can_attach_the_WRONG_subject(
        tmp_path):
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(version="four")}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.dropped_junk_version == 1
    assert scan.total_dropped == 1


# --------------------------------------------------------------------------
# Case 3 -- an HL7 classCode outside the shipped vocabulary
# --------------------------------------------------------------------------

def test_COLR_is_IN_the_documented_vocabulary_because_the_release_carries_it():
    """⇒ THE MEASUREMENT THAT CHANGED THE FIX. Ten `COLR` ingredients ship in the
    2026-08-21 release, named WHITE, RED, BLUE and YELLOW. Issue #162 proposed
    folding case 3 into `total_dropped`; done literally, that would have refused
    the release it was measured on."""
    recovered = dm.extract_subject_uniis(_document(_ingredient("COLR", unii=None)))
    assert recovered.unknown_class_codes == ()


def test_a_genuinely_unknown_classCode_is_reported_by_the_reader():
    recovered = dm.extract_subject_uniis(_document(_ingredient("ZZZZ", unii=None)))
    assert recovered.unknown_class_codes == ("ZZZZ",)
    assert recovered.unknown_class_code_uniis == ()


def test_an_unknown_classCode_CARRYING_a_unii_reports_that_unii():
    """The condition that HARMS: only an ingredient carrying a UNII could have
    contributed a subject, so only that one can have cost the label anything."""
    recovered = dm.extract_subject_uniis(_document(_ingredient("ZZZZ", "UNII-Z")))
    assert recovered.unknown_class_code_uniis == ("UNII-Z",)


def test_an_unknown_classCode_WITHOUT_a_unii_is_REPORTED_not_dropped(tmp_path):
    """COLR's real shape, and the reason the run is not refused over it."""
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(
        _ingredient("ZZZZ", unii=None))}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.skipped_unknown_class_code == 1
    assert scan.dropped_unknown_class_code_unii == 0
    assert scan.total_dropped == 0


def test_an_unknown_classCode_WITH_a_unii_IS_a_drop(tmp_path):
    """A future ACTIVE code would look exactly like this, and excluding it
    silently degrades recovery into `unresolved`/`absent_from_dailymed` -- with
    only a 2.3% margin over the pair floor to absorb it."""
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(
        _ingredient("ZZZZ", "UNII-Z"))}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.dropped_unknown_class_code_unii == 1
    assert scan.total_dropped == 1


def test_a_KNOWN_inactive_code_is_neither_dropped_nor_reported(tmp_path):
    """IACT, INGR and CNTM are ruled on already, and COLR now is too."""
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(
        _ingredient("IACT") + _ingredient("INGR") + _ingredient("CNTM")
        + _ingredient("COLR", unii=None))}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.skipped_unknown_class_code == 0
    assert scan.total_dropped == 0


# --------------------------------------------------------------------------
# The guard: every new drop counter WATCHED REFUSING, and the report one not
# --------------------------------------------------------------------------

@pytest.mark.parametrize("counter", [
    "dropped_untrustworthy_prefilter", "dropped_junk_version",
    "dropped_unknown_class_code_unii",
])
def test_each_new_drop_counter_is_watched_REFUSING(counter):
    with pytest.raises(ValueError, match="republished as 'absent from DailyMed'"):
        spl_checks.check_scan_dropped_nothing(_scan(**{counter: 1}))


def test_the_unknown_class_code_REPORT_counter_does_not_refuse():
    """Measured at 10 on the real release. A guard that refused over it would
    refuse every run against the corpus this slice was built on."""
    spl_checks.check_scan_dropped_nothing(_scan(skipped_unknown_class_code=10))


# --------------------------------------------------------------------------
# A counter nobody reports is a silent skip with extra steps
# --------------------------------------------------------------------------

def test_a_clean_scan_reports_no_skip_line_at_all():
    """The control. Every run would otherwise carry a line of zeroes, and a line
    that is always printed is a line nobody reads."""
    assert rel.describe_reported_skips(_scan()) == ""


def test_an_unknown_class_code_is_REPORTED_to_the_operator():
    """⇒ THIS COUNTER REFUSES NOTHING, SO PRINTING IT IS THE WHOLE OF ITS JOB.
    `COLR` is deliberately not a drop; if it were also not shown, admitting it
    to the vocabulary would have made it invisible rather than ruled on."""
    line = rel.describe_reported_skips(_scan(skipped_unknown_class_code=10))
    assert "10" in line
    assert "classCode" in line
    # DOCUMENTS, not labels: `found` is de-duplicated by set_id afterwards, and
    # counting documents as labels is how 6,583 was published where 6,539 existed.
    assert "document" in line


def test_a_member_that_was_never_a_label_is_reported_too():
    """`skipped_not_a_member_zip` has never been printed either -- it was
    counted, documented as "counted and reported", and then reported nowhere."""
    line = rel.describe_reported_skips(_scan(skipped_not_a_member_zip=3))
    assert "3" in line


def test_both_reported_counters_appear_together():
    line = rel.describe_reported_skips(
        _scan(skipped_unknown_class_code=10, skipped_not_a_member_zip=3))
    assert "10" in line and "3" in line


def test_a_DROPPED_counter_is_not_reported_here_because_it_RAISES():
    """The drop counters reach the operator as an exception with a full
    breakdown, so repeating them here would describe a run that cannot exist."""
    assert rel.describe_reported_skips(_scan(dropped_junk_version=4)) == ""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _part(tmp_path, members, *, name="dm_spl_release_human_rx_part1.zip") -> str:
    import io
    import zipfile
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as outer:
        for member_name, inner_members in members.items():
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as inner:
                for inner_name, payload in inner_members.items():
                    inner.writestr(inner_name, payload)
            outer.writestr(member_name, buffer.getvalue())
    return str(path)


def _scan(**overrides):
    """A clean `ScanResult`. Every counter spelled, none defaulted -- see
    `tests/test_spl_run.py::_scan` for why the type refuses defaults."""
    fields = dict(
        documents_read=10, found={}, dropped_no_set_id_bytes=0,
        dropped_unreadable=0, dropped_prefilter_disagreed=0,
        dropped_no_xml_member=0, dropped_several_xml_members=0,
        skipped_not_a_member_zip=0, dropped_untrustworthy_prefilter=0,
        dropped_junk_version=0, dropped_unknown_class_code_unii=0,
        skipped_unknown_class_code=0)
    return rel.ScanResult(**(fields | overrides))


def test_trustworthiness_only_reads_the_bytes_BEFORE_the_selected_setId():
    """A `<relatedDocument>` after the setId cannot change the verdict, and this
    runs on every one of ~44,000 non-target documents per release -- so it must
    not scan the whole document to say so. Pinned on a payload big enough that a
    full scan would be the wrong answer's only possible cause."""
    xml = _document(body="<x/>" * 50_000 + _related_document("SET-OLD"))
    assert dm.prefilter_is_trustworthy(xml) is True
