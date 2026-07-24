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
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

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


def registry_keys(values) -> tuple[set[str], set[str]]:
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


def _iter_records(path, record_tag: str):
    """Stream top-level `record_tag` elements, detaching each (and the growing
    root) after use so peak memory stays bounded on the ~750 MB supp file (§6/§F)."""
    context = ET.iterparse(str(path), events=("start", "end"))
    _event, root = next(context)                       # grab the root to clear it
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


def _parse_pa(pa_path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Read pa2026: the PA classes and their members.

    Returns (class_name_by_ui, member_uis_by_class):
    * class_name_by_ui  -- {descriptor_ui: name from the PA rollup} for every PA
      class (order-preserving via dict). A class with no members still appears.
    * member_uis_by_class -- {descriptor_ui: [member RecordUI, ...]} preserving the
      release's order so downstream edge lists are reproducible.
    """
    class_name: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for pa in _iter_records(pa_path, "PharmacologicalAction"):
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


def _parse_desc(desc_path, want_classes: set[str], want_members: set[str]):
    """Read desc2026 once, harvesting two disjoint things by DescriptorUI:

    * for a PA class     -- its tree numbers (and a fallback name);
    * for a Descriptor member -- its identity keys from <RegistryNumber>.

    Returns (trees_by_class, name_by_class, keys_by_member).
    """
    trees: dict[str, tuple[str, ...]] = {}
    names: dict[str, str] = {}
    keys: dict[str, MemberKeys] = {}
    for rec in _iter_records(desc_path, "DescriptorRecord"):
        ui = _findtext(rec, "DescriptorUI")
        if ui in want_classes:
            trees[ui] = tuple(_texts(rec, "TreeNumber"))
            names[ui] = _findtext(rec, "String")
        if ui in want_members:
            unii, cas = registry_keys(_texts(rec, "RegistryNumber"))
            keys[ui] = MemberKeys(frozenset(unii), frozenset(cas))
    return trees, names, keys


def _parse_supp(supp_path, want_members: set[str]) -> dict[str, MemberKeys]:
    """Read supp2026 once, harvesting each wanted SCR member's identity keys."""
    keys: dict[str, MemberKeys] = {}
    for rec in _iter_records(supp_path, "SupplementalRecord"):
        ui = _findtext(rec, "SupplementalRecordUI")
        if ui in want_members:
            unii, cas = registry_keys(_texts(rec, "RegistryNumber"))
            keys[ui] = MemberKeys(frozenset(unii), frozenset(cas))
    return keys


def _build_dag(classes: list[PaClass]) -> list[PaParentEdge]:
    """Derive the subclass DAG from tree-number nesting (spec §5.4).

    A MeSH tree number nests by dotted segments ("D27.505.954.158.030"); dropping
    the trailing ".NNN" gives the immediate parent tree number. Emit a child->parent
    edge only when BOTH the child and its immediate tree-parent are ingested PA
    classes -- the same endpoint-scoping MED-RT uses to keep the DAG closed over the
    ingested set. A parent tree number owned by no PA class drops the edge (the
    child attaches at its nearest ingested ancestor, or is a root). A descriptor
    bears several tree numbers, so a class genuinely has several parents (a DAG).
    """
    tree_to_class = {t: c.descriptor_ui for c in classes for t in c.tree_numbers}
    edges: set[PaParentEdge] = set()
    for c in classes:
        for t in c.tree_numbers:
            parent_tree = t.rsplit(".", 1)[0] if "." in t else None
            owner = tree_to_class.get(parent_tree)
            if owner and owner != c.descriptor_ui:
                edges.add(PaParentEdge(child_ui=c.descriptor_ui, parent_ui=owner))
    # Sorted for a reproducible edge order (a set has none).
    return sorted(edges, key=lambda e: (e.child_ui, e.parent_ui))


def parse(*, pa_path, desc_path, supp_path) -> ParsedMesh:
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
