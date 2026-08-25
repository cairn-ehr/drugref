"""Read one SPL label's subject drug out of its XML.

**Throwaway spike code for the slice 5c.3 design round.** Split out of
``tools/spl_subject_recovery.py`` under CLAUDE.md rule 4: that module was doing
two jobs -- reading a document, and tallying a corpus -- and only one of them
needs to know what HL7 v3 looks like.

Two parsing traps are guarded rather than assumed away, because getting either
wrong produces a confident number pointing the wrong way:

* **An inactive ingredient is never the subject.** Excipients carry UNIIs too,
  and reading one attaches a real interaction statement to lactose.
* **The salt is not the moiety.** SPL nests the active moiety's own UNII inside
  the substance's, and drugref keys the moiety. Both are READ here rather than
  one being chosen, because which of them resolves is the measurement -- but
  which one is USED as a subject is :func:`subject_uniis`, in one place, because
  the round published a delta whose two arms disagreed about that.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass

#: The HL7 v3 namespace every SPL document uses.
_SPL_NS = "{urn:hl7-org:v3}"

#: FDA SRS -- the code system that makes a <code> element a UNII. SPL is full of
#: <code> elements (dosage form, route, marketing category); keying on the
#: element name alone harvests all of them as if they were substances.
UNII_CODE_SYSTEM = "2.16.840.1.113883.4.9"

#: HL7 classCodes for an ACTIVE ingredient. ``IACT`` -- inactive -- is
#: deliberately absent, and its absence is what keeps excipients out.
_ACTIVE_CLASS_CODES = frozenset({"ACTIB", "ACTIM", "ACTIR", "ACTI"})


@dataclass(frozen=True, kw_only=True)
class SubjectUniis:
    """The UNIIs one label offers as its subject drug.

    ``moiety_uniis`` is the grain drugref keys on; ``substance_uniis`` is the
    salt or ester actually in the product. They are kept apart so the
    measurement can report how many labels resolve on each, rather than a single
    blended figure that hides which route did the work. Which of them is
    actually USED as the subject is :func:`subject_uniis`, in one place.

    ``version`` is SPL's ``<versionNumber>``. It exists because DailyMed ships
    successive versions of one label as separate documents sharing a ``set_id``,
    and "the first row the scan happened to emit" is not a rule for choosing
    between them -- see :func:`dedupe_by_set_id`.
    """

    set_id: str
    moiety_uniis: tuple[str, ...]
    substance_uniis: tuple[str, ...]
    version: int | None = None

    @property
    def has_any_unii(self) -> bool:
        """Whether this label offers any UNII at all.

        Named for what it measures, not for what the caller wants it to mean: a
        label offering only a SALT UNII has no subject drugref can key, which is
        issue #67 and is exactly the distinction this module exists to keep.
        """
        return bool(self.moiety_uniis or self.substance_uniis)


def _unii_of(element: ET.Element | None) -> str | None:
    """The UNII on this element's own ``<code>`` child, if it carries one."""
    if element is None:
        return None
    code = element.find(f"{_SPL_NS}code")
    if code is None or code.get("codeSystem") != UNII_CODE_SYSTEM:
        return None
    return code.get("code") or None


