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
**every DROP counter in this module is ZERO** -- which is what lets *"the limit is
the release, not the reading"* be a measurement rather than an inference.

⇒ THE TWO REPORTED COUNTERS ARE NOT ZERO, AND MERGING THEM WITH THAT SENTENCE IS
THE MISTAKE THIS SLICE KEEPS MAKING. `skipped_unknown_class_code` is zero for the
shipped run's TARGET SET, while the census counts `COLR` ten times RELEASE-WIDE:
two populations, two figures, neither a check on the other. The spec's §7 exists
because a previous round read one as the other.
"""
from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, fields

from drugref.ingest.spl_dailymed import (
    SubjectUniis, dedupe_by_set_id, extract_subject_uniis,
    prefilter_is_trustworthy, set_id_in_bytes,
)

#: Every reason `iter_release_labels` can decline a member, in ONE place.
#:
#: `scan_release` builds its tally with `dict.fromkeys(SKIP_REASONS, 0)` and
#: indexes it directly, so a reason added here without a home in `ScanResult`
#: fails loudly at the call site, and a reason passed to `on_skip` that is not
#: here raises `KeyError` rather than evaporating. `tools/spl_skip_census.py`
#: reads the same tuple.
SKIP_REASONS = (
    "not_a_member_zip", "no_xml_member", "several_xml_members",
    "unreadable_member_zip",
)


@dataclass(frozen=True, kw_only=True)
class ScanResult:
    """What one pass over the DailyMed release found, AND EVERY DOCUMENT IT DROPPED.

    **The drop counters are fields rather than local variables, and that is the
    whole point of this type.** A document silently skipped here is republished
    three stages later as `absent_from_dailymed` -- a fact about the READING sold
    as a fact about the RELEASE, and the design spec turns that route's population
    into a commitment. Measured on the 2026-08-21 Human Rx release, the THREE drop
    counters that existed at that run are ZERO, which is what lets "the limit is
    the release, not the reading" be a measurement rather than an inference. (It
    said "four" in three files: the number came from a commit message counting
    CONDITIONS, and `dropped_unreadable` folds two of them into one field.)

    ⇒ **THE NAME OF EACH COUNTER IS ITS VERDICT, AND `total_dropped` READS IT.**
    A field named `dropped_*` refuses the run; one named `skipped_*` is reported
    and never refuses. `total_dropped` sums by that prefix rather than by a
    hand-written list of terms, because the list was the failure mode: a twelfth
    counter added without a matching line in the sum would refuse nothing, and
    nothing would notice. `test_every_counter_DECLARES_its_verdict_in_its_name`
    refuses a counter that claims neither.

    ⇒ AT MOST ONE COUNTER MOVES PER DOCUMENT, AND A DROPPED DOCUMENT NEVER
    REACHES `found`. Every drop branch in `scan_release` `continue`s, so
    `total_dropped <= documents_read` holds and the guard's "dropped N
    document(s)" is a count of documents. An earlier draft let the three
    document-level counters fall through: one document tripping two of them was
    reported as two drops AND kept as a result.

    ⇒ THREE OF THESE COUNTERS DID NOT EXIST, AND THE HOLE WAS EXACTLY THE ONE
    THIS TYPE'S FIRST PARAGRAPH DESCRIBES. `iter_release_labels` skipped an outer
    member that was not a zip, and an inner zip carrying no `.xml`, with a bare
    `continue` INSIDE THE GENERATOR -- before `documents_read` was incremented by
    `scan_release`, so neither `documents_read` nor any drop counter could see
    them, and `check_scan_dropped_nothing` was structurally incapable of
    refusing. "All counters measured zero" was a measurement over the documents
    that reached the counters. `dropped_unreadable_member_zip` is the third: a
    member whose BYTES are not a readable zip raised out of the generator with no
    counter and no member name at all.

    `found` is keyed by `set_id` and already de-duplicated by `dedupe_by_set_id`,
    because DailyMed ships successive versions of one label as separate documents
    sharing a `set_id` and counting rows over-counts labels.

    ⇒ EVERY COUNTER BELOW COUNTS DOCUMENTS (or members), NEVER LABELS. Counting
    rows as labels is how 6,583 was published where 6,539 existed.
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
    #: An outer member whose BYTES ARE a zip, but which this reader could not
    #: open or read -- a truncated or CRC-broken container, say. A DROP on
    #: `dropped_no_xml_member`'s own reasoning: a label container this reader
    #: cannot read is a lost label. It previously raised `BadZipFile` out of the
    #: generator, naming no member and moving no counter.
    dropped_unreadable_member_zip: int
    #: An outer member that is not a zip at all -- a release-level manifest or
    #: index, say. Counted and reported but NOT a drop, because such a member was
    #: never a label container and calling it a lost label would be the same
    #: reader-versus-release confusion in the other direction. **Decided on the
    #: BYTES, not on the name**: a member called `M.ZIP` holding a real label was
    #: filed here by a `.zip` suffix test, and being the one member bucket that
    #: does not refuse, it lost that label without refusing the run.
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
    #: ⇒ ISSUE #162 CASE 3, KEYED ON THE CONDITION THAT HARMS. A document
    #: carrying an ingredient whose classCode is in neither vocabulary AND which
    #: carries a UNII: only such an ingredient could have contributed a subject,
    #: so only such a one can have cost the label anything. A future ACTIVE code
    #: looks exactly like this. Measured ZERO.
    dropped_unknown_class_code_unii: int
    #: A document carrying an unrecognised classCode, none of them under a UNII.
    #: **Reported, NOT a drop, and the measurement is why**: the release carries
    #: `COLR` ten times, so folding this into `total_dropped` as issue #162
    #: proposed would have aborted the ingest on the very release the fix was
    #: measured against.
    skipped_unknown_class_code: int
    #: ⇒ THE CODES THEMSELVES, because a count cannot name what a human has to
    #: rule on. Both counters above are document counts; this is the union of the
    #: codes behind them, and it is what reaches the operator through
    #: `describe_reported_skips` and the refusal message. Without it, "1 document
    #: carrying an unrecognised classCode" is a message ending "fix the reader"
    #: that hands over nothing to fix it with.
    unknown_class_codes: frozenset[str]

    @property
    def total_dropped(self) -> int:
        """Every `dropped_` counter, BY NAME.

        The prefix is the verdict, so a counter added to this class joins the
        sum by existing rather than by also being remembered here -- see the
        class docstring, and `test_every_counter_DECLARES_its_verdict_in_its_name`.
        """
        return sum(getattr(self, field.name) for field in fields(self)
                   if field.name.startswith("dropped_"))


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
            "unrecognised HL7 classCode with no UNII under it "
            f"({name_the_codes(scan.unknown_class_codes)})")
    if scan.skipped_not_a_member_zip:
        parts.append(
            f"{scan.skipped_not_a_member_zip:,} release member(s) that were "
            "never label containers")
    if not parts:
        return ""
    return "reported and NOT refused: " + "; ".join(parts)


