"""Resolve a DrugCentral `ddi` endpoint to a drugref moiety, structurally.

**The problem.** DrugCentral's `ddi` table names both endpoints as free text in a
``varchar(500)`` with no code of any kind -- ``'warfarin'``, ``'ciclosporin'``,
``'Strong CYP3A4 Inhibitors'``. Nothing in the row says whether a name denotes a
drug or a class, and nothing keys it to any external vocabulary.

**The obvious approach, and why it under-performs.** Issue #101 matched those names
against ``substance_moiety.display_name`` alone. It concluded that the residual INN
spellings -- drugref carries UNII's USAN spelling ``cyclosporine``, DrugCentral says
``ciclosporin`` -- *"need a synonym bridge"*, i.e. a hand-maintained list that
someone has to own forever.

**The route measured here instead.** DrugCentral resolves its own endpoint text
against its own tables: most endpoint names are a ``structures.name``, and most of
the rest a ``synonyms.name``. A `structures` row carries an **InChIKey** and a
**CAS** number, and drugref already holds both as live `identity_claim` rows. So an
endpoint can be resolved by the STRUCTURE it denotes rather than by how it is
spelled -- which is ROADMAP principle 2 (*never key on a name*) applied to the
resolution step itself.

The cascade materially beats name matching, with no hand-maintained list. **The
measured figures live in the generated results file, not here** -- see
PROJECT-NOTES § "The DrugCentral re-measurement", and re-run
``tools/drugcentral_ddi_spike.py`` rather than trusting a number in this docstring.
A registry count in a source comment rots on the next re-ingest, with no DrugCentral
release involved at all.

**Why the cascade is ordered display_name, then InChIKey, then CAS.**

1. `display_name` first because a direct hit needs no dump-side lookup and is the
   route a reader can check by eye.
2. InChIKey second: it denotes a structure exactly.
3. CAS last: it is an administrative registry number that upstream sources reuse
   loosely across hydrates and salt forms, so it is the weakest of the three.

Every resolution reports **which route answered, and when none did, why**. A
resolution figure that cannot say how it resolved cannot be audited, and this slice
exists to replace figures nobody could re-derive. The four unresolved routes are
kept apart because they mean genuinely different things: an endpoint DrugCentral
itself does not know as a substance is almost certainly a class name and is a
correct miss, while a `struct_id` that is missing from the `structures` projection
is a broken join and is a bug.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

# The closed vocabulary of ways an endpoint can resolve, or fail to. Closed on
# purpose -- a route that is not one of these is a bug, and `Resolution` refuses
# to hold one, so the comment is no longer the only thing enforcing this. Held as
# a frozenset because that is how every other closed vocabulary in this codebase
# is held (`medrt.CI_RELATIONSHIPS`, `gate.DRUG_LIKE_SUBSTANCE_TYPES`, ...).
ROUTE_DISPLAY_NAME = "display_name"
ROUTE_INCHIKEY = "inchikey"
ROUTE_CAS = "cas"

#: DrugCentral itself has no `struct_id` for this text -- so it is not a substance
#: DrugCentral knows, and a class name such as `Strong CYP3A4 Inhibitors` is the
#: overwhelmingly likely reason. A CORRECT miss, not a failure of the cascade.
ROUTE_NOT_A_SUBSTANCE = "not_a_substance"

#: A `struct_id` was found and its `structures` row carries neither an InChIKey nor
#: a CAS number. DrugCentral stores an empty InChIKey for biologics and mixtures,
#: so this is an honest structural dead end.
ROUTE_NO_STRUCTURAL_KEY = "no_structural_key"

#: A `struct_id` was found in the name index but is absent from the key index --
#: which cannot happen on a well-formed extract. Counted separately because it is
#: a BROKEN JOIN, and reporting it as an ordinary miss is how a corrupt extract
#: passes for a difficult one.
ROUTE_MISSING_KEYS_ROW = "missing_keys_row"

#: Keys existed and drugref simply does not hold them.
ROUTE_UNRESOLVED = "unresolved"

RESOLVED_ROUTES = frozenset({ROUTE_DISPLAY_NAME, ROUTE_INCHIKEY, ROUTE_CAS})
UNRESOLVED_ROUTES = frozenset({
    ROUTE_NOT_A_SUBSTANCE,
    ROUTE_NO_STRUCTURAL_KEY,
    ROUTE_MISSING_KEYS_ROW,
    ROUTE_UNRESOLVED,
})
ROUTES = RESOLVED_ROUTES | UNRESOLVED_ROUTES


@dataclass(frozen=True)
class Resolution:
    """How one endpoint resolved. NOT A TUPLE, AND NOT UNPACKABLE.

    ``moiety_uuid is None`` **if and only if** the route is one of
    `UNRESOLVED_ROUTES` -- checked here rather than assumed, because the report
    prints the resolved-name count and the route table side by side and derives
    them from different fields. If those two ever disagreed, the report would
    print two contradictory numbers and nothing would notice.

    A plain ``tuple[str | None, str]`` was the obvious shape and is the wrong one:
    a moiety UUID and a route label are both strings, so ``route, uuid = ...``
    type-checks, runs, and mislabels every figure it produces. `signing.Keypair`
    records the same decision for the same reason.
    """

    moiety_uuid: str | None
    route: str

    def __post_init__(self) -> None:
        if self.route not in ROUTES:
            raise ValueError(f"{self.route!r} is not one of {sorted(ROUTES)}")
        if (self.moiety_uuid is None) != (self.route in UNRESOLVED_ROUTES):
            raise ValueError(
                f"route {self.route!r} disagrees with "
                f"moiety_uuid={self.moiety_uuid!r}")

    @property
    def resolved(self) -> bool:
        """True when an endpoint reached a moiety. The one place this is decided."""
        return self.moiety_uuid is not None


def fold_name(value: str) -> str:
    """Fold a NAME to its lookup key. THE ONE HOME FOR THE NAME-SIDE RULE.

    Public because three modules need it and each having its own
    ``.strip().lower()`` is the shape this repo keeps losing to: the denominator
    in `drugcentral_ddi_measure` was counting raw spellings while resolution
    folded, so `Warfarin` and `warfarin ` were two names in every published
    figure that mentions a name count.
    """
    return value.strip().lower()


def _fold_key(value: str) -> str:
    """Fold a STRUCTURAL KEY (InChIKey, CAS) to its lookup key."""
    return value.strip().upper()


@dataclass(frozen=True, kw_only=True)
class Registry:
    """The drugref side of the join: three lookups onto ``moiety_uuid``.

    Held as plain dicts rather than a live connection so that every function in
    this module is pure and testable without a database. The caller loads them
    once (see ``tools/drugcentral_ddi_spike.py``).

    **Keyword-only, and it folds its own keys.** All three fields are
    ``Mapping[str, str]``, so a positional constructor lets ``inchikey`` and
    ``cas`` be swapped silently -- and the resulting figure is not merely wrong,
    it is wrong *and* labelled with the route it did not come from, so the audit
    trail corroborates it. Folding here rather than in the caller's SQL keeps the
    case rule in one place instead of two that drift.

    Attributes:
        display_name: ``substance_moiety.display_name`` -> ``moiety_uuid``.
        inchikey: ``identity_claim.value`` for scheme ``INCHIKEY``, **live claims
            only** (``superseded_by IS NULL``) -- a corrected-away identifier must
            not resurrect a resolution.
        cas: ``identity_claim.value`` for scheme ``CAS``, live claims only.
    """

    display_name: Mapping[str, str]
    inchikey: Mapping[str, str]
    cas: Mapping[str, str]

    def __post_init__(self) -> None:
        for field_name, fold in (("display_name", fold_name),
                                 ("inchikey", _fold_key),
                                 ("cas", _fold_key)):
            source: Mapping[str, str] = getattr(self, field_name)
            object.__setattr__(self, field_name, MappingProxyType(
                {fold(key): value for key, value in source.items()}))


@dataclass(frozen=True, kw_only=True)
class EndpointIndex:
    """The DrugCentral side: endpoint text -> ``struct_id`` -> structural keys.

    Attributes:
        names: folded ``name`` -> ``struct_id``, from `structures` then `synonyms`.
        structural_keys: ``struct_id`` -> ``(inchikey, cas_reg_no)``, folded,
            either possibly empty for a substance DrugCentral holds no structure
            for.

    Neither field has a default: an `EndpointIndex()` resolving nothing is not a
    state any caller wants, and `build_endpoint_index` is the sole constructor.
    """

    names: Mapping[str, str]
    structural_keys: Mapping[str, tuple[str, str]]

    def struct_id_for(self, name: str) -> str | None:
        """Return the ``struct_id`` this endpoint text denotes, or ``None``."""
        return self.names.get(fold_name(name))

    def structural_keys_for(self, struct_id: str) -> tuple[str, str] | None:
        """Return ``(inchikey, cas)`` for *struct_id*, or ``None`` if it has no row.

        ``None`` is deliberately NOT ``("", "")``. A `struct_id` reached through
        `names` but missing from `structural_keys` means the extract is
        inconsistent; defaulting it to blank keys would render that as an ordinary
        unresolved endpoint and hide a broken join behind a plausible miss.
        """
        return self.structural_keys.get(struct_id)


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

    ``structural_keys`` uses ``setdefault`` for the same reason and not merely for
    symmetry: ``structures.id`` is a primary key so a duplicate cannot occur in a
    well-formed dump, but if one ever does, first-wins is at least stable across
    runs where last-wins depends on row order.

    Args:
        structures: rows with ``id``, ``name``, ``inchikey``, ``cas_reg_no``.
        synonyms: rows with ``id`` (a ``struct_id``) and ``name``.
    """
    names: dict[str, str] = {}
    structural_keys: dict[str, tuple[str, str]] = {}

    for row in structures:
        # `not struct_id` rather than `is None`: the TSV cache round-trips SQL
        # NULL as the empty string, so an `is None` check could never fire for
        # the real caller and would read as a guard that is not one.
        struct_id = row.get("id")
        if not struct_id:
            continue
        structural_keys.setdefault(struct_id, (
            _fold_key(row.get("inchikey") or ""),
            _fold_key(row.get("cas_reg_no") or ""),
        ))
        name = fold_name(row.get("name") or "")
        if name:
            names.setdefault(name, struct_id)

    for row in synonyms:
        struct_id = row.get("id")
        name = fold_name(row.get("name") or "")
        if struct_id and name:
            names.setdefault(name, struct_id)

    return EndpointIndex(names=names, structural_keys=structural_keys)


