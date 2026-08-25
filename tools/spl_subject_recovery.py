"""Can the subject-less SPL labels have their subject recovered?

**Throwaway spike code for the slice 5c.3 design round.** It answers a
question; it is not the ingest, and nothing under ``src/drugref/`` imports it.
The document-reading half lives in :mod:`tools.spl_subject_read`.

**40,856 of the 68,550 section-carrying openFDA labels (59.6%) carry no
``unii``** in openFDA's normalising ``openfda`` block. ``moiety_uuid`` is UUIDv5
on UNII, so no ``unii`` means no subject, and an interaction statement with no
subject is not an interaction statement.

**That is not the parent round's 41,056, and the 200-label difference is a
definition rather than a discrepancy.** The parent counted labels with no
*resolvable* subject (68,550 - 27,494); this module counts labels with no UNII
at all (68,550 - 27,694). The 200 in between carry a UNII drugref does not
hold. Every classifier here branches on PRESENCE, so 200 labels are filed as
keyed that a pair rule would reject -- which makes the orphan-wording and
target populations below floors, not exact counts. Note also that the
``openfda`` block itself is PRESENT on 100% of them and merely empty: a
presence check would report full coverage.

The recovery route is known: the ``set_id`` joins to DailyMed's own SPL XML,
which carries the full ingredient list. It is not free -- 17.6 GB of nested
zips -- so this module measures the CHEAP BOUND first:

    Wordings are shared 2.50 labels to one. An unkeyed label whose wording also
    appears on a KEYED label carries no statement drugref cannot already reach;
    it is a second manufacturer printing the same words. Only wordings reachable
    *solely* through unkeyed labels can add anything, and that is computable
    from the openFDA cache alone.

Whatever survives that bound is then chased into DailyMed's XML by
:func:`tools.spl_subject_read.extract_subject_uniis`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from tools.spl_subject_read import SubjectUniis, dedupe_by_set_id, subject_uniis

@dataclass(frozen=True, kw_only=True)
class WordingReachability:
    """How much of the unkeyed corpus could possibly add anything.

    **Be clear about what the two sum identities can catch**, on the terms
    ``drugcentral_run.Measurement`` already states for its own: at the one call
    site in :func:`classify_wordings` both hold BY CONSTRUCTION -- ``keyed_keys``
    is a subset of ``all_keys`` by set algebra, and every row increments exactly
    one label bucket. They are a contract for a future caller, **not a guard that
    can fail where they are currently used**, and in particular neither can catch
    a swapped branch: invert the redundant/recoverable test and both still hold
    while every published figure flips.

    The checks below them CAN fail, and they are the ones aimed at the bug class
    this round was actually burned by -- a count of ROWS standing in for a count
    of distinct keys. A wording cannot be keyed by fewer labels than there are
    wordings, and no bucket may be negative.
    """

    distinct_wordings: int
    keyed_wordings: int
    orphan_wordings: int
    labels: int
    keyed_labels: int
    recoverable_unkeyed_labels: int
    redundant_unkeyed_labels: int

    def __post_init__(self) -> None:
        for name in (
            "distinct_wordings", "keyed_wordings", "orphan_wordings", "labels",
            "keyed_labels", "recoverable_unkeyed_labels",
            "redundant_unkeyed_labels",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name}={getattr(self, name)} is negative")
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
        if self.keyed_labels < self.keyed_wordings:
            raise ValueError(
                f"{self.keyed_labels} keyed labels cannot carry "
                f"{self.keyed_wordings} keyed wordings: each keyed wording needs "
                "at least one keyed label"
            )
        if self.recoverable_unkeyed_labels < self.orphan_wordings:
            raise ValueError(
                f"{self.recoverable_unkeyed_labels} recoverable labels cannot "
                f"carry {self.orphan_wordings} orphan wordings"
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

    **⇒ THE SKIP IS VALID FOR THE WORDING UNIT ONLY, AND AN INGEST MUST NOT
    INHERIT IT.** A label's SUBJECT is its own: an unkeyed label sharing a keyed
    label's wording may be a different drug, and would form pairs nobody has
    counted. So every pair figure derived from this target list is a **floor**,
    and the shipped ingest must scan every unkeyed label.

    Two collisions are refused rather than absorbed, because both silently
    DELETE wordings from the universe before the expensive pass starts -- and
    ``classify_wordings`` counts rows while this keys a dict, so the two
    published populations would then disagree with nothing to say so.
    """
    rows = list(rows)
    keyed_keys = {row["text_key"] for row in rows if row.get("uniis")}
    targets: dict[str, str] = {}
    for row in rows:
        if row.get("uniis") or row["text_key"] in keyed_keys:
            continue
        set_id = row["set_id"]
        if not set_id:
            raise ValueError(
                "a cache row carries no set_id: it can never be found in "
                "DailyMed, and keying it on '' would collapse every such row "
                "into one target"
            )
        if set_id in targets:
            raise ValueError(
                f"set_id {set_id!r} appears on more than one cache row: "
                "keying targets by set_id would drop a wording, and the row "
                "count and the target count would then disagree silently"
            )
        targets[set_id] = row["text_key"]
    return targets