def name_the_codes(codes: AbstractSet[str]) -> str:
    """The unknown classCodes, sorted, with a missing attribute spelled `<none>`.

    ⇒ THE COUNT IS NOT THE MESSAGE. Case 3 is reported rather than refused
    precisely so a human can rule on the code -- which requires knowing which
    code it is. An `<ingredient>` carrying no `classCode` attribute at all is a
    real shape and reads as `""`, so it is spelled rather than printed as a gap.
    """
    return ", ".join(sorted(code or "<none>" for code in codes)) or "none named"


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
    qualifies every such skip in the bytes already in memory.

    ⇒ THE THREE DOCUMENT-LEVEL COUNTERS DO NOT SHARE ONE POPULATION, and reading
    them as if they did is how a figure gets published against the wrong
    denominator. **Case 1 counts NON-TARGETS** -- it is incremented inside the
    `pre_filter not in targets` branch, over the ~44,000 documents per release
    this loop never reads a subject from. **Cases 2 and 3 count TARGETED
    documents**, past the target/readable/agreeing gauntlet. Neither population is
    the census's, which is release-wide over all 54,813.
    """
    found: list[SubjectUniis] = []
    documents_read = 0
    no_set_id_bytes = unreadable = disagreed = 0
    untrustworthy = junk_version = unknown_code_unii = unknown_code = 0
    unknown_codes: set[str] = set()
    skips = dict.fromkeys(SKIP_REASONS, 0)

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
            unknown_codes |= set(recovered.unknown_class_codes)
            # EVERY DROP `continue`s, so one document moves at most one counter
            # and never reaches `found`. Falling through would report a document
            # tripping two conditions as two drops AND keep it as a result.
            if recovered.version_was_unreadable:
                junk_version += 1
                continue
            if recovered.unknown_class_code_uniis:
                unknown_code_unii += 1
                continue
            if recovered.unknown_class_codes:
                # REPORTED, not dropped -- so this one falls through and the
                # document is kept. `COLR` is why; see the field's own comment.
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
        dropped_unreadable_member_zip=skips["unreadable_member_zip"],
        skipped_not_a_member_zip=skips["not_a_member_zip"],
        dropped_untrustworthy_prefilter=untrustworthy,
        dropped_junk_version=junk_version,
        dropped_unknown_class_code_unii=unknown_code_unii,
        skipped_unknown_class_code=unknown_code,
        unknown_class_codes=frozenset(unknown_codes))


def iter_release_labels(
    part_path: str, *, on_skip: Callable[[str, str], None],
) -> Iterator[tuple[str, bytes]]:
    """Yield `(document_id, xml_bytes)` for every label in one release part.

    Each outer member is itself a zip holding the XML plus the label's images;
    only the SOLE `.xml` member is read, so the images never leave the archive --
    which is both faster and the only part of the payload rule 6 has an opinion
    about.

    **`on_skip(member_name, reason)` IS HOW A SKIP BECOMES VISIBLE**, and it is
    REQUIRED rather than defaulted for that reason: a `continue` here is upstream
    of `scan_release`'s counters, so a member dropped silently at this level is
    invisible to `documents_read` AND to every field of `ScanResult`, and
    reappears three stages later as `absent_from_dailymed`. A caller that truly
    does not care passes an explicit discarder, which is a decision in the source
    rather than an omission. `reason` is one of `SKIP_REASONS`.

    ⇒ A MEMBER IS A LABEL CONTAINER BY ITS BYTES, NEVER BY ITS NAME. The suffix
    test this used to apply (`name.endswith(".zip")`) filed a member called
    `M.ZIP` under `not_a_member_zip` -- the one member bucket that does NOT
    refuse the run -- so a real label was republished as `absent_from_dailymed`
    with every counter reading clean. The `.xml` search inside is
    case-insensitive for the same reason.
    """
    def skip(member: str, reason: str) -> None:
        on_skip(member, reason)

    with zipfile.ZipFile(part_path) as outer:
        for name in outer.namelist():
            if name.endswith("/"):
                # A directory entry, not a member: it was never a container and
                # is not a skip either, so reporting it would pad the count of
                # members that "were never label containers".
                continue
            try:
                member_bytes = outer.read(name)
            except (zipfile.BadZipFile, RuntimeError, EOFError, OSError):
                # A CRC failure, or a member encrypted with a password we do not
                # have. Previously raised out of the generator naming nothing.
                skip(name, "unreadable_member_zip")
                continue
            if not zipfile.is_zipfile(io.BytesIO(member_bytes)):
                # `is_zipfile` rather than catching `BadZipFile` from the open
                # below, because the two outcomes need DIFFERENT verdicts: a
                # member that was never a zip is a manifest (reported), and one
                # that is a zip but unreadable is a lost label (a drop). It costs
                # a second read of the member's central directory -- microseconds
                # against the 163 s the release takes to walk.
                skip(name, "not_a_member_zip")
                continue
            try:
                xml_bytes = _sole_xml_of(member_bytes, name, skip)
            except (zipfile.BadZipFile, RuntimeError, EOFError, OSError):
                skip(name, "unreadable_member_zip")
                continue
            if xml_bytes is not None:
                yield name, xml_bytes


def _sole_xml_of(
    member_bytes: bytes, name: str, skip: Callable[[str, str], None],
) -> bytes | None:
    """The one `.xml` in a member zip, or `None` having reported why not."""
    with zipfile.ZipFile(io.BytesIO(member_bytes)) as inner:
        xml_names = [n for n in inner.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            skip(name, "no_xml_member")
            return None
        if len(xml_names) > 1:
            # NOT `xml_names[0]`: see ScanResult.dropped_several_xml_members.
            # Picking by member order is the defect this module refuses
            # everywhere else.
            skip(name, "several_xml_members")
            return None
        return inner.read(xml_names[0])
