"""Parse the MeSH release into Pharmacological Action (PA) classification records.

MeSH (Medical Subject Headings, U.S. National Library of Medicine) publishes a
**Pharmacological Action** axis: abstract action classes (e.g. "Anti-Inflammatory
Agents, Non-Steroidal") and the substances that belong to each. This is a second
classification axis beside MED-RT's six, on the same three tables (slice 2b).

WHAT THIS MODULE READS, FROM THREE FILES
----------------------------------------
* `pa2026.xml`  -- the consolidated PharmacologicalAction rollup: for each PA
  **class** (a Descriptor) the list of member Substances. This is the
  authoritative edge source (spec §5.1).
* `desc2026.xml` -- MeSH Descriptor records. Supplies each PA class's NAME and
  TREE NUMBERS (the DAG is derived from tree-number nesting), and the identity
  keys of any Descriptor-typed member.
* `supp2026.xml` -- Supplemental Concept Records (SCRs). Supplies the identity
  keys of SCR-typed members.

THREE FACTS ESTABLISHED FROM THE REAL 2026 RELEASE, NOT THE DOCUMENTATION
-------------------------------------------------------------------------
1. A MeSH **Descriptor carries its substance's UNII** in <RegistryNumber> (aspirin
   D001241 = UNII R16CO5Y76E). Issue #11 believed Descriptors held only CAS. The
   real split is per-record KEY TYPING, not per-record-type (spec §5.2).
2. The PA class **hierarchy is tree-number nesting**, not an explicit parent link,
   and is a genuine MULTI-PARENT DAG (a descriptor bears several tree numbers).
3. A record may expose **more than one UNII** across its concepts, so key
   extraction is SET-VALUED and the bridge must try every key (spec §5.2).

THE BRIDGE KEYS COME FROM <RegistryNumber> ONLY (design tension B). A CAS in
<RelatedRegistryNumber> is usually the record's own displaced CAS but can name a
*related* substance, so it is left to a later precision pass -- this parser never
puts a RelatedRegistryNumber value into a member's key set.

This module is PURE: it reads files and returns records. No database, no network,
no UUID minting. The orchestrator (mesh_run.py) does the bridge join and all DB
work. The parser STREAMS every file with iterparse + clear (supp2026.xml is
~750 MB uncompressed), so it scales to the full release by construction (spec §6).
"""
import gzip
import pathlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from xml.etree import ElementTree as ET

# A release file path -- accepted as str or Path throughout, mirroring medrt.parse.
StrPath = str | pathlib.Path

# Registry-number typing (spec §5.2). A UNII is 10 upper-alphanumerics; a CAS is
# n-nn-n (1-7 digits, then 2, then 1 check digit). Anything else -- the placeholder
# "0", an "EC ..." enzyme number, empty -- is neither, and is not a moiety identity
# key drugref holds. These two shapes never overlap (a UNII has no hyphens).
_UNII_RE = re.compile(r"^[0-9A-Z]{10}$")
_CAS_RE = re.compile(r"^[0-9]{1,7}-[0-9]{2}-[0-9]$")


@dataclass(frozen=True)
class MemberKeys:
    """A member's identity keys, extracted set-valued from <RegistryNumber>.

    Both are sets because one record may carry several UNIIs (and/or several CAS)
    across its concepts (spec §5.2). The bridge tries every one.
    """
    unii: frozenset[str]
    cas: frozenset[str]


@dataclass(frozen=True)
class PaClass:
    """One MeSH PA class -- an abstract pharmacologic-action Descriptor.

    `descriptor_ui` (e.g. "D000894") is the identity key class_uuid derives from.
    `concept_type` is always 'PA' (intrinsic to this axis); it is a field, not a
    constant, so a PaClass hands upsert_class the same shape a MED-RT ClassConcept
    does. `tree_numbers` are what the subclass DAG is derived from (§5.4).
    """
    descriptor_ui: str
    name: str
    tree_numbers: tuple[str, ...]
    concept_type: str = "PA"


@dataclass(frozen=True)
class PaParentEdge:
    """One DAG edge: `child_ui` is a kind of `parent_ui`. Both are PA classes."""
    child_ui: str
    parent_ui: str


