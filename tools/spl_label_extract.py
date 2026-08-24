"""Read openFDA drug-label records and account for what came out.

**Throwaway spike code for the slice 5c.3 measurement round.**

openFDA's ``drug/label`` export carries section 34073-7 DRUG INTERACTIONS
pre-split as a ``drug_interactions`` field, so this module does no XML or LOINC
work at all -- that is the whole reason the corpus decision moved off DailyMed's
18 GB of nested zips. What it must handle instead is openFDA's own shape:

* ``drug_interactions`` is a **list of strings**, not a string. Taking ``[0]``
  drops text, and the dropped part is exactly where a second interaction
  statement sits.
* The normalising ``openfda`` block -- ``unii``, ``product_type`` -- is
  **absent on 40,441 of the 68,595 section-carrying labels**. That is a measured
  population, not an error.
* The corpus is dominated by generic labels repeating one manufacturer's
  wording: a single UNII appears on up to 498 separate labels. Every rate
  derived from label counts is therefore wrong until the text de-duplication
  factor is divided out, which is what ``section_key`` exists for.

``Census`` refuses to exist unless its parts add up, because the failure it
guards against has already happened here once: the source evaluation's second
sampling attempt published a tally accounting for only 40 of its 50 labels.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class LabelSection:
    """One label's DRUG INTERACTIONS section, with its identity and provenance.

    ``uniis`` is the bridge to drugref's registry: ``moiety_uuid`` is UUIDv5 on
    UNII, so a populated ``unii`` resolves the SUBJECT drug without any string
    matching at all. An empty tuple means the label carries no identity block
    and its subject has to be recovered some other way (or counted as a gap).
    """

    set_id: str
    version: str | None
    effective_time: str | None
    product_type: str | None
    uniis: tuple[str, ...]
    text: str

    @property
    def text_key(self) -> str:
        """This section's de-duplication identity."""
        return section_key(self.text)


def normalise_text(text: str) -> str:
    """Collapse every whitespace run to one space and strip the ends.

    Reformatting is not a new statement. Without this, two labels carrying
    identical wording under different line-wrapping would count as two distinct
    texts and overstate the corpus.
    """
    return _WHITESPACE.sub(" ", text).strip()


def section_key(text: str) -> str:
    """A stable identity for one section's wording."""
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def extract_section(
    record: Mapping, *, field_name: str = "drug_interactions"
) -> LabelSection | None:
    """Pull one label's section out of an openFDA record, or ``None``.

    ``None`` means "this label does not carry the section" and covers a missing
    field, a null, an empty list, and a list whose entries are all blank.
    Blank-but-present is folded into absent deliberately: a zero-entity row in
    the yield denominator quietly depresses every rate derived from it.
    """
    parts = record.get(field_name) or []
    if isinstance(parts, str):
        parts = [parts]
    text = "\n\n".join(p for p in parts if p and p.strip())
    if not text.strip():
        return None

    openfda = record.get("openfda") or {}
    product_types = openfda.get("product_type") or []
    return LabelSection(
        set_id=record.get("set_id") or record.get("id") or "",
        version=record.get("version"),
        effective_time=record.get("effective_time"),
        product_type=product_types[0] if product_types else None,
        uniis=tuple(sorted(set(openfda.get("unii") or []))),
        text=text,
    )


@dataclass(frozen=True)
class Census:
    """What one pass over the corpus found -- and it must add up.

    Every field is a count of LABELS except ``distinct_text_keys``, which counts
    distinct wordings. Keeping both in one validated object is the point: the
    ratio between them is the de-duplication factor, and quoting either alone as
    "the size of the corpus" is the error this class exists to prevent.
    """

    records: int
    with_section: int
    by_product_type: Mapping[str | None, int]
    with_unii: int
    distinct_text_keys: int

    def __post_init__(self) -> None:
        if self.with_section > self.records:
            raise ValueError(
                f"{self.with_section} sections from {self.records} records: "
                "a label cannot carry the section more than once"
            )
        tallied = sum(self.by_product_type.values())
        if tallied != self.with_section:
            raise ValueError(
                f"product-type tally sums to {tallied}, but {self.with_section} "
                "labels carry the section; every section-carrying label has a "
                "product type or an explicit None"
            )
        if self.with_unii > self.with_section:
            raise ValueError(
                f"{self.with_unii} labels carry a unii but only "
                f"{self.with_section} carry the section"
            )
        if self.distinct_text_keys > self.with_section:
            raise ValueError(
                f"{self.distinct_text_keys} distinct texts from "
                f"{self.with_section} sections: de-duplication cannot add texts"
            )

    @property
    def without_section(self) -> int:
        return self.records - self.with_section
