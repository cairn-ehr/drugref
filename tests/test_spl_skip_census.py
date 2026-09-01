# tests/test_spl_skip_census.py
"""The skip census: counting the reader branches that count nothing.

⇒ WHY THIS FILE EXISTS. `check_scan_dropped_nothing` refuses a run whose scan
dropped anything, and the review of PR #161 folded two brand-new counters into
the total it refuses over -- `dropped_no_xml_member` and
`dropped_several_xml_members` -- which its own docstring concedes are
"UNMEASURED on a real release". Three sibling branches (issue #162) still count
nothing at all, and each reappears three stages later as `absent_from_dailymed`:
a fact about the reader sold as a fact about the release.

`tools/spl_skip_census.py` is the instrument that settles both questions in one
pass. It is a MEASUREMENT, so the thing these tests guard hardest is that it
measures the SHIPPED reader rather than a second implementation that happens to
agree: `test_the_census_NEVER_disagrees_with_the_shipped_reader` is the reason
the rest of the file is trustworthy.
"""
import zipfile

import pytest

from drugref.ingest import spl_dailymed as dm
from tools import spl_skip_census as census

UNII_SYSTEM = dm.UNII_CODE_SYSTEM


def _document(
    body: str = "", *, set_id: str | None = "SET-1", version: str | None = "4",
    before_set_id: str = "",
) -> bytes:
    """A minimal but structurally real SPL document.

    `before_set_id` puts content AHEAD of the document's own `<setId>`, which is
    how issue #162's first case is reproduced: SPL's `<relatedDocument>` names
    the label this one replaces and carries a `setId` of its own.
    """
    version_element = (
        f'<versionNumber value="{version}"/>' if version is not None else "")
    set_id_element = f'<setId root="{set_id}"/>' if set_id is not None else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<document xmlns="urn:hl7-org:v3">'
        f"{before_set_id}{set_id_element}{version_element}{body}"
        "</document>"
    ).encode()


def _related_document(set_id: str) -> str:
    """The `<relatedDocument>` shape whose `setId` the pre-filter could pick."""
    return (
        f'<relatedDocument typeCode="RPLC"><relatedDocument>'
        f'<setId root="{set_id}"/></relatedDocument></relatedDocument>')


def _ingredient(class_code: str, substance_unii: str = "UNII-A") -> str:
    return (
        f'<ingredient classCode="{class_code}"><ingredientSubstance>'
        f'<code code="{substance_unii}" codeSystem="{UNII_SYSTEM}"/>'
        f"</ingredientSubstance></ingredient>")


# --------------------------------------------------------------------------
# One document at a time: the pure half
# --------------------------------------------------------------------------

def test_a_clean_document_agrees_with_itself_and_has_no_reason_to_be_dropped():
    verdict = census.classify_document(_document())
    assert verdict.prefilter_set_id == "SET-1"
    assert verdict.tree_set_id == "SET-1"
    assert verdict.prefilter_disagreed is False
    assert verdict.unreadable_reason is None


def test_a_relatedDocument_setId_read_FIRST_is_seen_as_a_DISAGREEMENT():
    """Issue #162 case 1, in the shape the module's own docstring assumes away.

    `set_id_in_bytes` takes the FIRST `setId` in the raw bytes. When a
    `<relatedDocument>` precedes the document's own, the pre-filter names the
    label being REPLACED -- and `scan_release` can only notice when that name is
    in `targets`. This is the census that says whether the ordering ever
    actually happens.
    """
    xml = _document(before_set_id=_related_document("SET-OLD"))
    verdict = census.classify_document(xml)
    assert verdict.prefilter_set_id == "SET-OLD"
    assert verdict.tree_set_id == "SET-1"
    assert verdict.prefilter_disagreed is True


def test_a_DOCTYPE_document_is_told_APART_from_a_malformed_one():
    """The shipped reader returns `None` for both; the census must not.

    `dropped_unreadable` folds an entity-guard refusal and a broken document
    into one number, and they call for opposite responses: one is a document
    this reader refuses on purpose, the other is a document it cannot read.
    """
    doctype = b'<!DOCTYPE d [<!ENTITY a "x">]><document xmlns="urn:hl7-org:v3"/>'
    assert census.classify_document(doctype).unreadable_reason == "doctype"
    assert census.classify_document(b"<not xml").unreadable_reason == "parse_error"


