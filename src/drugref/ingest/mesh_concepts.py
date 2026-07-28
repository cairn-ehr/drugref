"""Resolve MED-RT's MeSH endpoints into MeSH records (slice 5b).

THE FACT THIS MODULE EXISTS FOR, established from the real 2026.07.06 release and
NOT from the documentation: MED-RT's `to_code` for a MeSH endpoint is a MeSH
**ConceptUI** ("M0004868"), not a DescriptorUI. Every MeSH record owns one or more
Concepts, exactly one of them preferred, so resolution is a plain lookup over files
drugref already ingests:

    desc2026 alone            2,385 / 2,474 = 96.4%
    desc2026 + supp2026       2,471 / 2,474 = 99.88%
    NDF-RT accessory crosswalk 2,103 / 2,474 = 85.0%, and yields only a NAME

The crosswalk route is therefore NOT used: it is worse, and a name is not a key
(ROADMAP principle 2). Two code shapes occur -- legacy "M0000006" and modern
"M000595362" -- and both are ConceptUIs; nothing here keys off the length.

WHY THIS IS NOT IN mesh.py. That module answers "what are the PA classes and their
members"; this one answers "which MeSH record is this concept". Different questions,
and mesh.py is already 296 lines against a ~500-line budget (CLAUDE.md rule 4).

This module is PURE and STREAMING: it reads files and returns records. No database,
no network, no UUID minting. The orchestrator (mesh_ci_run.py) does all of that.
Every file is streamed with iterparse + clear, so peak memory scales with the QUERY
(the wanted set), never with the release -- supp2026 is ~750 MB uncompressed.
"""
import gzip
import pathlib
from collections.abc import Iterable
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from drugref.ingest.mesh import registry_keys

StrPath = str | pathlib.Path

DESCRIPTOR = "DESCRIPTOR"
SCR = "SCR"


@dataclass(frozen=True)
class MeshRecord:
    """One MeSH record, reached through one of its concepts.

    `concept_ui` is what MED-RT pointed at; `record_ui` is the record that owns it
    and is what a condition is KEYED on. The two are deliberately kept apart: many
    concepts resolve to one record, so keying on the concept would split a single
    condition into several rows that no rebuild could ever merge.

    `is_preferred_concept` is recorded rather than discarded because a SUBORDINATE
    concept may be NARROWER than the record it belongs to -- 81 of this slice's
    1,051 resolved objects are subordinate. Storing the condition at record grain
    loses that nuance; this flag makes the loss visible and measurable instead of
    silent (spec §10 tension C).
    """
    concept_ui: str
    record_ui: str
    record_kind: str                    # DESCRIPTOR | SCR
    name: str
    tree_numbers: tuple[str, ...]
    unii: frozenset[str]
    cas: frozenset[str]
    is_preferred_concept: bool


@dataclass(frozen=True)
class ConditionParentEdge:
    """One condition-DAG edge: `child_code` is a kind of `parent_code`.
    Both are MeSH record UIs (the key drugref stores), never concept UIs."""
    child_code: str
    parent_code: str


def is_descendant_tree(tree_number: str, prefix: str) -> bool:
    """Is `tree_number` STRICTLY below `prefix` in the MeSH tree?

    Segment-aware on purpose. A bare str.startswith would make "C10.228.140.49" a
    parent of "C10.228.140.490" -- two unrelated concepts whose numbers merely share
    a text prefix -- and would also report a node as its own descendant, which would
    put a self-edge in condition_parent that db/013's CHECK then rejects mid-ingest.
    """
    return tree_number.startswith(prefix + ".")


def _open(path: StrPath):
    """Open a MeSH file, transparently handling the .gz the NLM publishes."""
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def _stream(path: StrPath, tag: str):
    """Yield each `tag` element, clearing it after use.

    Bounded memory by construction: nothing accumulates but what the caller keeps.
    """
    with _open(path) as fh:
        for _event, el in ET.iterparse(fh, events=("end",)):
            if el.tag == tag:
                yield el
                el.clear()


def _record(el, ui_tag: str, name_tag: str, kind: str, concept_ui: str,
            preferred: bool) -> MeshRecord:
    """Build a MeshRecord from a raw MeSH record element."""
    uniis, cas = registry_keys(
        [r.text for r in el.iter("RegistryNumber") if r.text])
    trees = tuple(t.text for t in el.findall("TreeNumberList/TreeNumber") if t.text)
    return MeshRecord(concept_ui=concept_ui,
                      record_ui=el.findtext(ui_tag) or "",
                      record_kind=kind,
                      name=el.findtext(name_tag) or "",
                      tree_numbers=trees,
                      unii=frozenset(uniis), cas=frozenset(cas),
                      is_preferred_concept=preferred)


