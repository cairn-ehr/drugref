"""Pure parser for LactMed BITS/JATS records used by the source spike.

The parser deliberately stops at source-native evidence sections. It does not assign
clinical recommendations, severities, or alert behavior. Archive retrieval, identity
resolution, and reporting belong to the spike runner rather than this module.
"""
from __future__ import annotations

import re
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO
from xml.etree import ElementTree

from drugref.ingest.checksum import StrPath


CAS_PATTERN = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
UNII_PATTERN = re.compile(r"\bUNII-([A-Z0-9]{10})\b", re.I)

SECTION_KINDS = {
    "Summary of Use during Lactation": "lactmed_summary",
    "Effects in Breastfed Infants": "infant_effect",
    "Effects on Lactation and Breastmilk": "lactation_effect",
    "Alternate Drugs to Consider": "alternative_medicine",
    "Alternative Drugs to Consider": "alternative_medicine",
}


@dataclass(frozen=True)
class LactMedSection:
    """One source-native evidence section, without a derived clinical judgment."""

    evidence_kind: str
    title: str
    text: str
    reference_ids: tuple[str, ...]


@dataclass(frozen=True)
class LactMedRecord:
    """The auditable identity, rights, and useful sections of one LactMed record."""

    record_id: str
    title: str
    revised: str | None
    publisher: str
    rights: str
    disclaimer: str
    keywords: tuple[str, ...]
    cas_numbers: tuple[str, ...]
    uniis: tuple[str, ...]
    sections: tuple[LactMedSection, ...]


@dataclass(frozen=True)
class ArchivedRecord:
    """A parsed record together with the archive member that supplied it."""

    member_name: str
    record: LactMedRecord


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _narrative_text(element: ElementTree.Element) -> str:
    """Flatten prose while excluding rendered bibliography callout numbers."""

    parts: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            if not (child.tag == "xref" and child.get("ref-type") == "bibr"):
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return " ".join("".join(parts).split())


def _references(element: ElementTree.Element) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        xref.get("rid", "")
        for xref in element.findall(".//xref[@ref-type='bibr']")
        if xref.get("rid")))


def _revision(root: ElementTree.Element) -> str | None:
    date = root.find(".//book-part-meta/pub-history/date[@date-type='revised']")
    if date is None:
        return None
    try:
        year = int(_text(date.find("year")))
        month = int(_text(date.find("month")))
        day = int(_text(date.find("day")))
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (TypeError, ValueError):
        return None


def _regular_section(sec: ElementTree.Element, kind: str) -> LactMedSection:
    title = _text(sec.find("title"))
    paragraphs = [_narrative_text(p) for p in sec.findall("./p")]
    text = " ".join(part for part in paragraphs if part)
    return LactMedSection(kind, title, text, _references(sec))


def _drug_level_sections(sec: ElementTree.Element) -> list[LactMedSection]:
    buckets: dict[str, list[str]] = {
        "drug_levels_context": [],
        "maternal_level": [],
        "infant_level": [],
    }
    current = "drug_levels_context"
    refs: dict[str, list[str]] = {kind: [] for kind in buckets}
    for paragraph in sec.findall("./p"):
        text = _narrative_text(paragraph)
        folded = text.casefold()
        if folded.startswith("maternal levels."):
            current = "maternal_level"
        elif folded.startswith("infant levels."):
            current = "infant_level"
        if text:
            buckets[current].append(text)
            refs[current].extend(_references(paragraph))

    title = _text(sec.find("title"))
    return [
        LactMedSection(
            kind,
            title,
            " ".join(parts),
            tuple(dict.fromkeys(refs[kind])),
        )
        for kind, parts in buckets.items()
        if parts
    ]


def parse_record(stream: BinaryIO) -> LactMedRecord:
    """Parse one LactMed ``.nxml`` stream without reading another source."""

    root = ElementTree.parse(stream).getroot()
    record_id = _text(root.find(".//book-part-id[@book-part-id-type='pmcid']"))
    title = _text(root.find(".//book-part-meta/title-group/title"))
    publisher = _text(root.find("./book-meta/publisher/publisher-name"))
    rights = _text(root.find("./book-meta/notes[@notes-type='rights']"))
    disclaimer = _text(root.find("./book-meta/notes[@notes-type='disclaimer']"))
    keywords = tuple(
        value for keyword in root.findall(".//book-part-meta/kwd-group/kwd")
        if (value := _text(keyword)))

    cas_values: list[str] = []
    for sec in root.findall(".//sec"):
        if _text(sec.find("title")) == "CAS Registry Number":
            cas_values.extend(CAS_PATTERN.findall(_text(sec)))
    if not cas_values:
        cas_values.extend(CAS_PATTERN.findall(" ".join(keywords)))
    uniis = UNII_PATTERN.findall(" ".join(keywords))

    sections: list[LactMedSection] = []
    for sec in root.findall(".//sec"):
        section_title = _text(sec.find("title"))
        if section_title == "Drug Levels":
            sections.extend(_drug_level_sections(sec))
        elif kind := SECTION_KINDS.get(section_title):
            sections.append(_regular_section(sec, kind))

    return LactMedRecord(
        record_id=record_id,
        title=title,
        revised=_revision(root),
        publisher=publisher,
        rights=rights,
        disclaimer=disclaimer,
        keywords=keywords,
        cas_numbers=tuple(dict.fromkeys(cas_values)),
        uniis=tuple(dict.fromkeys(value.upper() for value in uniis)),
        sections=tuple(sections),
    )


def is_evidence_record(record: LactMedRecord) -> bool:
    """Return whether an archive member has LactMed's clinical summary section."""

    return any(
        section.evidence_kind == "lactmed_summary"
        for section in record.sections
    )


def iter_archive(path: StrPath) -> Iterator[ArchivedRecord]:
    """Yield every ``.nxml`` member; PDFs and images never enter the parser."""

    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".nxml"):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            with stream:
                yield ArchivedRecord(member.name, parse_record(stream))
