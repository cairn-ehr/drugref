# src/drugref/ingest/spl_dailymed.py
"""Read one SPL label's subject drug out of DailyMed's own XML.

PURE AND STREAMING, per the architecture invariant. It exists because openFDA
leaves the subject empty on **59.6%** of section-carrying labels: `moiety_uuid`
is UUIDv5 on UNII, so no `openfda.unii` means no subject, and an interaction
statement with no subject is not an interaction statement.

Design: docs/superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md
Measurement: .../2026-08-24-drugref-slice-5c3-subject-recovery-measurement.md

WHAT IT BUYS, MEASURED BY THE SHIPPED INGEST (Human Rx release of 2026-08-21):
of **41,056** labels targeted, **10,670 are in DailyMed (26.0%)** and **10,578 of
those resolve (99.1%)** -- on its own bigger than DrugCentral's entire slice.
*The limit is the release, not the reading*: the four drop counters that EXISTED
at that run measured zero. `dropped_no_xml_member` and `dropped_several_xml
_members` were added afterwards, by the review round that found the two skips
they count were upstream of every counter -- and they have NOT yet been measured
against a real release. See `ScanResult`.

(The design-round probe reported 6,539 found of 26,401 targeted. It targeted a
smaller set and, as the 2026-08-27 results record sets out, filed 14,455 labels
it had never read as `unresolved`. Both passes are real; these are the shipped
ingest's, and the per-part tallies quoted further down are the probe's.)

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
   Counting documents reported 6,583 labels where 6,539 existed, on the probe's
   target set. The ratio moves with the release; the trap does not.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
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

#: HL7 classCodes SEEN IN THE RELEASE AND RULED ON AS NOT-ACTIVE. Kept apart from
#: `_ACTIVE_CLASS_CODES` so that "unknown" can mean *"nobody has ruled on this
#: code"* -- which is the only reading that makes issue #162 case 3 actionable,
#: and the difference between a guard and a tripwire.
#:
#: **`COLR` was added on measurement, and it is why case 3 is not a drop.** The
#: skip census of the 2026-08-21 Human Rx release found `COLR` ten times across
#: three labels -- the ONLY code outside the vocabulary in all 54,813 documents.
#: Issue #162 proposed folding case 3 into `total_dropped`; done literally, that
#: would have aborted the ingest on the very release it was measured against.
#: All ten name a colour (WHITE, RED, BLUE, YELLOW) and NONE carries a `<code>`
#: element, so not one of them could have contributed a subject even if the code
#: were admitted as active.
_DOCUMENTED_INACTIVE_CLASS_CODES = frozenset({"IACT", "INGR", "CNTM", "COLR"})

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
    #: `<versionNumber>` was PRESENT and did not parse -- issue #162 case 2.
    #: NOT the same as `version is None`, which is also true of a label carrying
    #: no version element at all, and only one of those two is a label whose
    #: tie-break was destroyed. `dedupe_by_set_id` then falls back to
    #: `(None or -1)`, i.e. to zip-member order -- the thing that function argues
    #: at length is not a rule.
    version_was_unreadable: bool = False
    #: HL7 classCodes on this label that are in NEITHER shipped vocabulary --
    #: issue #162 case 3. Reported rather than refused over; see
    #: `unknown_class_code_uniis` for the half that IS refused over.
    unknown_class_codes: tuple[str, ...] = ()
    #: The UNIIs carried by those ingredients. **This is the condition that
    #: HARMS**: an unknown code contributes nothing unless it carries a UNII, so
    #: this -- not the bare presence of an unknown code -- is what could have
    #: cost the label a subject.
    unknown_class_code_uniis: tuple[str, ...] = ()

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


#: SPL's `<relatedDocument>` names the label THIS one replaces, and carries a
#: `setId` of its own. `_SET_ID` takes the FIRST match in the bytes, so a
#: document that put its `<relatedDocument>` first would be pre-filtered under
#: the name of the label being REPLACED.
_RELATED_DOCUMENT = re.compile(rb"<(?:\w+:)?relatedDocument\b")


def prefilter_is_trustworthy(xml_bytes: bytes) -> bool:
    """Whether `set_id_in_bytes` can only have picked this document's OWN setId.

    ⇒ ISSUE #162 CASE 1. `scan_release` compares the pre-filter against the tree
    only for documents whose pre-filtered name IS a target. A document whose
    pre-filter named some OTHER label is skipped before any comparison happens,
    and the label it really was is filed `absent_from_dailymed` -- so the
    module's *"the pre-filter is never the authority"* held for the in-targets
    case alone. This is the cheap byte test that closes the other half: no tree
    is built, and the bytes are already in memory for `set_id_in_bytes`.

    MEASURED: on all 54,813 documents of the 2026-08-21 Human Rx release, a
    `<relatedDocument>` NEVER precedes the document's own `<setId>` -- so the
    ordering this guards was, and is, an assumption that happens to hold. It is
    now a guarded one.
    """
    set_id = _SET_ID.search(xml_bytes)
    if set_id is None:
        # Nothing was selected, so nothing can have been mis-selected. The
        # document is already counted by `dropped_no_set_id_bytes`.
        return True
    # `endpos` matters: this runs on EVERY non-target document -- some 44,000 of
    # them per release -- and only the bytes BEFORE the selected setId can
    # change the answer. Searching the whole document instead would scan the
    # full 17.6 GB a second time to reach the same verdict.
    return _RELATED_DOCUMENT.search(xml_bytes, 0, set_id.start()) is None


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


#: A DOCTYPE declaration, and the internal subset it may carry.
#:
#: `ET.fromstring` expands INTERNAL entities, and SPL is third-party content --
#: NLM publishes labeling *"submitted to the FDA by companies"*. Measured: five
#: levels of nesting expand to 100 KB in under 3 ms, and each further level
#: multiplies by ten, so a document a few lines long can exhaust memory (the
#: "billion laughs" shape). External entities are already refused by the
#: underlying expat build, so there is no XXE here and nothing to stop reading
#: from disk or the network -- the exposure is memory alone.
#:
#: **MATCHED ON THE DOCTYPE, NOT ON `<!ENTITY` ANYWHERE IN THE FILE**, because an
#: entity declaration is only legal inside a DOCTYPE's internal subset while the
#: LITERAL TEXT `<!ENTITY` is legal anywhere a comment is. Measured: `<!-- <!ENTITY
#: a "x" --><d>hello</d>` parses cleanly, so a bare byte search for `<!ENTITY`
#: would drop a valid document -- and because a drop here is counted, that one
#: false positive would abort the entire 41,056-document ingest. Valid SPL
#: carries no DOCTYPE at all, so the tight form costs nothing.
#:
#: Checked in the BYTES rather than through a parser callback because
#: `xml.etree`'s C parser exposes no handle to refuse a declaration mid-parse,
#: and the alternatives all mean parsing the document first, which is the thing
#: being avoided. The scan already reads these bytes for `set_id_in_bytes`.
_DOCTYPE = re.compile(rb"<!DOCTYPE\b", re.IGNORECASE)


def extract_subject_uniis(xml_bytes: bytes) -> SubjectUniis | None:
    """The subject UNIIs of one SPL label, or `None` if it offers no join key.

    `None` means the document could not be parsed, or carries no `setId` -- the
    key that joins a recovered subject back to the openFDA record it rescues.

    A label WITH a `setId` and no ingredients returns a `SubjectUniis` carrying
    empty tuples: it was read, and it had nothing. **That distinction is the
    point** -- folding the two together reports a parse failure as a source gap,
    and the recovery figure then measures this code rather than the release.
    """
    # Refused, not parsed: a DOCTYPE is where an entity declaration would live,
    # and no valid SPL carries one. `None` files it under `dropped_unreadable`,
    # which `check_scan_dropped_nothing` refuses the whole run over -- loud, and
    # before the run row exists.
    if _DOCTYPE.search(xml_bytes):
        return None

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
    version_was_unreadable = False
    if version_element is not None:
        try:
            version = int(version_element.get("value") or "")
        except ValueError:
            # A junk version does not lose the label HERE -- it loses its
            # tie-break, and `dedupe_by_set_id` never lets it displace a
            # versioned row. It is REPORTED so that `scan_release` can refuse
            # the run rather than silently attach the wrong version's subject.
            version = None
            version_was_unreadable = True

    unknown_codes, unknown_uniis = _unknown_class_code_findings(root)

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
        version_was_unreadable=version_was_unreadable,
        unknown_class_codes=unknown_codes,
        unknown_class_code_uniis=unknown_uniis,
    )


def _unknown_class_code_findings(
    root: ET.Element,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(codes, uniis)` for ingredients whose classCode nobody has ruled on.

    Both vocabularies are READ here rather than restated, so this can never
    disagree with the set `_active_substance_elements` applies -- a vocabulary
    with two homes is the defect this slice has now found four times.
    """
    ruled_on = _ACTIVE_CLASS_CODES | _DOCUMENTED_INACTIVE_CLASS_CODES
    codes: set[str] = set()
    uniis: set[str] = set()
    for ingredient in root.iter(f"{_SPL_NS}ingredient"):
        code = ingredient.get("classCode") or ""
        if code in ruled_on:
            continue
        codes.add(code)
        for substance in ingredient.findall(f"{_SPL_NS}ingredientSubstance"):
            unii = _unii_of(substance)
            if unii:
                uniis.add(unii)
    return tuple(sorted(codes)), tuple(sorted(uniis))


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
    31,618 pairs where this rule gave 29,258 in the same comparison. Both are
    figures from that one measurement, not standing yields -- the shipped ingest
    publishes 29,952; `spl_checks.MEASURED_PAIR_FLOOR` is the only home for the
    number anything asserts.
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