# (file, record tag, UI tag, name tag, record kind) -- descriptors FIRST, because a
# concept defined in both files is authoritatively a descriptor.
_SOURCES = (
    ("desc", "DescriptorRecord", "DescriptorUI", "DescriptorName/String", DESCRIPTOR),
    ("supp", "SupplementalRecord", "SupplementalRecordUI",
     "SupplementalRecordName/String", SCR),
)


def resolve_concepts(desc_path: StrPath, supp_path: StrPath,
                     wanted: set[str]) -> dict[str, MeshRecord]:
    """Resolve each wanted MeSH ConceptUI to the record that owns it.

    Returns {concept_ui: MeshRecord} containing ONLY codes that resolved. A code
    that resolves nowhere is simply ABSENT -- never mapped to a plausible
    substitute -- so the caller can count it as a gap rather than ship a wrong
    condition. Exactly 2 of this slice's 1,053 object codes are withdrawn upstream
    and land here.

    Descriptors win over SCRs when a concept appears in both: a descriptor is the
    fuller record, and preferring it deterministically stops the answer depending on
    file order.
    """
    out: dict[str, MeshRecord] = {}
    remaining = set(wanted)
    for path, tag, ui_tag, name_tag, kind in (
            (desc_path, *_SOURCES[0][1:]), (supp_path, *_SOURCES[1][1:])):
        if not remaining:
            break                                   # everything already resolved
        for el in _stream(path, tag):
            for concept in el.findall("ConceptList/Concept"):
                cui = concept.findtext("ConceptUI") or ""
                if cui in remaining:
                    out[cui] = _record(el, ui_tag, name_tag, kind, cui,
                                       concept.get("PreferredConceptYN") == "Y")
        remaining -= set(out)
    return out


def descriptors_under(desc_path: StrPath,
                      tree_prefixes: frozenset[str]) -> list[MeshRecord]:
    """Every descriptor STRICTLY below one of `tree_prefixes`.

    THE DESCENDANT CLOSURE, and the reason the registry is not merely the set of
    referenced conditions. Expansion exists so a rule on Epilepsy fires for a
    patient coded Temporal Lobe Epilepsy -- and that descendant is NOT itself a
    CI_with object. A registry scoped to referenced objects would have nothing to
    expand into, and the feature would be inert while appearing to work.

    Measured on the real release: 664 referenced descriptors -> 5,190 in closure.

    Each record is returned under its own PREFERRED concept where it has one, since
    the caller keys conditions by record_ui and only needs a concept for provenance.
    """
    found: list[MeshRecord] = []
    if not tree_prefixes:
        return found
    for el in _stream(desc_path, "DescriptorRecord"):
        trees = [t.text for t in el.findall("TreeNumberList/TreeNumber") if t.text]
        if not any(is_descendant_tree(t, p) for t in trees for p in tree_prefixes):
            continue
        concepts = el.findall("ConceptList/Concept")
        preferred = next((c for c in concepts
                          if c.get("PreferredConceptYN") == "Y"), None)
        chosen = preferred if preferred is not None else (
            concepts[0] if concepts else None)
        found.append(_record(
            el, "DescriptorUI", "DescriptorName/String", DESCRIPTOR,
            (chosen.findtext("ConceptUI") or "") if chosen is not None else "",
            preferred is not None))
    return found


def parent_edges(records: Iterable[MeshRecord]) -> list[ConditionParentEdge]:
    """Derive the condition DAG from tree-number nesting.

    The same idiom as mesh._build_dag, deliberately -- one way of turning MeSH tree
    numbers into a DAG in this codebase, not two. Only the IMMEDIATE tree-parent of
    each tree number counts, and only when that parent is itself an ingested record;
    a record whose immediate parent is outside the set is simply a ROOT of the
    ingested subset, not re-attached to a more distant ancestor.

    Multi-parent by construction: a descriptor bears several tree numbers, which is
    why 1,690 of the 5,190 conditions have more than one parent.
    """
    records = list(records)
    owner_of_tree = {t: r.record_ui for r in records for t in r.tree_numbers}
    edges: set[ConditionParentEdge] = set()
    for r in records:
        for tree in r.tree_numbers:
            if "." not in tree:
                continue                            # a top-level node has no parent
            owner = owner_of_tree.get(tree.rsplit(".", 1)[0])
            if owner and owner != r.record_ui:
                edges.add(ConditionParentEdge(child_code=r.record_ui,
                                              parent_code=owner))
    # Sorted so the edge order is reproducible (a set has none).
    return sorted(edges, key=lambda e: (e.child_code, e.parent_code))