@dataclass(frozen=True)
class PaMembership:
    """MeSH asserts the substance `record_ui` is a member of PA class
    `descriptor_ui`; `keys` are that substance's resolved identity keys, which the
    orchestrator's bridge joins against the moiety registry.

    A member appears under several PA classes; each is a separate PaMembership, and
    every one of them carries the member's (identical) key set.
    """
    record_ui: str
    descriptor_ui: str
    keys: MemberKeys


@dataclass(frozen=True)
class ParsedMesh:
    """Everything the three MeSH files yield, scoped to the PA axis.

    `classes`     -- the PA class descriptors (enriched with name + tree numbers);
    `parents`     -- the tree-number-derived subclass DAG (both endpoints PA);
    `memberships` -- one row per (member, PA class), each carrying the member's keys.
    """
    classes: list[PaClass]
    parents: list[PaParentEdge]
    memberships: list[PaMembership]


def registry_keys(values: Iterable[str]) -> tuple[set[str], set[str]]:
    """Classify a bag of registry-number strings into (uniis, cas) sets.

    Pure and set-valued (spec §5.2). A RelatedRegistryNumber may be annotated
    "<cas> (<name>)", so any parenthetical/space annotation is stripped before
    matching -- harmless for a plain <RegistryNumber> (which carries none) and what
    lets the same function serve a future RelatedRegistryNumber precision pass.
    """
    uniis: set[str] = set()
    cas: set[str] = set()
    for raw in values:
        token = (raw or "").strip().split(" ", 1)[0]   # drop any " (name)" annotation
        if _UNII_RE.match(token):
            uniis.add(token)
        elif _CAS_RE.match(token):
            cas.add(token)
        # else: "0", "EC ...", empty -- not a key drugref holds; ignored.
    return uniis, cas


def _local(tag: str) -> str:
    """The local name of a (possibly namespaced) tag. MeSH ships without a
    namespace today, but stripping any prefix keeps the parser robust to one."""
    return tag.rsplit("}", 1)[-1]


def open_release_file(path: StrPath):
    """Open a MeSH release file, transparently handling the `.gz` NLM publishes.

    THE ONE PLACE THIS DECISION LIVES. NLM ships desc/supp compressed (~750 MB
    each expanded), so which half of drugref's MeSH ingest needs a manual gunzip
    is not a per-module choice to make twice: slice 5b's reader handled `.gz` and
    slice 2b's did not, and the asymmetry was invisible from either call site
    until an operator hit it (#40).

    Returns a binary file object, which is what ET.iterparse wants either way --
    passing a path string instead would push the open() back into the caller and
    with it the very decision this function exists to centralise.
    """
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def iter_records(path: StrPath, record_tag: str):
    """Stream top-level `record_tag` elements from a MeSH file, plain or gzipped.

    THE ONE READER for every MeSH file drugref parses -- this module's PA axis
    (slice 2b) and `mesh_concepts`' condition resolution (slice 5b) both go
    through it, which is what keeps `.gz` support from being true of one and not
    the other.

    Bounded memory by construction: nothing accumulates but what the caller keeps.
    Clearing only the yielded element is not enough -- iterparse still leaves each
    retired sibling hanging off the growing root underneath, so peak memory would
    climb with the FILE rather than with the query. Grabbing the root from the
    first "start" event and clearing it too drops those retired siblings as well,
    which is what keeps this flat on the ~750 MB supp file however many records it
    holds (§6/§F).

    Matching is on the tag's LOCAL name: MeSH ships without an XML namespace
    today, and stripping any prefix keeps every caller robust to one appearing.
    """
    with open_release_file(path) as fh:
        context = ET.iterparse(fh, events=("start", "end"))
        _event, root = next(context)                   # grab the root to clear it
        for event, elem in context:
            if event == "end" and _local(elem.tag) == record_tag:
                yield elem
                elem.clear()
                root.clear()


def _texts(record, tag: str) -> list[str]:
    """Every non-empty text of `tag` anywhere under a record (across its concepts)."""
    return [e.text.strip() for e in record.iter()
            if _local(e.tag) == tag and e.text and e.text.strip()]


