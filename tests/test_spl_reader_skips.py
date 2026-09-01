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

from tests.conftest import clean_scan as _scan
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




def test_trustworthiness_only_reads_the_bytes_BEFORE_the_selected_setId():
    """A `<relatedDocument>` after the setId cannot change the verdict, and this
    runs on every one of ~44,000 non-target documents per release -- so it must
    not scan the whole document to say so. Pinned on a payload big enough that a
    full scan would be the wrong answer's only possible cause."""
    xml = _document(body="<x/>" * 50_000 + _related_document("SET-OLD"))
    assert dm.prefilter_is_trustworthy(xml) is True


# --------------------------------------------------------------------------
# The review round: what the counters were, and were not, able to say
# --------------------------------------------------------------------------

def test_every_counter_DECLARES_its_verdict_in_its_name():
    """⇒ THE DROP/REPORT SPLIT IS STRUCTURAL, NOT A LIST TO REMEMBER.

    `total_dropped` sums by the `dropped_` prefix, so a counter that names
    neither verdict would be silently ignored by the guard -- the twelfth-counter
    version of the hole this whole module exists to close. A hand-written sum
    was the earlier design, and a field could simply be left out of it.
    """
    import dataclasses

    for field in dataclasses.fields(rel.ScanResult):
        if field.name in {"documents_read", "found", "unknown_class_codes"}:
            continue
        assert field.name.startswith(("dropped_", "skipped_")), (
            f"{field.name} declares no verdict, so `total_dropped` ignores it")


@pytest.mark.parametrize("counter,summed", [
    ("dropped_no_set_id_bytes", True), ("dropped_unreadable", True),
    ("dropped_prefilter_disagreed", True), ("dropped_no_xml_member", True),
    ("dropped_several_xml_members", True),
    ("dropped_unreadable_member_zip", True),
    ("dropped_untrustworthy_prefilter", True), ("dropped_junk_version", True),
    ("dropped_unknown_class_code_unii", True),
    ("skipped_not_a_member_zip", False), ("skipped_unknown_class_code", False),
])
def test_the_prefix_decides_whether_a_counter_is_SUMMED(counter, summed):
    """Each counter moved ALONE, so the sum cannot be right by cancellation."""
    assert _scan(**{counter: 1}).total_dropped == (1 if summed else 0)


def test_one_document_tripping_TWO_conditions_is_ONE_drop(tmp_path):
    """⇒ `total_dropped` COUNTS DOCUMENTS, and it did not.

    The three document-level counters used to fall through instead of
    `continue`-ing, so a document that was both junk-versioned and carrying a
    UNII under an unknown code was reported as TWO drops -- and appended to
    `found` anyway, as a result the type said had been dropped.
    """
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(
        _ingredient("ZZZZ", "UNII-Z"), version="four")}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.documents_read == 1
    assert scan.total_dropped == 1
    assert scan.total_dropped <= scan.documents_read
    assert scan.found == {}, "a dropped document must not survive as a result"


def test_an_unknown_code_with_NO_unii_still_keeps_the_document(tmp_path):
    """The control for the test above: `skipped_*` reports and does NOT drop, so
    the document is kept. `COLR` is the whole reason that distinction exists."""
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(
        _ingredient("ZZZZ", unii=None))}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.skipped_unknown_class_code == 1
    assert set(scan.found) == {"SET-1"}


