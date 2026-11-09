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
    UNII_RE, MemberKeys, StrPath, findtext, iter_records, local_name, registry_keys,
    texts,
)
from drugref.ingest.mesh_concepts import is_descendant_tree

REVIEW_STATUSES = frozenset({"structural-seed", "reviewed"})
_DESCRIPTOR_UI_RE = re.compile(r"^D\d+$")
# Every key the allow-list may use. Anything else is a typo, and a typo here is
# silent clinical drift (a misspelt `exclude` re-admits the beta-lactamase
# inhibitors), so an unknown key is an error, never ignored.
_TOP_KEYS = frozenset({"group"})
_GROUP_KEYS = frozenset({"root", "review_status", "reviewed_by", "reviewed_against",
                         "exclude", "include"})
_EXCLUDE_KEYS = frozenset({"ui", "reason"})
_INCLUDE_KEYS = frozenset({"unii", "reason"})


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
    """`placement_keys` holds the keys of EVERY record that carries any, in or out
    of a group -- ONE ENTRY PER RECORD, not a union, so the orchestrator can bridge
    each record UNII-first/CAS-fallback exactly as for membership. A union would let
    a salt-form CAS place a moiety MeSH never named, turning "unplaced" into a
    silent "no-match"."""
    root_names: dict[str, str]
    members: tuple[GroupMemberKeys, ...]
    placement_keys: tuple[MemberKeys, ...]
    scr_records: int
    desc_records: int


def _refuse_unknown_keys(table: dict, allowed: frozenset, where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"allergy allow-list: unknown key(s) {', '.join(unknown)} "
                         f"in {where}")


def _entries(raw: list[dict], field: str, valid, what: str,
             allowed: frozenset) -> tuple[AllowEntry, ...]:
    out = []
    for e in raw:
        _refuse_unknown_keys(e, allowed, what)
        value = e.get(field, "")
        if not valid(value):
            raise ValueError(f"allergy allow-list: malformed {what} {value!r}")
        if not e.get("reason"):
            raise ValueError(f"allergy allow-list: {value} has no reason")
        out.append(AllowEntry(value, e["reason"]))
    return tuple(out)


def load_allow_list(path: StrPath) -> tuple[GroupSpec, ...]:
    """Read and validate the allow-list TOML. Raises ValueError on an unknown key,
    an unknown review_status, a duplicate root, a malformed descriptor UI / UNII,
    or a file with no group at all (an ingest of zero groups would wipe them all)."""
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    _refuse_unknown_keys(raw, _TOP_KEYS, "the top level")
    groups, seen = [], set()
    for g in raw.get("group", []):
        _refuse_unknown_keys(g, _GROUP_KEYS, f"group {g.get('root', '?')}")
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
                     "excluded UI", _EXCLUDE_KEYS),
            _entries(g.get("include", []), "unii", UNII_RE.match, "included UNII",
                     _INCLUDE_KEYS)))
    if not groups:
        raise ValueError(f"allergy allow-list {path} names no [[group]]")
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
    in UI order), then supp is streamed. Raises ValueError if:
      * a group's root or excluded UI is not a descriptor in the release;
      * an excluded UI is not STRICTLY below its root (excluding the root or an
        ancestor empties the group, and an empty group answers no-match for every
        placed drug; excluding something outside the tree does nothing at all);
      * an SCR's HeadingMappedTo names a descriptor absent from desc (desc and
        supp from different releases: the SCR would be placed yet never a member,
        so it would answer no-match. MeSH 2026 has 0 such SCRs).

    PLACEMENT: every desc or SCR record carrying any UNII/CAS contributes its own
    MemberKeys entry to `placement_keys` (keyless records are omitted).
    """
    desc: dict[str, tuple[str, tuple[str, ...], MemberKeys]] = {}
    for rec in iter_records(desc_path, "DescriptorRecord"):
        unii, cas = registry_keys(texts(rec, "RegistryNumber"))
        desc[findtext(rec, "DescriptorUI")] = (
            findtext(rec, "String"), tuple(texts(rec, "TreeNumber")),
            MemberKeys(frozenset(unii), frozenset(cas)))

    if not desc:  # before the missing-UI check, which would blame the allow-list
        raise ValueError(f"MeSH descriptor file {desc_path} holds no DescriptorRecord "
                         "(truncated or wrong file?)")

    # Fail loudly: a root or exclude UI the release lacks would otherwise give an
    # empty group, or -- worse -- a typo'd exclusion that silently admits an inhibitor.
    missing = sorted({ui for g in groups
                      for ui in (g.root_ui, *(e.ui_or_unii for e in g.exclude))
                      if ui not in desc})
    if missing:
        raise ValueError("allergy allow-list names descriptor UI(s) absent from "
                         f"the MeSH release: {', '.join(missing)}")

    members: list[GroupMemberKeys] = []
    in_group: dict[str, set[str]] = {}          # root_ui -> descriptor UIs in
    for g in groups:
        root_trees = desc[g.root_ui][1]
        excl = {e.ui_or_unii for e in g.exclude}
        misplaced = sorted(ui for ui in excl if not any(
            is_descendant_tree(t, r) for t in desc[ui][1] for r in root_trees))
        if misplaced:
            raise ValueError(f"allergy allow-list: exclude(s) {', '.join(misplaced)} "
                             f"not strictly below group root {g.root_ui}")
        excl_trees = [t for ui in excl for t in desc[ui][1]]
        inside = in_group[g.root_ui] = set()
        for ui, (_name, trees, keys) in desc.items():
            if ui in excl or not any(_at_or_below(t, root_trees) for t in trees):
                continue
            if any(_at_or_below(t, excl_trees) for t in trees):
                continue
            inside.add(ui)
            members.append(GroupMemberKeys(g.root_ui, f"descriptor:{ui}", keys))

    placement = [k for _, _, k in desc.values() if k.unii or k.cas]
    scr_records = 0
    unknown_heading: list[str] = []
    for rec in iter_records(supp_path, "SupplementalRecord"):
        scr_records += 1
        u, c = registry_keys(texts(rec, "RegistryNumber"))
        keys = MemberKeys(frozenset(u), frozenset(c))
        if u or c:
            placement.append(keys)
        # HeadingMappedTo only: IndexingInformation also holds DescriptorUIs. Local
        # names, like iter_records/texts, so a namespaced release still matches.
        mapped = {m.lstrip("*") for hm in rec.iter()
                  if local_name(hm.tag) == "HeadingMappedTo"
                  for m in texts(hm, "DescriptorUI")}
        scr_ui = findtext(rec, "SupplementalRecordUI")
        if mapped - desc.keys():
            unknown_heading.append(scr_ui)
        for g in groups:
            if mapped & in_group[g.root_ui]:
                members.append(GroupMemberKeys(g.root_ui, f"scr:{scr_ui}", keys))

    if unknown_heading:
        raise ValueError(
            f"{len(unknown_heading)} MeSH SCR(s) map to descriptors absent from "
            f"{desc_path} (desc and supp from different releases?), e.g. "
            + ", ".join(unknown_heading[:5]))

    return ParsedAllergyGroups(
        root_names={g.root_ui: desc.get(g.root_ui, (g.root_ui,))[0] for g in groups},
        members=tuple(members),
        placement_keys=tuple(placement),
        scr_records=scr_records, desc_records=len(desc))
