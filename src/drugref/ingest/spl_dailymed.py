# src/drugref/ingest/spl_dailymed.py
"""Read one SPL label's subject drug out of DailyMed's own XML.

PURE AND STREAMING, per the architecture invariant. It exists because openFDA
leaves the subject empty on **59.6%** of section-carrying labels: `moiety_uuid`
is UUIDv5 on UNII, so no `openfda.unii` means no subject, and an interaction
statement with no subject is not an interaction statement.

Design: docs/superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md
Measurement: .../2026-08-24-drugref-slice-5c3-subject-recovery-measurement.md

WHAT IT BUYS, MEASURED (2026-08-25, Human Rx release of 2026-08-21): of 26,401
orphan-wording labels targeted, **6,539 are in DailyMed (24.8%)**, **6,514 of
those resolve (99.6%)**, and 25 carry a UNII drugref does not hold. That adds
**8,704 pairs (+42.3%), 7,853 novel (90.2%)** -- on its own bigger than
DrugCentral's entire slice. *The limit is the release, not the reading*: all four
of the scan's drop counters measured zero.

FOUR TRAPS ARE GUARDED HERE RATHER THAN ASSUMED AWAY, because getting any of them
wrong produces a confident number pointing the wrong way. They are stated once,
and each has a test named after it:

1. **An inactive ingredient is never the subject.** Excipients carry UNIIs too,
   and reading one attaches a real interaction statement to lactose.
2. **SPL spells "active ingredient" two ways, and the classCode spelling
   dominates.** Reading only `<activeIngredientSubstance>` would have recovered
   close to nothing -- not merely under-counted -- and under-counting is the
   direction that quietly kills a design option by making recovery look not worth
   building.
3. **The salt is not the moiety, and drugref registers a salt as its own moiety.**
   Both grains are READ, because which of them resolves is the measurement; which
   one is USED is `subject_uniis`, in exactly one place.
4. **DailyMed ships successive versions of one label sharing a `set_id`.**
   Counting documents reported 6,583 labels where 6,539 exist.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import (
    Callable, Iterable, Iterator, Mapping, Sequence, Set as AbstractSet,
)
from dataclasses import dataclass

#: The HL7 v3 namespace every SPL document uses.
_SPL_NS = "{urn:hl7-org:v3}"

#: FDA SRS -- the code system that makes a `<code>` element a UNII. SPL is full
#: of `<code>` elements (dosage form, route, marketing category); keying on the
#: element name alone harvests all of them as if they were substances.
UNII_CODE_SYSTEM = "2.16.840.1.113883.4.9"

#: HL7 classCodes for an ACTIVE ingredient. `IACT` -- inactive -- is deliberately
#: absent, and its absence is what keeps excipients out. `INGR` (generic
#: ingredient) and `CNTM` (container component) are seen in the release and also
#: excluded: neither declares the ingredient active, and guessing would key an
#: interaction statement to whatever the package is made of.
_ACTIVE_CLASS_CODES = frozenset({"ACTIB", "ACTIM", "ACTIR", "ACTI"})

#: The two routes this module can put a subject on, in the order they are tried.
#: THE SECOND HOME of two of `db/051`'s five route values -- admitted on the same
#: terms `drugcentral_ddi_assertion_route_1` lives under, and pinned by a test
#: that reads the Python vocabulary and the catalog CHECK and compares them.
ROUTE_MOIETY = "dailymed_active_moiety"
ROUTE_SUBSTANCE = "dailymed_active_substance"

#: Pulled from the raw bytes before any tree is built: building a tree for every
#: document in a 17.6 GB release to discover most are unwanted costs far more
#: than one regex.
#:
#: **Both quote styles and optional whitespace are accepted.** `root='x'` is legal
#: XML, and a regex that missed it would drop a targeted label BEFORE it was ever
#: parsed -- landing it in "absent from DailyMed", which is a fact about the
#: reading republished as a fact about the release. `extract_subject_uniis`
#: re-reads the set_id from the tree and the caller asserts the two agree, so
#: this filter can only ever be a cheap pre-pass, never the authority.
_SET_ID = re.compile(rb"<(?:\w+:)?setId[^>]*\sroot\s*=\s*[\"']([^\"']+)[\"']")


@dataclass(frozen=True, kw_only=True)
class SubjectUniis:
    """The UNIIs one label offers as its subject drug.

    `moiety_uniis` is the grain drugref keys on; `substance_uniis` is the salt or
    ester actually in the product. They are kept apart so the ingest can record
    which route did the work, rather than a single blended figure that hides it.
    Which of them is actually USED is `subject_uniis`, in one place.

    `version` is SPL's `<versionNumber>`. It exists because DailyMed ships
    successive versions of one label as separate documents sharing a `set_id`,
    and "the first row the scan happened to emit" is not a rule for choosing
    between them -- see `dedupe_by_set_id`.
    """

    set_id: str
    moiety_uniis: tuple[str, ...]
    substance_uniis: tuple[str, ...]
    version: int | None = None

    @property
    def has_any_unii(self) -> bool:
        """Whether this label offers any UNII at all.

        Named for what it MEASURES, not for what a caller wants it to mean: a
        label offering only a SALT UNII has no subject drugref can key at moiety
        grain, which is #67 and exactly the distinction this module keeps.
        """
        return bool(self.moiety_uniis or self.substance_uniis)


def set_id_in_bytes(xml_bytes: bytes) -> str | None:
    """The `setId` root attribute, read from the raw bytes. A PRE-FILTER only."""
    match = _SET_ID.search(xml_bytes)
    return match.group(1).decode() if match else None


def _unii_of(element: ET.Element | None) -> str | None:
    """The UNII on this element's own `<code>` child, if it carries one."""
    if element is None:
        return None
    code = element.find(f"{_SPL_NS}code")
    if code is None or code.get("codeSystem") != UNII_CODE_SYSTEM:
        return None
    return code.get("code") or None


