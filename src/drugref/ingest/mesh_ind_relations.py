"""The INDICATION relation pass: MED-RT's MeSH-keyed indication assertions -> rows.

The second relation family of the run mesh_rel_run.py orchestrates, and the sibling of
mesh_ci_relations.py in every structural respect: everything SHARED -- the ingest_run,
the condition registry and its DAG, the moiety index -- belongs to that orchestrator and
is handed in here, because `condition` and `condition_parent` are rebuilt per
`ingest_run.source` and a second orchestrator would clear this one's edges (spec 6.1:
#39 one layer deeper, and unfixable by a discriminator, since a (child, parent) edge is
derived by BOTH closures). What lives here is only what is true of may_treat,
may_prevent, may_diagnose and induces in particular.

So this module writes rows and returns a tally: it opens no transaction, commits
nothing, registers no condition, reads no index it was not given, and does not name the
authority it writes under -- `source` is an ARGUMENT, because the rows belong to the run
that called this pass (#43's shape, and the reason exactly one SOURCE = 'MED-RT' exists
for this run, in the orchestrator).

SIMPLER THAN ITS SIBLING, AND THE ASYMMETRY IS THE MEANING RATHER THAN AN OMISSION.
CI_ChemClass's object is usually a DRUG, so that pass has to bridge its object into the
moiety registry and decide what a failure to bridge means. An indication's object is
ALWAYS a patient state: MED-RT never says "this drug may_treat that drug". So there is
no object-side bridge here, no withheld/unregistered split, no self-pair to refuse --
and this pass therefore takes only the RxCUI index rather than its sibling's three,
because the UNII and CAS indexes would have no reader.

WHERE THE ROW GOES IS DECIDED HERE, ONCE. may_treat / may_prevent / may_diagnose are
therapeutic and land in `moiety_condition_indication`; `induces` says the drug CAUSES
the state and lands in `moiety_induced_condition`. db/019 made that a table split rather
than a WHERE clause because the unfiltered read of a table must be one true sentence: a
consumer who forgets a `relationship` filter on a shared table would read "carbamazepine
treats agranulocytosis" off an induces row (spec 5.1).
"""
from dataclasses import dataclass, field

from drugref import indications
from drugref.ingest import medrt

# MeSH's top-level tree letter for CHEMICALS AND DRUGS. Named rather than spelled inline
# because it is a claim about MeSH's tree, not a string: db/013 stores tree_numbers
# precisely so the leading letter distinguishes a disease (C) from a physiological state
# (G) from a procedure (E) -- and, here, from a substance.
CHEMICAL_TREE = "D"


@dataclass
class IndicationRelations:
    """The tally of one pass over the indication assertions (see write_indications).

    A mutable scratch record rather than a handful of loose locals, for the reason
    CiRelations gives: the pass produces two row counts and two worklists, and returning
    them as one named thing is what keeps the orchestrator readable and stops a caller
    pairing them up in the wrong order.

    The two row counts are separate because the two TABLES are (db/019 section 5.1), not
    because the predicates are interesting apart: adding them would report a number that
    matches neither table.
    """
    indication_rows: int = 0
    induced_rows: int = 0
    # Subjects no moiety carries. A SET, so a subject stating many indications is one
    # worklist entry -- the grain gap_unmatched_ingredient and the question register
    # use.
    unmatched_rxcuis: set[str] = field(default_factory=set)
    # Assertions whose OBJECT is a MeSH chemical rather than a patient state. Counted
    # per ASSERTION, not per record, because the operator's question is "how much of
    # this release is a category error" and the release states 17 of them over 13
    # records -- reporting 13 would understate what it costs to be wrong about them.
    chemical_object_assertions: int = 0
    # Assertions MED-RT keyed to a SUBORDINATE MeSH concept, so any row that follows
    # sits on a BROADER record than the release named. Per ASSERTION for the same reason
    # as the line above, and PRE-GATE like it: the operator's question is how much of
    # the RELEASE is widened, not how many concepts do the widening nor how many rows
    # resulted. 422 of 18,314 (2.30%) on the 2026.07.06 release. See write_indications
    # for why this is the UNSAFE direction for an indication and the safe one for a
    # contraindication (#52).
    broadened_object_assertions: int = 0


