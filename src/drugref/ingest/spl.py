# src/drugref/ingest/spl.py
"""Read openFDA drug-label records: identity, subject UNIIs, and the wording.

PURE AND STREAMING, per the architecture invariant -- no database access, no
resolution, no clinical judgement. It answers one question per record: does this
label carry SPL section `34073-7` DRUG INTERACTIONS, and if so, what is its
identity, what does openFDA say its subject drug is, and what is the wording's
de-duplication key?

Design: docs/superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md

WHY openFDA AND NOT DailyMed for this half. openFDA's bulk `drug/label` export
carries the section pre-split as a `drug_interactions` field under an explicit
**CC0 1.0** dedication, at 1.73 GB. DailyMed publishes the same labels as 17.6 GB
of nested zips needing LOINC section splitting, under NLM's *"cannot guarantee
the copyright status"* disclaimer. Both are read by this slice -- DailyMed is
`spl_dailymed.py`'s job and exists to recover the subject openFDA leaves empty --
but the section text itself comes from here.

FOUR SHAPES OF THE SOURCE THAT ARE MEASURED, NOT ASSUMED (2026-08-27, over all
262,032 records of the 2026-08-22 export):

* `drug_interactions` is a **list of strings**, not a string. Reading `[0]` drops
  text, and the dropped part is exactly where a second interaction statement
  sits.
* **68,550 labels carry the section**, and they carry **27,406 distinct
  wordings** -- 2.50 labels to one, because the corpus is dominated by generic
  labels reprinting one manufacturer's words. Every rate in this slice is quoted
  per WORDING for that reason.
* `set_id`, `version` and `effective_time` are populated on **100%** of those
  68,550, `(set_id, version)` never repeats, and on this export `set_id` alone
  never repeats either -- openFDA ships the CURRENT version of a label only.
  (DailyMed does not: it ships successive versions sharing a `set_id`, which is
  why `spl_dailymed.dedupe_by_set_id` exists and this module needs no such rule.)
* the normalising `openfda` block is **PRESENT and EMPTY** on 100% of the labels
  that carry no UNII, so a presence check reports full coverage of a population
  where nothing is keyed.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

#: The authority, as `ingest_run.source` spells it. Paired with the CHECK db/051
#: widens and with `ids._SOURCE_CANONICAL` -- see that module on why a source
#: spelled in only two of the three places fails SILENTLY.
SOURCE = "SPL"

#: The orchestrator, as `ingest_run.writer` spells it. Same trio.
WRITER = "spl_run"

#: The openFDA field carrying SPL section 34073-7. Named rather than inlined
#: because the LOINC code and openFDA's field name are two spellings of one
#: thing, and `spl_dailymed` keys on the code while this module keys on the name.
INTERACTIONS_FIELD = "drug_interactions"

_WHITESPACE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    """Collapse every whitespace run to one space and strip the ends.

    REFORMATTING IS NOT A NEW STATEMENT. Two labels carrying identical wording
    under different line-wrapping would otherwise count as two distinct texts and
    overstate the corpus by the difference.

    This is also **the string every offset in this slice indexes**. Occurrences,
    quote windows and `char_length` all refer to the normalised text, so a stored
    `char_start` cuts the same characters out that the matcher saw. Storing
    offsets against the raw text and quoting against the normalised one is the
    silent way to hand a reader the wrong words.
    """
    return _WHITESPACE.sub(" ", text).strip()


def section_key(text: str) -> str:
    """A stable identity for one section's wording: SHA-256 of the normal form.

    Hex, lower-case, 64 characters -- `db/051`'s `spl_wording_key_shape` CHECK
    pins exactly that, so a producer that changed digest would be refused at the
    table rather than quietly filling it with keys nothing joins to.
    """
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LabelSection:
    """One label's DRUG INTERACTIONS section, with its identity and provenance.

    `uniis` is the bridge to drugref's registry: `moiety_uuid` is UUIDv5 on UNII,
    so a populated `openfda.unii` resolves the SUBJECT drug with no string
    matching at all. An **empty tuple means openFDA offers no subject** -- which
    is 59.6% of the corpus, and what `spl_dailymed.py` exists to recover.

    `text` is the raw joined section; `normalised_text` is what everything else
    in the slice indexes. Both are kept because the raw form is what was read and
    the normal form is what is measured, and conflating them is how offsets go
    wrong by a variable amount nobody can reconstruct.
    """

    set_id: str
    version: str | None
    effective_time: str | None
    product_type: str | None
    uniis: tuple[str, ...]
    text: str

    @property
    def normalised_text(self) -> str:
        """The string every offset in this slice indexes. See `normalise_text`."""
        return normalise_text(self.text)

    @property
    def char_length(self) -> int:
        """The denominator the 25% quote budget is spent against."""
        return len(self.normalised_text)

    @property
    def text_key(self) -> str:
        """This section's de-duplication identity."""
        return section_key(self.text)


