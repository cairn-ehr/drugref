"""Extract a MeSH desc/supp subset covering slice 5b's contraindication objects.

Run:
    uv run python tests/fixtures/make_mesh_ci_subset.py \
        downloads/mesh/desc2026.gz downloads/mesh/supp2026.gz tests/fixtures/

WHY A SEPARATE FIXTURE FROM slice 2b's mesh_desc_subset.xml: 2b's fixture is scoped
to the PHARMACOLOGICAL ACTION axis, and 5b's objects are diseases, physiological
states and procedures -- disjoint records. Extending 2b's file would grow it for a
purpose its own tests do not share; a separate file keeps each fixture legible.

EXTRACTED FROM THE REAL RELEASE, NEVER HAND-WRITTEN. This is the standing rule since
issue #27, where the last hand-written fixture concealed a wrong column name that
would have shipped an entirely unlabelled registry. A fixture invented by hand can
only ever confirm what its author already believed.

THE WANTED SET IS DERIVED, NOT LISTED, AND THAT IS THIS SCRIPT'S WHOLE POINT.
The first version of this file carried a hand-picked `WANT_DESC` list, and two
things went wrong at once:

  1. It named D010860 for Pimozide. The real 2026 release has D010860 = "Pigments,
     Biological"; Pimozide is D010868. A list of codes a human typed is a list of
     codes a human can mistype, and nothing downstream could have noticed.
  2. Not one of its codes was an object that tests/fixtures/medrt_subset.xml
     actually asserts. The MeSH fixture was picked from a descriptor list while the
     MED-RT fixture was built from an ingredient list, so the two files described
     disjoint worlds: every CI object resolved to nothing, and an end-to-end ingest
     over the pair produced zero rows while both fixtures looked healthy alone.

So the wanted set is now READ OUT OF medrt_subset.xml: every MeSH `to_code` of a
CI_with / CI_ChemClass association, resolved against the real release. Regenerating
the MED-RT fixture and then this one cannot leave them disagreeing, and no code here
is ever typed by hand. That removes both failure modes permanently rather than
fixing the two instances of them.

WHAT IS ADDED ON TOP OF THE DERIVED SET, and why that is not a relapse:

  * A DESCENDANT CLOSURE SAMPLE (see MAX_CHILDREN_*). The referenced objects are
    what a rule NAMES; the read path expands a rule DOWN the tree, and a descendant
    is by definition not itself a CI object. Without a few real children the
    expansion path has nothing to run on and would look correct while inert. These
    are found by tree nesting in the release, not named here.
  * RESOLVER_TEST_DESC / RESOLVER_TEST_SUPP -- a handful of records that exist for
    tests/test_mesh_concepts.py, which unit-tests the resolver itself and needs a
    known parent/child pair (Epilepsy), a known unrelated branch (Pregnancy) and a
    supplementary record. They are keyed by UI because they are cited BY UI in that
    test file; each is verified to exist in the release, and generation FAILS if one
    ever stops existing rather than quietly shrinking the fixture.

RECORDS ARE COPIED FROM THE RELEASE, NEVER RECONSTRUCTED -- element names, nesting
and every value come from upstream, so the fixture cannot disagree with MeSH about
its own shape. Two subtrees are DROPPED from each copied record (PRUNED_SUBTREES):
they are 70-80% of the bytes and no drugref code path reads either. Pruning whole
named subtrees is not reconstruction: everything retained sits exactly where MeSH
put it. Slice 2b's make_mesh_subset.py goes further and rebuilds records outright,
so minimising a MeSH fixture is established practice here; this script deliberately
stops at removal.

LICENCE: MeSH is NLM, redistributable with attribution (cleared in slice 2b, see
NOTICE). No other namespace appears in these files.
"""
import gzip
import pathlib
import re
import sys
from xml.etree import ElementTree as ET

