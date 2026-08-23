"""Turn resolved DrugCentral `ddi` rows into the figures the report prints.

Split out of ``tools/drugcentral_ddi_spike.py`` under CLAUDE.md rule 1 -- pure
functions in a small module -- because this is where every published number is
actually computed, and the spike around it needs a database and a 1.4 GB dump.
Nothing here needs either.

**Three units, and they are not interchangeable.** PROJECT-NOTES § "The 5c.3 source
evaluation" records that the original evaluation quoted them as if they were:

* **rows** -- one `ddi` record;
* **pair rows** -- rows whose endpoints resolved to two DIFFERENT moieties;
* **distinct pairs** -- those pairs, orientation-normalised and deduplicated.

`Measurement` refuses to exist unless ``rows`` accounts for itself exactly, so the
33 rows that used to fall between "7,571 rows minus 37 unresolvable" and "7,501
distinct pairs" now have to be named.
"""
from __future__ import annotations

import collections
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from tools.drugcentral_resolve import (
    Resolution,
    build_endpoint_index,
    fold_name,
    unordered_pair,
)

#: `qt` and `torsade` as whole words -- `Qtern` is a marketed product, and these
#: rows are quoted verbatim into a licensing narrative where a false positive
#: changes the finding. `qtc` is included because DrugCentral's own prose uses it.
_QT_PATTERN = re.compile(r"\b(qtc?|torsades?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Measurement:
    """Every figure one resolution run produces, with its arithmetic checked.

    Attributes:
        rows: `ddi` rows measured.
        raw_names: distinct endpoint SPELLINGS, before folding. Reported because
            issue #101's 924/970 were spelling counts and the comparison has to
            stay honest about that.
        names: distinct endpoint names after folding case and whitespace -- the
            denominator resolution actually runs on.
        names_resolved: how many of `names` reached a moiety.
        routes: folded name counts per route, resolved and unresolved alike.
        unresolved_names: ``(folded name, route)`` for every name no route
            answered, sorted. The route is carried because the four ways to
            fail mean different things -- a class name DrugCentral does not
            know is a correct miss, a `missing_keys_row` is a broken join.
        unresolvable_rows: rows with at least one unresolved endpoint.
        self_pair_rows: rows whose endpoints resolved to the SAME moiety.
        pair_rows: rows that yielded a pair of two different moieties.
        pairs: distinct unordered pairs among them.
        held: how many of `pairs` drugref already holds.
    """

    rows: int
    raw_names: int
    names: int
    names_resolved: int
    routes: Mapping[str, int]
    unresolved_names: Sequence[tuple[str, str]]
    unresolvable_rows: int
    self_pair_rows: int
    pair_rows: int
    pairs: int
    held: int

    def __post_init__(self) -> None:
        accounted = self.unresolvable_rows + self.self_pair_rows + self.pair_rows
        if accounted != self.rows:
            raise ValueError(
                f"{self.rows} rows but {accounted} accounted for "
                f"({self.unresolvable_rows} unresolvable + {self.self_pair_rows} "
                f"self-pairs + {self.pair_rows} pair rows): a row has gone missing")
        if self.held > self.pairs:
            raise ValueError(
                f"{self.held} held pairs out of {self.pairs} is impossible")

    @property
    def new(self) -> int:
        """Pairs drugref does not already hold. Derived, so it cannot disagree."""
        return self.pairs - self.held


def names_a_qt_population(text: str) -> bool:
    """True if *text* names a QT population, by the whole-token rule.

    Shared with `mentions_qt` so the report's `pharma_class` figure and its
    `ddi` row selection are counted the same way -- one of them used to be a
    substring test and the other a different substring test.
    """
    return _QT_PATTERN.search(text) is not None


def mentions_qt(row: Mapping[str, str]) -> bool:
    """True if a `ddi` row mentions QT prolongation anywhere (issue 93).

    Matches ``qt``, ``qtc``, ``torsade`` and ``torsades`` as WHOLE WORDS, case
    insensitively, across both endpoints and the description -- the two
    class-named QT populations appear as endpoints while a third row mentions QT
    only in its prose.

    Whole words because a substring test matched `Qtern`, a real marketed
    dapagliflozin/saxagliptin product, and these rows are printed verbatim into a
    licensing and safety narrative.
    """
    blob = f'{row["drug_class1"]} {row["drug_class2"]} {row["description"]}'
    return names_a_qt_population(blob)


def measure(
    rows: Sequence[Mapping[str, str]],
    resolve: Callable[[str], Resolution],
    held: set[tuple[str, str]],
) -> Measurement:
    """Resolve every endpoint in *rows* and count rows, pairs and overlap.

    Args:
        rows: `ddi` records, each with ``drug_class1`` and ``drug_class2``.
        resolve: the cascade under test -- either the full structural one or
            issue #101's name-only baseline, so the report can print both. A
            comparison against a remembered number is not a comparison.
        held: the unordered moiety pairs drugref already holds.

    Returns an empty `Measurement` for empty *rows*. Refusing that is the caller's
    job, not this function's: the spike asserts non-empty inputs before rendering,
    because a report full of confident zeros is the failure mode worth stopping.
    """
    raw = {r["drug_class1"] for r in rows} | {r["drug_class2"] for r in rows}
    folded = sorted({fold_name(name) for name in raw})

    resolved: dict[str, Resolution] = {name: resolve(name) for name in folded}
    routes = collections.Counter(r.route for r in resolved.values())

    pairs: set[tuple[str, str]] = set()
    unresolvable_rows = 0
    self_pair_rows = 0
    pair_rows = 0
    for row in rows:
        left = resolved[fold_name(row["drug_class1"])]
        right = resolved[fold_name(row["drug_class2"])]
        if not (left.resolved and right.resolved):
            unresolvable_rows += 1
            continue
        assert left.moiety_uuid is not None and right.moiety_uuid is not None
        pair = unordered_pair(left.moiety_uuid, right.moiety_uuid)
        if pair is None:
            self_pair_rows += 1
            continue
        pair_rows += 1
        pairs.add(pair)

    return Measurement(
        rows=len(rows),
        raw_names=len(raw),
        names=len(folded),
        names_resolved=sum(1 for r in resolved.values() if r.resolved),
        routes=dict(routes),
        unresolved_names=tuple(
            (n, resolved[n].route) for n in folded if not resolved[n].resolved),
        unresolvable_rows=unresolvable_rows,
        self_pair_rows=self_pair_rows,
        pair_rows=pair_rows,
        pairs=len(pairs),
        held=len(pairs & held),
    )


@dataclass(frozen=True)
class ClassCoverage:
    """How much of the endpoint residue is a drugref CLASS rather than a moiety.

    Issue #101 reported *"860 match a display_name, **8 match a MED-RT class name**,
    102 match neither"*. The class half was wrong in its number AND its authority --
    it is 4, and they are MeSH -- and the instrument could not re-derive either
    figure, so a hand-measured correction was filed under "re-derivable". This
    computes both, with the authority attached.

    Attributes:
        names: distinct folded endpoint names.
        names_resolved: names the cascade reached a moiety for.
        names_matching_a_class: unresolved names that ARE a `substance_class`
            name. The cascade runs first, so a name is never counted twice.
        by_source: those class matches, split by the authority that defines them --
            the half of the original claim that was wrong.
        names_matching_nothing: neither a moiety nor a class.
        keyable_rows: rows whose endpoints are BOTH a moiety or a class.
        moiety_by_moiety_rows: the subset with two moiety endpoints.

    `keyable_rows` and `moiety_by_moiety_rows` are different denominators and
    PROJECT-NOTES records them being quoted interchangeably: their difference is
    the rows with exactly one class endpoint, which is why
    ``rows - keyable_rows`` is not the unresolvable count.
    """

    names: int
    names_resolved: int
    names_matching_a_class: int
    by_source: Mapping[str, int]
    names_matching_nothing: int
    keyable_rows: int
    moiety_by_moiety_rows: int


def class_coverage(
    rows: Sequence[Mapping[str, str]],
    resolve: Callable[[str], Resolution],
    class_sources: Mapping[str, str],
) -> ClassCoverage:
    """Split the endpoint names into moiety, class, and neither.

    Args:
        rows: `ddi` records.
        resolve: the structural cascade.
        class_sources: folded ``substance_class.class_name`` -> ``source``.
    """
    raw = {r["drug_class1"] for r in rows} | {r["drug_class2"] for r in rows}
    folded = sorted({fold_name(name) for name in raw})

    resolved = {name: resolve(name) for name in folded}
    is_class: dict[str, str | None] = {
        name: class_sources.get(name)
        for name in folded if not resolved[name].resolved
    }
    by_source = collections.Counter(
        source for source in is_class.values() if source is not None)

    keyable_rows = 0
    moiety_by_moiety_rows = 0
    for row in rows:
        left, right = fold_name(row["drug_class1"]), fold_name(row["drug_class2"])
        moieties = resolved[left].resolved and resolved[right].resolved
        both_keyed = all(
            resolved[name].resolved or is_class.get(name) is not None
            for name in (left, right))
        moiety_by_moiety_rows += moieties
        keyable_rows += both_keyed

    return ClassCoverage(
        names=len(folded),
        names_resolved=sum(1 for r in resolved.values() if r.resolved),
        names_matching_a_class=sum(by_source.values()),
        by_source=dict(by_source),
        names_matching_nothing=sum(
            1 for source in is_class.values() if source is None),
        keyable_rows=keyable_rows,
        moiety_by_moiety_rows=moiety_by_moiety_rows,
    )


@dataclass(frozen=True)
class NameProvenance:
    """Which of DrugCentral's OWN tables knows each endpoint name.

    The step before drugref is consulted at all, and the evidence for the claim
    that a synonym bridge is unnecessary: if DrugCentral can name the structure
    itself, drugref never has to learn the spelling.
    """

    names: int
    in_structures: int
    in_synonyms_only: int
    in_neither: int


def name_provenance(
    names: Iterable[str],
    structures: Sequence[Mapping[str, str | None]],
    synonyms: Sequence[Mapping[str, str | None]],
) -> NameProvenance:
    """Count endpoint names against `structures` first, then `synonyms`."""
    primary = build_endpoint_index(structures, ())
    full = build_endpoint_index(structures, synonyms)

    counts = collections.Counter()
    distinct = {fold_name(name) for name in names}
    for name in distinct:
        if primary.struct_id_for(name) is not None:
            counts["structures"] += 1
        elif full.struct_id_for(name) is not None:
            counts["synonyms"] += 1
        else:
            counts["neither"] += 1

    return NameProvenance(
        names=len(distinct),
        in_structures=counts["structures"],
        in_synonyms_only=counts["synonyms"],
        in_neither=counts["neither"],
    )
