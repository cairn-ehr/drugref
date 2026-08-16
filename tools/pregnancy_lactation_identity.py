"""Read-only identity measurements for the pregnancy/lactation source spike."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from drugref.ingest import lactmed
from drugref.ingest import regulatory_population as regulatory


@dataclass(frozen=True)
class IdentityIndex:
    """Existing exact claims and normalized names, indexed by moiety UUID."""

    claims: dict[str, dict[str, frozenset[str]]]
    names: dict[str, frozenset[str]]


def load_identity_index(conn) -> IdentityIndex:
    """Load a read-only identity index from the current Drugref registry."""

    claims: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    rows = conn.execute(
        "SELECT scheme, value, moiety_uuid FROM drugref.identity_claim "
        "WHERE superseded_by IS NULL AND scheme IN ('UNII', 'CAS') "
        "ORDER BY scheme, value, moiety_uuid"
    ).fetchall()
    for scheme, value, moiety_uuid in rows:
        claims[scheme][value].add(str(moiety_uuid))

    names: dict[str, set[str]] = defaultdict(set)
    rows = conn.execute(
        "SELECT display_name, moiety_uuid FROM drugref.substance_moiety "
        "ORDER BY display_name, moiety_uuid"
    ).fetchall()
    for name, moiety_uuid in rows:
        names[regulatory.normalized_name(name)].add(str(moiety_uuid))
    return IdentityIndex(
        claims={
            scheme: {
                value: frozenset(ids) for value, ids in index.items()
            }
            for scheme, index in claims.items()
        },
        names={name: frozenset(ids) for name, ids in names.items()},
    )


def resolve_lactmed(
    record: lactmed.LactMedRecord,
    index: IdentityIndex,
) -> tuple[str, frozenset[str]]:
    """Measure exact claim resolution before considering a name candidate."""

    matches: set[str] = set()
    for unii in record.uniis:
        matches.update(index.claims.get("UNII", {}).get(unii, ()))
    for cas in record.cas_numbers:
        matches.update(index.claims.get("CAS", {}).get(cas, ()))
    if len(matches) == 1:
        return "resolved_exact_claim", frozenset(matches)
    if len(matches) > 1:
        return "ambiguous_identity", frozenset(matches)
    by_name = index.names.get(
        regulatory.normalized_name(record.title), frozenset()
    )
    if len(by_name) == 1:
        return "candidate_unique_name", by_name
    if len(by_name) > 1:
        return "ambiguous_name", by_name
    return "unresolved", frozenset()


def resolve_name(
    name: str,
    index: IdentityIndex,
) -> tuple[str, frozenset[str]]:
    """Measure normalized-name candidates without admitting an identity claim."""

    matches = index.names.get(regulatory.normalized_name(name), frozenset())
    if len(matches) == 1:
        return "candidate_unique_name", matches
    if len(matches) > 1:
        return "ambiguous_name", matches
    return "unresolved", frozenset()