def _findtext(record, tag: str) -> str:
    """The first non-empty text of `tag` under a record, or ''."""
    for e in record.iter():
        if _local(e.tag) == tag and e.text and e.text.strip():
            return e.text.strip()
    return ""


def _parse_pa(pa_path: StrPath) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Read pa2026: the PA classes and their members.

    Returns (class_name_by_ui, member_uis_by_class):
    * class_name_by_ui  -- {descriptor_ui: name from the PA rollup} for every PA
      class (order-preserving via dict). A class with no members still appears.
    * member_uis_by_class -- {descriptor_ui: [member RecordUI, ...]} preserving the
      release's order so downstream edge lists are reproducible.
    """
    class_name: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for pa in iter_records(pa_path, "PharmacologicalAction"):
        # The PA class is named inside DescriptorReferredTo; members inside
        # PharmacologicalActionSubstanceList. Both descriptor and member names sit
        # in <String>, so read the structural UI tags rather than names.
        dui = _findtext(pa, "DescriptorUI")
        if not dui:
            continue
        class_name[dui] = _findtext(pa, "String")      # DescriptorName is first String
        member_uis = [e.text.strip() for e in pa.iter()
                      if _local(e.tag) == "RecordUI" and e.text and e.text.strip()]
        members.setdefault(dui, []).extend(member_uis)
    return class_name, members


def _parse_desc(desc_path: StrPath, want_classes: set[str], want_members: set[str]):
    """Read desc2026 once, harvesting two disjoint things by DescriptorUI:

    * for a PA class     -- its tree numbers (and a fallback name);
    * for a Descriptor member -- its identity keys from <RegistryNumber>.

    Returns (trees_by_class, name_by_class, keys_by_member).
    """
    trees: dict[str, tuple[str, ...]] = {}
    names: dict[str, str] = {}
    keys: dict[str, MemberKeys] = {}
    for rec in iter_records(desc_path, "DescriptorRecord"):
        ui = _findtext(rec, "DescriptorUI")
        if ui in want_classes:
            trees[ui] = tuple(_texts(rec, "TreeNumber"))
            names[ui] = _findtext(rec, "String")
        if ui in want_members:
            unii, cas = registry_keys(_texts(rec, "RegistryNumber"))
            keys[ui] = MemberKeys(frozenset(unii), frozenset(cas))
    return trees, names, keys


def _parse_supp(supp_path: StrPath, want_members: set[str]) -> dict[str, MemberKeys]:
    """Read supp2026 once, harvesting each wanted SCR member's identity keys."""
    keys: dict[str, MemberKeys] = {}
    for rec in iter_records(supp_path, "SupplementalRecord"):
        ui = _findtext(rec, "SupplementalRecordUI")
        if ui in want_members:
            unii, cas = registry_keys(_texts(rec, "RegistryNumber"))
            keys[ui] = MemberKeys(frozenset(unii), frozenset(cas))
    return keys