def test_a_document_with_no_setId_in_the_tree_carries_its_own_reason():
    verdict = census.classify_document(_document(set_id=None))
    assert verdict.unreadable_reason == "no_set_id"
    assert verdict.tree_set_id is None


def test_a_junk_versionNumber_is_counted_and_its_raw_value_is_KEPT():
    """Issue #162 case 2. The raw value is kept because the decision the
    measurement feeds is whether junk is a typo worth tolerating or a broken
    document worth refusing, and a bare count cannot tell them apart."""
    verdict = census.classify_document(_document(version="four"))
    assert verdict.version_is_junk is True
    assert verdict.version_raw == "four"


def test_an_ABSENT_versionNumber_is_not_junk():
    """The distinction that keeps case 2's counter honest: `version is None`
    means BOTH "no element" and "unreadable element", and only the second is a
    label whose tie-break was destroyed."""
    verdict = census.classify_document(_document(version=None))
    assert verdict.version_is_junk is False
    assert verdict.version_raw is None


def test_EVERY_ingredient_classCode_is_censused_including_the_active_ones():
    """Issue #162 case 3. A histogram, not a flag: the question is which codes
    the release actually contains, and a boolean "saw an unknown one" cannot
    name it."""
    xml = _document(
        _ingredient("ACTIB") + _ingredient("IACT") + _ingredient("ZZZZ"))
    verdict = census.classify_document(xml)
    assert dict(verdict.class_code_counts) == {"ACTIB": 1, "IACT": 1, "ZZZZ": 1}


def test_an_unknown_class_code_is_named_by_SUBTRACTING_the_shipped_vocabulary(
        monkeypatch):
    """The shipped set is READ, never retyped -- the "three homes" defect this
    slice keeps finding. Pinned by MOVING the module's own frozenset and
    watching the answer follow it."""
    counts = {"ACTIB": 1, "IACT": 1, "ZZZZ": 1}
    assert census.codes_outside_the_vocabulary(counts) == {"ZZZZ"}

    monkeypatch.setattr(
        dm, "_ACTIVE_CLASS_CODES", frozenset({*dm._ACTIVE_CLASS_CODES, "ZZZZ"}))
    assert census.codes_outside_the_vocabulary(counts) == set()


# --------------------------------------------------------------------------
# The guard that makes the measurement worth reading
# --------------------------------------------------------------------------

@pytest.mark.parametrize("xml", [
    _document(),
    _document(before_set_id=_related_document("SET-OLD")),
    _document(set_id=None),
    _document(version="four"),
    _document(version=None),
    # ⇒ THE SHAPE THE TWO ACTUALLY DISAGREED ON. A `<versionNumber/>` carrying no
    # `value` attribute is JUNK to the shipped reader (which does
    # `int(get("value") or "")` the moment the ELEMENT exists) and was "absent"
    # to the census, which keyed on the attribute. The census therefore reported
    # zero for a condition the ingest aborts the whole run over -- and this test
    # passed anyway, because it compared only `version`, which is `None` either way.
    _document(version="") .replace(b'value=""', b''),
    _document(version=""),
    _document(_ingredient("ACTIB") + _ingredient("ZZZZ")),
    b'<!DOCTYPE d [<!ENTITY a "x">]><document xmlns="urn:hl7-org:v3"/>',
    b"<not xml",
    # An encoding no codec provides: `LookupError`, not `ET.ParseError`. The
    # census used to CRASH here rather than count the branch it exists to count.
    b'<?xml version="1.0" encoding="no-such-codec"?><d/>',
])
def test_the_census_NEVER_disagrees_with_the_shipped_reader(xml):
    """⇒ THE POINT OF THE WHOLE FILE.

    The census needs finer granularity than `extract_subject_uniis` exposes, so
    it parses the tree itself -- which is exactly how a probe ends up measuring
    its own second implementation rather than the code that ships. This asserts
    the census is a REFINEMENT of the shipped reader and never a rival to it: it
    is unreadable precisely when the shipped reader returns `None`, and when it
    is readable it reports the same `set_id` and the same `version`.
    """
    verdict = census.classify_document(xml)
    shipped = dm.extract_subject_uniis(xml)

    assert (verdict.unreadable_reason is None) == (shipped is not None)
    if shipped is not None:
        assert verdict.tree_set_id == shipped.set_id
        assert verdict.version == shipped.version
        # ⇒ AND THE JUNK VERDICT, which is the half that DECIDES something:
        # `version` is `None` for an absent element AND for a broken one, so
        # comparing it alone let the two disagree in the only way that matters.
        assert verdict.version_is_junk == shipped.version_was_unreadable


