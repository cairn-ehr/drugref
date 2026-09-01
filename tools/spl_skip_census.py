# tools/spl_skip_census.py
"""Count every branch of the DailyMed reader that declines a document.

⇒ WHY THIS TOOL EXISTS (in the past tense: it has since done its job).
`spl_checks.check_scan_dropped_nothing` aborts an ingest whose scan dropped
anything, and the review of PR #161 folded two BRAND-NEW counters into the total
it refuses over -- `dropped_no_xml_member` and `dropped_several_xml_members` --
while conceding in its own docstring that they were "NOT YET MEASURED ON A REAL
RELEASE". The shipped ingest might therefore have refused the very release the
last run read successfully. Three sibling branches (issue #162) counted nothing
at all, and each reappeared three stages later as `absent_from_dailymed` -- a
fact about this reader sold as a fact about the release, on the route whose
population the design spec turns into a commitment.

This pass is what let all five be counted: every one measured zero except an
unknown classCode with no UNII under it, which the release carries ten times.
Both questions are answered by the SAME pass over the release, so this tool
makes it once:

* the two folded-but-unmeasured member skips, and the reported-not-dropped third
  (`skipped_not_a_member_zip`);
* issue #162 case 1 -- a pre-filter `setId` that is not the document's own,
  measured over EVERY document rather than only the targeted ones, which makes
  the answer independent of any target set and so of any database;
* issue #162 case 2 -- a `<versionNumber>` that is not an integer, which costs
  the label its tie-break and hands `dedupe_by_set_id` back the zip-member order
  the module argues at length is not a rule;
* issue #162 case 3 -- an HL7 `classCode` outside the shipped vocabulary, as a
  HISTOGRAM, because "saw an unknown one" cannot name the code a human has to
  rule on.

⇒ IT MEASURES THE SHIPPED READER, NOT A SECOND ONE. Member-level skips come from
`spl_release.iter_release_labels` itself, and the classCode vocabulary and the
`<versionNumber>` rule are read from `spl_dailymed` at call time. The document
level needs finer granularity than `extract_subject_uniis` exposes -- it returns
one `None` for three different situations -- so this module parses again, and
`tests/test_spl_skip_census.py::test_the_census_NEVER_disagrees_with_the_shipped
_reader` pins that second parse as a REFINEMENT of the shipped one rather than a
rival to it. A probe that quietly measures itself is how seven wrong figures got
published in this project.

Usage:
    uv run python -m tools.spl_skip_census downloads/DAILYMED/*.zip
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Literal

from drugref.ingest import spl_dailymed as dm
from drugref.ingest import spl_release

# ⇒ A VOCABULARY CONSTANT USED TO LIVE HERE, AND IT WENT OUT OF DATE IMMEDIATELY.
#
# A copy of the shipped module's not-active class codes was retyped here, three
# lines under a comment explaining that the ACTIVE codes are read at call time
# "because a vocabulary with two homes is the defect this slice has now found
# four times". It became the fifth: `COLR` was added to the shipped set and not
# to the copy, so re-running this census on the very release that ruled on
# `COLR` reported it as unruled -- the instrument contradicting the verdict it
# had produced. BOTH vocabularies are read from `spl_dailymed` at call time now;
# see `codes_outside_the_vocabulary`.


#: The three situations `extract_subject_uniis` folds into a single `None`.
UnreadableReason = Literal["doctype", "parse_error", "no_set_id"]


@dataclass(frozen=True, kw_only=True)
class DocumentVerdict:
    """What one label document does at each branch that declines a document.

    `unreadable_reason` splits the single `None` that `extract_subject_uniis`
    returns into the three situations it actually covers, because they call for
    different responses: `doctype` is a document this reader refuses ON PURPOSE
    (the entity guard), `parse_error` is one it cannot read, and `no_set_id` is
    one it can read but can never join to an openFDA record.
    """

    #: The `setId` the cheap byte pre-filter picked -- the FIRST one in the bytes.
    prefilter_set_id: str | None
    #: The `setId` the document's own tree carries. The authority.
    tree_set_id: str | None
    #: `None` when the document was read, otherwise WHY not. Typed as a `Literal`
    #: rather than `str` because `census_part` dispatches on these three spellings
    #: through an `elif` chain: a typo there counts nothing, silently, in the tool
    #: whose entire job is noticing what nothing counts.
    unreadable_reason: UnreadableReason | None
    #: `<versionNumber value=...>` exactly as written, or `None` if the element
    #: is absent. Kept raw because a bare count cannot tell a typo from a broken
    #: document, and that is the distinction the fix for issue #162 case 2 turns on.
    version_raw: str | None
    #: The parsed version, by the shipped rule. `None` for absent AND for junk.
    version: int | None
    #: Every `<ingredient classCode=...>` this document carries, with counts.
    class_code_counts: Mapping[str, int]

    @property
    def prefilter_disagreed(self) -> bool:
        """Whether the pre-filter named a `setId` other than the document's own.

        False when the document is unreadable: there is no authority to disagree
        WITH, and counting it here would double-count a drop the reader already
        has a counter for.
        """
        if self.tree_set_id is None or self.prefilter_set_id is None:
            return False
        return self.prefilter_set_id != self.tree_set_id

    @property
    def version_is_junk(self) -> bool:
        """A `<versionNumber>` that is present and does not parse.

        NOT the same as `version is None`, which is also true of a label that
        simply carries no version element -- and only one of those two is a
        label whose tie-break was destroyed.
        """
        return self.version_raw is not None and self.version is None


def classify_document(xml_bytes: bytes) -> DocumentVerdict:
    """Read one document's bytes at every branch the reader can decline at.

    The order of the three unreadable reasons is the SHIPPED order, deliberately:
    `extract_subject_uniis` refuses a DOCTYPE before it parses, and parses before
    it looks for a `setId`, so classifying in any other order would report a
    different reason than the one the ingest would have recorded.
    """
    prefilter = dm.set_id_in_bytes(xml_bytes)
    unreadable = _unreadable_reason_and_root(xml_bytes)
    reason, root = unreadable
    if root is None:
        return DocumentVerdict(
            prefilter_set_id=prefilter, tree_set_id=None,
            unreadable_reason=reason, version_raw=None, version=None,
            class_code_counts={})

    set_id_element = root.find(f"{dm._SPL_NS}setId")
    tree_set_id = set_id_element.get("root") if set_id_element is not None else None
    if not tree_set_id:
        return DocumentVerdict(
            prefilter_set_id=prefilter, tree_set_id=None,
            unreadable_reason="no_set_id", version_raw=None, version=None,
            class_code_counts={})

    version_raw, version = _version_of(root)
    return DocumentVerdict(
        prefilter_set_id=prefilter, tree_set_id=tree_set_id,
        unreadable_reason=None, version_raw=version_raw, version=version,
        class_code_counts=Counter(
            ingredient.get("classCode") or ""
            for ingredient in root.iter(f"{dm._SPL_NS}ingredient")))


def _unreadable_reason_and_root(
    xml_bytes: bytes,
) -> tuple[UnreadableReason | None, ET.Element | None]:
    """The shipped refuse-then-parse sequence, with the reason kept."""
    if dm._DOCTYPE.search(xml_bytes):
        return "doctype", None
    try:
        return None, ET.fromstring(xml_bytes)
    except (ET.ParseError, LookupError, ValueError):
        # The SAME tuple the shipped reader catches. Narrower here would make
        # the census CRASH on a document the ingest files as `dropped_unreadable`
        # -- and a census that dies on the branch it exists to count reports
        # nothing for it.
        return "parse_error", None


def _version_of(root: ET.Element) -> tuple[str | None, int | None]:
    """`(raw, parsed)` for `<versionNumber>`, by the shipped parsing rule.

    ⇒ THE SHIPPED RULE KEYS ON THE ELEMENT, NOT ON THE ATTRIBUTE, and an earlier
    draft of this function keyed on the attribute. `extract_subject_uniis` does
    `int(element.get("value") or "")` the moment the ELEMENT exists, so
    `<versionNumber/>` with no `value` raises `ValueError` and is JUNK there --
    a drop that refuses the whole run. Returning `(None, None)` for it here
    called the same document "absent version", a benign context line. The census
    would have reported zero for a condition the ingest aborts on, which is
    exactly the "probe that quietly measures itself" this tool exists to avoid.
    `raw` is therefore `""` -- present but empty -- rather than `None`, so
    `version_is_junk` can tell the element apart from its absence.
    """
    element = root.find(f"{dm._SPL_NS}versionNumber")
    if element is None:
        return None, None
    raw = element.get("value") or ""
    try:
        return raw, int(raw)
    except ValueError:
        return raw, None


def codes_outside_the_vocabulary(counts: Mapping[str, int]) -> set[str]:
    """The classCodes nobody has ruled on -- issue #162 case 3's real question.

    ⇒ BOTH shipped vocabularies are read AT CALL TIME rather than copied, so this
    census cannot disagree with the ingest about what "unknown" means. The
    not-active half used to be a local copy, and it was wrong within one commit;
    `test_the_INACTIVE_vocabulary_is_READ_not_retyped` now moves each frozenset
    in turn and watches the answer follow it.
    """
    known = (frozenset(dm._ACTIVE_CLASS_CODES)
             | frozenset(dm._DOCUMENTED_INACTIVE_CLASS_CODES))
    return {code for code in counts if code not in known}


@dataclass(frozen=True, kw_only=True)
class SkipCensus:
    """Every declining branch, counted over one part or over a whole release.

    ⇒ NO DEFAULTS ON THE COUNTERS, deliberately, and it is the same call
    `spl_release.ScanResult` makes. `merge` and `census_part` build their totals
    through `_COUNTER_FIELDS` and splat them in, so a counter added to this class
    but forgotten there would silently take a default of 0 -- in every merged
    total AND in the JSON a spec later quotes. Without defaults it is a
    `TypeError` at both construction sites the moment the field is added, and
    `test_every_census_counter_is_actually_MERGED` pins the two lists together.

    Named `SkipCensus` rather than `Census` because `tools/spl_label_extract.py`
    already has an unrelated `Census` in the same package.
    """

    documents_read: int
    #: Member-level, straight from `iter_release_labels`'s own `on_skip`.
    not_a_member_zip: int
    no_xml_member: int
    several_xml_members: int
    unreadable_member_zip: int
    #: Document-level, RELEASE-WIDE -- so these are a superset of the
    #: target-scoped counters `ScanResult` carries, not a check on them. All are
    #: counted by `ScanResult` too except `absent_version`, which has no
    #: counterpart there because a label with no version element at all keeps its
    #: tie-break; only a BROKEN one loses it.
    doctype_refused: int
    parse_error: int
    no_set_id_in_tree: int
    no_set_id_in_bytes: int
    prefilter_disagreed: int
    #: ⇒ THE CAUSE, beside the outcome. `prefilter_disagreed` is what the reader
    #: SEES; this is the byte ordering that produces it -- a `<relatedDocument>`
    #: ahead of the document's own `<setId>`, read through the shipped
    #: `prefilter_is_trustworthy`. The spec claimed both were measured while only
    #: the outcome was instrumented, so the "two measurements" it reports could
    #: not be reproduced by the command it gives for reproducing them.
    untrustworthy_prefilter: int
    junk_version: int
    absent_version: int
    class_code_counts: Mapping[str, int] = field(default_factory=Counter)
    #: Present only on a whole-release census.
    by_part: Mapping[str, "SkipCensus"] = field(default_factory=dict)

    @property
    def unknown_class_codes(self) -> set[str]:
        return codes_outside_the_vocabulary(self.class_code_counts)


#: Every `int` counter on `SkipCensus`, DERIVED rather than retyped -- the same
#: reasoning as the vocabulary above, applied to a field list. A counter added to
#: the class joins every merge and both JSON payloads by existing.
_COUNTER_FIELDS = tuple(
    f.name for f in fields(SkipCensus) if f.type in ("int", int))


def merge(censuses: Iterable[SkipCensus]) -> SkipCensus:
    """Add censuses together. Pure, so a release total is a fold over its parts."""
    totals = dict.fromkeys(_COUNTER_FIELDS, 0)
    codes: Counter[str] = Counter()
    for census in censuses:
        for name in _COUNTER_FIELDS:
            totals[name] += getattr(census, name)
        codes.update(census.class_code_counts)
    return SkipCensus(**totals, class_code_counts=codes)


def census_part(
    part_path: str, *, progress: Callable[[int], None] | None = None,
) -> SkipCensus:
    """Walk one release part through the SHIPPED reader and count every branch."""
    skips = dict.fromkeys(spl_release.SKIP_REASONS, 0)

    def note_skip(_member: str, reason: str) -> None:
        skips[reason] += 1

    totals = dict.fromkeys(_COUNTER_FIELDS, 0)
    codes: Counter[str] = Counter()
    for _document_id, xml_bytes in spl_release.iter_release_labels(
            part_path, on_skip=note_skip):
        totals["documents_read"] += 1
        verdict = classify_document(xml_bytes)
        if verdict.prefilter_set_id is None:
            totals["no_set_id_in_bytes"] += 1
        if verdict.unreadable_reason == "doctype":
            totals["doctype_refused"] += 1
        elif verdict.unreadable_reason == "parse_error":
            totals["parse_error"] += 1
        elif verdict.unreadable_reason == "no_set_id":
            totals["no_set_id_in_tree"] += 1
        if verdict.prefilter_disagreed:
            totals["prefilter_disagreed"] += 1
        if not dm.prefilter_is_trustworthy(xml_bytes):
            # The CAUSE of the line above, through the shipped predicate.
            totals["untrustworthy_prefilter"] += 1
        if verdict.version_is_junk:
            totals["junk_version"] += 1
        elif verdict.version_raw is None and verdict.unreadable_reason is None:
            totals["absent_version"] += 1
        codes.update(verdict.class_code_counts)
        if progress is not None and totals["documents_read"] % 2000 == 0:
            progress(totals["documents_read"])

    return SkipCensus(**{**totals, **skips}, class_code_counts=codes)


def census_release(
    parts: Sequence[str], *, progress: Callable[[str, int], None] | None = None,
) -> SkipCensus:
    """Census every part, keeping the per-part breakdown beside the total."""
    by_part: dict[str, SkipCensus] = {}
    for part in parts:
        by_part[part] = census_part(
            part,
            progress=None if progress is None
            else (lambda read, p=part: progress(p, read)))
    return replace(merge(by_part.values()), by_part=by_part)


def _report(census: SkipCensus) -> str:
    """The measurement, in the shape the two decisions actually need."""
    lines = [
        "=== SPL reader skip census ===",
        f"documents_read                {census.documents_read:>9,}",
        "",
        "-- member-level (iter_release_labels) --",
        f"not_a_member_zip   REPORTED   {census.not_a_member_zip:>9,}",
        f"no_xml_member      *DROPS*    {census.no_xml_member:>9,}",
        f"several_xml_members *DROPS*   {census.several_xml_members:>9,}",
        f"unreadable_member_zip *DROPS* {census.unreadable_member_zip:>9,}",
        "",
        "-- document-level (issue #162), RELEASE-WIDE --",
        f"prefilter_disagreed  (case 1) {census.prefilter_disagreed:>9,}",
        f"  its cause: relatedDocument first {census.untrustworthy_prefilter:>5,}",
        f"junk_version         (case 2) {census.junk_version:>9,}",
        f"unknown class codes  (case 3) {sorted(census.unknown_class_codes)}",
        "",
        "-- context --",
        f"no_set_id_in_bytes            {census.no_set_id_in_bytes:>9,}",
        f"doctype_refused               {census.doctype_refused:>9,}",
        f"parse_error                   {census.parse_error:>9,}",
        f"no_set_id_in_tree             {census.no_set_id_in_tree:>9,}",
        f"absent_version                {census.absent_version:>9,}",
        "",
        "-- every classCode in the release --",
    ]
    for code, count in sorted(census.class_code_counts.items(),
                              key=lambda item: -item[1]):
        known = "" if code in codes_outside_the_vocabulary(
            census.class_code_counts) else "  (known)"
        lines.append(f"  {code or '<none>':<12} {count:>9,}{known}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("parts", nargs="+", help="DailyMed release part zips")
    parser.add_argument("--json", dest="json_out",
                        help="also write the census as JSON to this path")
    args = parser.parse_args(argv)

    started = time.monotonic()

    def progress(part: str, read: int) -> None:
        print(f"  [{time.monotonic() - started:7.1f}s] {part.split('/')[-1]} "
              f"{read:,} documents", file=sys.stderr, flush=True)

    census = census_release(args.parts, progress=progress)
    print(_report(census))
    print(f"\nread in {time.monotonic() - started:.1f}s", file=sys.stderr)

    if args.json_out:
        payload = {
            **{name: getattr(census, name) for name in _COUNTER_FIELDS},
            "class_code_counts": dict(census.class_code_counts),
            "unknown_class_codes": sorted(census.unknown_class_codes),
            "by_part": {
                part: {name: getattr(part_census, name)
                       for name in _COUNTER_FIELDS}
                for part, part_census in census.by_part.items()},
        }
        try:
            with open(args.json_out, "w") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as error:
            # Named, and AFTER the report is printed, so a bad --json path costs
            # the record but never the 163-second measurement itself.
            print(f"could not write {args.json_out}: {error}", file=sys.stderr)
            return 2

    # ⇒ THE EXIT CODE ANSWERS THE QUESTION THE TOOL WAS RUN TO ANSWER: would
    # `check_scan_dropped_nothing` refuse this release? Returning 0 either way
    # made a census that counted thousands of drops indistinguishable, to any
    # caller or CI step, from one that counted none.
    return 1 if _would_refuse(census) else 0


def _would_refuse(census: SkipCensus) -> bool:
    """Whether these figures would abort an ingest.

    The DROPPING branches only -- `not_a_member_zip` and an unknown classCode
    with no UNII are reported and never refuse, so counting them here would
    report a refusal the ingest would not make.
    """
    return any((census.no_xml_member, census.several_xml_members,
                census.unreadable_member_zip, census.doctype_refused,
                census.parse_error, census.no_set_id_in_tree,
                census.no_set_id_in_bytes, census.prefilter_disagreed,
                census.junk_version))


if __name__ == "__main__":
    raise SystemExit(main())
