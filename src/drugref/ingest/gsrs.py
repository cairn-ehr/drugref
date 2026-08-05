# src/drugref/ingest/gsrs.py
"""Pure, streaming parser for the GSRS public data dump (slice 3).

Reads ~2.05 GB of JSON-lines without holding it in memory, and TOUCHES NO
DATABASE -- the architecture invariant every ingest parser in this repo obeys.
The orchestrator (ingest/gsrs_run.py) owns the transaction.

THE DIRECTION CONVENTION IS THE WHOLE MODULE, and it is inverted from the naive
reading. For a relationship of type "A->B" stored on record X and pointing at Y:

    X plays role B, and Y plays role A.

The stored relationship is the INBOUND edge. This is the same class of upstream
erratum as MED-RT's "Parent Of runs parent -> child" (PROJECT-NOTES), and like
that one it is invisible to a small fixture: read naively, one "salt" in the real
release had 124 parents; read correctly, the busiest PARENTS are Maleic Acid
(124 salts), Tartaric Acid (123) and citric acid (117) -- exactly the counterions
a base should have many salts of. Two independent checks pin it (test_gsrs_fixture):
the two mirror encodings agree on 15,039 edges, and every solvate has exactly one
anhydrous parent.

WHAT IS DELIBERATELY NOT AN EDGE. `ACTIVE MOIETY` is the ION level, not a
composition: 71% of its 33,647 edges are self-references, and every magnesium form
-- including drugref's own moiety -- points at MAGNESIUM CATION. As an equivalence
join it would assert that levomefolate magnesium is interchangeable with magnesium
sulfate (35 substances share that cation, 27 of them drugref moieties), which is
the discredited sulfonamide inference one level down. It reaches the projection
ONLY as `is_active_component`, a discriminator INSIDE a composition.

Likewise NOT an edge: the top-level `moieties` key, which holds STRUCTURAL
fragments of the chemical (two for chlortetracycline bisulfate, carrying no UNII)
and is a different concept that merely shares a word.
"""

import dataclasses
import gzip
import json
import logging
import pathlib
from collections.abc import Iterator

# Declared locally, exactly as ingest/checksum.py and ingest/mesh.py each do. There
# is no shared paths module and one type alias does not justify creating one.
StrPath = str | pathlib.Path

log = logging.getLogger(__name__)

# The two composition axes, as stored in substance_composition.relation. These
# strings are also seeded into db/028's composition_relation table, which the
# column is a foreign key into -- the vocabulary's one home is the TABLE, and
# these constants exist only so the writer can name a row it inserts.
SALT_SOLVATE = "SALT_SOLVATE"
SOLVATE_ANHYDROUS = "SOLVATE_ANHYDROUS"

# The direction convention, as data rather than as four if-branches.
#
# Each entry maps an upstream relationship type to (relation, record_is_composite).
# `record_is_composite` says which end of the stored relationship is the COMPOSITE:
# True  -- the record holding the relationship is the salt/solvate, the target is
#          the component;
# False -- the record is the component (the parent base or anhydrous form) and the
#          TARGET is the composite.
#
# Read it against the convention above: for "PARENT->SALT/SOLVATE" the record plays
# the right-hand role (SALT/SOLVATE), so it is the composite.
_AXES = {
    "PARENT->SALT/SOLVATE": (SALT_SOLVATE, True),
    "SALT/SOLVATE->PARENT": (SALT_SOLVATE, False),
    "ANHYDROUS->SOLVATE": (SOLVATE_ANHYDROUS, True),
    "SOLVATE->ANHYDROUS": (SOLVATE_ANHYDROUS, False),
}

ACTIVE_MOIETY = "ACTIVE MOIETY"


@dataclasses.dataclass(frozen=True)
class CompositionEdge:
    """One composition statement, normalised so both encodings produce one row."""

    substance_unii: str  # the COMPOSITE (a salt, or a hydrate)
    component_unii: str  # what it is composed of
    relation: str  # SALT_SOLVATE | SOLVATE_ANHYDROUS


@dataclasses.dataclass(frozen=True)
class GsrsRecord:
    """One substance, reduced to the two things slice 3 needs.

    NO display_name, deliberately. An earlier draft parsed one and nothing ever read
    it: substance_composition has no name column, because the composite side is a
    bare UNII from the source and this slice mints no identity for it. Carrying a
    field with no consumer meant a per-record scan of `names` across all 173,080
    records to populate something only a test asserted on. The slice that needs a
    name adds it with the consumer that wants it.
    """

    unii: str
    edges: tuple[CompositionEdge, ...]
    # NON-SELF active-moiety targets. Empty means the release says nothing about
    # which component is active -- which the writer records as NULL (unruled), NOT
    # as false. A self-reference is not a ruling about a component either, so it is
    # excluded here rather than downstream.
    active_moieties: frozenset[str]


def normalise_relationship(
    record_unii: str, rel_type: str, target_unii: str
) -> CompositionEdge | None:
    """Turn one stored relationship into a normalised edge, or None.

    None means "not a composition statement" -- an unrelated type, an ACTIVE MOIETY
    (which is handled separately), or a self-edge. Pure: no I/O, no state.
    """
    axis = _AXES.get(rel_type)
    if axis is None or record_unii == target_unii:
        return None
    relation, record_is_composite = axis
    composite, component = (
        (record_unii, target_unii)
        if record_is_composite
        else (target_unii, record_unii)
    )
    return CompositionEdge(
        substance_unii=composite, component_unii=component, relation=relation
    )


def iter_records(path: StrPath) -> Iterator[GsrsRecord]:
    """Stream the dump, yielding one GsrsRecord per substance carrying a UNII.

    THE LINE FORMAT IS NOT PLAIN JSON-LINES: every line is prefixed by two TAB
    characters before the '{'. Slicing from the first brace rather than stripping a
    fixed prefix keeps this working if upstream changes the padding.

    A record with no `approvalID` is skipped -- 5,078 of 173,080 carry none, and
    they can join to nothing. A malformed line is logged and skipped rather than
    aborting: one bad line in 2.05 GB must not cost the other 173,079 records.
    """
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle, start=1):
            brace = line.find("{")
            if brace < 0:
                continue
            try:
                record = json.loads(line[brace:])
            except ValueError:
                log.warning("gsrs: skipping unparseable line %d", lineno)
                continue
            unii = record.get("approvalID")
            if not unii:
                continue

            edges: list[CompositionEdge] = []
            actives: set[str] = set()
            for relationship in record.get("relationships") or []:
                target = (relationship.get("relatedSubstance") or {}).get("approvalID")
                if not target:
                    continue
                rel_type = relationship.get("type")
                if rel_type == ACTIVE_MOIETY:
                    if target != unii:
                        actives.add(target)
                    continue
                edge = normalise_relationship(unii, rel_type, target)
                if edge is not None:
                    edges.append(edge)

            yield GsrsRecord(
                unii=unii,
                edges=tuple(dict.fromkeys(edges)),
                active_moieties=frozenset(actives),
            )