# --------------------------------------------------------------------------
# The whole release: the I/O shell
# --------------------------------------------------------------------------

def _member(members: dict[str, bytes]) -> bytes:
    """One inner member zip -- an SPL label plus, sometimes, its images."""
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as inner:
        for name, payload in members.items():
            inner.writestr(name, payload)
    return buffer.getvalue()


def _part(tmp_path, members: dict[str, bytes], name="part1.zip") -> str:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as outer:
        for member_name, payload in members.items():
            outer.writestr(member_name, payload)
    return str(path)


def test_census_release_counts_member_skips_THROUGH_the_shipped_reader(tmp_path):
    """The member-level counters are `iter_release_labels`'s own, because the
    two this round exists to measure are refused over by
    `check_scan_dropped_nothing` -- and a probe that re-walked the zips would
    measure a reader nothing ships."""
    part = _part(tmp_path, {
        "a.zip": _member({"a.xml": _document(set_id="SET-A")}),
        "b.zip": _member({"only-an-image.jpg": b"\xff\xd8"}),
        "c.zip": _member({"one.xml": _document(), "two.xml": _document()}),
        "RELEASE-MANIFEST.txt": b"not a zip at all",
    })

    result = census.census_release([part])

    assert result.documents_read == 1
    assert result.no_xml_member == 1
    assert result.several_xml_members == 1
    assert result.not_a_member_zip == 1


def test_census_release_totals_the_document_verdicts_across_parts(tmp_path):
    part_one = _part(tmp_path, {
        "a.zip": _member({"a.xml": _document(set_id="SET-A", version="junk")}),
    }, name="part1.zip")
    part_two = _part(tmp_path, {
        "b.zip": _member({"b.xml": _document(
            set_id="SET-B", before_set_id=_related_document("SET-OLD"))}),
        "c.zip": _member({"c.xml": _document(
            set_id="SET-C", body=_ingredient("ZZZZ"))}),
    }, name="part2.zip")

    result = census.census_release([part_one, part_two])

    assert result.documents_read == 3
    assert result.junk_version == 1
    assert result.prefilter_disagreed == 1
    assert result.class_code_counts["ZZZZ"] == 1
    assert result.unknown_class_codes == {"ZZZZ"}


# --------------------------------------------------------------------------
# The review round: the instrument that contradicted its own verdict
# --------------------------------------------------------------------------

def test_the_INACTIVE_vocabulary_is_READ_not_retyped(monkeypatch):
    """⇒ THE DEFECT THIS FILE'S OWN COMMENT WARNED ABOUT, COMMITTED THREE LINES
    BELOW IT.

    The not-active vocabulary was retyped here while the active one was read at
    call time -- under a comment explaining that a vocabulary with two homes is
    the defect this slice keeps finding. `COLR` was then added to the shipped set
    and not to the copy, so re-running the census on the very release that ruled
    on `COLR` reported it as unruled: the instrument contradicting the verdict it
    had produced. Both sets are read at call time now, and this moves the
    not-active one to prove it.
    """
    assert census.codes_outside_the_vocabulary({"ZZZZ": 1}) == {"ZZZZ"}

    monkeypatch.setattr(
        dm, "_DOCUMENTED_INACTIVE_CLASS_CODES",
        frozenset({*dm._DOCUMENTED_INACTIVE_CLASS_CODES, "ZZZZ"}))
    assert census.codes_outside_the_vocabulary({"ZZZZ": 1}) == set()


def test_the_census_does_NOT_call_COLR_unknown_now_that_the_reader_has_ruled():
    """The concrete consequence of the drift: this is the code, the release and
    the verdict the whole round turned on."""
    assert census.codes_outside_the_vocabulary({"COLR": 10}) == set()
    assert dm.extract_subject_uniis(
        _document(_ingredient_without_code("COLR"))).unknown_class_codes == ()


