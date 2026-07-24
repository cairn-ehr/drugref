"""Parse the MED-RT release file into classification records.

MED-RT (Medication Reference Terminology, US Dept. of Veterans Affairs) is the
successor to NDF-RT. It publishes pharmacologic CLASS concepts and the asserted
relationships between them -- exactly the is-a-kind-of structure drugref needs to
hang class-level knowledge off. It is distributed as Apelon-DTS XML; the release
zip ships its own schema, MED-RT_Schema_v1.xsd.

WHAT THIS MODULE READS, AND WHY ONLY THIS
-----------------------------------------
* Class concepts live in the "MED-RT" namespace, identified by their NUI (MED-RT's
  "code in source"), each carrying a CTY (concept type) property. We ingest six
  types -- see INGESTED_CONCEPT_TYPES for which, and why HC and EXT are not
  among them.
* Ingredient concepts live in the "RxNorm" namespace, where the code in source IS
  the RxCUI. That is the join key back to our moiety registry, because slice 1
  already records an RXNORM_IN identity claim for every moiety.

LICENCE-CRITICAL: MED-RT is built partly from SNOMED CT US Edition and MeSH, and
its hierarchy genuinely maps out into both (761 SNOMED->MED-RT edges in the
2026.07.06 release). SNOMED is NOT redistributable under our licence. Usefully,
only MED-RT-namespace concepts are *defined* in the file -- SNOMED and MeSH appear
only as association endpoints -- so unlicensed content can enter through exactly
one door: an edge. This parser closes that door by requiring both endpoints of any
edge to be classes we ingested. Do not relax that check.

TWO FACTS ESTABLISHED FROM THE REAL RELEASE, NOT FROM THE DOCUMENTATION
-----------------------------------------------------------------------
1. 'Parent Of' runs FROM the parent TO the child. Verified two ways: the MoA root
   N0000000223 appears as from_code 9 times and as to_code never (a root has no
   parent), and 'A [Preparations]' is the *from* of paracetamol. Reading this
   backwards inverts the entire DAG, and no hand-written fixture would catch it.
2. [HC] concepts are the 26 alphabetical navigation bins ('A [Preparations]',
   'M [Preparations]'), not classifications. They account for 18,450 of the 21,058
   class->ingredient edges, so ingesting them would file nearly every drug under a
   meaningless letter. They are excluded outright.

This module is PURE: it reads a file and returns records. No database, no network,
no UUID minting. The orchestrator (medrt_run.py) does all of that.
"""
import pathlib
from dataclasses import dataclass
from xml.etree import ElementTree

# The only two namespaces we are licensed to read (see the module docstring).
MEDRT_NAMESPACE = "MED-RT"
RXNORM_NAMESPACE = "RxNorm"

# MED-RT concept types (CTY) we ingest as classes. MoA/PE/TC/PK are reached by the
# matching has_* association; EPC is reached hierarchically (see EPC_RELATIONSHIP);
# APC is the parent type of the APC->EPC hierarchy edges, without which the EPC
# tree is truncated at the top. Kept in lockstep with the CHECK constraint on
# drugref.substance_class.concept_type.
#
# NOT ingested: HC (alphabetical navigation bins) and EXT (chemical concepts staged
# for eventual addition to MeSH, with no ingredient membership).
INGESTED_CONCEPT_TYPES = frozenset({"MoA", "PE", "TC", "PK", "EPC", "APC"})

# Ingredient -> class assertions, all of which run RxNorm -> MED-RT. Kept in
# lockstep with the CHECK constraint on drugref.class_membership.relationship.
# Absent on purpose: may_treat / may_prevent / CI_* (curated-overlay data for a
# later slice) and has_SC (targets MeSH; slice 2b).
MEMBERSHIP_RELATIONSHIPS = frozenset({"has_MoA", "has_PE", "has_TC", "has_PK"})

# The hierarchical relationship, which does double duty: MED-RT -> MED-RT builds
# the subclass DAG, while EPC -> RxNorm expresses a drug's membership of an
# Established Pharmacologic Class.
PARENT_RELATIONSHIP = "Parent Of"

# drugref's own label for EPC membership. MED-RT has no has_EPC association type,
# so we normalise the hierarchical form into the same shape as the other four axes.
EPC_RELATIONSHIP = "has_EPC"
EPC_CONCEPT_TYPE = "EPC"


@dataclass(frozen=True)
class ClassConcept:
    """One MED-RT pharmacologic class."""
    nui: str
    name: str
    concept_type: str