def extract_section(
    record: Mapping, *, field_name: str = INTERACTIONS_FIELD
) -> LabelSection | None:
    """Pull one label's section out of an openFDA record, or `None`.

    `None` means "this label does not carry the section" and covers a missing
    field, a null, an empty list, and a list whose entries are all blank.

    **Blank-but-present is folded into absent deliberately.** A zero-entity row
    left in the yield denominator depresses every rate derived from it, which is
    how a corpus census lies without any single figure being wrong.

    Every part of the list is joined, never just `[0]`: openFDA splits one
    label's section into several strings and the later ones carry real
    interaction statements.
    """
    parts = record.get(field_name) or []
    if isinstance(parts, str):
        parts = [parts]
    text = "\n\n".join(part for part in parts if part and part.strip())
    if not text.strip():
        return None

    # `or {}` twice, not once: openFDA ships the block as an explicit null on
    # some records and as `{}` on others, and both mean "no identity offered".
    openfda = record.get("openfda") or {}
    product_types = openfda.get("product_type") or []
    return LabelSection(
        # `id` is openFDA's per-document key and `set_id` the label's; the
        # fallback exists because a record missing `set_id` still has to be
        # counted somewhere rather than silently keyed on the empty string.
        set_id=record.get("set_id") or record.get("id") or "",
        version=record.get("version"),
        effective_time=record.get("effective_time"),
        # ONE product type, not the list: measured, the field is single-valued
        # wherever it is populated, and it is NULLABLE downstream because it is
        # absent on most records -- absence is a population, not a bug.
        product_type=product_types[0] if product_types else None,
        # Sorted and de-duplicated so two reads of one record agree. A
        # combination product legitimately carries several.
        uniis=tuple(sorted(set(openfda.get("unii") or []))),
        text=text,
    )


def iter_partition_records(path: str | pathlib.Path) -> Iterator[dict]:
    """Yield every record from one openFDA `*.json.zip` partition.

    Each partition is a single JSON document holding 20,000 records under
    `results`. At ~633 MB uncompressed that is comfortably loadable one partition
    at a time, so this deliberately does NOT pull in a streaming JSON parser --
    one fewer dependency to licence-check (CLAUDE.md rule 6) for a gain the
    measurement says is not needed.
    """
    with zipfile.ZipFile(path) as archive:
        (member,) = archive.namelist()
        with archive.open(member) as handle:
            document = json.load(handle)
    yield from document.get("results", [])


def iter_sections(
    partitions: Sequence[str | pathlib.Path],
) -> Iterator[tuple[int, LabelSection | None]]:
    """Stream `(record_number, section-or-None)` across every partition in order.

    The record count is yielded alongside because the DENOMINATOR is a published
    figure: "68,550 of 262,032" says something "68,550" alone does not, and a
    reader that silently skipped a partition would look identical to one that
    read them all.
    """
    seen = 0
    for partition in partitions:
        for record in iter_partition_records(partition):
            seen += 1
            yield seen, extract_section(record)


def check_something_was_read(
    sections: Sequence[LabelSection], *, records: int
) -> None:
    """Refuse a corpus in which no label carries the section.

    `db/050`'s lesson, taken before the review round instead of during it: an
    ingest that would publish nothing must not first CLEAR what the last one
    published. A renamed field, a changed export layout or a wrong `--downloads`
    path all present as "read the corpus, found no sections", and the difference
    between that and a genuinely empty release is not something this code can
    tell -- so it refuses both and makes an operator say which.

    Raised BEFORE the run row is opened, so a refusal leaves the database exactly
    as it was.
    """
    if not sections:
        raise ValueError(
            f"SPL: no label carries section 34073-7 across {records:,} record(s). "
            "Refusing to clear the existing projection and rebuild it empty -- "
            "check the --downloads path and that openFDA still publishes a "
            f"{INTERACTIONS_FIELD!r} field.")