def _ingredient_without_code(class_code: str) -> str:
    """COLR's real shape in the release: no `<code>` element at all."""
    return (f'<ingredient classCode="{class_code}">'
            "<ingredientSubstance/></ingredient>")


def test_every_census_counter_is_actually_MERGED():
    """⇒ A FIELD MISSING FROM `_COUNTER_FIELDS` IS SILENTLY ZERO -- in every
    merged total AND in the JSON a spec later quotes. The list is derived from
    the dataclass now; this pins that it stays derived and stays complete."""
    import dataclasses

    ints = {f.name for f in dataclasses.fields(census.SkipCensus)
            if f.type in ("int", int)}
    assert ints == set(census._COUNTER_FIELDS)


def test_a_census_counter_may_NOT_default_to_zero():
    """The same call `ScanResult` makes: a counter added to the type must break
    both construction sites loudly rather than quietly reading zero."""
    with pytest.raises(TypeError):
        census.SkipCensus()


def test_the_census_counts_the_CAUSE_of_case_1_beside_its_outcome(tmp_path):
    """⇒ THE SPEC CLAIMED TWO MEASUREMENTS AND THE TOOL INSTRUMENTED ONE.

    `prefilter_disagreed` is the OUTCOME the reader sees. The cause is the byte
    ordering that produces it -- a `<relatedDocument>` ahead of the document's
    own `<setId>` -- and it was never counted, so the command the spec gives for
    reproducing "both" figures could only produce one. Read through the shipped
    `prefilter_is_trustworthy`, so it cannot drift from the guard.
    """
    part = _part(tmp_path, {"a.zip": _member({"a.xml": _document(
        set_id="SET-MINE", before_set_id=_related_document("SET-OLD"))})})

    result = census.census_release([part])

    assert result.prefilter_disagreed == 1
    assert result.untrustworthy_prefilter == 1


def test_an_ordinary_document_shows_neither_cause_nor_outcome(tmp_path):
    """The control: the release's real ordering, where both measured zero."""
    part = _part(tmp_path, {"a.zip": _member({"a.xml": _document()})})

    result = census.census_release([part])

    assert result.prefilter_disagreed == 0
    assert result.untrustworthy_prefilter == 0


def test_a_member_the_reader_cannot_READ_is_censused_as_its_own_branch(tmp_path):
    """The census walks the shipped `iter_release_labels`, so a skip reason added
    there must reach a counter here -- `SKIP_REASONS` is the one vocabulary both
    read, and an unregistered reason raises `KeyError` rather than evaporating."""
    import io as _io

    buffer = _io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as inner:
        inner.writestr("a.xml", b"A" * 200)
    corrupt = buffer.getvalue().replace(b"A" * 200, b"B" * 200)

    result = census.census_release([_part(tmp_path, {"broken.zip": corrupt})])

    assert result.unreadable_member_zip == 1
    assert result.documents_read == 0


def test_the_exit_code_ANSWERS_the_question_the_tool_was_run_to_ask(tmp_path):
    """⇒ A CENSUS THAT COUNTED THOUSANDS OF DROPS EXITED 0, exactly like one that
    counted none, so nothing could gate on it. Non-zero means
    `check_scan_dropped_nothing` would refuse this release."""
    clean = _part(tmp_path, {"a.zip": _member({"a.xml": _document()})},
                  name="clean.zip")
    assert census.main([clean]) == 0

    dropping = _part(tmp_path, {"a.zip": _member({"a.jpg": b"\xff\xd8"})},
                     name="dropping.zip")
    assert census.main([dropping]) == 1


def test_a_REPORTED_skip_alone_does_not_claim_the_ingest_would_refuse(tmp_path):
    """`not_a_member_zip` and an unknown classCode with no UNII are reported and
    never refuse, so counting them in the exit code would announce a refusal the
    ingest would not make."""
    part = _part(tmp_path, {
        "a.zip": _member({"a.xml": _document(
            _ingredient_without_code("ZZZZ"))}),
        "MANIFEST.txt": b"not a zip at all",
    })
    assert census.main([part]) == 0
