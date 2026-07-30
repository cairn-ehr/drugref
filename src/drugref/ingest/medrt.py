"""Parse the MED-RT release file into classification records.

MED-RT (Medication Reference Terminology, US Dept. of Veterans Affairs) is the
successor to NDF-RT. It publishes pharmacologic CLASS concepts and the asserted
relationships between them -- exactly the is-a-kind-of structure drugref needs to
hang class-level knowledge off. It is distributed as Apelon-DTS XML; the release
zip ships its own schema, MED-RT_Schema_v1.xsd.

WHAT THIS MODULE READS, AND WHY ONLY THIS
-----------------------------------------
* Class concepts live in the "MED-RT" namespace, each carrying a CTY (concept
  type) property. We ingest six types -- see INGESTED_CONCEPT_TYPES for which, and
  why HC and EXT are not among them. A concept has both a NUI (its stable identity,
  which class_uuid derives from) and a published <code> (what associations
  reference it by); see parse() for why those are kept apart.
* Ingredient concepts live in the "RxNorm" namespace, where the code in source IS
  the RxCUI. That is the join key back to our moiety registry, because slice 1
  already records an RXNORM_IN identity claim for every moiety.

* MeSH concepts appear as association endpoints only, and are read for exactly six
  predicates -- CI_with, CI_ChemClass (MESH_CI_RELATIONSHIPS), and may_treat,
  may_prevent, may_diagnose, induces (MESH_INDICATION_RELATIONSHIPS). Their object
  code is handed on RAW, never resolved here; ingest/mesh_concepts.py resolves it
  against the MeSH release.

LICENCE-CRITICAL: MED-RT is built partly from SNOMED CT US Edition and MeSH, and
its hierarchy genuinely maps out into both (761 SNOMED->MED-RT edges in the
2026.07.06 release). SNOMED is NOT redistributable under our licence; MeSH IS
licence-cleared (slice 2b, NLM terms). Usefully, only MED-RT-namespace concepts are
*defined* in the file -- SNOMED and MeSH appear only as association endpoints -- so
unlicensed content can enter through exactly one door: an edge. This parser closes
that door by scoping every edge to a NAMED namespace pair: class hierarchy and
membership require both endpoints to be classes we ingested, and the six MeSH-keyed
contraindications and indications require RxNorm -> MeSH specifically. No branch
admits an endpoint just because it is "not MED-RT", so SNOMED has nowhere to enter.
Do not relax that.

The parser is not the only channel, though: a committed TEST FIXTURE naming an
out-of-scope endpoint redistributes that term whatever this code does with it, so
tests/fixtures/make_medrt_subset.py redacts the terms and codes of endpoints
outside the namespaces drugref may redistribute (SNOMED among them) on extraction.

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
from dataclasses import dataclass, field
from xml.etree import ElementTree

# ClassConcept is the source-neutral "class row to upsert" shape. It lives in
# drugref.classes (beside the writer that consumes it) now that MeSH feeds it too;
# re-imported here so medrt.ClassConcept and ParsedMedrt keep working unchanged.
from drugref.classes import ClassConcept

# The namespaces this parser reads. MED-RT owns the class concepts, RxNorm names the
# ingredients; MeSH (below, MESH_NAMESPACE) reaches only the six MeSH-keyed predicates
# -- two contraindications, four indications. See the module docstring for why the
# list is closed and named.
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
# Absent on purpose: may_treat / may_prevent / may_diagnose / induces (MeSH-keyed
# indications, slice 5b.2 -- see MESH_INDICATION_RELATIONSHIPS, below) and has_SC
# (targets MeSH; slice 2b). CI_MoA / CI_PE are handled separately, below.
MEMBERSHIP_RELATIONSHIPS = frozenset({"has_MoA", "has_PE", "has_TC", "has_PK"})

# Drug -> class CONTRAINDICATIONS (slice 5a): "contraindicated MoA / physiological
# effect of a CO-ADMINISTERED ingredient" -- drug-drug interaction rules, not
# membership (the subject is not a member of the class; it is contraindicated with
# drugs that are). Both run RxNorm -> MED-RT, so both endpoints are already-ingested
# drugref content. Kept in lockstep with the CHECK on
# drugref.class_contraindication.relationship. NOT here: CI_with / CI_ChemClass,
# whose object is a MeSH concept rather than a MED-RT class -- they have their own
# list and their own record type, immediately below.
CI_RELATIONSHIPS = frozenset({"CI_MoA", "CI_PE"})

# MeSH-keyed contraindications (slice 5b). These normally run RxNorm -> MeSH, so
# their OBJECT is a MeSH ConceptUI this parser cannot resolve on its own --
# ingest/mesh_concepts.py does that, from the MeSH release. The parser therefore
# hands the raw code on rather than resolving or dropping it. "Normally" is doing
# real work in that sentence: a handful of rows run elsewhere, and parse() refuses
# and counts them rather than assuming the object is MeSH (non_mesh_ci_objects).
#
#   CI_with       -- "contraindicated in a patient with <condition>". 11,524 of the
#                    2026.07.06 release's 11,526 CI_with assertions are MeSH-keyed;
#                    the other 2 point at a MED-RT EXT concept. The object is usually
#                    a disease, but also pregnancy, lactation, a procedure or a
#                    demographic.
#   CI_ChemClass  -- "do not co-administer with <this chemical>". All 1,939 assertions
#                    are MeSH-keyed, and the object is mostly a SPECIFIC DRUG
#                    (Pimozide, Cisapride, Ritonavir) rather than a class, which is
#                    why slice 5b resolves its object against the moiety registry
#                    first.
MESH_CI_RELATIONSHIPS = frozenset({"CI_with", "CI_ChemClass"})

# MeSH-keyed INDICATIONS (slice 5b.2). Same endpoint shape as MESH_CI_RELATIONSHIPS --
# RxNorm subject, MeSH ConceptUI object this parser hands on RAW -- and scoped the same
# way, which is what keeps SNOMED endpoints unreadable.
#
#   may_treat     -- 15,319 RxNorm->MeSH assertions in the 2026.07.06 release
#   may_prevent   --  2,670, and the object is often the ORGANISM rather than the
#                     infection (Influenza A virus 76): these are the vaccines.
#   may_diagnose  --    155
INDICATION_RELATIONSHIPS = frozenset({"may_treat", "may_prevent", "may_diagnose"})

# `induces` points the OTHER WAY: the drug CAUSES the state (Unconsciousness 32,
# Mydriasis 14, Diarrhea 8), which is sometimes the therapeutic point and sometimes the
# adverse effect -- MED-RT does not say which. It is neither an indication nor a
# contraindication and db/019 gives it its own table so it cannot be read as either.
INDUCES_RELATIONSHIP = "induces"

# Parsed together because the parsing problem is identical; separated downstream,
# where the MEANING differs. 170 induces assertions, all RxNorm->MeSH.
MESH_INDICATION_RELATIONSHIPS = INDICATION_RELATIONSHIPS | {INDUCES_RELATIONSHIP}

# The namespace a MeSH-keyed contraindication or indication's object must live in.
# MeSH is licence-cleared for drugref (NLM terms: attribution, no-endorsement,
# version-currency), which is what makes reading these six predicates possible at
# all -- SNOMED CT endpoints remain unreadable and unredistributable.
MESH_NAMESPACE = "MeSH"

# The hierarchical relationship, which does double duty: MED-RT -> MED-RT builds
# the subclass DAG, while EPC -> RxNorm expresses a drug's membership of an
# Established Pharmacologic Class.
PARENT_RELATIONSHIP = "Parent Of"

# drugref's own label for EPC membership. MED-RT has no has_EPC association type,
# so we normalise the hierarchical form into the same shape as the other four axes.
EPC_RELATIONSHIP = "has_EPC"
EPC_CONCEPT_TYPE = "EPC"


# A concept is only ingested while upstream still asserts it is active. MED-RT
# ships every concept as status 'A' today, so this never fires against the current
# release -- it exists so that a future release which starts publishing retired
# concepts cannot quietly seed dead classes into a registry that never deletes.
ACTIVE_STATUS = "A"


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
class ContraindicationAssertion:
    """MED-RT asserts that the ingredient with `rxcui` (the drug the statement is
    ABOUT) is contraindicated with a co-administered drug of class `class_nui`, on
    the axis named by `relationship` (CI_MoA or CI_PE). The clinical direction is
    carried entirely by which side is which -- reversing it inverts the meaning."""
    rxcui: str
    class_nui: str
    relationship: str


@dataclass(frozen=True)
class MeshObjectAssertion:
    """One MED-RT assertion whose SUBJECT is an RxNorm ingredient and whose OBJECT
    is a MeSH concept -- shared by both the MeSH-keyed contraindications
    (MESH_CI_RELATIONSHIPS) and the MeSH-keyed indications (MESH_INDICATION_RELATIONSHIPS,
    slice 5b.2). The MEANING lives entirely in `relationship`: a CI_with row is a
    contraindication, a may_treat row is an indication, and an induces row is neither
    (MED-RT does not say whether the drug causing the state is the therapeutic point
    or an adverse effect) -- this record only carries the endpoints, not a judgement
    about what kind of claim they form.

    `rxcui` is the drug the statement is ABOUT and `mesh_code` is the MeSH concept on
    the other end of `relationship` -- the direction is load-bearing for every one of
    these predicates, and reversing it inverts the meaning regardless of which one it is.

    `mesh_code` is a MeSH ConceptUI ("M0004868") -- NOT a DescriptorUI. It is left
    unresolved here on purpose: this module is pure and reads only the MED-RT file,
    while resolving the code needs the MeSH release. The orchestrator joins the two.
    """
    rxcui: str
    mesh_code: str
    relationship: str


@dataclass(frozen=True)
class ParsedMedrt:
    """Everything one MED-RT file yields, already scoped to what we may ingest.

    The counts are not decoration: a concept or assertion this parser refuses is one
    that will never reach the registry, so it is reported as a worklist number rather
    than dropped invisibly -- the same posture the slice-1 gate takes, and the same
    one MedrtSummary.unmatched_rxcuis takes for the membership join.
    """
    classes: list[ClassConcept]
    parents: list[ParentEdge]
    memberships: list[MembershipAssertion]
    contraindications: list[ContraindicationAssertion] = field(default_factory=list)
    mesh_contraindications: list[MeshObjectAssertion] = field(default_factory=list)
    # CI_with/CI_ChemClass assertions this parse could not use. Strictly, it counts
    # any endpoint pair OTHER than RxNorm -> MeSH, so a subject outside RxNorm lands
    # here too -- the name describes the only case the release actually contains, not
    # the only case that increments it. Two such rows exist in the 2026.07.06 release,
    # both RxNorm -> MED-RT pointing at the EXT concept 'Current Non-smoker', and EXT
    # is deliberately not an ingested concept type.
    # Counted rather than dropped, the same posture as inactive_concepts.
    non_mesh_ci_objects: int = 0
    mesh_indications: list[MeshObjectAssertion] = field(default_factory=list)
    # Indication assertions this parse could not use. Every one in the 2026.07.06
    # release is MED-RT -> MeSH -- a pharmacologic CLASS as the subject (may_treat 100,
    # may_prevent 90, may_diagnose 3) -- which has no RxCUI to bridge to a moiety.
    # Strictly it counts ANY endpoint pair other than RxNorm -> MeSH, so the name
    # describes the only case the release contains, not the only case that increments
    # it: the same honesty non_mesh_ci_objects' comment applies to itself. Ingesting
    # these needs a class->condition relation and a second expansion question, so they
    # are counted and filed against #8 rather than guessed at.
    class_subject_indications: int = 0
    inactive_concepts: int = 0        # right CTY, but upstream no longer marks it active
    unidentified_concepts: int = 0    # right CTY, but carries neither a NUI nor a code
    ambiguous_codes: int = 0          # one published code claimed by several concepts
    # The DISTINCT names this parse saw and ignored, sorted. Not errors -- HC/EXT
    # and may_treat/has_SC are deliberately out of scope -- but an upstream RENAME
    # of something we DO ingest looks identical to an ignore, so the vocabulary we
    # skipped is reported rather than assumed. A release-to-release diff of these
    # two tuples is what makes such a change visible at all.
    skipped_concept_types: tuple[str, ...] = ()
    skipped_predicates: tuple[str, ...] = ()


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


def _parse_concepts(root) -> tuple[list[ClassConcept], int, int, set[str]]:
    """Keep only active MED-RT-namespace concepts whose CTY is one we ingest.

    Returns the classes, the counts of concepts that had the right CTY but were
    refused anyway (inactive, and unidentified), and the DISTINCT concept types
    that were skipped -- all so the caller can report them rather than let an
    upstream vocabulary change pass unnoticed.
    """
    classes: list[ClassConcept] = []
    inactive = unidentified = 0
    skipped_types: set[str] = set()
    for concept in root.findall("concept"):
        if _text(concept, "namespace") != MEDRT_NAMESPACE:
            continue                                 # not a MED-RT-owned concept
        props = _properties(concept)
        concept_type = props.get("CTY", "")
        if concept_type not in INGESTED_CONCEPT_TYPES:
            skipped_types.add(concept_type)          # HC bins, EXT, anything new upstream
            continue
        # Only a status upstream still asserts is active. An absent <status> is
        # treated as active: every concept in the current release carries one, so
        # a missing element means a shape change, not a retirement.
        status = _text(concept, "status")
        if status and status != ACTIVE_STATUS:
            inactive += 1
            continue
        # NUI is the identity key, <code> is what associations reference. Either
        # may stand in for the other, but a concept carrying NEITHER has no usable
        # identity: minting from "" would collapse every such concept onto one
        # class_uuid and let them silently overwrite each other's names.
        code = _text(concept, "code")
        nui = props.get("NUI", "").strip() or code
        if not nui:
            unidentified += 1
            continue
        classes.append(ClassConcept(nui=nui, code=code or nui,
                                    name=_text(concept, "name"),
                                    concept_type=concept_type))
    return classes, inactive, unidentified, skipped_types


def _resolve_codes(classes: list[ClassConcept]) -> tuple[dict[str, str], int]:
    """Build the published-code -> NUI map an association endpoint resolves through.

    A code claimed by MORE THAN ONE concept is dropped from the map entirely and
    counted, rather than resolved to whichever concept happened to come last in
    document order. Last-write-wins here is not a near-miss: the two claimants may
    sit on different axes, so an edge would be filed against a class that has
    nothing to do with the assertion -- a has_MoA membership landing on a [PE]
    class, with no error anywhere. Refusing the code loses those edges (counted),
    which is recoverable; misfiling them is not.
    """
    nui_by_code: dict[str, str] = {}
    ambiguous: set[str] = set()
    for concept in classes:
        if concept.code in nui_by_code and nui_by_code[concept.code] != concept.nui:
            ambiguous.add(concept.code)
        nui_by_code[concept.code] = concept.nui
    for code in ambiguous:
        del nui_by_code[code]
    return nui_by_code, len(ambiguous)


def parse(path: str | pathlib.Path) -> ParsedMedrt:
    """Read one MED-RT XML release into the records slice 2a ingests.

    CODE VS NUI -- why there are two lookups below and not one set. An association
    references its endpoints by CODE (`from_code`/`to_code`), while a class's
    identity -- and therefore its class_uuid -- is its NUI. Those two strings are
    equal for every concept in the 2026.07.06 release, so matching endpoint codes
    against a set of NUIs happens to work today. It would stop working silently the
    moment upstream let them diverge: every edge would simply fail to match and the
    DAG would come back empty, with no error and no count. Resolving codes through
    an explicit code -> NUI map costs nothing and removes that failure mode.
    """
    root = ElementTree.parse(path).getroot()
    classes, inactive, unidentified, skipped_types = _parse_concepts(root)
    nui_by_code, ambiguous = _resolve_codes(classes)
    # Scoped to resolvable codes, so an ambiguous EPC code cannot reach the
    # nui_by_code lookup below (which no longer holds it).
    epc_codes = {c.code for c in classes
                 if c.concept_type == EPC_CONCEPT_TYPE and c.code in nui_by_code}

    parents: list[ParentEdge] = []
    memberships: list[MembershipAssertion] = []
    contraindications: list[ContraindicationAssertion] = []
    mesh_contraindications: list[MeshObjectAssertion] = []
    non_mesh_ci_objects = 0
    mesh_indications: list[MeshObjectAssertion] = []
    class_subject_indications = 0
    skipped_predicates: set[str] = set()
    for assoc in root.findall("association"):
        name = _text(assoc, "name")
        from_ns, from_code = _text(assoc, "from_namespace"), _text(assoc, "from_code")
        to_ns, to_code = _text(assoc, "to_namespace"), _text(assoc, "to_code")

        if name == PARENT_RELATIONSHIP:
            if from_ns == MEDRT_NAMESPACE and to_ns == MEDRT_NAMESPACE:
                # Class hierarchy. Requiring BOTH endpoints to be ingested classes
                # is what drops hierarchy mapped out into SNOMED/MeSH and edges
                # hanging off HC navigation bins.
                if from_code in nui_by_code and to_code in nui_by_code:
                    # 'Parent Of' is parent -> child, so `from` is the parent.
                    parents.append(ParentEdge(child_nui=nui_by_code[to_code],
                                              parent_nui=nui_by_code[from_code]))
            elif (from_ns == MEDRT_NAMESPACE and to_ns == RXNORM_NAMESPACE
                    and from_code in epc_codes):
                # An EPC class sitting above a drug: this IS the drug's EPC
                # membership. Only EPC parents count -- the same shape with an HC
                # parent is just the alphabetical bin the drug is filed under.
                memberships.append(MembershipAssertion(
                    rxcui=to_code, class_nui=nui_by_code[from_code],
                    relationship=EPC_RELATIONSHIP))
        elif name in MEMBERSHIP_RELATIONSHIPS:
            # Axis membership always runs ingredient (RxNorm) -> class (MED-RT).
            # The MED-RT -> MED-RT variant is a class describing itself, not
            # membership, so it is skipped by the namespace test.
            if (from_ns == RXNORM_NAMESPACE and to_ns == MEDRT_NAMESPACE
                    and to_code in nui_by_code):
                memberships.append(MembershipAssertion(
                    rxcui=from_code, class_nui=nui_by_code[to_code], relationship=name))
        elif name in CI_RELATIONSHIPS:
            # A drug-drug contraindication by mechanism/effect (slice 5a). Runs
            # ingredient (RxNorm) -> class (MED-RT), and endpoint-scoped to an
            # ingested class exactly as membership is: an object not in nui_by_code
            # is a CI_MoA/CI_PE whose class we did not ingest, so it is dropped.
            if (from_ns == RXNORM_NAMESPACE and to_ns == MEDRT_NAMESPACE
                    and to_code in nui_by_code):
                contraindications.append(ContraindicationAssertion(
                    rxcui=from_code, class_nui=nui_by_code[to_code], relationship=name))
        elif name in MESH_CI_RELATIONSHIPS:
            # A MeSH-keyed contraindication (slice 5b). Endpoint-scoped exactly as
            # the branches above are, but to the MeSH namespace instead of MED-RT:
            # the object is a MeSH ConceptUI, resolved later against the MeSH release
            # by ingest/mesh_concepts.py, so there is nothing to look up here. Any
            # OTHER endpoint pair is refused and COUNTED -- not just a non-MeSH
            # object, but also a subject outside RxNorm, since neither can be
            # resolved. In the real release two such rows point at a MED-RT EXT
            # concept, which drugref does not ingest, so silently dropping them would
            # hide a real gap.
            if from_ns == RXNORM_NAMESPACE and to_ns == MESH_NAMESPACE:
                mesh_contraindications.append(MeshObjectAssertion(
                    rxcui=from_code, mesh_code=to_code, relationship=name))
            else:
                non_mesh_ci_objects += 1
        elif name in MESH_INDICATION_RELATIONSHIPS:
            # Scoped exactly as the MeSH-keyed contraindications are, and for the same
            # reason: the object is a ConceptUI resolved later against the MeSH release
            # (ingest/mesh_concepts.py), so there is nothing to look up here, and any
            # OTHER endpoint pair is refused rather than assumed to be MeSH.
            if from_ns == RXNORM_NAMESPACE and to_ns == MESH_NAMESPACE:
                mesh_indications.append(MeshObjectAssertion(
                    rxcui=from_code, mesh_code=to_code, relationship=name))
            else:
                class_subject_indications += 1
        else:
            # Everything else (site_of_metabolism, has_SC, Synonym Of, ...) is
            # either curated-overlay data for a later slice, or points at
            # a namespace we may not read. Recorded by NAME so that an upstream
            # rename of a predicate we DO ingest -- which otherwise looks exactly
            # like one of these deliberate skips -- shows up as a new entry rather
            # than as edges quietly going missing.
            skipped_predicates.add(name)
    return ParsedMedrt(classes=classes, parents=parents, memberships=memberships,
                       contraindications=contraindications,
                       mesh_contraindications=mesh_contraindications,
                       non_mesh_ci_objects=non_mesh_ci_objects,
                       mesh_indications=mesh_indications,
                       class_subject_indications=class_subject_indications,
                       inactive_concepts=inactive, unidentified_concepts=unidentified,
                       ambiguous_codes=ambiguous,
                       skipped_concept_types=tuple(sorted(skipped_types)),
                       skipped_predicates=tuple(sorted(skipped_predicates)))