@dataclass(frozen=True)
class ParentEdge:
    """One DAG edge: `child_nui` is a kind of `parent_nui`."""
    child_nui: str
    parent_nui: str


@dataclass(frozen=True)
class MembershipAssertion:
    """MED-RT asserts that the ingredient with `rxcui` belongs to class
    `class_nui` on the axis named by `relationship`."""
    rxcui: str
    class_nui: str
    relationship: str


@dataclass(frozen=True)
class ParsedMedrt:
    """Everything one MED-RT file yields, already scoped to what we may ingest."""
    classes: list[ClassConcept]
    parents: list[ParentEdge]
    memberships: list[MembershipAssertion]


def _text(element, tag: str) -> str:
    """Return the stripped text of a child tag, or '' when absent.

    XML text nodes are None for an empty or missing tag, so every read goes
    through here rather than risking None.strip() on a short record.
    """
    found = element.find(tag)
    return (found.text or "").strip() if found is not None else ""


def _properties(concept) -> dict[str, str]:
    """Collapse a concept's nested <property><name>/<value> pairs into a dict."""
    return {_text(p, "name"): _text(p, "value") for p in concept.findall("property")}


def _parse_concepts(root) -> list[ClassConcept]:
    """Keep only MED-RT-namespace concepts whose CTY is one we ingest."""
    classes = []
    for concept in root.findall("concept"):
        if _text(concept, "namespace") != MEDRT_NAMESPACE:
            continue                                 # not a MED-RT-owned concept
        props = _properties(concept)
        concept_type = props.get("CTY", "")
        if concept_type not in INGESTED_CONCEPT_TYPES:
            continue                                 # HC bins, EXT, anything new upstream
        # The NUI property is authoritative; <code> carries the same value in
        # practice, so it is only a fallback.
        nui = props.get("NUI") or _text(concept, "code")
        classes.append(ClassConcept(nui=nui, name=_text(concept, "name"),
                                    concept_type=concept_type))
    return classes


def parse(path: str | pathlib.Path) -> ParsedMedrt:
    """Read one MED-RT XML release into the records slice 2a ingests."""
    root = ElementTree.parse(path).getroot()
    classes = _parse_concepts(root)
    known = {c.nui for c in classes}
    epc_nuis = {c.nui for c in classes if c.concept_type == EPC_CONCEPT_TYPE}

    parents: list[ParentEdge] = []
    memberships: list[MembershipAssertion] = []
    for assoc in root.findall("association"):
        name = _text(assoc, "name")
        from_ns, from_code = _text(assoc, "from_namespace"), _text(assoc, "from_code")
        to_ns, to_code = _text(assoc, "to_namespace"), _text(assoc, "to_code")

        if name == PARENT_RELATIONSHIP:
            if from_ns == MEDRT_NAMESPACE and to_ns == MEDRT_NAMESPACE:
                # Class hierarchy. Requiring BOTH endpoints to be ingested classes
                # is what drops hierarchy mapped out into SNOMED/MeSH and edges
                # hanging off HC navigation bins.
                if from_code in known and to_code in known:
                    # 'Parent Of' is parent -> child, so `from` is the parent.
                    parents.append(ParentEdge(child_nui=to_code, parent_nui=from_code))
            elif (from_ns == MEDRT_NAMESPACE and to_ns == RXNORM_NAMESPACE
                    and from_code in epc_nuis):
                # An EPC class sitting above a drug: this IS the drug's EPC
                # membership. Only EPC parents count -- the same shape with an HC
                # parent is just the alphabetical bin the drug is filed under.
                memberships.append(MembershipAssertion(
                    rxcui=to_code, class_nui=from_code, relationship=EPC_RELATIONSHIP))
        elif name in MEMBERSHIP_RELATIONSHIPS:
            # Axis membership always runs ingredient (RxNorm) -> class (MED-RT).
            # The MED-RT -> MED-RT variant is a class describing itself, not
            # membership, so it is skipped by the namespace test.
            if from_ns == RXNORM_NAMESPACE and to_ns == MEDRT_NAMESPACE and to_code in known:
                memberships.append(MembershipAssertion(
                    rxcui=from_code, class_nui=to_code, relationship=name))
        # Everything else (may_treat, CI_with, has_SC, Synonym Of, ...) is either
        # curated-overlay data for a later slice or points at a namespace we may
        # not read, and is deliberately ignored.
    return ParsedMedrt(classes=classes, parents=parents, memberships=memberships)
