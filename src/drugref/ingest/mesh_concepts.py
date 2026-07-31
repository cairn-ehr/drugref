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
and mesh.py already sits around 380 lines against a ~500-line budget (CLAUDE.md
rule 4). What the two genuinely SHARE -- READING A RELEASE FILE, reading registry
numbers, and turning tree numbers into DAG edges -- is imported from mesh.py, never
copied: `mesh.iter_records` (the one gz-aware streaming reader, #40) and
`mesh.tree_parent_edges` (the one tree-nesting rule, which both DAG builders wrap).

This module is PURE and STREAMING: it reads files and returns records. No database,
no network, no UUID minting. The orchestrator (mesh_rel_run.py) does all of that.
Every file is streamed with iterparse + clear, so peak memory scales with the QUERY
(the wanted set), never with the release -- supp2026 is ~750 MB uncompressed.
"""
import pathlib
from collections.abc import Iterable
from dataclasses import dataclass

from drugref.ingest import mesh
from drugref.ingest.mesh import iter_records, registry_keys

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
    concept may be NARROWER than the record it belongs to. Storing the condition at
    record grain loses that nuance; this flag makes the loss visible and measurable
    instead of silent (spec §10 tension C).

    MEASURED SEPARATELY PER HALF, because the module now serves both and the two
    populations differ in size AND in what the loss costs:
      * contraindications -- 81 of 1,051 resolved objects are subordinate, carrying
        550 of 13,463 assertions. SAFE: broadening a contraindication widens recall,
        which is the direction db/014 wants.
      * indications       -- 90 of 1,528 resolved objects are subordinate, carrying
        422 of 18,314 assertions (2.30%) onto 85 broader records. UNSAFE: it offers a
        drug for a condition the release never named it for.
    The reader that makes the flag do work is mesh_ind_relations.write_indications,
    which counts the second figure on every run; read its docstring for the
    eslicarbazepine case and #52 for making the affected ROWS detectable.
    """
    concept_ui: str
    record_ui: str
    record_kind: str                    # DESCRIPTOR | SCR
    name: str
    tree_numbers: tuple[str, ...]
    unii: frozenset[str]
    cas: frozenset[str]
    is_preferred_concept: bool
    # MeSH's SCRClass, AS PUBLISHED, and None for a descriptor (which carries
    # DescriptorClass, a different vocabulary). Stored rather than interpreted because
    # supp2026 publishes SIX values -- 1: 249,245 · 4: 65,236 · 3: 6,542 · 5: 1,763 ·
    # 2: 1,236 · 6: 23 -- while the documentation describes four, so drugref asserts a
    # meaning for none of them here. Exactly one consumer reads it, and it reads only
    # '3' (rare disease): db/019's gap_condition_without_indication, which needs to tell
    # 'Short QT Syndrome' from 'aliskiren' among records that bear no tree numbers and
    # so have no DAG position to reason about.
    scr_class: str | None = None


@dataclass(frozen=True)
class ConditionParentEdge:
    """One condition-DAG edge: `child_code` is a kind of `parent_code`.
    Both are MeSH record UIs (the key drugref stores), never concept UIs."""
    child_code: str
    parent_code: str


def is_descendant_tree(tree_number: str, prefix: str) -> bool:
    """Is `tree_number` STRICTLY below `prefix` in the MeSH tree?

    THE DEFINITION OF THE RULE, stated in the clearest form there is. Segment-aware
    on purpose: a bare str.startswith would make "C10.228.140.49" a parent of
    "C10.228.140.490" -- two unrelated concepts whose numbers merely share a text
    prefix -- and would also report a node as its own descendant, which would put a
    self-edge in condition_parent that db/013's CHECK then rejects mid-ingest.

    `ancestor_trees` is the same rule read backwards, and is what bulk matching
    uses; test_mesh_concepts pins the two against each other.
    """
    return tree_number.startswith(prefix + ".")


def ancestor_trees(tree_number: str) -> list[str]:
    """Every tree number STRICTLY ABOVE `tree_number`, outermost first.

    THE SAME RULE AS is_descendant_tree, INVERTED, and the inversion is worth a
    function because of what it does to the cost of a closure scan. Asking
    "is this record below any of my prefixes?" the direct way is
    len(trees) x len(prefixes) string comparisons per record; against the real
    release that is ~31k descriptors x ~2 tree numbers x ~1.1k prefixes, and it
    dominated the ingest. Asking it this way is len(trees) x DEPTH set lookups --
    a MeSH tree number is at most a dozen segments deep, and the prefix count stops
    mattering entirely. Measured on release-shaped data: 4.49s -> 0.03s.

    `p in ancestor_trees(t)` and `is_descendant_tree(t, p)` are the same predicate.
    A single-segment number is top-level and has no ancestors, so this returns [].
    """
    parts = tree_number.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


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
                      is_preferred_concept=preferred,
                      # .get() returns None on a DescriptorRecord, which has no such
                      # attribute -- the desired answer, arrived at structurally.
                      scr_class=el.get("SCRClass"))


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
    file order. Implemented by reading desc BEFORE supp and subtracting what
    resolved, so a concept already found in the descriptor file is never looked for
    in the supplemental one.

    MEASURED, so nobody reads that guarantee as describing today's release: the 2026
    release defines 61,794 ConceptUIs in desc2026 and 402,107 in supp2026, and
    **exactly 0 appear in both** -- MeSH partitions concept ids across the two files.
    The tie-break is therefore a GUARD against a release whose partition changes, not
    a live case, and it is pinned on controlled input in test_mesh_concepts (#42)
    because there is nothing in the release to extract. Keep it: preferring the SCR
    would mint a different -- and immortal -- condition_uuid for the same clinical
    concept, and an SCR bears no tree numbers, so the condition would silently drop
    out of the DAG taking its whole descendant expansion with it.
    """
    out: dict[str, MeshRecord] = {}
    remaining = set(wanted)
    for path, tag, ui_tag, name_tag, kind in (
            (desc_path, *_SOURCES[0][1:]), (supp_path, *_SOURCES[1][1:])):
        if not remaining:
            break                                   # everything already resolved
        for el in iter_records(path, tag):
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

    Measured on the real 2026 releases, over every MeSH-keyed object slice 5b.2
    registers: 1,730 referenced descriptors -> 4,294 tree prefixes -> 5,718 descriptors
    strictly below them. That 5,718 is the DESCRIPTOR CLOSURE, and it is neither the
    registry nor the registry's descriptor count. The caller unions it with the records
    that were REFERENCED (mesh_rel_run._condition_closure), which brings the registry to
    5,963: 5,929 descriptors plus 34 SCRs, which bear no tree numbers and so can only
    ever appear as themselves. Three numbers, three different questions.

    Each record is returned under its own PREFERRED concept where it has one, since
    the caller keys conditions by record_ui and only needs a concept for provenance.
    """
    found: list[MeshRecord] = []
    if not tree_prefixes:
        return found
    for el in iter_records(desc_path, "DescriptorRecord"):
        trees = [t.text for t in el.findall("TreeNumberList/TreeNumber") if t.text]
        # Matched by walking each tree number's OWN ancestors and probing the prefix
        # SET, not by testing every prefix against every tree number: same predicate
        # as is_descendant_tree, but independent of how many prefixes there are.
        if not any(a in tree_prefixes for t in trees for a in ancestor_trees(t)):
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

    THE RULE IS mesh.tree_parent_edges, and this function only wraps its
    `(child, parent)` pairs in ConditionParentEdge -- deliberately, so there is ONE
    way of turning MeSH tree numbers into a DAG in this codebase and not two. Read
    that docstring for what the rule actually decides (immediate tree-parent only,
    both endpoints ingested, no self-edges, deterministic order).

    The only thing this layer adds is the CONCEPT-TO-RECORD collapse. `records` is
    keyed by concept upstream, so several entries can carry the same `record_ui`
    (that is the whole point of MeshRecord holding both). Grouping their tree numbers
    under the record ui before applying the rule is what stops one condition being
    treated as several -- the same collapse the worklist and the registry make.

    Multi-parent by construction: a descriptor bears several tree numbers, which is
    why 2,149 of the registry's 5,963 conditions have more than one parent. That is
    not a curiosity -- it is the mechanism behind spec 3.6: an edge is written only
    when BOTH endpoints are registered, so widening the registry COMPLETES edges an
    already-registered condition was missing rather than merely adding new leaves.
    """
    trees_by_ui: dict[str, list[str]] = {}
    for record in records:
        trees_by_ui.setdefault(record.record_ui, []).extend(record.tree_numbers)
    return [ConditionParentEdge(child_code=child, parent_code=parent)
            for child, parent in mesh.tree_parent_edges(trees_by_ui)]