def resolve_endpoint(
    name: str,
    index: EndpointIndex,
    registry: Registry,
) -> Resolution:
    """Resolve one endpoint name to a `Resolution`.

    A blank name never resolves. `structures` stores an empty InChIKey for
    biologics and mixtures and the TSV cache round-trips SQL NULL as ``""``, so a
    registry that happened to contain the empty string would otherwise collapse
    every keyless substance -- and every NULL endpoint -- onto one moiety: a
    silent, catastrophic merge rather than an honest miss. The guard covers all
    three lookups, not just the two structural ones.
    """
    folded = fold_name(name)
    if not folded:
        return Resolution(None, ROUTE_NOT_A_SUBSTANCE)

    direct = registry.display_name.get(folded)
    if direct is not None:
        return Resolution(direct, ROUTE_DISPLAY_NAME)

    struct_id = index.struct_id_for(name)
    if struct_id is None:
        return Resolution(None, ROUTE_NOT_A_SUBSTANCE)

    keys = index.structural_keys_for(struct_id)
    if keys is None:
        return Resolution(None, ROUTE_MISSING_KEYS_ROW)

    inchikey, cas = keys

    if inchikey:
        by_key = registry.inchikey.get(inchikey)
        if by_key is not None:
            return Resolution(by_key, ROUTE_INCHIKEY)

    if cas:
        by_cas = registry.cas.get(cas)
        if by_cas is not None:
            return Resolution(by_cas, ROUTE_CAS)

    if not inchikey and not cas:
        return Resolution(None, ROUTE_NO_STRUCTURAL_KEY)
    return Resolution(None, ROUTE_UNRESOLVED)


def unordered_pair(left: str, right: str) -> tuple[str, str] | None:
    """Normalise two RESOLVED moiety UUIDs into one orientation-independent pair.

    Both arguments are required to be resolved: the caller separates the
    unresolved case first, and narrowing the input here means ``None`` carries
    exactly one meaning instead of three. It previously accepted ``str | None``,
    which made the caller's self-pair counter correct only because its
    unresolved-endpoint check happened to run first -- reorder those two blocks
    and self-pairs would silently be counted as unresolvable rows.

    Returns ``None`` when **both endpoints are the same moiety**. A rule whose two
    endpoints denote one substance asserts nothing about an interaction between
    two drugs; `db/010_descendant_expansion.sql` subtracts exactly that case in
    `ddi_candidate_pair`'s own definition (`db/018` restates the rule for the gap
    views), and counting it here would inflate every overlap figure by rows that
    cannot become candidate pairs.

    PROJECT-NOTES § "The 5c.3 source evaluation" warns that rows, pairs and
    distinct pairs are three different units that were being quoted
    interchangeably. This function defines the third one.
    """
    if left == right:
        return None
    return (left, right) if left <= right else (right, left)