# The two slice-5b predicates. CI_with objects are CONDITIONS (a patient state) and
# are the ones whose descendants matter; CI_ChemClass objects are substances or
# chemical classes and are never expanded (db/014, db/016).
CI_PREDICATES = ("CI_with", "CI_ChemClass")
CONDITION_PREDICATE = "CI_with"
MESH_NAMESPACE = "MeSH"

# Records kept for tests/test_mesh_concepts.py, which tests the RESOLVER rather than
# the ingest and therefore needs specific, citable records. Keyed by UI, and checked
# to exist -- see the module docstring for why an explicit set is safe here and a
# hand-picked *wanted* set was not.
RESOLVER_TEST_DESC = {
    "D004827": "Epilepsy -- the worked example; the parent of the closure pair",
    "D004829": "Epilepsy, Generalized -- strictly below D004827 (closure + DAG edge)",
    "D011247": "Pregnancy -- a record in an unrelated branch, so 'not a descendant' "
               "assertions cannot pass merely because the fixture is small",
}
RESOLVER_TEST_SUPP = {
    "C536778": "an SCR, to exercise the supplementary-record fallback (86 of the "
               "release's objects resolve only here)",
}

# Subtrees removed from every copied record. NOTHING in drugref reads either:
# mesh_concepts.py walks DescriptorUI / SupplementalRecordUI, the record name,
# TreeNumberList/TreeNumber, ConceptList/Concept (ConceptUI + PreferredConceptYN)
# and RegistryNumber. AllowableQualifiersList alone is ~9 KB of every descriptor --
# with both kept, a fixture this size would be ~500 KB of XML nobody reads.
PRUNED_SUBTREES = ("AllowableQualifiersList", "TermList")

# How much of the descendant closure to sample. A handful of real children is enough
# to prove expansion works; the true closure of one condition (Liver Diseases has
# 400+ descendants) would make the fixture unreadable and slow, and proves nothing
# the first child does not. Both caps are applied in sorted order, so the selection
# is reproducible rather than dependent on file order.
MAX_CHILDREN_PER_CONDITION = 2
MAX_CHILDREN_TOTAL = 8


def _field(block: str, tag: str) -> str:
    """One flat <tag>value</tag> out of a MED-RT association block."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return m.group(1).strip() if m else ""


def ci_object_codes(medrt_path) -> tuple[set[str], set[str]]:
    """The MeSH ConceptUIs `medrt_path` states contraindications against.

    Returns (every CI object code, just the CI_with ones). Read with the same regex
    idiom make_medrt_subset.py writes the file with -- the fixture is small and flat,
    and a shared shape between the writer and the reader is easier to keep true than
    a second parser would be.

    Scoped to MeSH endpoints: MED-RT also states CI_with against its own EXT
    concepts, which no MeSH file defines and which the ingest counts separately.
    """
    data = pathlib.Path(medrt_path).read_text(encoding="utf-8")
    wanted: set[str] = set()
    conditions: set[str] = set()
    for block in re.findall(r"<association>(.*?)</association>", data, re.S):
        name = _field(block, "name")
        if name not in CI_PREDICATES:
            continue
        if _field(block, "to_namespace") != MESH_NAMESPACE:
            continue
        code = _field(block, "to_code")
        wanted.add(code)
        if name == CONDITION_PREDICATE:
            conditions.add(code)
    return wanted, conditions


def _open(path):
    """Open a MeSH file, transparently handling the .gz the NLM publishes."""
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def _stream(path, tag):
    """Yield each `tag` element, then detach it AND the growing root.

    Same idiom as ingest/mesh_concepts._stream, and load-bearing for the same reason:
    supp2026 is ~750 MB uncompressed, and keeping the retired siblings hanging off
    the root would make peak memory grow with the file.
    """
    with _open(path) as fh:
        context = ET.iterparse(fh, events=("start", "end"))
        _event, root = next(context)
        for event, el in context:
            if event == "end" and el.tag == tag:
                yield el
                el.clear()
                root.clear()


def _copy(el) -> str:
    """Serialise one record, minus the subtrees nothing reads (PRUNED_SUBTREES).

    Removal, not reconstruction: what survives is upstream's own element tree in
    upstream's own nesting, so the fixture keeps every fact a parser could trip over.
    """
    for tag in PRUNED_SUBTREES:
        for parent in el.iter():
            for child in list(parent):
                if child.tag == tag:
                    parent.remove(child)
    return ET.tostring(el, encoding="unicode")


def _concept_uis(el) -> set[str]:
    """Every ConceptUI a record owns -- the key MED-RT's `to_code` points at."""
    return {c.text for c in el.iter("ConceptUI") if c.text}