def write_indications(conn, assertions, records, uuid_by_code, rxcui_index,
                      source: str, run_id: int) -> IndicationRelations:
    """Write both indication relations, tallying every assertion that is not a row.

    ONE PASS, and every exit from it is counted somewhere: an assertion either becomes
    a row, or lands in `unmatched_rxcuis` (no moiety carries the subject), or belongs
    to an object code the caller already counted as unresolved. Nothing falls off the
    end (spec 7) -- and `chemical_object_assertions` and `broadened_object_assertions`
    differ in kind from every other number here: they report not a LOSS but what the
    release SAYS about assertions this pass does not refuse.

    THE OBJECT QUESTIONS ARE ASKED BEFORE THE SUBJECT TEST, exactly as in
    write_contraindications, and for the same reason: whether an object is a chemical
    rather than a patient state, and whether it was reached through a subordinate
    concept, are facts about the OBJECT, and neither depends on whether this particular
    rule's subject happened to resolve. Testing the subject first would make both
    reported figures a function of the moiety gate -- the mistake that cost the CI half
    35 assertions over 4 objects before it was found.

    SO BOTH ARE RELEASE-GRAIN, PRE-GATE COUNTS, AND NEITHER COUNTS STORED ROWS. An
    assertion whose subject no moiety carries increments them and then leaves through
    `unmatched_rxcuis` having produced nothing; an assertion whose subject TWO moieties
    carry increments them once and produces two rows. Both directions are deliberate --
    the question these numbers answer is "how much of the release is like this", which
    is a fact about MED-RT and MeSH rather than about drugref's registry coverage. So
    do NOT restate either as "n rows are stored like this": the post-gate row figure
    has never been measured against a real release, and #52 is what would make the row
    itself detectable. Pinned by
    test_mesh_rel_run_ind.test_the_widening_counters_are_release_grain_not_row_grain,
    which exists because this docstring is the only thing that makes the numbers
    legible.

    BROADENED OBJECTS: THE ONE PLACE is_preferred_concept IS READ IN PRODUCTION, and its
    own docstring promised this reader. MED-RT names a MeSH **ConceptUI**; drugref keys
    conditions on the **record** that owns it (mesh_concepts.resolve_concepts explains
    why -- many concepts resolve to one record, and keying on the concept would split
    one condition into rows no rebuild could merge). When the named concept is the
    record's
    preferred one, nothing is lost. When it is SUBORDINATE, the concept may be NARROWER
    than the record, and the assertion is stored against something BROADER than the
    release said.

    MEASURED on the 2026.07.06 release: 422 of 18,314 assertions (2.30%) -- may_treat
    340, may_prevent 80, induces 2 -- arrive through 90 non-preferred ConceptUIs and
    collapse onto 85 broader records, over 102 distinct (predicate, concept, record)
    triples.

    THE DIRECTION OF THE HARM FLIPS HERE, which is why this counter is new in 5b.2 even
    though the grain is 5b's. The same collapse hits the contraindication half harder
    (550 of the 13,458 assertions whose object RESOLVED, via 81 concepts -- the release
    carries 13,463, and the 5-assertion difference is the two withdrawn object codes,
    which have no concept to test; #53) and there it is SAFE: broadening a
    contraindication widens recall. Broadening an INDICATION offers a drug for a
    condition the release never named it for, and the read path then walks DOWN from
    that broader record to every patient coded below it. Worked case: MED-RT asserts
    may_treat against M0335931 'Seizures, Focal' for eslicarbazepine (RxCUIs 1482501,
    1482502); drugref stores it on D012640 'Seizures' with is_direct = true.
    Eslicarbazepine is a sodium-channel blocker licensed for focal-onset seizures that,
    like carbamazepine and phenytoin, can AGGRAVATE generalised myoclonic and absence
    seizures. The is_direct = false "weaker claim" label cannot help: the claim is not
    weaker, it is wrong.

    NEVER WITHHELD, for the reason the D-tree objects are not: most of the 102 triples
    are benign synonymy ('Breast Cancer' -> Breast Neoplasms), so refusing all 422
    would lose far more than it saves, and drugref has nothing on the row that tells a
    consumer which is which. Making the row itself detectable -- by storing the
    ConceptUI MED-RT
    named -- is #52, slice 5c's work. Until then this number is the only evidence, so it
    must be reported rather than derivable.

    D-TREE OBJECTS ARE INGESTED, AND THAT IS A RECORDED DECISION (spec 11 tension C).
    17 of the 2026.07.06 release's 18,144 therapeutic assertions (0.09%) -- 14 may_treat
    and 3 may_prevent, over 13 records -- name a MeSH CHEMICAL. The percentage is of ALL
    therapeutic assertions, not of may_treat: 17/15,319 would be 0.11%, and the 17 are
    not all may_treat anyway. Objects: LDL Cholesterol (2), Antioxidants (2),
    Prostate-Specific Antigen (2), Analgesics, Antiemetics, Antiparkinson Agents,
    Deodorants,
    Neuroprotective Agents, Radioactive Tracers, von Willebrand Factor (2), ... Some are
    defensible treatment targets ("a statin may_treat LDL cholesterol") and some are
    upstream quirks ("may_treat Analgesics"), and MED-RT does not distinguish them. They
    are not refused -- `condition.tree_numbers` lets a consumer scope on the leading
    letter, and 5b already registered 18 such CI_with objects -- but COUNTED, because
    withholding them behind a new worklist kind would cost more than it buys while
    leaving an operator to DISCOVER the split rather than be told it.

    `source` and `run_id` are the run's provenance, taken as arguments and forwarded
    together to every writer call below, in the order indications.add_* takes them. The
    source is not a constant here because this pass does not decide it: the orchestrator
    opened the ingest_run, and these rows belong to it (see the module docstring).
    """
    out = IndicationRelations()
    for a in assertions:
        record = records.get(a.mesh_code)
        if record is None:
            # The CODE is counted by the caller (unresolved_object_codes); THIS
            # ASSERTION is not, and the grain difference is stated rather than glossed
            # -- one dead code can carry many assertions. Nothing reports that assertion
            # count today, which is honest only because the figure is 0 on the
            # 2026.07.06 release (1,528 of 1,528 indication object codes resolve).
            # Should a future release withdraw a code, the loss is visible as a code,
            # not as its rules.
            continue
        object_uuid = uuid_by_code.get(record.record_ui)
        if object_uuid is None:
            # Defensive rather than live: the orchestrator's closure covers every
            # indication object code, so a resolved record is always registered, and on
            # the real 2026 releases this branch is never taken.
            #
            # BE CLEAR ABOUT WHAT IT COSTS IF IT EVER IS. This guard makes such a loss
            # SILENT -- the assertion is skipped, no tally counts it, and the ingest
            # reports success -- which is the opposite of letting the foreign key refuse
            # the row. It is kept because a crash is worse than a skip for a condition
            # the CALLER is responsible for establishing, but that makes the row counts
            # the only evidence: a change that narrows the closure must be checked
            # against them, never trusted to fail loudly here.
            continue
        # BOTH COUNTERS ARE RELEASE-GRAIN AND SIT ABOVE THE SUBJECT GATE ON PURPOSE, so
        # neither is a count of rows -- see the docstring. Moving either below the
        # `continue` would silently turn it into a post-gate figure that every comment
        # about it, here and in db/019, would then be wrong about.
        if any(t.startswith(CHEMICAL_TREE) for t in record.tree_numbers):
            out.chemical_object_assertions += 1     # not refused -- see above
        if not record.is_preferred_concept:
            out.broadened_object_assertions += 1    # not refused -- see above
        subjects = rxcui_index.get(a.rxcui, ())
        if not subjects:
            out.unmatched_rxcuis.add(a.rxcui)       # counted, never dropped
            continue
        for subject in subjects:
            # THE ONE BRANCH THAT DECIDES WHICH TABLE. `induces` is neither an
            # indication nor a contraindication -- MED-RT does not say whether the
            # caused state is the therapeutic point or the adverse effect -- so it gets
            # its own table and its own counter, and the writer supplies the predicate
            # so this call cannot file a may_treat row there by passing a string.
            if a.relationship == medrt.INDUCES_RELATIONSHIP:
                if indications.add_induced_condition(conn, subject, object_uuid,
                                                     source, run_id):
                    out.induced_rows += 1
            elif indications.add_condition_indication(conn, subject, object_uuid,
                                                      a.relationship, source, run_id):
                out.indication_rows += 1
    return out
