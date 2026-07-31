"""The CONTRAINDICATION relation pass: MED-RT's MeSH-keyed CI assertions -> rows.

One relation family of the run mesh_rel_run.py orchestrates. Everything SHARED --
the ingest_run, the condition registry and its DAG, the moiety indexes -- belongs to
that orchestrator and is handed in here, because `condition` and `condition_parent`
are rebuilt per `ingest_run.source` and a second orchestrator would clear this one's
edges (spec 6.1: #39 one layer deeper, and unfixable by a discriminator, since a
(child, parent) edge is derived by BOTH closures). What lives here is only what is
true of CI_with and CI_ChemClass in particular.

So this module writes rows and returns a tally: it opens no transaction, commits
nothing, registers no condition, and reads no index it was not given. That is what
lets the orchestrator build the registry once and hand the same one to every pass.
"""
import uuid
from collections import Counter
from dataclasses import dataclass, field

from drugref import interactions
from drugref.ingest import mesh_concepts

# The authority whose assertions these rows carry, and therefore the `source` stamped
# on every one of them. Declared here rather than imported because the import runs the
# other way -- the orchestrator imports this module -- exactly as medrt_run.py and this
# module's predecessor each declared their own. It MUST equal mesh_rel_run.SOURCE, and
# a divergence is unstorable rather than merely wrong: db/014 CHECK-constrains every
# source column written below (db/012 finding 3 -- an unconstrained source once let
# 'MEDRT' insert cleanly and match nothing, ever).
SOURCE = "MED-RT"

# The two MeSH-keyed CI predicates, named together because the pair is the point: they
# share an endpoint shape (RxNorm -> MeSH) and differ in what the object IS, which is
# why db/014 gives them two relations rather than one table and a WHERE clause.
CONDITION_PREDICATE = "CI_with"        # object is a patient state -> a condition row
PAIR_PREDICATE = "CI_ChemClass"        # object is usually a drug   -> a drug-drug pair

# Why a CI_ChemClass object was not ingested (db/014's object_kind vocabulary). Kept
# in lockstep with that migration's CHECK, and named here rather than spelled inline
# so the two writers below cannot disagree about the spelling.
CHEMICAL_CLASS = "CHEMICAL_CLASS"                  # the record carries no registry key
UNREGISTERED_SUBSTANCE = "UNREGISTERED_SUBSTANCE"  # a real UNII/CAS, but no moiety


@dataclass
class CiRelations:
    """The tally of one pass over the assertions (see write_contraindications).

    A mutable scratch record rather than a handful of loose locals: the pass produces
    two row counts and four worklists, and returning them as one named thing is what
    keeps the orchestrator readable and stops a caller pairing them up in the wrong
    order.
    """
    condition_rows: int = 0
    pair_rows: int = 0
    unmatched_rxcuis: set[str] = field(default_factory=set)
    # object record_ui -> how many assertions ride on it. Counted per OBJECT because
    # the curator's decision is per object (db/016), and split across TWO counters
    # because the decision itself differs (db/014): `withheld` holds records naming a
    # CLASS, `unregistered` holds records naming a SUBSTANCE drugref does not carry.
    #
    # A record_ui can never land in both. The kind is a pure function of the MeSH
    # record -- does it carry a registry key? -- and every concept resolving to one
    # record resolves to the same record, so the two counters partition the objects
    # rather than overlapping. That matters downstream: ingest_unresolved_ci_object's
    # primary key does NOT include object_kind, so one code emitting two kinds would
    # lose a row to ON CONFLICT DO NOTHING.
    withheld: Counter[str] = field(default_factory=Counter)
    unregistered: Counter[str] = field(default_factory=Counter)
    object_names: dict[str, str] = field(default_factory=dict)
    # CI_ChemClass rules whose subject and object are the SAME moiety. Counted, not
    # merely skipped -- see write_contraindications.
    self_pairs: int = 0


def _resolve_object_moiety(record: mesh_concepts.MeshRecord, unii_index,
                           cas_index) -> uuid.UUID | None:
    """Resolve a MeSH record to a moiety: UNII-primary, CAS-fallback.

    The same rule mesh_run._resolve_moieties applies, reduced to a single answer
    because a contraindication names ONE partner drug. UNII is drugref's own identity
    key so it wins outright; CAS is tried only when no UNII resolved at all. Keys are
    set-valued (a record may carry several), and sorted iteration keeps the ingest
    reproducible.

    Returning None is not a failure: it is the CLASS arm's signal. "Alkalies" and
    "Organic Chemicals" carry no registry number in MeSH at all, and that absence is
    exactly what tells this ingest the object is a class rather than a drug.
    """
    for value in sorted(record.unii):
        for moiety_uuid in unii_index.get(value, ()):
            return moiety_uuid
    for value in sorted(record.cas):
        for moiety_uuid in cas_index.get(value, ()):
            return moiety_uuid
    return None