def _trees(el) -> list[str]:
    return [t.text for t in el.findall("TreeNumberList/TreeNumber") if t.text]


def _is_immediate_child(tree: str, prefix: str) -> bool:
    """Is `tree` DIRECTLY below `prefix` (one segment deeper, same lineage)?

    Segment-aware, matching mesh_concepts.is_descendant_tree: a bare startswith would
    make C10.228.140.49 a parent of C10.228.140.490, two unrelated concepts whose
    numbers merely share text. Immediate children only, because an edge is only
    written when the immediate tree-parent is itself in the fixture -- a deeper
    descendant would arrive parentless and contribute no DAG edge to test.
    """
    return tree.startswith(prefix + ".") and "." not in tree[len(prefix) + 1:]


def collect_descriptors(desc_path, wanted, conditions):
    """Pass 1: the records MED-RT names, plus the resolver test's own records.

    Returns (kept {ui: xml}, condition_prefixes {ui: [tree numbers]}). The prefixes
    are collected here because pass 2 cannot know what to look below until every
    referenced condition has been read.
    """
    kept: dict[str, str] = {}
    prefixes: dict[str, list[str]] = {}
    for el in _stream(desc_path, "DescriptorRecord"):
        ui = el.findtext("DescriptorUI") or ""
        concepts = _concept_uis(el)
        if not (concepts & wanted or ui in RESOLVER_TEST_DESC):
            continue
        if concepts & conditions:
            prefixes[ui] = _trees(el)
        kept[ui] = _copy(el)
    return kept, prefixes


def collect_children(desc_path, prefixes, already):
    """Pass 2: a capped, reproducible sample of the referenced conditions' children.

    Two passes over the release rather than one, because a child may be serialised
    before its parent and nothing may be selected until every prefix is known.
    """
    found: dict[str, list[tuple[str, str, str]]] = {}   # prefix -> [(tree, ui, xml)]
    flat = {tree: ui for ui, trees in prefixes.items() for tree in trees}
    for el in _stream(desc_path, "DescriptorRecord"):
        ui = el.findtext("DescriptorUI") or ""
        if ui in already:
            continue                        # already kept in its own right
        hits = [(prefix, tree) for tree in _trees(el) for prefix in flat
                if _is_immediate_child(tree, prefix)]
        if not hits:
            continue
        xml = _copy(el)
        for prefix, tree in hits:
            found.setdefault(prefix, []).append((tree, ui, xml))
    chosen: dict[str, str] = {}
    # Sorted twice over: prefixes in tree order, then children in tree order, so the
    # same release always yields the same sample no matter how the files were read.
    for prefix in sorted(found):
        for _tree, ui, xml in sorted(found[prefix])[:MAX_CHILDREN_PER_CONDITION]:
            if len(chosen) >= MAX_CHILDREN_TOTAL:
                return chosen
            chosen.setdefault(ui, xml)
    return chosen


def collect_supplementals(supp_path, wanted):
    """Every SCR owning a still-unresolved wanted concept, plus the resolver's own."""
    kept: dict[str, str] = {}
    for el in _stream(supp_path, "SupplementalRecord"):
        ui = el.findtext("SupplementalRecordUI") or ""
        if not (_concept_uis(el) & wanted or ui in RESOLVER_TEST_SUPP):
            continue
        kept[ui] = _copy(el)
    return kept