def tree_parent_edges(
        trees_by_ui: Mapping[str, Sequence[str]]) -> list[tuple[str, str]]:
    """Turn MeSH tree numbers into DAG edges: `[(child_ui, parent_ui), ...]`.

    THE ONE PLACE THIS RULE LIVES. drugref derives two different DAGs from MeSH tree
    numbers -- slice 2b's PA class DAG (`_build_dag` below) and slice 5b's condition
    DAG (`mesh_concepts.parent_edges`) -- and they are the same rule over different
    records. It was written out twice once, which is exactly how two copies drift
    apart without anything failing; each caller now wraps THIS function's plain
    `(child, parent)` pairs in its own edge dataclass and adds nothing else.

    `trees_by_ui` maps each ingested record's UI to the tree numbers it bears. Four
    decisions are baked in, and every one of them changes which edges exist:

    * **Dotted nesting.** A MeSH tree number nests by dotted segments
      ("D27.505.954.158.030"); dropping the trailing ".NNN" names its immediate
      parent tree number. A single-segment number is top-level and has no parent.
    * **The IMMEDIATE tree-parent only**, per tree number -- this never walks further
      up a path. A tree number whose immediate parent is not in `trees_by_ui`
      contributes no edge for THAT path and is NOT re-attached to a more distant
      ancestor that happens to be present: the release does not assert that kinship.
    * **Both endpoints must be in the ingested set** -- the same endpoint-scoping
      MED-RT uses, which is what keeps the DAG closed over what drugref actually
      holds. A record none of whose tree numbers has an ingested immediate parent is
      a ROOT of the ingested subset, not an orphan and not an error.
    * **No self-edges**, because a record can bear both a tree number and its own
      tree-parent's; `db/013`'s `condition_parent_not_self` CHECK would reject one
      mid-ingest.

    Multi-parent by construction: a record bears several tree numbers, so it lands
    under every ingested immediate parent it has. That is why both axes are genuine
    multi-parent DAGs rather than trees (spec §5.4 for PA; 1,690 of slice 5b's
    conditions have more than one parent).

    Returns a DEDUPED, SORTED list: a set has no order, and both callers insert rows
    in this order, so a non-deterministic answer would make two ingests of one
    release differ.
    """
    owner_of_tree = {tree: ui for ui, trees in trees_by_ui.items() for tree in trees}
    edges: set[tuple[str, str]] = set()
    for ui, trees in trees_by_ui.items():
        for tree in trees:
            if "." not in tree:
                continue                        # a top-level node has no parent
            owner = owner_of_tree.get(tree.rsplit(".", 1)[0])
            if owner and owner != ui:
                edges.add((ui, owner))
    return sorted(edges)


def _build_dag(classes: list[PaClass]) -> list[PaParentEdge]:
    """Derive the PA subclass DAG from tree-number nesting (spec §5.4).

    The rule is `tree_parent_edges`; this only wraps its pairs in PaParentEdge. Of
    the 1,042 tree numbers PA classes bear, 794 have a PA-class immediate parent;
    the rest drop, by construction, and that is the approved design.

    `classes` is built from a dict keyed by DescriptorUI (see `parse`), so each UI
    appears exactly once and the mapping below loses nothing.
    """
    return [PaParentEdge(child_ui=child, parent_ui=parent)
            for child, parent in tree_parent_edges(
                {c.descriptor_ui: c.tree_numbers for c in classes})]


def parse(*, pa_path: StrPath, desc_path: StrPath, supp_path: StrPath) -> ParsedMesh:
    """Read one MeSH release (three files) into the PA records slice 2b ingests.

    The read order is deliberate: pa2026 first establishes WHICH descriptors are PA
    classes and WHICH substances are members, so the two big files (desc, supp) are
    each streamed once and only the wanted records are retained.
    """
    class_name, member_uis_by_class = _parse_pa(pa_path)
    pa_class_uis = set(class_name)

    # Every distinct member, split by record type (D = Descriptor, C = SCR), so
    # each member's keys are looked up in exactly the file that defines it.
    all_members = {ui for uis in member_uis_by_class.values() for ui in uis}
    desc_members = {ui for ui in all_members if ui.startswith("D")}
    supp_members = {ui for ui in all_members if ui.startswith("C")}

    trees, desc_names, desc_keys = _parse_desc(desc_path, pa_class_uis, desc_members)
    supp_keys = _parse_supp(supp_path, supp_members)

    # PA classes: prefer the descriptor-file name (authoritative), fall back to the
    # PA rollup's. Tree numbers come only from the descriptor file.
    classes = [
        PaClass(descriptor_ui=ui,
                name=desc_names.get(ui) or class_name[ui],
                tree_numbers=trees.get(ui, ()))
        for ui in class_name
    ]

    keys_by_member = {**desc_keys, **supp_keys}
    empty = MemberKeys(frozenset(), frozenset())
    memberships = [
        PaMembership(record_ui=member_ui, descriptor_ui=class_ui,
                     keys=keys_by_member.get(member_ui, empty))
        for class_ui, member_uis in member_uis_by_class.items()
        for member_ui in member_uis
    ]

    return ParsedMesh(classes=classes, parents=_build_dag(classes),
                      memberships=memberships)
