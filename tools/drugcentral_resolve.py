"""Resolve a DrugCentral `ddi` endpoint to a drugref moiety, structurally.

**The problem.** DrugCentral's `ddi` table names both endpoints as free text in a
``varchar(500)`` with no code of any kind -- ``'warfarin'``, ``'ciclosporin'``,
``'Strong CYP3A4 Inhibitors'``. Nothing in the row says whether a name denotes a
drug or a class, and nothing keys it to any external vocabulary.

**The obvious approach, and why it under-performs.** Issue #101 matched those names
against ``substance_moiety.display_name`` and reached 857 of the 924 NDF-RT
endpoint names. It concluded that the ~87 residual INN spellings -- drugref carries
UNII's USAN spelling ``cyclosporine``, DrugCentral says ``ciclosporin`` -- *"need a
synonym bridge"*, i.e. a hand-maintained list that someone has to own forever.

**The route measured here instead.** DrugCentral resolves its own endpoint text
against its own tables: 905 of 924 names are a ``structures.name`` and 17 more a
``synonyms.name``, leaving 2. A `structures` row carries an **InChIKey** and a
**CAS** number, and drugref already holds both as live `identity_claim` rows
(16,046 InChIKeys, 19,010 CAS). So an endpoint can be resolved by the STRUCTURE it
denotes rather than by how it is spelled -- which is principle 2 of this project
(*never key on a name*) applied to the resolution step itself.

Measured on the real 2023-11-01 dump against a `db/048` registry built from the
pinned releases, the cascade moves endpoint resolution from **857/924 to 914/924**
and unresolvable rows from **598 to 37**, with no hand-maintained list.

**Why the cascade is ordered display_name, then InChIKey, then CAS.**

1. `display_name` first because a direct hit needs no dump-side lookup and is the
   route a reader can check by eye.
2. InChIKey second: it denotes a structure exactly.
3. CAS last: it is an administrative registry number that upstream sources reuse
   loosely across hydrates and salt forms, so it is the weakest of the three.

Every function reports **which route answered**. A resolution figure that cannot
say how it resolved cannot be audited, and this slice exists to replace figures
nobody could re-derive.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# The closed vocabulary of ways an endpoint can be resolved. Closed on purpose:
# a route that is not one of these is a bug, not a new case to tolerate.
ROUTE_DISPLAY_NAME = "display_name"
ROUTE_INCHIKEY = "inchikey"
ROUTE_CAS = "cas"
ROUTE_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Registry:
    """The drugref side of the join: three lookups onto ``moiety_uuid``.

    Held as plain dicts rather than a live connection so that every function in
    this module is pure and testable without a database. The caller loads them
    once (see ``tools/drugcentral_ddi_spike.py``).

    Attributes:
        display_name: ``lower(substance_moiety.display_name)`` -> ``moiety_uuid``.
        inchikey: ``upper(identity_claim.value)`` for scheme ``INCHIKEY``.
        cas: ``upper(identity_claim.value)`` for scheme ``CAS``.
    """

    display_name: Mapping[str, str]
    inchikey: Mapping[str, str]
    cas: Mapping[str, str]


@dataclass(frozen=True)
class EndpointIndex:
    """The DrugCentral side: endpoint text -> ``struct_id`` -> structural keys.

    Attributes:
        names: ``lower(name)`` -> ``struct_id``, from `structures` then `synonyms`.
        keys: ``struct_id`` -> ``(inchikey, cas_reg_no)``, upper-cased, possibly
            empty strings for a substance DrugCentral holds no structure for.
    """

    names: Mapping[str, str] = field(default_factory=dict)
    keys: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    def struct_id_for(self, name: str) -> str | None:
        """Return the ``struct_id`` this endpoint text denotes, or ``None``."""
        return self.names.get(name.strip().lower())


def build_endpoint_index(
    structures: Iterable[Mapping[str, str | None]],
    synonyms: Iterable[Mapping[str, str | None]],
) -> EndpointIndex:
    """Index DrugCentral's own name tables.

    `structures` is loaded first and `synonyms` second, with ``setdefault``
    semantics, so **a primary name always wins over a synonym claiming the same
    text**. DrugCentral's `synonyms` table is much the larger of the two and grows
    between releases; letting it displace a primary name would make the index
    unstable for no gain.

    Args:
        structures: rows with ``id``, ``name``, ``inchikey``, ``cas_reg_no``.
        synonyms: rows with ``id`` (a ``struct_id``) and ``name``.
    """
    names: dict[str, str] = {}
    keys: dict[str, tuple[str, str]] = {}

    for row in structures:
        struct_id = row["id"]
        if struct_id is None:
            continue
        keys[struct_id] = (
            (row.get("inchikey") or "").strip().upper(),
            (row.get("cas_reg_no") or "").strip().upper(),
        )
        name = (row.get("name") or "").strip().lower()
        if name:
            names.setdefault(name, struct_id)

    for row in synonyms:
        struct_id = row["id"]
        name = (row.get("name") or "").strip().lower()
        if struct_id is not None and name:
            names.setdefault(name, struct_id)

    return EndpointIndex(names=names, keys=keys)


def resolve_endpoint(
    name: str,
    index: EndpointIndex,
    registry: Registry,
) -> tuple[str | None, str]:
    """Resolve one endpoint name to ``(moiety_uuid, route)``.

    Returns ``(None, ROUTE_UNRESOLVED)`` when no route answers -- which is the
    correct outcome for a CLASS-named endpoint such as
    ``'Strong CYP3A4 Inhibitors'``, since this function resolves substances only.

    A blank structural key is never looked up. `structures` stores an empty
    InChIKey for biologics and mixtures, and a registry that happened to contain
    the empty string would otherwise collapse every keyless substance onto one
    moiety -- a silent, catastrophic merge rather than an honest miss.
    """
    direct = registry.display_name.get(name.strip().lower())
    if direct is not None:
        return direct, ROUTE_DISPLAY_NAME

    struct_id = index.struct_id_for(name)
    if struct_id is None:
        return None, ROUTE_UNRESOLVED

    inchikey, cas = index.keys.get(struct_id, ("", ""))

    if inchikey:
        by_key = registry.inchikey.get(inchikey)
        if by_key is not None:
            return by_key, ROUTE_INCHIKEY

    if cas:
        by_cas = registry.cas.get(cas)
        if by_cas is not None:
            return by_cas, ROUTE_CAS

    return None, ROUTE_UNRESOLVED


def unordered_pair(left: str | None, right: str | None) -> tuple[str, str] | None:
    """Normalise two moiety UUIDs into one orientation-independent pair.

    Returns ``None`` when either endpoint is unresolved, and **also when both
    resolve to the same moiety**. A rule whose two endpoints denote one substance
    asserts nothing about an interaction between two drugs; `db/018` subtracts
    exactly that case on the drugref side, and counting it here would inflate
    every overlap figure by rows that cannot become candidate pairs.

    PROJECT-NOTES § "The 5c.3 source evaluation" warns that rows, pairs and
    distinct pairs are three different units that were being quoted
    interchangeably. This function defines the third one.
    """
    if left is None or right is None or left == right:
        return None
    return (left, right) if left <= right else (right, left)