@dataclass(frozen=True, kw_only=True)
class RecoverySummary:
    """What the DailyMed scan bought, counted in the unit that matters.

    ``wordings_rescued`` is the headline and ``labels_resolved`` is not: two
    labels carrying one orphan wording rescue ONE statement between them, and
    reporting labels would publish the de-duplication factor as if it were a
    result. The corpus census was already burned once by quoting one unit as
    the other.

    **``labels_found_but_unresolvable`` is a bucket because it was a
    subtraction.** A label DailyMed carries, whose UNII drugref has never heard
    of, used to increment nothing at all: it was neither "without any UNII" nor
    "resolved", so 25 labels on the real release sat in a population the report
    never named, and "99.6% of those found resolve" was true only if they did
    not exist. They are a **registry coverage gap** -- a finding, not a rounding.
    """

    wordings_targeted: int
    labels_targeted: int
    labels_found: int
    labels_missing_from_dailymed: int
    labels_without_any_unii: int
    labels_resolved: int
    labels_found_but_unresolvable: int
    resolved_on_moiety: int
    resolved_on_substance_only: int
    wordings_rescued: int

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value < 0:
                raise ValueError(f"{name}={value} is negative")
        if self.labels_found + self.labels_missing_from_dailymed != (
            self.labels_targeted
        ):
            raise ValueError(
                f"{self.labels_found} found + "
                f"{self.labels_missing_from_dailymed} missing != "
                f"{self.labels_targeted} targeted"
            )
        accounted = (
            self.labels_without_any_unii
            + self.labels_resolved
            + self.labels_found_but_unresolvable
        )
        if accounted != self.labels_found:
            raise ValueError(
                f"found labels account for {accounted} of {self.labels_found}: "
                "every label read is empty, resolved, or unresolvable"
            )
        if self.resolved_on_moiety + self.resolved_on_substance_only != (
            self.labels_resolved
        ):
            raise ValueError(
                f"{self.resolved_on_moiety} on moiety + "
                f"{self.resolved_on_substance_only} on salt != "
                f"{self.labels_resolved} resolved"
            )
        if self.wordings_targeted > self.labels_targeted:
            raise ValueError("more wordings targeted than labels targeted")
        if self.wordings_rescued > min(self.wordings_targeted,
                                       self.labels_resolved):
            raise ValueError(
                f"{self.wordings_rescued} wordings rescued exceeds what "
                "the targeted wordings or the resolved labels can support"
            )

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

    **Rows are de-duplicated by ``set_id`` first**, through
    :func:`dedupe_by_set_id`, which is also what the yield stage uses. DailyMed
    ships successive VERSIONS of one label as separate documents sharing a
    set_id, so the scan emits more rows than labels -- 44 more on the real
    corpus. Counting rows reported 6,583 labels found where 6,539 exist, and the
    error was invisible until the total was cross-checked against an independent
    pass.

    **``labels_missing_from_dailymed`` is COUNTED, not derived.** It used to be
    ``len(targets) - found``, and that is why the guard added with the fix would
    still not have caught the original bug: 6,583 + 19,818 balances
    ``len(targets)`` exactly. A subtraction absorbs any upstream drop without
    residue. Counted as a set difference, the same bad input no longer adds up.

    A label resolving only on its SALT is counted separately rather than folded
    in: it needs a salt-to-base step drugref does not have
    ([#67](https://github.com/cairn-ehr/drugref/issues/67)), so counting it as
    recovered would promise a route that is not built. :func:`subject_uniis`
    applies the same precedence wherever a subject is actually used.
    """
    without_unii = 0
    unresolvable = 0
    on_moiety = 0
    on_substance_only = 0
    rescued_keys: set[str] = set()

    rows = list(recovered)
    for row in rows:
        if row.set_id not in targets:
            raise ValueError(
                f"set_id {row.set_id!r} was not a scan target: the cache and "
                "the scan disagree about which corpus they read"
            )
    by_set_id = dedupe_by_set_id(rows)

    for row in by_set_id.values():
        key = targets[row.set_id]
        if not row.has_any_unii:
            without_unii += 1
            continue
        moiety_hit = any(unii in known_uniis for unii in row.moiety_uniis)
        substance_hit = any(unii in known_uniis for unii in row.substance_uniis)
        if moiety_hit:
            on_moiety += 1
        elif substance_hit:
            on_substance_only += 1
        else:
            unresolvable += 1
            continue
        rescued_keys.add(key)

    return RecoverySummary(
        wordings_targeted=len(set(targets.values())),
        labels_targeted=len(targets),
        labels_found=len(by_set_id),
        labels_missing_from_dailymed=len(targets.keys() - by_set_id.keys()),
        labels_without_any_unii=without_unii,
        labels_resolved=on_moiety + on_substance_only,
        labels_found_but_unresolvable=unresolvable,
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
    rows: Iterable[Mapping],
    recovered: Mapping[str, SubjectUniis],
    *,
    known_uniis: set[str] | frozenset[str],
) -> list[dict]:
    """The cache rows with recovered subjects filled into the empty ones.

    Feeding these to the same pair counter the measurement round used is what
    makes the delta comparable: the only thing that changed is which labels have
    a subject.

    A row that already carries ``uniis`` is returned untouched. openFDA's own
    ``openfda.unii`` is the authority where it exists, and overwriting it with a
    DailyMed reading would move the baseline the delta is measured against.

    **The subject rule is :func:`subject_uniis`, not "every UNII we read".** This
    function used to write ``moiety_uniis + substance_uniis``, and because
    drugref registers a salt as its own moiety with its own live UNII claim, a
    salt product then contributed TWO subjects to the pair counter where the
    openFDA baseline arm contributed one. The delta was measured with a broader
    rule than its own baseline.
    """
    augmented = []
    for row in rows:
        new_row = dict(row)
        if not new_row.get("uniis"):
            found = recovered.get(new_row["set_id"])
            if found is not None:
                new_row["uniis"] = list(subject_uniis(found, known_uniis))
        augmented.append(new_row)
    return augmented
