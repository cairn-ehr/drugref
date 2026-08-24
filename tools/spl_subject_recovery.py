"""Can the 41,056 subject-less SPL labels have their subject recovered?

**Throwaway spike code for the slice 5c.3 design round.** It answers a
question; it is not the ingest, and nothing under ``src/drugref/`` imports it.

The measurement round left one number as an open design question: **41,056 of
the 68,550 section-carrying openFDA labels (60%) are discarded before a pair can
form**, because openFDA's normalising ``openfda`` block -- the one carrying
``unii`` -- is absent from them. ``moiety_uuid`` is UUIDv5 on UNII, so no
``unii`` means no subject, and an interaction statement with no subject is not
an interaction statement.

The recovery route is known: the ``set_id`` joins to DailyMed's own SPL XML,
which carries the full ingredient list. It is not free -- 17.6 GB of nested
zips -- so this module measures the CHEAP BOUND first:

    Wordings are shared 2.50 labels to one. An unkeyed label whose wording also
    appears on a KEYED label carries no statement drugref cannot already reach;
    it is a second manufacturer printing the same words. Only wordings reachable
    *solely* through unkeyed labels can add anything, and that is computable
    from the openFDA cache alone.

Whatever survives that bound is then chased into DailyMed's XML by
:func:`extract_subject_uniis`.

Two parsing traps are guarded rather than assumed away, because getting either
wrong produces a confident number pointing the wrong way:

* **An inactive ingredient is never the subject.** Excipients carry UNIIs too,
  and reading one attaches a real interaction statement to lactose.
* **The salt is not the moiety.** SPL nests the active moiety's own UNII inside
  the substance's, and drugref keys the moiety. Both are returned here rather
  than one being chosen, because which of them resolves is the measurement.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
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


@dataclass(frozen=True)
class SubjectUniis:
    """The UNIIs one label offers as its subject drug.

    ``moiety_uniis`` is the grain drugref keys on; ``substance_uniis`` is the
    salt or ester actually in the product. They are kept apart so the
    measurement can report how many labels resolve on each, rather than a single
    blended figure that hides which route did the work.
    """

    set_id: str
    moiety_uniis: tuple[str, ...]
    substance_uniis: tuple[str, ...]

    @property
    def has_subject(self) -> bool:
        """Whether this label offers any UNII at all."""
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

    SPL spells this two ways and both occur in the wild:

    * ``<activeIngredient><activeIngredientSubstance>`` -- the common modern
      spelling, where "active" is part of the element name;
    * ``<ingredient classCode="ACTIB"><ingredientSubstance>`` -- where the
      element is neutral and the ``classCode`` attribute carries the
      distinction.

    Reading only the first spelling would UNDER-count the recovery route, and
    under-counting is the direction that quietly kills a design option: it makes
    recovery look not worth building.
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
    )


@dataclass(frozen=True)
class WordingReachability:
    """How much of the unkeyed corpus could possibly add anything.

    Both tallies must add up, and the class refuses to exist otherwise. That
    guard is not decoration: this project has already published a tally
    accounting for 40 of its 50 labels, and the whole value of this measurement
    is that a later reader can trust its arithmetic.
    """

    distinct_wordings: int
    keyed_wordings: int
    orphan_wordings: int
    labels: int
    keyed_labels: int
    recoverable_unkeyed_labels: int
    redundant_unkeyed_labels: int

    def __post_init__(self) -> None:
        if self.keyed_wordings + self.orphan_wordings != self.distinct_wordings:
            raise ValueError(
                f"{self.keyed_wordings} keyed + {self.orphan_wordings} orphan "
                f"!= {self.distinct_wordings} distinct wordings: every wording "
                "is reachable through a keyed label or it is not"
            )
        tallied = (
            self.keyed_labels
            + self.recoverable_unkeyed_labels
            + self.redundant_unkeyed_labels
        )
        if tallied != self.labels:
            raise ValueError(
                f"label tally sums to {tallied}, but {self.labels} labels were "
                "read: every label is keyed, recoverable, or redundant"
            )

    @property
    def orphan_share(self) -> float:
        """Orphan wordings as a share of all wordings."""
        if not self.distinct_wordings:
            return 0.0
        return self.orphan_wordings / self.distinct_wordings


def classify_wordings(rows: Iterable[Mapping]) -> WordingReachability:
    """Split the corpus by whether recovery could add a NEW wording.

    ``rows`` are the spike cache's per-label rows (``set_id``, ``text_key``,
    ``uniis``). The pass is two-phase because reachability is a property of the
    WORDING, not of the label: a label's redundancy cannot be decided until
    every label carrying its wording has been seen.
    """
    keyed_keys: set[str] = set()
    all_keys: set[str] = set()
    labels = 0
    keyed_labels = 0
    unkeyed_by_key: dict[str, int] = {}

    for row in rows:
        labels += 1
        key = row["text_key"]
        all_keys.add(key)
        if row.get("uniis"):
            keyed_labels += 1
            keyed_keys.add(key)
        else:
            unkeyed_by_key[key] = unkeyed_by_key.get(key, 0) + 1

    recoverable = sum(
        count for key, count in unkeyed_by_key.items() if key not in keyed_keys
    )
    redundant = sum(
        count for key, count in unkeyed_by_key.items() if key in keyed_keys
    )
    return WordingReachability(
        distinct_wordings=len(all_keys),
        keyed_wordings=len(keyed_keys),
        orphan_wordings=len(all_keys - keyed_keys),
        labels=labels,
        keyed_labels=keyed_labels,
        recoverable_unkeyed_labels=recoverable,
        redundant_unkeyed_labels=redundant,
    )


def orphan_label_targets(rows: Iterable[Mapping]) -> dict[str, str]:
    """``set_id -> text_key`` for the labels the DailyMed scan should look for.

    Only unkeyed labels carrying an ORPHAN wording qualify. Scanning 17.6 GB of
    nested zips for a label whose wording a keyed label already carries spends
    the expensive pass to rediscover a statement drugref can already reach.
    """
    rows = list(rows)
    keyed_keys = {row["text_key"] for row in rows if row.get("uniis")}
    return {
        row["set_id"]: row["text_key"]
        for row in rows
        if not row.get("uniis") and row["text_key"] not in keyed_keys
    }


@dataclass(frozen=True)
class RecoverySummary:
    """What the DailyMed scan bought, counted in the unit that matters.

    ``wordings_rescued`` is the headline and ``labels_resolved`` is not: two
    labels carrying one orphan wording rescue ONE statement between them, and
    reporting labels would publish the de-duplication factor as if it were a
    result. The corpus census was already burned once by quoting one unit as
    the other.
    """

    wordings_targeted: int
    labels_targeted: int
    labels_found: int
    labels_missing_from_dailymed: int
    labels_without_any_unii: int
    labels_resolved: int
    resolved_on_moiety: int
    resolved_on_substance_only: int
    wordings_rescued: int

    @property
    def rescue_share(self) -> float:
        """Share of targeted orphan wordings that gained a resolvable subject."""
        if not self.wordings_targeted:
            return 0.0
        return self.wordings_rescued / self.wordings_targeted


def summarise_recovery(
    recovered: Iterable[SubjectUniis],
    targets: Mapping[str, str],
    known_uniis: set[str] | frozenset[str],
) -> RecoverySummary:
    """Join the scan's findings back to the orphan wordings they rescue.

    ``known_uniis`` is drugref's own ``identity_claim`` UNII set -- the same
    bridge ``moiety_uuid`` is minted from -- so "resolved" here means the same
    thing the ingest would mean by it.

    **Rows are de-duplicated by ``set_id`` first.** DailyMed ships successive
    VERSIONS of one label as separate documents sharing a set_id, so the scan
    emits more rows than labels -- 44 more on the real corpus. Counting rows
    reported 6,583 labels found where 6,539 exist, and the error was invisible
    until the total was cross-checked against an independent pass.

    A label resolving only on its SALT is counted separately rather than folded
    in: it needs a salt-to-base step drugref does not have
    ([#67](https://github.com/cairn-ehr/drugref/issues/67)), so counting it as
    recovered would promise a route that is not built.
    """
    without_unii = 0
    on_moiety = 0
    on_substance_only = 0
    rescued_keys: set[str] = set()

    by_set_id: dict[str, SubjectUniis] = {}
    for row in recovered:
        if row.set_id not in targets:
            raise ValueError(
                f"set_id {row.set_id!r} was not a scan target: the cache and "
                "the scan disagree about which corpus they read"
            )
        by_set_id.setdefault(row.set_id, row)
    found = len(by_set_id)

    for row in by_set_id.values():
        key = targets[row.set_id]
        if not row.has_subject:
            without_unii += 1
            continue
        moiety_hit = any(unii in known_uniis for unii in row.moiety_uniis)
        substance_hit = any(unii in known_uniis for unii in row.substance_uniis)
        if moiety_hit:
            on_moiety += 1
        elif substance_hit:
            on_substance_only += 1
        if moiety_hit or substance_hit:
            rescued_keys.add(key)

    return RecoverySummary(
        wordings_targeted=len(set(targets.values())),
        labels_targeted=len(targets),
        labels_found=found,
        labels_missing_from_dailymed=len(targets) - found,
        labels_without_any_unii=without_unii,
        labels_resolved=on_moiety + on_substance_only,
        resolved_on_moiety=on_moiety,
        resolved_on_substance_only=on_substance_only,
        wordings_rescued=len(rescued_keys),
    )


def split_wordings_by_reachability(
    rows: Iterable[Mapping],
) -> tuple[set[str], set[str]]:
    """``(keyed_wordings, orphan_wordings)`` as sets of ``text_key``.

    The counts alone cannot answer the second half of the question. 56% of
    wordings being orphaned only matters if those wordings carry comparable
    material -- if they named fewer known drugs, recovering their subjects
    would buy proportionally less than their share suggests. Answering that
    needs the keys, so the matcher can be run over each population separately.
    """
    rows = list(rows)
    keyed = {row["text_key"] for row in rows if row.get("uniis")}
    everything = {row["text_key"] for row in rows}
    return keyed, everything - keyed


def augment_rows(
    rows: Iterable[Mapping], recovered: Mapping[str, SubjectUniis]
) -> list[dict]:
    """The cache rows with recovered subjects filled into the empty ones.

    Feeding these to the same pair counter the measurement round used is what
    makes the delta comparable: the only thing that changed is which labels have
    a subject.

    A row that already carries ``uniis`` is returned untouched. openFDA's own
    ``openfda.unii`` is the authority where it exists, and overwriting it with a
    DailyMed reading would move the baseline the delta is measured against.
    """
    augmented = []
    for row in rows:
        new_row = dict(row)
        if not new_row.get("uniis"):
            found = recovered.get(new_row["set_id"])
            if found is not None:
                new_row["uniis"] = [*found.moiety_uniis, *found.substance_uniis]
        augmented.append(new_row)
    return augmented
