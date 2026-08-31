# src/drugref/ingest/spl_release.py
"""Walk one DailyMed release's nested zips, and COUNT EVERY DOCUMENT DECLINED.

Split out of `spl_dailymed` (which reads ONE label's XML) because the two are
different jobs: this module never looks at a drug, and that one never opens a
zip. The split happened when issue #162's counters pushed the combined file past
the ~500-line guideline, and the seam was already there.

⇒ WHY THE COUNTERS ARE THE POINT. A document this module declines is republished
three stages later as `absent_from_dailymed` -- a fact about the READING sold as
a fact about the RELEASE, on the route whose population the design spec turns
into a commitment. `spl_checks.check_scan_dropped_nothing` refuses a run over
the drop counters, BEFORE the run row exists, so a release this reader cannot
handle leaves the previous projection standing.

MEASURED ON THE HUMAN Rx RELEASE OF 2026-08-21 (54,813 documents, all six parts;
`tools/spl_skip_census.py`, recorded in
docs/superpowers/specs/2026-08-31-drugref-spl-reader-skip-census.md):
**every counter in this module is ZERO** -- which is what lets *"the limit is the
release, not the reading"* be a measurement rather than an inference.
"""
from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass

from drugref.ingest.spl_dailymed import (
    SubjectUniis, dedupe_by_set_id, extract_subject_uniis,
    prefilter_is_trustworthy, set_id_in_bytes,
)

@dataclass(frozen=True, kw_only=True)
class ScanResult:
    """What one pass over the DailyMed release found, AND EVERY DOCUMENT IT DROPPED.

    **The drop counters are fields rather than local variables, and that is the
    whole point of this type.** A document silently skipped here is republished
    three stages later as `absent_from_dailymed` -- a fact about the READING sold
    as a fact about the RELEASE, and the design spec turns that route's population
    into a commitment. Measured on the 2026-08-21 Human Rx release, the four
    counters that existed at that run are ZERO, which is what lets "the limit is
    the release, not the reading" be a measurement rather than an inference.

    ⇒ **THE TWO ADDED BELOW ARE NOT YET MEASURED ON A REAL RELEASE**, and saying
    so is the whole point of this paragraph. They are folded into `total_dropped`,
    so `check_scan_dropped_nothing` refuses over them -- which means the first
    real run after this change may refuse where the previous one succeeded. That
    is the intended direction (a member zip this reader cannot read is a lost
    label, and the alternative was losing it silently), but it is an inference
    until a release has been scanned with them in place. Issue #162 carries the
    three remaining skips, which are NOT folded in for exactly this reason.

    ⇒ TWO OF THESE COUNTERS DID NOT EXIST, AND THE HOLE WAS EXACTLY THE ONE THIS
    TYPE'S FIRST PARAGRAPH DESCRIBES. `iter_release_labels` skipped an outer
    member that was not a zip, and an inner zip carrying no `.xml`, with a bare
    `continue` INSIDE THE GENERATOR -- before `documents_read` was incremented by
    the loop below, so neither `documents_read` nor any drop counter could see
    them, and `check_scan_dropped_nothing` was structurally incapable of
    refusing. "All counters measured zero" was a measurement over the documents
    that reached the counters.

    `found` is keyed by `set_id` and already de-duplicated by `dedupe_by_set_id`,
    because DailyMed ships successive versions of one label as separate documents
    sharing a `set_id` and counting rows over-counts labels.
    """

    documents_read: int
    found: Mapping[str, SubjectUniis]
    #: No `setId` in the raw bytes at all -- the cheap pre-filter found nothing.
    dropped_no_set_id_bytes: int
    #: Unparseable, or no `setId` in the parsed tree. A READING failure.
    dropped_unreadable: int
    #: The byte pre-filter matched a DIFFERENT setId than the document's own -- an
    #: SPL `<relatedDocument>` names the label it replaces, so writing the tree's
    #: value under a regex-selected target would attach a subject to the wrong
    #: wording.
    dropped_prefilter_disagreed: int
    #: An inner member zip holding no `.xml` at all. A label container this reader
    #: could not read: a DROP.
    dropped_no_xml_member: int
    #: An inner member zip holding SEVERAL `.xml` members. Also a drop, and NOT
    #: resolved by taking the first: which one `namelist()` returns first is zip
    #: member order, and this module's `dedupe_by_set_id` already argues at length
    #: that member order is not a rule. Reading the wrong one would attach a
    #: subject to the wrong wording silently, which is strictly worse than
    #: refusing.
    dropped_several_xml_members: int
    #: An outer member that is not a zip at all -- a release-level manifest or
    #: index, say. Counted and reported but NOT a drop, because such a member was
    #: never a label container and calling it a lost label would be the same
    #: reader-versus-release confusion in the other direction.
    skipped_not_a_member_zip: int
    #: ⇒ ISSUE #162 CASE 1. A document that is not a target BY A NAME THAT MAY
    #: NOT BE ITS OWN: `<relatedDocument>` preceded its `<setId>`, so the cheap
    #: pre-filter may have named the label being REPLACED. A DROP, because the
    #: alternative is filing a label drugref wanted as `absent_from_dailymed`.
    #: Measured ZERO on the 2026-08-21 release, at the outcome AND at the cause.
    dropped_untrustworthy_prefilter: int
    #: ⇒ ISSUE #162 CASE 2. `<versionNumber>` present and unparseable, which
    #: hands `dedupe_by_set_id` back the zip-member order it exists to refuse.
    #: 44 targeted labels ship several versions, so the WRONG subject can attach
    #: -- and attaching it silently is strictly worse than refusing. Measured
    #: ZERO; so is the count of labels carrying no version element at all.
    dropped_junk_version: int
    #: NOTE ON ALL THREE COUNTERS BELOW AND ABOVE: they count DOCUMENTS, not
    #: labels. `found` is de-duplicated by `set_id` afterwards, and DailyMed
    #: ships successive versions of one label as separate documents -- counting
    #: rows as labels is how 6,583 was published where 6,539 existed.
    #: ⇒ ISSUE #162 CASE 3, KEYED ON THE CONDITION THAT HARMS. An ingredient
    #: whose classCode is in neither vocabulary AND which carries a UNII: only
    #: such an ingredient could have contributed a subject, so only such a one
    #: can have cost the label anything. A future ACTIVE code looks exactly like
    #: this. Measured ZERO.
    dropped_unknown_class_code_unii: int
    #: The same unknown code carrying NO UNII. **Reported, NOT a drop, and the
    #: measurement is why**: the release carries `COLR` ten times, so folding
    #: this into `total_dropped` as issue #162 proposed would have aborted the
    #: ingest on the very release the fix was measured against.
    skipped_unknown_class_code: int

    @property
    def total_dropped(self) -> int:
        return (self.dropped_no_set_id_bytes + self.dropped_unreadable
                + self.dropped_prefilter_disagreed
                + self.dropped_no_xml_member + self.dropped_several_xml_members
                + self.dropped_untrustworthy_prefilter
                + self.dropped_junk_version
                + self.dropped_unknown_class_code_unii)