def _active_substance_elements(root: ET.Element) -> list[ET.Element]:
    """Every element describing an ACTIVE ingredient's substance. BOTH spellings.

    * `<ingredient classCode="ACTIB|ACTIM|ACTIR|ACTI">` with an
      `<ingredientSubstance>` child -- the element is neutral and the attribute
      carries the distinction. **This is the dominant spelling.**
    * `<activeIngredient><activeIngredientSubstance>` -- where "active" is part
      of the element name.

    Measured on the release this slice reads, and the ranking is not close: part6
    has 2,912 classCode-active elements and **zero** `activeIngredientSubstance`;
    part1 has 7,639 against 727. In a 3,000-label sample no label used both, so
    they are alternatives rather than layers.
    """
    found = [*root.iter(f"{_SPL_NS}activeIngredientSubstance")]
    for ingredient in root.iter(f"{_SPL_NS}ingredient"):
        if ingredient.get("classCode") not in _ACTIVE_CLASS_CODES:
            continue
        found.extend(ingredient.findall(f"{_SPL_NS}ingredientSubstance"))
    return found


def _moiety_uniis_under(substance: ET.Element) -> list[str]:
    """The active-moiety UNIIs declared under one substance.

    SPL nests the element inside itself -- `<activeMoiety><activeMoiety>` -- with
    the code on the inner one. Iterating every descendant with that tag and
    keeping those carrying a UNII code handles both levels without depending on
    the nesting depth, which varies between SPL versions.
    """
    uniis = []
    for moiety in substance.iter(f"{_SPL_NS}activeMoiety"):
        unii = _unii_of(moiety)
        if unii:
            uniis.append(unii)
    return uniis


