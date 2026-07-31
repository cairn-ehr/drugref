"""What one MeSH-keyed relation run DID -- the shapes a caller or a test asserts on.

Pure data: four frozen dataclasses and not one line of behaviour. They are separated
from the orchestrator that fills them (ingest/mesh_rel_run.py) for CLAUDE.md rule 4 --
that module had reached exactly 500 lines against the ~500-line ceiling, so the next
line added to it would have broken the rule silently, with nobody deciding. Splitting
on the data/behaviour seam is the split that costs nothing to read: the orchestrator
keeps every step it performs and the tallies keep every explanation of what a number
MEANS, which is the part a reader consults without needing the run's control flow.

RE-EXPORTED BY mesh_rel_run, so `mesh_rel_run.MeshRelSummary` keeps working. This was a
mechanical move with ZERO behaviour change -- no number moved, and the docstrings came
across verbatim -- so an existing import must not have to know it happened.

THESE DOCSTRINGS CARRY MEASURED FIGURES from the real 2026 releases (UNII 26Feb2026 →
MED-RT 2026.07.06 → MeSH desc2026/supp2026). A figure here is a claim about a release,
not about the code: when the next release lands, expect them to move, and re-measure
rather than assume.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryTally:
    """What one run did to the CONDITION REGISTRY, stated ONCE for the whole run.

    Conditions ACCUMULATE while edges and contraindications are REBUILT, so the two
    condition numbers are reported separately rather than as one ambiguous count.

    `conditions_registered` counts CONDITION ROWS this run put in the registry -- the
    referenced objects plus their descendant closure -- and is deliberately NOT called
    "in_release". Measured on the real 2026 releases: desc2026 holds 31,110 descriptor
    records, of which this slice registers 5,963 conditions (5,929 descriptors plus 34
    supplementary records). Naming it after the release would invite a reader to check
    it against MeSH's own record count and conclude the ingest had lost 25,000 records.

    Do not confuse it with the closure's DESCRIPTOR count (5,929) quoted in
    mesh_rel_run._condition_closure and mesh_concepts.descriptors_under: SCRs bear no
    tree numbers, so they never enter the closure and appear only as themselves. Both
    figures are right about different things, which is why each says which it is
    counting.

    ONE TALLY FOR THE WHOLE RUN, not one per relation family, because it IS one fact
    about one closure over one registry (spec 6.1). Reporting `conditions_registered`
    under each family would be one quantity stated twice, and db/018's round is the
    standing evidence for what happens next: only one of the two copies learns the
    next correction.
    """
    conditions_registered: int
    conditions_added: int
    condition_parent_edges: int
    # How many registered conditions carry each MeSH SCRClass, sorted, descriptors
    # (which carry none) excluded. THE DRIFT DETECTOR FOR A COLUMN WITH NO CHECK:
    # db/019 stores scr_class as published because supp2026 already publishes six
    # values against a documented four, so a constraint would abort an ingest the first
    # time NLM adds a seventh. A reported count catches the same drift without that risk
    # (the posture skipped_predicates takes), and is the column's ONLY reader outside
    # gap_condition_without_indication. 29 x '3' and 5 x '1' on the real 2026 releases.
    scr_class_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CiTally:
    """What the CONTRAINDICATION pass produced -- rows written, and every loss.

    `condition_rows` and `pair_rows` are db/014's two relations (drug->condition and
    drug->drug). They are named for the row SHAPE rather than repeating
    "contraindication", which the field holding this tally already says.

    The six worklist numbers are reported, never swallowed:
      * unmatched_subject_rxcuis      -- the rule's subject is carried by no moiety
      * withheld_class_objects        -- CI_ChemClass objects that name a CLASS
      * unregistered_object_substances -- CI_ChemClass objects that name a SUBSTANCE
                                         drugref's registry does not carry
      * self_paired_assertions        -- CI_ChemClass rules whose two ends collapse
                                         to one moiety, which db/014 forbids storing
      * unresolved_object_codes       -- M-codes MeSH no longer defines
      * non_mesh_objects              -- objects outside the MeSH namespace (MED-RT EXT)

    The last two are counted by the ORCHESTRATOR rather than by the pass: an M-code
    that resolves to no record never reaches the pass, and a non-MeSH object never
    reaches this ingest at all (the parser refuses it). They are reported here anyway,
    because a reader asking "what did the contraindication half lose?" must find every
    answer in one place.

    THE TWO OBJECT NUMBERS ARE NOT ONE NUMBER, and separating them is the point of
    db/014's object_kind. Both are CI_ChemClass objects that failed to bridge, but the
    reasons differ and so do the remedies: a CLASS (no registry key on the MeSH
    record at all) is withheld pending a curator ruling on structural-tree expansion,
    while an UNREGISTERED SUBSTANCE (a real UNII or CAS, no moiety) is a registry
    coverage gap. Reporting them as one figure is what let a leaf drug descriptor be
    asked whether it should expand over the drugs beneath it.
    """
    condition_rows: int
    pair_rows: int
    unmatched_subject_rxcuis: int
    withheld_class_objects: int
    unregistered_object_substances: int
    self_paired_assertions: int
    unresolved_object_codes: int
    non_mesh_objects: int


@dataclass(frozen=True)
class IndicationTally:
    """What the INDICATION pass produced -- rows written, and every loss.

    `indication_rows` and `induced_rows` are db/019's two relations, named for the
    TABLE each lands in rather than for the predicates, because that split is the
    slice's one structural decision: may_treat / may_prevent / may_diagnose say what a
    drug is FOR, `induces` says what it CAUSES, and a shared table would let a consumer
    who forgets a `relationship` filter read the second as the first (spec 5.1).

    The four worklist numbers are reported, never swallowed (spec 7):
      * unmatched_subject_rxcuis    -- the rule's subject is carried by no moiety
      * class_subject_assertions    -- the subject is a pharmacologic CLASS, not an
                                       ingredient, so there is no RxCUI to bridge: 193
                                       in the real release (may_treat 100, may_prevent
                                       90, may_diagnose 3), filed against #8
      * unresolved_object_codes     -- M-codes MeSH no longer defines. 0 in the real
                                       release, where all 1,528 resolve -- kept because
                                       that is a fact about a release, not a guarantee
      * chemical_object_assertions  -- the object is a MeSH D-tree CHEMICAL rather than
                                       a patient state. 17 assertions over 13 records,
                                       0.09% of may_treat. INGESTED and counted, unlike
                                       every other number here: see write_indications

    THE LAST ONE IS NOT A LOSS AT ALL, which is why it is described rather than merely
    listed: those assertions ARE ingested, and the number exists so an operator learns
    the release's category errors from a figure rather than by meeting them in a query.

    Two of these are counted by the PARSER and the ORCHESTRATOR rather than by the pass,
    for the reason CiTally gives: an assertion the parser refused never reaches the pass,
    and an M-code that resolves to no record never reaches it either. They are reported
    here anyway, because a reader asking "what did the indication half lose?" must find
    every answer in one place.
    """
    indication_rows: int
    induced_rows: int
    unmatched_subject_rxcuis: int
    class_subject_assertions: int
    unresolved_object_codes: int
    chemical_object_assertions: int


@dataclass(frozen=True)
class MeshRelSummary:
    """What one MeSH-keyed relation run did -- for a caller or a test to assert on.

    NESTED, not flat, and the nesting carries the argument: the registry is ONE thing
    this run built and every relation family references it, so `registry` is stated
    once and each family reports its own rows and losses under its own name. A flat
    summary would have to either repeat the registry figures per family (one quantity
    stated twice) or leave the reader guessing which family a bare
    `conditions_registered` belonged to.
    """
    registry: RegistryTally
    contraindications: CiTally
    indications: IndicationTally