def describe_reported_skips(scan: ScanResult) -> str:
    """The counters that do NOT refuse, in one line -- or `""` when there are none.

    ⇒ A COUNTER NOBODY REPORTS IS A SILENT SKIP WITH EXTRA STEPS. The drop
    counters reach an operator as an exception carrying a full breakdown, so
    they are the loud half by construction. The reported ones had no reader at
    all: `skipped_not_a_member_zip` has been documented as *"counted and
    reported"* since it was added and was reported nowhere, and
    `skipped_unknown_class_code` would have inherited exactly that -- which
    would have made admitting `COLR` to the vocabulary a way of hiding it rather
    than of ruling on it.

    Empty when there is nothing to say, because a line of zeroes printed on
    every run is a line nobody reads.
    """
    parts = []
    if scan.skipped_unknown_class_code:
        parts.append(
            f"{scan.skipped_unknown_class_code:,} document(s) carrying an "
            "unrecognised HL7 classCode with no UNII under it")
    if scan.skipped_not_a_member_zip:
        parts.append(
            f"{scan.skipped_not_a_member_zip:,} release member(s) that were "
            "never label containers")
    if not parts:
        return ""
    return "reported and NOT refused: " + "; ".join(parts)


def scan_release(
    parts: Sequence[str], targets: AbstractSet[str], *,
    progress: Callable[[str], None] | None = None,
) -> ScanResult:
    """Read the release once, keeping only the labels `targets` names.

    THE EXPENSIVE PASS: 17.6 GB of nested zips. It is done ONCE, and the cheap
    byte pre-filter runs before any tree is built, because building a tree for
    every document to discover most are unwanted costs far more than one regex.

    **The pre-filter is never the authority.** `extract_subject_uniis` re-reads the
    `setId` from the tree and the two are compared here; a disagreement is counted
    and the document dropped rather than filed under the target the regex picked.

    The reader's own skips -- members it never opened -- are counted through
    `iter_release_labels`'s `on_skip`, so `ScanResult` accounts for every member
    of every part rather than only for the documents that reached this loop.

    ⇒ AND A NON-TARGET IS NO LONGER A FREE SKIP. Issue #162 case 1: a document
    whose pre-filtered name is not wanted may have been named by a
    `<relatedDocument>` rather than by itself, so `prefilter_is_trustworthy`
    qualifies every such skip in the bytes already in memory. The three
    document-level counters added with it (cases 1-3) are scoped to the
    documents this loop actually reads a subject from -- NOT to the release.
    """
    found: list[SubjectUniis] = []
    documents_read = 0
    no_set_id_bytes = unreadable = disagreed = 0
    untrustworthy = junk_version = unknown_code_unii = unknown_code = 0
    skips = {"not_a_member_zip": 0, "no_xml_member": 0, "several_xml_members": 0}

    def note_skip(_member: str, reason: str) -> None:
        skips[reason] += 1

    for part in parts:
        if progress is not None:
            progress(str(part))
        for _document_id, xml_bytes in iter_release_labels(part, on_skip=note_skip):
            documents_read += 1
            pre_filter = set_id_in_bytes(xml_bytes)
            if pre_filter is None:
                no_set_id_bytes += 1
                continue
            if pre_filter not in targets:
                # NOT a plain skip: the name it was judged by may not be its
                # own. Issue #162 case 1 -- see `prefilter_is_trustworthy`.
                if not prefilter_is_trustworthy(xml_bytes):
                    untrustworthy += 1
                continue
            recovered = extract_subject_uniis(xml_bytes)
            if recovered is None:
                unreadable += 1
                continue
            if recovered.set_id != pre_filter:
                disagreed += 1
                continue
            # Counted for every TARGETED DOCUMENT -- the population whose
            # subject this scan actually reads. The skip census's figures are
            # RELEASE-WIDE and over documents that were never targeted too, so
            # the two are not the same number and must not be read as a check on
            # one another.
            if recovered.version_was_unreadable:
                junk_version += 1
            if recovered.unknown_class_code_uniis:
                unknown_code_unii += 1
            elif recovered.unknown_class_codes:
                unknown_code += 1
            found.append(recovered)

    return ScanResult(
        documents_read=documents_read,
        found=dedupe_by_set_id(found),
        dropped_no_set_id_bytes=no_set_id_bytes,
        dropped_unreadable=unreadable,
        dropped_prefilter_disagreed=disagreed,
        dropped_no_xml_member=skips["no_xml_member"],
        dropped_several_xml_members=skips["several_xml_members"],
        skipped_not_a_member_zip=skips["not_a_member_zip"],
        dropped_untrustworthy_prefilter=untrustworthy,
        dropped_junk_version=junk_version,
        dropped_unknown_class_code_unii=unknown_code_unii,
        skipped_unknown_class_code=unknown_code)


