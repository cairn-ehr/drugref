# tools/spl_skip_census.py
"""Count every branch of the DailyMed reader that declines a document.

⇒ WHY THIS TOOL EXISTS. `spl_checks.check_scan_dropped_nothing` aborts an ingest
whose scan dropped anything, and the review of PR #161 folded two BRAND-NEW
counters into the total it refuses over -- `dropped_no_xml_member` and
`dropped_several_xml_members`. Its own docstring concedes they are "UNMEASURED
on a real release", which means the shipped ingest may now refuse the very
release the last run read successfully. Three sibling branches (issue #162)
count nothing at all, and each reappears three stages later as
`absent_from_dailymed` -- a fact about this reader sold as a fact about the
release, on the route whose population the design spec turns into a commitment.

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
`spl_dailymed.iter_release_labels` itself. The document level needs finer
granularity than `extract_subject_uniis` exposes -- which returns one `None` for
three different situations -- so this module parses the tree again, and
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
from dataclasses import dataclass, field, replace

from drugref.ingest import spl_dailymed as dm
from drugref.ingest import spl_release

#: HL7 classCodes the shipped module's own docstring records as SEEN IN THE
#: RELEASE AND DELIBERATELY EXCLUDED. They are listed here rather than treated as
#: unknown so that the census's "unknown" set means *"nobody has ruled on this
#: code"* -- which is the only reading that makes issue #162 case 3 actionable.
#: The ACTIVE codes are NOT restated here: they are read from
#: `spl_dailymed._ACTIVE_CLASS_CODES` at call time, because a vocabulary with two
#: homes is the defect this slice has now found four times.
DOCUMENTED_INACTIVE_CLASS_CODES = frozenset({"IACT", "INGR", "CNTM"})


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
    #: `None` when the document was read. One of `doctype`, `parse_error`,
    #: `no_set_id` otherwise.
    unreadable_reason: str | None
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
) -> tuple[str | None, ET.Element | None]:
    """The shipped refuse-then-parse sequence, with the reason kept."""
    if dm._DOCTYPE.search(xml_bytes):
        return "doctype", None
    try:
        return None, ET.fromstring(xml_bytes)
    except ET.ParseError:
        return "parse_error", None


def _version_of(root: ET.Element) -> tuple[str | None, int | None]:
    """`(raw, parsed)` for `<versionNumber>`, by the shipped parsing rule."""
    element = root.find(f"{dm._SPL_NS}versionNumber")
    if element is None:
        return None, None
    raw = element.get("value")
    try:
        return raw, int(raw or "")
    except ValueError:
        return raw, None


def codes_outside_the_vocabulary(counts: Mapping[str, int]) -> set[str]:
    """The classCodes nobody has ruled on -- issue #162 case 3's real question.

    Reads `spl_dailymed._ACTIVE_CLASS_CODES` AT CALL TIME rather than copying it,
    so this census can never disagree with the vocabulary the ingest applies.
    """
    known = frozenset(dm._ACTIVE_CLASS_CODES) | DOCUMENTED_INACTIVE_CLASS_CODES
    return {code for code in counts if code not in known}


@dataclass(frozen=True, kw_only=True)
class Census:
    """Every declining branch, counted over one part or over a whole release."""

    documents_read: int = 0
    #: Member-level, straight from `iter_release_labels`'s own `on_skip`.
    not_a_member_zip: int = 0
    no_xml_member: int = 0
    several_xml_members: int = 0
    #: Document-level, all currently UNCOUNTED by `ScanResult`.
    doctype_refused: int = 0
    parse_error: int = 0
    no_set_id_in_tree: int = 0
    no_set_id_in_bytes: int = 0
    prefilter_disagreed: int = 0
    junk_version: int = 0
    absent_version: int = 0
    class_code_counts: Mapping[str, int] = field(default_factory=Counter)
    #: Present only on a whole-release census.
    by_part: Mapping[str, "Census"] = field(default_factory=dict)

    @property
    def unknown_class_codes(self) -> set[str]:
        return codes_outside_the_vocabulary(self.class_code_counts)


_COUNTER_FIELDS = (
    "documents_read", "not_a_member_zip", "no_xml_member", "several_xml_members",
    "doctype_refused", "parse_error", "no_set_id_in_tree", "no_set_id_in_bytes",
    "prefilter_disagreed", "junk_version", "absent_version",
)


def merge(censuses: Iterable[Census]) -> Census:
    """Add censuses together. Pure, so a release total is a fold over its parts."""
    totals = dict.fromkeys(_COUNTER_FIELDS, 0)
    codes: Counter[str] = Counter()
    for census in censuses:
        for name in _COUNTER_FIELDS:
            totals[name] += getattr(census, name)
        codes.update(census.class_code_counts)
    return Census(**totals, class_code_counts=codes)


def census_part(
    part_path: str, *, progress: Callable[[int], None] | None = None,
) -> Census:
    """Walk one release part through the SHIPPED reader and count every branch."""
    skips = {"not_a_member_zip": 0, "no_xml_member": 0, "several_xml_members": 0}

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
        if verdict.version_is_junk:
            totals["junk_version"] += 1
        elif verdict.version_raw is None and verdict.unreadable_reason is None:
            totals["absent_version"] += 1
        codes.update(verdict.class_code_counts)
        if progress is not None and totals["documents_read"] % 2000 == 0:
            progress(totals["documents_read"])

    return Census(**{**totals, **skips}, class_code_counts=codes)


def census_release(
    parts: Sequence[str], *, progress: Callable[[str, int], None] | None = None,
) -> Census:
    """Census every part, keeping the per-part breakdown beside the total."""
    by_part: dict[str, Census] = {}
    for part in parts:
        by_part[part] = census_part(
            part,
            progress=None if progress is None
            else (lambda read, p=part: progress(p, read)))
    return replace(merge(by_part.values()), by_part=by_part)


def _report(census: Census) -> str:
    """The measurement, in the shape the two decisions actually need."""
    lines = [
        "=== SPL reader skip census ===",
        f"documents_read                {census.documents_read:>9,}",
        "",
        "-- member-level (iter_release_labels) --",
        f"not_a_member_zip   REPORTED   {census.not_a_member_zip:>9,}",
        f"no_xml_member      *DROPS*    {census.no_xml_member:>9,}",
        f"several_xml_members *DROPS*   {census.several_xml_members:>9,}",
        "",
        "-- document-level (issue #162, all currently UNCOUNTED) --",
        f"prefilter_disagreed  (case 1) {census.prefilter_disagreed:>9,}",
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
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
        with open(args.json_out, "w") as handle:
            json.dump(payload, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