def _active_substance_elements(root: ET.Element) -> list[ET.Element]:
    """Every element describing an ACTIVE ingredient's substance.

    SPL spells this two ways and **the classCode spelling is the dominant one**:

    * ``<ingredient classCode="ACTIB|ACTIM|ACTIR">`` with an
      ``<ingredientSubstance>`` child -- the element is neutral and the
      attribute carries the distinction;
    * ``<activeIngredient><activeIngredientSubstance>`` -- where "active" is
      part of the element name.

    **Measured on the release this round read**, and the ranking is not close:
    part6 has 2,912 classCode-active elements and **zero**
    ``activeIngredientSubstance``; part1 has 7,639 against 727. In a 3,000-label
    sample **no label used both spellings**, so they are alternatives, not
    layers. Reading only ``activeIngredientSubstance`` would therefore have
    recovered close to NOTHING -- not merely under-counted -- and under-counting
    is the direction that quietly kills a design option by making recovery look
    not worth building.

    ``INGR`` (generic ingredient) and ``CNTM`` (container component) are seen in
    the release and deliberately excluded: neither declares the ingredient
    active, and guessing would key an interaction statement to whatever the
    package is made of.
    """
    found = [*root.iter(f"{_SPL_NS}activeIngredientSubstance")]
    for ingredient in root.iter(f"{_SPL_NS}ingredient"):
        if ingredient.get("classCode") not in _ACTIVE_CLASS_CODES:
            continue
        found.extend(ingredient.findall(f"{_SPL_NS}ingredientSubstance"))
    return found


def _moiety_uniis_under(substance: ET.Element) -> list[str]:
    """The active-moiety UNIIs declared under one substance.

    SPL nests the element inside itself -- ``<activeMoiety><activeMoiety>`` --
    with the code on the inner one. Iterating every descendant with that tag and
    keeping those that carry a UNII code handles both levels without depending
    on the nesting depth, which varies between SPL versions.
    """
    uniis = []
    for moiety in substance.iter(f"{_SPL_NS}activeMoiety"):
        unii = _unii_of(moiety)
        if unii:
            uniis.append(unii)
    return uniis


def extract_subject_uniis(xml_bytes: bytes) -> SubjectUniis | None:
    """The subject UNIIs of one SPL label, or ``None`` if it offers no join key.

    ``None`` means the document could not be parsed, or carries no ``setId`` --
    the key that joins a recovered subject back to the openFDA record it would
    rescue. A label with a ``setId`` but no ingredients returns a
    :class:`SubjectUniis` with empty tuples: it was read, and it had nothing.
    That distinction matters, because folding the two together would report a
    parse failure as a source gap.
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
    """The UNIIs this label actually contributes as a subject. **One rule.**

    The moiety when a moiety UNII resolves; the salt ONLY when none does. This
    is the design spec's route table -- ``dailymed_active_moiety`` and
    ``dailymed_active_substance`` are alternatives, and 16 labels take the
    second on the real release.

    **It exists because the round published a delta measured with two different
    rules.** ``summarise_recovery`` reported a salt-only resolution on its own
    route, exactly as intended, while ``augment_rows`` handed the pair counter
    the moiety UNII *and* the salt UNII together. drugref registers the salt as
    its own moiety with its own live UNII claim, so a salt product contributed
    TWO subjects and paired against every partner twice -- on 56.7% of resolvable
    DailyMed labels, against zero on the openFDA baseline arm it was compared
    with. Both stages now call this.
    """
    if any(unii in known_uniis for unii in found.moiety_uniis):
        return found.moiety_uniis
    if any(unii in known_uniis for unii in found.substance_uniis):
        return found.substance_uniis
    return ()


def dedupe_by_set_id(
    rows: Iterable[SubjectUniis],
) -> dict[str, SubjectUniis]:
    """One row per label, keeping the HIGHEST ``version``. **One policy.**

    DailyMed ships successive versions of one label as separate documents
    sharing a ``set_id`` -- 44 of them on the real release. Counting the rows
    reported 6,583 labels where 6,539 exist.

    The fix for that count introduced a SECOND de-duplication rule rather than
    one: the resolve stage kept the first row it saw and the yield stage the
    last, so two published tables described different readings of the same 44
    labels. Neither "first" nor "last" is a rule -- both are zip-member order.
    The version number is a rule, and an unversioned row never displaces a
    versioned one.
    """
    best: dict[str, SubjectUniis] = {}
    for row in rows:
        current = best.get(row.set_id)
        if current is None:
            best[row.set_id] = row
            continue
        if (row.version or -1) > (current.version or -1):
            best[row.set_id] = row
    return best