# The provenance header every committed release extract carries, matching
# make_medrt_subset.py's and make_mesh_subset.py's. It exists so a reader who opens
# the fixture -- rather than this script -- still learns three things they cannot
# otherwise see: the file is EXTRACTED (so hand-editing it will be silently undone by
# the next regeneration), the exact command that regenerates it, and, for a MeSH
# extract, the NLM courtesy line the licence asks for wherever the content travels.
#
# NB: an XML comment may not contain a double hyphen, so this text uses none.
_HEADER = (
    "<!-- EXTRACTED FROM A REAL MeSH 2026 RELEASE by make_mesh_ci_subset.py. Do not hand-edit.\n"
    "     Regenerate: python tests/fixtures/make_mesh_ci_subset.py DESC.gz SUPP.gz OUT_DIR\n"
    "     and regenerate it AFTER make_medrt_subset.py: the wanted set is read out of\n"
    "     medrt_subset.xml, so the two fixtures cannot be left describing disjoint worlds.\n"
    "     Records are COPIED from the release, never invented or reconstructed; only the\n"
    "     AllowableQualifiersList and TermList subtrees, which no drugref code path reads,\n"
    "     are removed. Courtesy of the U.S. National Library of Medicine.\n"
    "     MeSH is attributable (see NOTICE); nothing is redacted; these files are single source. -->")


def write(out_path: pathlib.Path, root_tag: str, records: dict[str, str]) -> None:
    """Write one fixture file, records in UI order so a regeneration diffs cleanly."""
    body = "\n".join(records[ui] for ui in sorted(records))
    out_path.write_text(
        f'<?xml version="1.0"?>\n{_HEADER}\n<{root_tag}>\n{body}\n</{root_tag}>\n',
        encoding="utf-8")
    print(f"{out_path}: {len(records)} records, {out_path.stat().st_size} bytes")


def main(desc_path, supp_path, outdir: pathlib.Path) -> None:
    medrt_path = outdir / "medrt_subset.xml"
    wanted, conditions = ci_object_codes(medrt_path)
    print(f"{medrt_path}: {len(wanted)} CI object codes "
          f"({len(conditions)} of them CI_with)")

    descriptors, prefixes = collect_descriptors(desc_path, wanted, conditions)
    children = collect_children(desc_path, prefixes, set(descriptors))
    descriptors.update(children)
    supplementals = collect_supplementals(supp_path, wanted)
    print(f"  {len(prefixes)} referenced conditions -> {len(children)} sampled "
          f"descendants: {sorted(children)}")

    write(outdir / "mesh_ci_desc_subset.xml", "DescriptorRecordSet", descriptors)
    write(outdir / "mesh_ci_supp_subset.xml", "SupplementalRecordSet", supplementals)

    # A derived code that resolves nowhere is REPORTED, not fatal: 2 of the real
    # release's 1,053 objects are withdrawn upstream, and the ingest counts exactly
    # that case. An EXPLICIT record that vanished is fatal, for make_unii_subset.py's
    # reason -- it would silently delete a test case nobody would notice was gone.
    resolved = {cui for xml in list(descriptors.values()) + list(supplementals.values())
                for cui in _concept_uis(ET.fromstring(xml))}
    missing_codes = sorted(wanted - resolved)
    if missing_codes:
        print(f"  NOTE: {len(missing_codes)} CI object code(s) resolve to no MeSH "
              f"record and will be counted as unresolved: {missing_codes}")
    missing_records = (sorted(set(RESOLVER_TEST_DESC) - set(descriptors))
                       + sorted(set(RESOLVER_TEST_SUPP) - set(supplementals)))
    if missing_records:
        raise SystemExit(
            f"make_mesh_ci_subset.py: resolver-test record(s) {missing_records} are "
            f"not in this release. Update RESOLVER_TEST_* and the tests citing them "
            f"together -- do not ship a fixture that quietly lost a test case.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]))