def extract_subject_uniis(xml_bytes: bytes) -> SubjectUniis | None:
    """The subject UNIIs of one SPL label, or `None` if it offers no join key.

    `None` means the document could not be parsed, or carries no `setId` -- the
    key that joins a recovered subject back to the openFDA record it rescues.

    A label WITH a `setId` and no ingredients returns a `SubjectUniis` carrying
    empty tuples: it was read, and it had nothing. **That distinction is the
    point** -- folding the two together reports a parse failure as a source gap,
    and the recovery figure then measures this code rather than the release.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    set_id_element = root.find(f"{_SPL_NS}setId")
    set_id = set_id_element.get("root") if set_id_element is not None else None
    if not set_id:
        return None

    version_element = root.find(f"{_SPL_NS}versionNumber")
    version = None
    if version_element is not None:
        try:
            version = int(version_element.get("value") or "")
        except ValueError:
            # A junk version is not a reason to lose the label: it simply loses
            # its tie-break, and `dedupe_by_set_id` never lets it displace a
            # versioned row.
            version = None

    moiety_uniis: set[str] = set()
    substance_uniis: set[str] = set()
    for substance in _active_substance_elements(root):
        unii = _unii_of(substance)
        if unii:
            substance_uniis.add(unii)
        moiety_uniis.update(_moiety_uniis_under(substance))

    return SubjectUniis(
        set_id=set_id,
        moiety_uniis=tuple(sorted(moiety_uniis)),
        substance_uniis=tuple(sorted(substance_uniis)),
        version=version,
    )


def subject_uniis(
    found: SubjectUniis, known_uniis: set[str] | frozenset[str]
) -> tuple[str, ...]:
    """The UNIIs this label actually contributes as a subject. **ONE RULE.**

    The moiety when a moiety UNII resolves; the salt ONLY when none does. The two
    are `db/051`'s `dailymed_active_moiety` and `dailymed_active_substance`, and
    they are **alternatives, never layers** -- 16 labels take the second on the
    real release.

    **It exists because a round published a delta measured with two different
    rules.** One stage reported a salt-only resolution on its own route, exactly
    as intended, while another handed the pair counter the moiety UNII *and* the
    salt UNII together. drugref registers the salt as its own moiety with its own
    live UNII claim, so a salt product contributed TWO subjects and paired
    against every partner twice -- on 56.7% of resolvable DailyMed labels,
    against zero on the openFDA arm it was compared with. That alone published
    31,618 pairs where this rule gives 29,258.
    """
    if any(unii in known_uniis for unii in found.moiety_uniis):
        return found.moiety_uniis
    if any(unii in known_uniis for unii in found.substance_uniis):
        return found.substance_uniis
    return ()


def subject_route(
    found: SubjectUniis, known_uniis: set[str] | frozenset[str]
) -> str | None:
    """Which DailyMed route answered, or `None` if neither did.

    Derived from the SAME precedence `subject_uniis` applies, in the same order,
    so the recorded route and the recorded moieties can never describe different
    readings of one label. A route stated independently of the values it
    describes is the shape `db/006` exists to remove.
    """
    if any(unii in known_uniis for unii in found.moiety_uniis):
        return ROUTE_MOIETY
    if any(unii in known_uniis for unii in found.substance_uniis):
        return ROUTE_SUBSTANCE
    return None


def dedupe_by_set_id(rows: Iterable[SubjectUniis]) -> dict[str, SubjectUniis]:
    """One row per label, keeping the HIGHEST `version`. **ONE POLICY.**

    DailyMed ships successive versions of one label as separate documents sharing
    a `set_id` -- 44 of them among the labels this slice targets. Counting the
    rows reported 6,583 labels where 6,539 exist, and every test of the code that
    did it passed.

    The first fix for that count introduced a SECOND de-duplication rule rather
    than one: one stage kept the first row it saw and another the last, so two
    published tables described different readings of the same 44 labels. Neither
    "first" nor "last" is a rule -- both are zip-member order. **The version
    number is a rule**, and an unversioned row never displaces a versioned one.
    """
    best: dict[str, SubjectUniis] = {}
    for row in rows:
        current = best.get(row.set_id)
        if current is None or (row.version or -1) > (current.version or -1):
            best[row.set_id] = row
    return best


@dataclass(frozen=True, kw_only=True)
class ScanResult:
    """What one pass over the DailyMed release found, AND EVERY DOCUMENT IT DROPPED.

    **The drop counters are fields rather than local variables, and that is the
    whole point of this type.** A document silently skipped here is republished
    three stages later as `absent_from_dailymed` -- a fact about the READING sold
    as a fact about the RELEASE, and the design spec turns that route's population
    into a commitment. Measured on the 2026-08-21 Human Rx release, all four are
    ZERO, which is what lets "the limit is the release, not the reading" be a
    measurement rather than an inference.

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

    @property
    def total_dropped(self) -> int:
        return (self.dropped_no_set_id_bytes + self.dropped_unreadable
                + self.dropped_prefilter_disagreed)


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
    """
    found: list[SubjectUniis] = []
    documents_read = 0
    no_set_id_bytes = unreadable = disagreed = 0

    for part in parts:
        if progress is not None:
            progress(str(part))
        for _document_id, xml_bytes in iter_release_labels(part):
            documents_read += 1
            pre_filter = set_id_in_bytes(xml_bytes)
            if pre_filter is None:
                no_set_id_bytes += 1
                continue
            if pre_filter not in targets:
                continue
            recovered = extract_subject_uniis(xml_bytes)
            if recovered is None:
                unreadable += 1
                continue
            if recovered.set_id != pre_filter:
                disagreed += 1
                continue
            found.append(recovered)

    return ScanResult(
        documents_read=documents_read,
        found=dedupe_by_set_id(found),
        dropped_no_set_id_bytes=no_set_id_bytes,
        dropped_unreadable=unreadable,
        dropped_prefilter_disagreed=disagreed)


def iter_release_labels(
    part_path: str, *, limit: int | None = None
) -> Iterator[tuple[str, bytes]]:
    """Yield `(document_id, xml_bytes)` for every label in one release part.

    Each outer member is itself a zip holding the XML plus the label's images;
    only the `.xml` member is read, so the images never leave the archive -- which
    is both faster and the only part of the payload rule 6 has an opinion about.
    """
    seen = 0
    with zipfile.ZipFile(part_path) as outer:
        for name in outer.namelist():
            if not name.endswith(".zip"):
                continue
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                xml_names = [n for n in inner.namelist() if n.endswith(".xml")]
                if not xml_names:
                    continue
                yield name, inner.read(xml_names[0])
            seen += 1
            if limit is not None and seen >= limit:
                return