def iter_release_labels(
    part_path: str, *, limit: int | None = None,
    on_skip: Callable[[str, str], None] | None = None,
) -> Iterator[tuple[str, bytes]]:
    """Yield `(document_id, xml_bytes)` for every label in one release part.

    Each outer member is itself a zip holding the XML plus the label's images;
    only the SOLE `.xml` member is read, so the images never leave the archive --
    which is both faster and the only part of the payload rule 6 has an opinion
    about.

    **`on_skip(member_name, reason)` IS HOW A SKIP BECOMES VISIBLE.** Every
    branch below that declines a member calls it, because a `continue` here is
    upstream of `scan_release`'s counters: a member dropped silently at this
    level is invisible to `documents_read` AND to every field of `ScanResult`,
    and reappears three stages later as `absent_from_dailymed`. `reason` is one
    of `not_a_member_zip`, `no_xml_member`, `several_xml_members`.
    """
    def skip(member: str, reason: str) -> None:
        if on_skip is not None:
            on_skip(member, reason)

    seen = 0
    with zipfile.ZipFile(part_path) as outer:
        for name in outer.namelist():
            if not name.endswith(".zip"):
                skip(name, "not_a_member_zip")
                continue
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                xml_names = [n for n in inner.namelist() if n.endswith(".xml")]
                if not xml_names:
                    skip(name, "no_xml_member")
                    continue
                if len(xml_names) > 1:
                    # NOT `xml_names[0]`: see ScanResult.dropped_several_xml
                    # _members. Picking by member order is the defect this
                    # module refuses everywhere else.
                    skip(name, "several_xml_members")
                    continue
                yield name, inner.read(xml_names[0])
            seen += 1
            if limit is not None and seen >= limit:
                return