def write_contraindications(conn, assertions, records, uuid_by_code, indexes,
                            run_id: int) -> CiRelations:
    """Write both contraindication relations, tallying every assertion that is not.

    ONE PASS, and every exit from it is counted somewhere: an assertion either
    becomes a row, or lands in `unmatched_rxcuis` (no moiety carries the subject), or
    in `withheld` (CI_ChemClass on a chemical class), or in `unregistered`
    (CI_ChemClass on a substance drugref does not carry), or in `self_pairs` (both
    ends are one moiety), or was already counted as an unresolved object code by the
    caller. Nothing falls off the end -- and `self_pairs` is on that list because it
    once was not: the guard existed, silently, and a skip nobody counts is the exact
    shape of the drops spec 7 forbids.

    THE OBJECT QUESTION IS ASKED BEFORE THE SUBJECT TEST, and the order is
    load-bearing. Withholding is a curator decision about the OBJECT -- "should a
    contraindication naming this class expand over MeSH's structural tree?" -- and
    that question does not depend on whether this particular rule's subject happened
    to resolve. Testing the subject first silently lost every class object ALL of
    whose subjects are unregistered: measured against the real 2026.07.06 release,
    370 assertions over 99 objects instead of the 405 over 103 the release contains,
    with D000963, D003911, D050256 and D056747 dropped outright.

    The two tallies are therefore separate axes, not a partition: one assertion whose
    object is a class AND whose subject is unmatched is counted in BOTH, because both
    statements about it are true and each answers a different person's question.

    WHICH KIND OF UNRESOLVED OBJECT, decided from the RECORD and never from the
    failure to resolve (db/014). `_resolve_object_moiety` returning None is the
    disjunction of two different facts, and collapsing them asked a curator whether
    Pimozide -- a leaf drug descriptor -- should expand over the drugs beneath it:
      * the record carries NO registry key  -> it names a CLASS (Alkalies and
        Organic Chemicals carry only MeSH's '0' placeholder, which
        mesh.registry_keys already discards). Withheld pending a curator ruling.
      * the record carries a UNII or CAS    -> it names a SUBSTANCE drugref's gated
        registry does not hold. A coverage gap, not a policy question.
    Both are recorded by identity; only the question differs.

    ON THE OBJECT COUNT, for whoever checks this against spec 7: the worklist is
    keyed on the MeSH RECORD ui, so the release's 108 withheld ConceptUIs collapse
    into 103 curator questions. Five records are named by two concepts each (D010406
    by both "Penicillins" and "Penicillin", plus D001569, D020902, D006993, D000701),
    and one record is one decision. 103 is correct; do not "fix" it by keying the
    worklist on the concept, which is the split mesh_concepts.py exists to prevent.
    That 103 is the WORKLIST total and is unchanged by the object_kind split -- both
    kinds stay on it. What the split changed is how those 103 divide between the two
    counters, a figure the next run against a real release establishes.
    """
    rxcui_index, unii_index, cas_index = indexes
    out = CiRelations()
    for a in assertions:
        record = records.get(a.mesh_code)
        if record is None:
            continue                                # already counted by the caller

        object_moiety = None
        if a.relationship == PAIR_PREDICATE:
            object_moiety = _resolve_object_moiety(record, unii_index, cas_index)
            if object_moiety is None:
                # Not ingested either way -- but the RECORD says which of the two
                # reasons applies, and therefore which question a curator gets
                # (db/014). Never inferred from the resolution failure alone.
                if record.unii or record.cas:
                    out.unregistered[record.record_ui] += 1
                else:
                    out.withheld[record.record_ui] += 1
                out.object_names[record.record_ui] = record.name

        subjects = rxcui_index.get(a.rxcui, ())
        if not subjects:
            out.unmatched_rxcuis.add(a.rxcui)       # counted, never dropped
            continue
        if a.relationship == PAIR_PREDICATE:
            if object_moiety is None:
                continue                            # withheld, recorded above
            for subject in subjects:
                if subject == object_moiety:
                    # db/014 forbids a self-pair, and rightly: MED-RT states this
                    # when a salt and its parent moiety collapse to one identity.
                    # COUNTED, because storing it is impossible but losing it
                    # silently is a choice -- and without the count, removing this
                    # guard would surface as an ingest-aborting CHECK violation
                    # rather than as a number that moved.
                    out.self_pairs += 1
                    continue
                if interactions.add_moiety_contraindication(
                        conn, subject, object_moiety, a.relationship, SOURCE, run_id):
                    out.pair_rows += 1
        else:                                        # CI_with
            object_uuid = uuid_by_code.get(record.record_ui)
            if object_uuid is None:
                continue                            # not a registered condition
            for subject in subjects:
                if interactions.add_condition_contraindication(
                        conn, subject, object_uuid, a.relationship, SOURCE, run_id):
                    out.condition_rows += 1
    return out
