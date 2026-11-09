"""Allergy groups: the reviewed allow-list and the pure MeSH group parser.

An *allergy group* (e.g. "penicillins") is a MeSH descriptor subtree chosen by a
human-reviewed allow-list (`data/allergy_groups.toml`). This module is PURE: it
reads the allow-list and the MeSH desc/supp files and returns which records are
in each group and what identity keys they carry. No database, no UUIDs -- the
orchestrator does the registry join and all writes.
"""
import re
import tomllib
from dataclasses import dataclass

from drugref.ingest.mesh import (
    UNII_RE, MemberKeys, StrPath, findtext, iter_records, registry_keys, texts,
)
from drugref.ingest.mesh_concepts import is_descendant_tree

REVIEW_STATUSES = frozenset({"structural-seed", "reviewed"})
_DESCRIPTOR_UI_RE = re.compile(r"^D\d+$")


@dataclass(frozen=True)
class AllowEntry:
    """One exclude (a descriptor UI) or include (a UNII), with the reviewer's why."""
    ui_or_unii: str
    reason: str


@dataclass(frozen=True)
class GroupSpec:
    """One allow-listed group: a MeSH root descriptor plus reviewed adjustments."""
    root_ui: str
    review_status: str
    reviewed_by: str | None
    reviewed_against: str | None
    exclude: tuple[AllowEntry, ...]
    include: tuple[AllowEntry, ...]


@dataclass(frozen=True)
class GroupMemberKeys:
    """A MeSH record in group `root_ui`; `via` is "descriptor:<UI>" or "scr:<UI>"."""
    root_ui: str
    via: str
    keys: MemberKeys


@dataclass(frozen=True)
class ParsedAllergyGroups:
    """`placement_keys` is the union of EVERY record's keys, in or out of a group,
    so the orchestrator can place each moiety's own MeSH identity."""
    root_names: dict[str, str]
    members: tuple[GroupMemberKeys, ...]
    placement_keys: MemberKeys
    scr_records: int
    desc_records: int


def _entries(raw: list[dict], field: str, valid, what: str) -> tuple[AllowEntry, ...]:
    out = []
    for e in raw:
        value = e.get(field, "")
        if not valid(value):
            raise ValueError(f"allergy allow-list: malformed {what} {value!r}")
        if not e.get("reason"):
            raise ValueError(f"allergy allow-list: {value} has no reason")
        out.append(AllowEntry(value, e["reason"]))
    return tuple(out)


def load_allow_list(path: StrPath) -> tuple[GroupSpec, ...]:
    """Read and validate the allow-list TOML. Raises ValueError on an unknown
    review_status, a duplicate root, or a malformed descriptor UI / UNII."""
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    groups, seen = [], set()
    for g in raw.get("group", []):
        root = g.get("root", "")
        if not _DESCRIPTOR_UI_RE.match(root):
            raise ValueError(f"allergy allow-list: malformed root {root!r}")
        if root in seen:
            raise ValueError(f"allergy allow-list: duplicate root {root}")
        seen.add(root)
        status = g.get("review_status", "")
        if status not in REVIEW_STATUSES:
            raise ValueError(f"allergy allow-list: unknown review_status {status!r}")
        groups.append(GroupSpec(
            root, status, g.get("reviewed_by"), g.get("reviewed_against"),
            _entries(g.get("exclude", []), "ui", _DESCRIPTOR_UI_RE.match,
                     "excluded UI"),
            _entries(g.get("include", []), "unii", UNII_RE.match, "included UNII")))
    return tuple(groups)


def _at_or_below(tree: str, roots) -> bool:
    """True if `tree` IS one of `roots` or lies strictly below one. (mesh_concepts'
    is_descendant_tree is strict, but a group's root descriptor must count too.)"""
    return any(tree == r or is_descendant_tree(tree, r) for r in roots)


def parse(*, desc_path: StrPath, supp_path: StrPath,
          groups: tuple[GroupSpec, ...]) -> ParsedAllergyGroups:
    """Decide which MeSH records belong to each group.

    MEMBERSHIP RULE (spec 4.2). A descriptor is IN a group if any of its tree
    numbers equals or is below one of the root's, AND none of its tree numbers
    equals or is below a tree number of an excluded descriptor. An SCR is IN if
    any HeadingMappedTo descriptor (leading "*" stripped) is IN.

    Two passes: desc is read whole first (a root may come after its descendants
    in UI order), then supp is streamed.
    """
    desc: dict[str, tuple[str, tuple[str, ...], MemberKeys]] = {}
    for rec in iter_records(desc_path, "DescriptorRecord"):
        unii, cas = registry_keys(texts(rec, "RegistryNumber"))
        desc[findtext(rec, "DescriptorUI")] = (
            findtext(rec, "String"), tuple(texts(rec, "TreeNumber")),
            MemberKeys(frozenset(unii), frozenset(cas)))

    members: list[GroupMemberKeys] = []
    in_group: dict[str, set[str]] = {}          # root_ui -> descriptor UIs in
    for g in groups:
        root_trees = desc.get(g.root_ui, ("", (), None))[1]
        excl = {e.ui_or_unii for e in g.exclude}
        excl_trees = [t for ui in excl for t in desc.get(ui, ("", (), None))[1]]
        inside = in_group[g.root_ui] = set()
        for ui, (_name, trees, keys) in desc.items():
            if ui in excl or not any(_at_or_below(t, root_trees) for t in trees):
                continue
            if any(_at_or_below(t, excl_trees) for t in trees):
                continue
            inside.add(ui)
            members.append(GroupMemberKeys(g.root_ui, f"descriptor:{ui}", keys))

    unii = {u for _, _, k in desc.values() for u in k.unii}
    cas = {c for _, _, k in desc.values() for c in k.cas}
    scr_records = 0
    for rec in iter_records(supp_path, "SupplementalRecord"):
        scr_records += 1
        u, c = registry_keys(texts(rec, "RegistryNumber"))
        unii |= u
        cas |= c
        keys = MemberKeys(frozenset(u), frozenset(c))
        # HeadingMappedTo only: IndexingInformation also holds DescriptorUIs.
        mapped = {m.lstrip("*") for hm in rec.iter("HeadingMappedTo")
                  for m in texts(hm, "DescriptorUI")}
        for g in groups:
            if mapped & in_group[g.root_ui]:
                members.append(GroupMemberKeys(
                    g.root_ui, f"scr:{findtext(rec, 'SupplementalRecordUI')}", keys))

    return ParsedAllergyGroups(
        root_names={g.root_ui: desc.get(g.root_ui, (g.root_ui,))[0] for g in groups},
        members=tuple(members),
        placement_keys=MemberKeys(frozenset(unii), frozenset(cas)),
        scr_records=scr_records, desc_records=len(desc))
