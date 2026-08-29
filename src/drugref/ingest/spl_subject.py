# src/drugref/ingest/spl_subject.py
"""Which drug a label's interactions section is ABOUT, and how we know.

PURE, per the architecture invariant: it takes a label's UNIIs, an optional
DailyMed reading and drugref's own UNII bridge, and returns a route plus the
moieties on it. No database, no I/O.

An interaction statement with no subject is not an interaction statement, and
`moiety_uuid` is UUIDv5 on UNII -- so the subject question is entirely a question
about UNIIs. **THREE STRUCTURAL ROUTES ANSWER IT** and two come from DailyMed's
XML. A fourth, HEURISTIC route was found and rejected (owner's call, 2026-08-24):
`spl_product_data_elements` is populated on 99.5% of unkeyed records but is one
flattened uppercase string of product name, active ingredients, moieties **and
excipients, undelimited**, averaging 7.69 registry matches per label; taking rank
0 is **genuinely wrong 6.2%** of the time. Its 6,317-label overlap with route 2
is kept as a permanent calibration set (#158), so any future heuristic route has
ground truth to be measured against before it ships.

**THE ROUTES ARE EXCLUSIVE BY CONSTRUCTION**, and `db/051`'s
`spl_label_subject_complete` CHECK depends on it: one label, one route, and **the
salt is never a second subject beside the moiety**. Blending them published
31,618 pairs where the exclusive rule gave 29,258 in the SAME comparison --
drugref registers a salt as its own moiety with its own live UNII claim, so a
salt product paired against every partner twice, on 56.7% of resolvable DailyMed
labels.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from drugref.ingest import spl_dailymed

#: THE ROUTE VOCABULARY. `db/051`'s `spl_label_subject_route` CHECK is its SECOND
#: home, admitted deliberately on the same terms `drugcentral_ddi_assertion_route_1`
#: lives under, and pinned by a test that reads this tuple and the catalog CHECK
#: and compares them. A value the resolver can produce and the CHECK does not
#: admit aborts an ingest at whichever row happens to carry it first.
SUBJECT_ROUTES = (
    # openFDA's own `openfda.unii` block. 27,494 labels.
    "openfda_unii",
    # SPL `<activeMoiety>` under an ACTIVE ingredient in DailyMed's XML. 10,555.
    "dailymed_active_moiety",
    # The SALT only -- issue 67, counted apart so it cannot hide inside the
    # recovery figure: it needs a salt-to-base step drugref does not have, so
    # folding it in would promise a route that is not built. 23 labels.
    "dailymed_active_substance",
    # The label is not in the current DailyMed Human Rx release. 30,386 -- and it
    # may be in tomorrow's, which is why *absence is a population, not a bug*.
    "absent_from_dailymed",
    # Present, read, and still unkeyable. **92 labels.** The design round
    # predicted 14,680 here, because its probe filed 14,455 labels it had never
    # READ into a bucket whose definition is "present, read, and still
    # unkeyable". Scanned for real, the recovery register is 99.7% a RELEASE gap
    # and 0.3% a registry gap -- the opposite of what that table would have
    # anyone plan for. See the 2026-08-27 results record, section 3.
    "unresolved",
)

#: The routes that put a `moiety_uuid` on the row. The complement is not a second
#: list: `db/051`'s CHECK is written as `(route IN <these>) = (moiety_uuid IS NOT
#: NULL)`, so "resolved with no uuid" and "a uuid on an unresolved route" are both
#: UNREPRESENTABLE rather than merely discouraged.
RESOLVING_ROUTES = (
    "openfda_unii", "dailymed_active_moiety", "dailymed_active_substance")


@dataclass(frozen=True, kw_only=True)
class Subject:
    """One label's subject drug: the route that answered, and what it found.

    `moiety_uuids` is empty on exactly the non-resolving routes -- asserted here
    rather than left to the caller, because the caller is a writer and the
    database CHECK it would violate fires at the END of a 68,550-label ingest.
    """

    route: str
    moiety_uuids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.route not in SUBJECT_ROUTES:
            raise ValueError(
                f"route {self.route!r} is not one of {SUBJECT_ROUTES}; "
                "db/051's spl_label_subject_route CHECK would refuse it")
        if (self.route in RESOLVING_ROUTES) != bool(self.moiety_uuids):
            raise ValueError(
                f"route {self.route!r} carries {len(self.moiety_uuids)} "
                "moieties; db/051's spl_label_subject_complete CHECK makes "
                "'resolved with no moiety' and 'a moiety on an unresolved "
                "route' both unrepresentable")


def _resolved(uniis: Iterable[str], known_uniis: Mapping[str, str]) -> tuple[str, ...]:
    """The moiety UUIDs these UNIIs reach, ordered and de-duplicated.

    ORDERED because two runs over one release must agree: an unordered set would
    let `subject_ordinal` describe a different subject on the second run, and the
    row is externally readable.
    """
    return tuple(sorted({known_uniis[u] for u in uniis if u in known_uniis}))


def resolve_subject(
    *,
    openfda_uniis: Sequence[str],
    dailymed: spl_dailymed.SubjectUniis | None,
    known_uniis: Mapping[str, str],
) -> Subject:
    """The one subject rule, applied in one place.

    PRECEDENCE, and each step is a measured decision rather than a preference:

    1. **openFDA's own `openfda.unii`, where it resolves.** It is the authority
       where it exists, and preferring a DailyMed reading over it would move the
       baseline the published +42.3% recovery delta was measured against.
    2. **DailyMed's active MOIETY**, which is the grain drugref keys on.
    3. **DailyMed's active SUBSTANCE** -- the salt -- ONLY where no moiety UNII
       resolved. Never beside one.
    4. Otherwise the label has no subject, and *which kind of nothing* is
       recorded: `absent_from_dailymed` when the release does not carry it,
       `unresolved` when it was read and is still unkeyable. Folding those two
       together would republish a fact about a RELEASE as a fact about drugref's
       registry coverage.

    Note that step 1 branches on RESOLUTION, not presence, which is what catches
    the **200 labels carrying a UNII drugref does not hold**: they offer a UNII,
    it resolves to nothing, and step 1 therefore declines them. They do NOT stop
    there -- they fall through to DailyMed like any other unkeyed label, and many
    of them get a subject that way. The subject-recovery probe's classifiers
    branched on PRESENCE and filed them as already keyed, which is one of the two
    reasons this slice's pair count is a floor rather than a target.

    (The sentence this replaces said they "therefore have no subject", which
    contradicted the fall-through two lines below it and described the probe's
    classification rather than this function's.)
    """
    openfda_hit = _resolved(openfda_uniis, known_uniis)
    if openfda_hit:
        return Subject(route="openfda_unii", moiety_uuids=openfda_hit)

    if dailymed is None:
        return Subject(route="absent_from_dailymed", moiety_uuids=())

    # The precedence below is spl_dailymed.subject_uniis's, and the route is
    # spl_dailymed.subject_route's -- both derived from the same comparison in the
    # same order, so the recorded route and the recorded moieties can never
    # describe different readings of one label.
    known = set(known_uniis)
    chosen = spl_dailymed.subject_uniis(dailymed, known)
    route = spl_dailymed.subject_route(dailymed, known)
    if route is None:
        return Subject(route="unresolved", moiety_uuids=())
    return Subject(route=route, moiety_uuids=_resolved(chosen, known_uniis))


def needs_dailymed(
    *, openfda_uniis: Sequence[str], known_uniis: Mapping[str, str]
) -> bool:
    """Whether the expensive DailyMed pass has to look for this label.

    The test is "does openFDA give it a subject drugref can KEY", not "does
    openFDA give it a UNII". Those differ by the 200 labels above, and branching
    on presence is exactly what excluded them from the measurement.
    """
    return not _resolved(openfda_uniis, known_uniis)


def dailymed_targets(
    rows: Iterable[Mapping], *, known_uniis: Mapping[str, str]
) -> set[str]:
    """The `set_id`s the DailyMed scan must look for.

    ⇒ **EVERY LABEL WITHOUT A RESOLVED SUBJECT, INCLUDING THE ONES SHARING A
    KEYED LABEL'S WORDING.** The subject-recovery probe skipped 14,455 of those
    as an optimisation, and the skip is valid for the WORDING unit: another
    manufacturer reprinting a wording drugref already reaches rediscovers a
    statement drugref already has. **It is not valid for a SUBJECT.** A label's
    subject is its own, an unkeyed label sharing a keyed label's wording may be a
    different drug, and its pairs are uncounted -- which is why every pair figure
    here is a floor and the orchestrator's check asserts `>=`.

    Two collisions are REFUSED rather than absorbed, because both silently delete
    a label from the universe before the expensive pass starts, and the label
    count and the target count would then disagree with nothing to say so.
    """
    targets: set[str] = set()
    seen: set[str] = set()
    for row in rows:
        set_id = row["set_id"]
        if not set_id:
            raise ValueError(
                "a label carries no set_id: it can never be found in DailyMed, "
                "and keying it on '' would collapse every such label into one "
                "target")
        if set_id in seen:
            raise ValueError(
                f"set_id {set_id!r} appears on more than one label: keying "
                "targets by set_id would drop one, and the label count and the "
                "target count would then disagree silently")
        seen.add(set_id)
        if needs_dailymed(openfda_uniis=row["uniis"], known_uniis=known_uniis):
            targets.add(set_id)
    return targets