def test_the_unknown_codes_are_NAMED_not_merely_counted(tmp_path):
    """⇒ CASE 3 IS REPORTED SO A HUMAN CAN RULE ON THE CODE -- which requires
    knowing WHICH code. The reader collected the codes, sorted them, and threw
    them away before anything printed; the operator was told "1 document
    carrying an unrecognised classCode" and left to re-read 17.6 GB to find it.
    """
    part = _part(tmp_path, {"m.zip": {"a.xml": _document(
        _ingredient("ZZZZ", unii=None))}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.unknown_class_codes == frozenset({"ZZZZ"})
    assert "ZZZZ" in rel.describe_reported_skips(scan)


def test_an_ingredient_with_NO_classCode_at_all_is_an_unknown_code():
    """The attribute is what separates an excipient from the subject drug, so an
    ingredient that declines to say which it is has told us nothing. Measured
    zero on the release -- the census histogram has no `<none>` row -- and it is
    refused rather than assumed inactive."""
    xml = _document(
        '<ingredient><ingredientSubstance>'
        f'<code code="UNII-Q" codeSystem="{UNII_SYSTEM}"/>'
        "</ingredientSubstance></ingredient>")
    recovered = dm.extract_subject_uniis(xml)

    assert recovered.unknown_class_codes == ("",)
    assert recovered.unknown_class_code_uniis == ("UNII-Q",)


def test_a_missing_classCode_is_spelled_out_rather_than_printed_as_a_gap():
    """`""` in a message reads as a formatting bug, not as a finding."""
    assert rel.name_the_codes(frozenset({""})) == "<none>"
    assert rel.name_the_codes(frozenset({"ZZZZ", ""})) == "<none>, ZZZZ"


def test_the_refusal_message_NAMES_the_unrecognised_code():
    """The message ends "fix the reader"; a bare count hands over nothing to fix
    it with."""
    with pytest.raises(ValueError, match="ZZZZ"):
        spl_checks.check_scan_dropped_nothing(_scan(
            dropped_unknown_class_code_unii=1,
            unknown_class_codes=frozenset({"ZZZZ"})))


# --------------------------------------------------------------------------
# The member level: three ways a label was lost with every counter clean
# --------------------------------------------------------------------------

def test_a_member_zip_named_in_UPPERCASE_is_still_a_label_container(tmp_path):
    """⇒ THE PR'S OWN HEADLINE DEFECT, REPRODUCED INSIDE THE FIX.

    Membership was decided by `name.endswith(".zip")`, so `M.ZIP` was filed
    under `not_a_member_zip` -- the ONE member bucket that does not refuse the
    run -- and a real targeted label was republished as `absent_from_dailymed`
    with `total_dropped` reading zero. It is decided on the BYTES now.
    """
    part = _part(tmp_path, {"M.ZIP": {"a.xml": _document(set_id="SET-1")}})

    scan = rel.scan_release([part], {"SET-1"})

    assert set(scan.found) == {"SET-1"}
    assert scan.skipped_not_a_member_zip == 0


def test_an_xml_named_in_UPPERCASE_is_found_rather_than_called_missing(tmp_path):
    """Same defect one level in: the member DID hold an XML, and the reason
    reported for declining it said it did not."""
    part = _part(tmp_path, {"m.zip": {"A.XML": _document(set_id="SET-1")}})

    scan = rel.scan_release([part], {"SET-1"})

    assert set(scan.found) == {"SET-1"}
    assert scan.dropped_no_xml_member == 0


def test_a_member_whose_bytes_are_NOT_A_READABLE_ZIP_is_counted_and_dropped(
        tmp_path):
    """⇒ THE ONE SHAPE THAT MAKES A MEMBER UNREADABLE HAD NO COUNTER.

    `ScanResult` argues that a member zip this reader cannot read is a lost
    label and must be a drop -- and a member whose bytes are corrupt raised
    `BadZipFile` straight out of the generator, naming no member, no part and no
    setId, after tens of minutes of scanning.
    """
    import io
    import zipfile as zf

    # A REAL member zip whose directory is intact but whose payload is not: the
    # end-of-central-directory record still identifies it as a zip, `namelist()`
    # still reports one `.xml`, and only `read()` discovers the CRC is wrong.
    # That is what a truncated or corrupted download actually looks like -- and
    # it is the shape that used to raise straight out of the generator.
    buffer = io.BytesIO()
    with zf.ZipFile(buffer, "w", zf.ZIP_STORED) as inner:
        inner.writestr("a.xml", b"A" * 200)
    corrupt = buffer.getvalue().replace(b"A" * 200, b"B" * 200)
    assert zf.is_zipfile(io.BytesIO(corrupt)), "the fixture must still BE a zip"

    path = tmp_path / "part1.zip"
    with zf.ZipFile(path, "w") as outer:
        outer.writestr("broken.zip", corrupt)

    scan = rel.scan_release([str(path)], {"SET-1"})

    assert scan.dropped_unreadable_member_zip == 1
    assert scan.total_dropped == 1


def test_a_DIRECTORY_entry_is_not_reported_as_a_declined_member(tmp_path):
    """It was never a container, so counting it pads the one counter that is
    supposed to mean "a release-level manifest or index"."""
    import zipfile as zf
    path = tmp_path / "part1.zip"
    with zf.ZipFile(path, "w") as outer:
        outer.writestr("labels/", b"")

    scan = rel.scan_release([str(path)], {"SET-1"})

    assert scan.skipped_not_a_member_zip == 0
    assert scan.total_dropped == 0


# --------------------------------------------------------------------------
# Reading one document: the two shapes that aborted the whole scan
# --------------------------------------------------------------------------

def test_an_UNKNOWN_ENCODING_is_an_unreadable_document_not_a_crash():
    """⇒ `ET.ParseError` IS NOT WHAT expat RAISES HERE.

    An XML declaration naming a codec Python does not have raises `LookupError`,
    which the reader did not catch -- so one such document among 54,813 aborted
    the entire 17.6 GB scan with a message naming nothing. SPL is third-party
    content, so the encoding is chosen by the submitter.
    """
    assert dm.extract_subject_uniis(
        b'<?xml version="1.0" encoding="no-such-codec"?><d/>') is None


def test_an_unknown_encoding_reaches_the_drop_counter_it_belongs_to(tmp_path):
    """It is a READING failure, so it must land on `dropped_unreadable` and
    refuse the run -- not vanish, and not raise."""
    part = _part(tmp_path, {"m.zip": {"a.xml":
        b'<?xml version="1.0" encoding="no-such-codec"?>'
        b'<document xmlns="urn:hl7-org:v3"><setId root="SET-1"/></document>'}})

    scan = rel.scan_release([part], {"SET-1"})

    assert scan.dropped_unreadable == 1
    assert scan.total_dropped == 1


# --------------------------------------------------------------------------
# The de-duplication tie-break, and the pre-filter's namespace handling
# --------------------------------------------------------------------------

def test_version_ZERO_still_outranks_a_label_with_no_version_at_all():
    """`(row.version or -1)` collapsed version 0 into the no-version sentinel,
    so the two tied and zip-member order decided -- the one thing
    `dedupe_by_set_id` exists to refuse."""
    versioned = dm.extract_subject_uniis(_document(version="0"))
    unversioned = dm.extract_subject_uniis(_document(version=None))

    assert dm.dedupe_by_set_id([versioned, unversioned])["SET-1"] is versioned
    assert dm.dedupe_by_set_id([unversioned, versioned])["SET-1"] is versioned


def test_a_NAMESPACE_PREFIXED_relatedDocument_is_seen_too():
    """Real SPL in the wild uses prefixes. Both patterns carry `(?:\\w+:)?`, and
    every other fixture in this file emits unprefixed elements -- so removing
    the alternation from either one passed the whole suite."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hl7:document xmlns:hl7="urn:hl7-org:v3">'
        '<hl7:relatedDocument><hl7:setId root="SET-OLD"/></hl7:relatedDocument>'
        '<hl7:setId root="SET-MINE"/></hl7:document>').encode()

    assert dm.set_id_in_bytes(xml) == "SET-OLD"
    assert dm.prefilter_is_trustworthy(xml) is False


def test_a_document_with_no_setId_at_all_cannot_have_been_MIS_selected():
    """The contract for a caller that does not pre-check: nothing was selected,
    so nothing was mis-selected. `scan_release` counts these under
    `dropped_no_set_id_bytes` and never reaches this branch."""
    assert dm.prefilter_is_trustworthy(b"<document/>") is True
