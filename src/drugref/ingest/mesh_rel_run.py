"""Orchestrate MED-RT's MeSH-keyed relations over ONE condition registry.

Reads TWO authorities and joins them: MED-RT states the relation, MeSH defines its
object. Mirrors medrt_run/mesh_run (open an ingest_run for provenance, do the work,
stamp finished_at, commit) with two genuinely new pieces:

  1. M-CODE RESOLUTION. MED-RT's MeSH endpoint is a ConceptUI, so every object is
     resolved against the MeSH release (ingest/mesh_concepts.py). This run resolves
     `ci_wanted | ind_wanted` in ONE pass: 2,196 of 2,198 concepts resolve (99.91%),
     the two unresolved being CI objects MeSH withdrew (the indication half is
     1,528/1,528). The rest are counted, and per half -- see step 1 of _ingest for why
     a code both halves name costs each of them an assertion. Do not reconcile 99.91%
     with the 99.88% in mesh_concepts.py: that figure is 2,471/2,474 over every MeSH
     endpoint in the release, a wider denominator, and both are right about different
     populations. Nor with slice 5b's 1,051/1,053 (99.81%) -- that was this run when
     it carried only the contraindication half.
  2. THE TWO ARMS OF CI_ChemClass. Its object is usually a SPECIFIC DRUG (Pimozide,
     Cisapride, Ritonavir), so it is first resolved against the moiety registry via
     slice 2b's two-key UNII->CAS bridge. When it resolves, the assertion is an exact
     drug-drug pair. When it does not, the assertion is not ingested and the object
     goes on a worklist -- but WHICH worklist question is decided by the MeSH record,
     not by the resolution failure (db/014): a record with no registry key names a
     CLASS and is withheld pending a curator ruling, because expanding it over MeSH's
     structural tree would make a rule on Sulfonamides reach bendroflumethiazide (see
     db/014 and db/016); a record carrying a real UNII or CAS names a SUBSTANCE
     drugref does not register, which is a coverage gap and not a policy question.
     The pass that does this lives in ingest/mesh_ci_relations.py.

ONE ORCHESTRATOR, ONE REGISTRY, AND THAT IS STRUCTURAL RATHER THAN TIDY (spec 6.1).
`condition` and `condition_parent` are rebuilt per `ingest_run.source`, and every
MeSH-keyed relation family MED-RT states runs under source 'MED-RT'. A second
orchestrator would therefore clear this one's DAG edges -- #39 one layer deeper, and
unfixable the way #39 was fixed: a (child, parent) edge is derived by BOTH closures,
so no `reason` discriminator can split it. So this module owns the shared machinery
(the run, the registry, the closure, the DAG, the moiety indexes) and each relation
family is a PASS over assertions in its own module, handed what it needs. Today that
is TWO families -- the contraindication predicates (ingest/mesh_ci_relations.py) and
the indication predicates (ingest/mesh_ind_relations.py) -- over ONE closure taken
across every MeSH-keyed object at once.

Order matters:
  1. parse MED-RT (pure) -> the set of MeSH codes to resolve;
  2. resolve those codes, then walk their tree positions for the DESCENDANT CLOSURE,
     without which a rule on Epilepsy has nothing to expand into;
  3. upsert conditions, then clear this source's edges, contraindications and
     indications, then write the DAG and the relation passes;
  4. measure the indication/contraindication OVERLAP -- only possible once both passes
     have written;
  5. rebuild the open-question register LAST, before the commit.

WORKLIST NUMBERS, NOT SILENT DROPS -- nine distinct losses, each counted separately
so they stay legible (spec 7). Four of them are also PERSISTED by identity, because
a count cannot be worked: the withheld class objects and the unregistered-substance
objects (a curator works each by name, and is asked a DIFFERENT question about each)
and the unmatched subjects of EACH half. The last two share a table with medrt_run's
own unmatched list, so every writer scopes its clear on `reason` as well as source
(#39, db/018) -- see step 6 of _ingest for the measurement behind that. This run owns
two of those buckets, which does not weaken db/018's invariant: it is still exactly
one writer per (source, reason), and two writers sharing one bucket is what #39 was.

AND THREE NUMBERS THAT ARE NOT LOSSES, reported for the mirror-image reason. A count of
assertions that never became rows is worthless if the rows that DID land are quietly
wrong or quietly contradictory, so the summary also carries:
`chemical_object_assertions` (the object is a MeSH chemical, not a patient state),
`broadened_object_assertions` (MED-RT named a subordinate concept, so every row that
follows sits on a BROADER condition than the release said -- 422 of 18,314, #52), and
`also_contraindicated_pairs` (one drug is both indicated and contraindicated for one
condition -- 168 pairs, #51). Nothing is refused on account of any of them, and none
has a worklist yet, because each is a curated question rather than a coverage gap.

THE FIRST TWO ARE RELEASE-GRAIN AND THE THIRD IS ROW-GRAIN, and the difference is not a
detail: the two indication counters are taken ABOVE the moiety gate (mesh_ind_relations
explains why), so they describe MED-RT's own content and match its totals, while
`also_contraindicated_pairs` is measured by querying the two stored tables and therefore
describes drugref. Reading either kind as the other is the pre-gate/post-gate confusion
this slice already had to publish an erratum for.
"""
import logging
import uuid
from collections import Counter

import psycopg

from drugref import classes as class_writer
from drugref import conditions as condition_writer
from drugref import indications, interactions, provenance, questions
from drugref.ingest import medrt, mesh_ci_relations, mesh_concepts, mesh_ind_relations
from drugref.ingest.checksum import checksum
# The four tallies this run returns. They live in their own module (pure data, no
# behaviour) because this one had reached exactly the ~500-line ceiling CLAUDE.md rule 4
# sets, so the next line added here would have broken the rule silently. IMPORTED INTO
# THIS NAMESPACE RATHER THAN LEFT WHERE THEY SIT, so `mesh_rel_run.MeshRelSummary` keeps
# working for every caller and test that already spells it that way -- the move changed
# no behaviour and no number, and must not force an import to know it happened.
from drugref.ingest.mesh_rel_summary import (
    CiTally,
    IndicationTally,
    MeshRelSummary,
    RegistryTally,
)

# The authority that STATES the relations, and therefore the source this run's
# ingest_run is opened under. Every per-source rebuild in this module scopes on it,
# because every row this module writes hangs off this run.
#
# THE ONLY DECLARATION FOR THIS RUN, and each relation pass is HANDED it rather than
# repeating it: one run opens one ingest_run under one source, so a copy in a pass
# would be one answer written down twice with no way to disagree usefully (#43).
SOURCE = "MED-RT"
# The authority that DEFINES the objects. It is the condition registry's source
# (a condition_uuid is minted from 'MeSH' + a DescriptorUI), which is a different
# question from who asserted the rule -- hence two constants, not one.
OBJECT_SOURCE = "MeSH"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). MED-RT has two writers -- this one and medrt_run -- so a release is only
# unambiguous per (source, writer).
WRITER = "mesh_rel_run"

log = logging.getLogger(__name__)


def _condition_closure(
        desc_path, records: dict[str, mesh_concepts.MeshRecord],
        condition_codes: set[str]) -> dict[str, mesh_concepts.MeshRecord]:
    """The conditions this run registers: those NAMED, plus everything BELOW them.

    The closure is what a rule expands INTO. The descendants are not themselves CI
    objects, so a registry scoped to referenced objects would leave the read path
    with nothing to find and the feature would be inert while appearing to work
    (spec 5.1).

    Measured on the real 2026 releases, and stated as two numbers because this function
    returns records of two KINDS: 1,764 referenced records come in, and 5,963 conditions
    (5,929 descriptors + 34 SCRs) come out. Only descriptors carry tree numbers -- an
    SCR contributes no prefix and gains no descendant, so it appears in the closure only
    as itself, which is why mesh_concepts.descriptors_under is measured on descriptors.

    THESE ARE 5b.2's NUMBERS. A reader checking against slice 5b's documents will find
    the CI-only pair instead: 677 referenced records -> 5,203 conditions (5,190
    descriptors + 13 SCRs) and 7,157 DAG edges, against 8,507 now. Nothing in this
    function changed; the caller's SET did (spec 3.6).

    Keyed by record_ui, never by concept_ui: many concepts resolve to one record, and
    keying on the concept would split one condition into rows no rebuild could merge.
    The named records are written LAST so that a condition which is also a descendant
    of another is stored under the concept MED-RT actually pointed at.

    WHAT THE REGISTRY COVERS IS THE ARGUMENT `condition_codes`, never a predicate test
    inside this function: the closure is the same walk whichever relation named the
    object, so widening the registry over a further family of objects is a change to
    the caller's SET and to nothing here.
    """
    prefixes = frozenset(tree for code in condition_codes if code in records
                         for tree in records[code].tree_numbers)
    closure = {r.record_ui: r for r in mesh_concepts.descriptors_under(desc_path,
                                                                      prefixes)}
    for code in condition_codes:
        if code in records:
            closure[records[code].record_ui] = records[code]
    return closure


def ingest_mesh_relations(conn: psycopg.Connection, *, medrt_path, desc_path,
                          supp_path, upstream_release: str) -> MeshRelSummary:
    """Ingest MED-RT's MeSH-keyed relations. Idempotent.

    TRANSACTION OWNERSHIP: TWO transactions on one connection. provenance.open_run
    commits the run record before the WRITES, so a crash during them leaves it standing
    with finished_at NULL (ingest_run_incomplete reports it); everything after it is
    the work, which this function owns, commits on success, and rolls back before
    re-raising. A caller with pending work has it committed at the provenance boundary,
    so callers must commit their own work before calling.

    "BEFORE THE WRITES" IS NOT "BEFORE THE COMMAND", and this orchestrator is one of
    the three where the gap is wide: the parse runs FIRST (it is pure and takes no
    connection), so a crash while parsing still leaves no row at all -- a view cannot
    report a run nobody opened. The six orchestrators are not uniform in this, and
    ingest_run_incomplete's own comment says so.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    log.info("MeSH-keyed relation ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest(conn, medrt_path, desc_path, supp_path, upstream_release,
                          clock)
    except Exception:
        conn.rollback()
        log.exception("MeSH-keyed relation ingest failed (release=%s); rolled back",
                      upstream_release)
        raise
    log.info("MeSH-keyed relation ingest finished (release=%s): %s", upstream_release,
             summary)
    ci = summary.contraindications
    if ci.withheld_class_objects:
        # WARNING, not an error: withholding is the designed behaviour, but the
        # operator's next move is to look at those exact rows, so the number is put
        # where they will see it -- the same posture medrt_run takes for
        # unresolved_expansion_policy.
        log.warning("%d contraindication object(s) withheld pending review; see "
                    "drugref.gap_unresolved_ci_object", ci.withheld_class_objects)
    if ci.unregistered_object_substances:
        # A DIFFERENT operator action from the line above, which is why it is a
        # different line: these objects name real substances drugref's registry does
        # not carry, so the remedy is to widen the registry, never to rule on tree
        # expansion (db/014).
        log.warning("%d contraindication object(s) name a substance no moiety "
                    "carries, so their rules were not ingested; see "
                    "drugref.gap_unresolved_ci_object",
                    ci.unregistered_object_substances)
    if ci.unmatched_subject_rxcuis:
        log.warning("%d contraindication subject RxCUI(s) are carried by no moiety, "
                    "so their rules were not ingested; see "
                    "drugref.gap_unmatched_ingredient", ci.unmatched_subject_rxcuis)
    if summary.indications.unmatched_subject_rxcuis:
        # A SEPARATE LINE FROM THE ONE ABOVE, naming the same view, because the two
        # counts are different populations and land in different `reason` buckets: the
        # release contraindicates and indicates over overlapping-but-unequal ingredient
        # sets, so summing them would report a number that matches neither query.
        log.warning("%d indication subject RxCUI(s) are carried by no moiety, so "
                    "their rules were not ingested; see "
                    "drugref.gap_unmatched_ingredient",
                    summary.indications.unmatched_subject_rxcuis)
    # THE TWO LINES BELOW REPORT ROWS THAT WERE WRITTEN, not rows that were lost, and
    # they are warnings for that reason rather than in spite of it: every other number
    # here is something an operator can go and look at, while these two are things the
    # database will state confidently and wrongly unless somebody knows to expect them.
    # Neither has a worklist yet -- both are curated questions (#51, #52) -- so the log
    # line is the whole of the surfacing until slice 5c gives them one.
    if summary.also_contraindicated_pairs:
        log.warning("%d (drug, condition) pair(s) are asserted as BOTH an indication "
                    "and a contraindication by this release; consumers must not read "
                    "the pair as a contradiction to resolve automatically (#51)",
                    summary.also_contraindicated_pairs)
    if summary.indications.broadened_object_assertions:
        # RELEASE-GRAIN, like the counter itself: this says how many assertions the
        # release keys to a subordinate concept, not how many rows drugref stored --
        # an assertion whose subject no moiety carries is counted here and stored
        # nowhere. Phrased so an operator cannot read it as a row count (#52).
        log.warning("%d indication assertion(s) in this release name a SUBORDINATE "
                    "MeSH concept, so every row they produce sits on a BROADER "
                    "condition than the release named (#52)",
                    summary.indications.broadened_object_assertions)
    return summary


def _ingest(conn, medrt_path, desc_path, supp_path, upstream_release,
            clock: provenance.RunClock) -> MeshRelSummary:
    """The body of one MeSH-keyed relation ingest (see ingest_mesh_relations)."""
    parsed = medrt.parse(medrt_path)
    ci_assertions = parsed.mesh_contraindications
    ind_assertions = parsed.mesh_indications

    run_id = provenance.open_run(
        conn, source=SOURCE, upstream_release=upstream_release,
        source_checksum=checksum(medrt_path, desc_path, supp_path), writer=WRITER,
        clock=clock)

    # 1. Resolve every referenced MeSH code, then take the descendant closure of the
    #    condition objects (see _condition_closure).
    #
    #    RESOLVED IN ONE PASS OVER THE MeSH FILES (supp2026 is ~750 MB), but the two
    #    halves' WANTED SETS stay apart, because each reports its own unresolved-code
    #    loss: a code both halves name and MeSH no longer defines costs each of them an
    #    assertion, so it is counted twice on purpose -- one number per question.
    ci_wanted = {a.mesh_code for a in ci_assertions}
    ind_wanted = {a.mesh_code for a in ind_assertions}
    records = mesh_concepts.resolve_concepts(desc_path, supp_path,
                                             ci_wanted | ind_wanted)
    unresolved_object_codes = len(ci_wanted - set(records))
    unresolved_indication_codes = len(ind_wanted - set(records))
    # The registry covers the objects named as CONDITIONS, and this expression is the
    # whole statement of which those are -- one set, built here, closed over below.
    #
    # ONE CLOSURE OVER EVERY MeSH-KEYED OBJECT (spec 3.6), which is what makes the
    # registry 5,203 -> 5,963 and condition_parent 7,157 -> 8,507 on the real releases.
    # EVERY indication object is a condition -- unlike CI_ChemClass there is no second
    # arm to fall back to -- so a registry scoped to CI_with would leave the indication
    # pass resolving records it then found no condition row for, and the assertions
    # would vanish while the ingest reported success.
    #
    # THE CONTRAINDICATION HALF MOVES TOO, UPWARD, and that is a completion rather than
    # scope creep: a condition bears several tree numbers and an edge is written only
    # when BOTH endpoints are registered, so a condition already in the CI closure can
    # have a second tree parent that only the indication half registers. The edge then
    # appears and the condition becomes reachable from a CI root it could not be reached
    # from before -- 10 of 641 roots, condition_subtree 11,512 -> 11,605 (+93), and
    # condition_contraindication_expanded 191,728 -> 192,161, +0.226%
    # assertion-weighted. Acute Pain really is filed under nervous system disease in
    # MeSH; the old registry was simply too narrow to see the edge.
    #
    # POST-GATE FIGURES. The spec's 11 of 677 / 12,311 -> 12,415 / +0.39% counted the
    # CI_with objects the RELEASE references; condition_subtree (db/015) walks only the
    # roots that STORED rules name, which the moiety gate cuts to 641. Both are right
    # about different populations; only the post-gate pair describes the view.
    condition_codes = {a.mesh_code for a in ci_assertions
                       if a.relationship == mesh_ci_relations.CONDITION_PREDICATE}
    closure = _condition_closure(desc_path, records, condition_codes | ind_wanted)

    # 2. Conditions first: every edge and every contraindication references one.
    #    Their source is MeSH -- a condition is a MeSH record whoever cites it.
    uuid_by_code: dict[str, uuid.UUID] = {}
    added = 0
    for record in closure.values():
        cu, is_new = condition_writer.upsert_condition(conn, record, run_id,
                                                       OBJECT_SOURCE)
        uuid_by_code[record.record_ui] = cu
        added += is_new

    # 3. Clear this source's previous projection before writing this run's.
    #
    #    SCOPED ON 'MED-RT', NOT 'MeSH', and the distinction is load-bearing: both
    #    clears filter on ingest_run.source, and the runs that wrote these rows are
    #    THIS orchestrator's, which are opened under MED-RT because MED-RT is who
    #    asserts them. Scoping the edge clear on the condition registry's source
    #    instead would match no run this module ever opened, so a parent that
    #    vanished upstream would survive every rebuild -- the projection would be
    #    append-only in all but name.
    #
    #    EVERY ROW CLEAR HAPPENS HERE, BEFORE ANY PASS RUNS, and that placement is the
    #    correctness argument: a clear running AFTER its pass deletes the rows that pass
    #    just wrote, and the projection comes back EMPTY on every run while every count
    #    in the summary still looks right. A new relation family adds its clear here.
    condition_writer.clear_source_condition_edges(conn, SOURCE)
    interactions.clear_source_mesh_contraindications(conn, SOURCE)
    indications.clear_source_indications(conn, SOURCE)

    # 4. The condition DAG. Edges into records outside the closure are dropped rather
    #    than re-attached to a more distant ancestor: such a record is simply a ROOT
    #    of the ingested subset (mesh_concepts.parent_edges).
    parent_edges = sum(
        condition_writer.add_condition_parent_edge(
            conn, uuid_by_code[e.child_code], uuid_by_code[e.parent_code], run_id)
        for e in mesh_concepts.parent_edges(closure.values())
        if e.child_code in uuid_by_code and e.parent_code in uuid_by_code)

    # 5. The relation passes. Read every index ONCE -- a subject appears in many
    #    assertions, and a pass re-reading them would re-ask an answered question --
    #    then hand each pass the indexes it actually reads.
    #
    #    THE INDICATION PASS GETS ONE INDEX, NOT THREE, and the interface is saying
    #    something true: an indication's object is always a patient state, never a
    #    drug, so the UNII and CAS indexes have no reader there. Handing them over
    #    invites one.
    rxcui_index = class_writer.moieties_by_rxcui(conn)
    indexes = (rxcui_index,
               class_writer.moieties_by_scheme(conn, "UNII"),
               class_writer.moieties_by_scheme(conn, "CAS"))
    #    Each pass is TOLD which run it writes for -- SOURCE and run_id travel
    #    together, because both are provenance of the ingest_run opened above.
    ci = mesh_ci_relations.write_contraindications(conn, ci_assertions, records,
                                                  uuid_by_code, indexes, SOURCE,
                                                  run_id)
    ind = mesh_ind_relations.write_indications(conn, ind_assertions, records,
                                               uuid_by_code, rxcui_index, SOURCE,
                                               run_id)

    # 6. Persist the withheld objects' IDENTITIES, not merely their count: a worklist
    #    that says "2 objects were withheld" cannot be worked, which is the lesson
    #    db/008 drew when the earlier ingest kept only the COUNT of unmatched
    #    ingredients.
    #
    #    THE UNMATCHED SUBJECTS ARE THIS RUN'S OWN BUCKET, and since #39 (db/018) it
    #    both clears and writes them, exactly as medrt_run does with its own. Both
    #    orchestrators open their runs under source 'MED-RT' and the two lists are
    #    built from different upstream assertions, so `reason` is what tells them
    #    apart. Measured on the real 2026.07.06 release through the real gate, which
    #    is why neither writer may own the whole table: MED-RT classifies 6,012
    #    ingredients and contraindicates 3,757, 2,271 of the classified are not CI
    #    subjects (medrt_run's rows alone), and 16 CI subjects are never classified at
    #    all -- three of them (221083 sulfur colloidal, 5924 inulin, 89767 colloid
    #    sulfur) outside the moiety registry, one CI_with rule each, which medrt_run
    #    could never record because it builds its list from MEMBERSHIP assertions.
    #
    #    Before the discriminator this run wrote without clearing, and paid for it
    #    twice: a later medrt_run removed these rows and could not re-add them, and
    #    consecutive runs of this orchestrator accumulated because only medrt_run
    #    collected the garbage. Both are gone -- the answer no longer depends on which
    #    orchestrator ran last.
    class_writer.clear_source_unmatched_ingredients(
        conn, SOURCE, class_writer.CONTRAINDICATION)
    class_writer.add_unmatched_ingredients(conn, sorted(ci.unmatched_rxcuis), run_id,
                                           class_writer.CONTRAINDICATION)
    #    THE INDICATION BUCKET IS THE SAME PAIR, NEVER A BARE WRITE -- #39 restated.
    #    The table is keyed (ingest_run, reason, rxcui), so a writer that inserts
    #    without first collecting its own garbage adds a fresh copy of every row under
    #    each new run id, forever. Nor may it widen the clear to cover the bucket
    #    above: the two lists are different populations (a subject unmatched for one
    #    half may be matched, or absent, for the other), so a shared clear would make
    #    the answer depend on which pass ran last. One writer per (source, reason);
    #    this run owns two.
    class_writer.clear_source_unmatched_ingredients(
        conn, SOURCE, class_writer.INDICATION)
    class_writer.add_unmatched_ingredients(conn, sorted(ind.unmatched_rxcuis), run_id,
                                           class_writer.INDICATION)
    interactions.record_unresolved_ci_objects(
        conn,
        [(SOURCE, mesh_ci_relations.PAIR_PREDICATE, OBJECT_SOURCE, code,
          ci.object_names[code], kind, count)
         for kind, counter in ((mesh_ci_relations.CHEMICAL_CLASS, ci.withheld),
                               (mesh_ci_relations.UNREGISTERED_SUBSTANCE,
                                ci.unregistered))
         for code, count in sorted(counter.items())],
        run_id)

    # 7. Measure what the two halves say about the SAME (drug, condition) pair.
    #
    #    ONE QUERY, AFTER BOTH PASSES, AND IT COULD NOT LIVE ANYWHERE ELSE: neither
    #    pass can see the other's rows, and the overlap is not a loss either pass
    #    could count on its way past. The no-silent-drops posture (spec 7) is about
    #    assertions that do not become rows; this is its mirror -- two assertions that
    #    DO become rows and contradict each other -- and leaving it uncounted would be
    #    the same failure in the other direction.
    #
    #    168 pairs on the 2026.07.06 release, and they are the hardest rows in it
    #    rather than noise: carvedilol is may_treat AND CI_with for Heart Failure,
    #    alteplase for Stroke, budesonide for Asthma. MED-RT asserts both with no
    #    qualifier, because the distinction (stable chronic HFrEF vs acute
    #    decompensation; ischaemic vs haemorrhagic stroke) is one the MeSH descriptor
    #    grain cannot carry. See #51 for the curated question of how a consumer should
    #    be TOLD, which is 5c's.
    #
    #    DISTINCT PAIRS, NOT ROWS, and the grain is the clinical unit: a pair is what a
    #    consumer asking about one drug and one patient's diagnosis gets both answers
    #    for. 7 pairs carry two therapeutic predicates, so the indication-row count is
    #    175 -- reporting that instead would answer a question nobody asks.
    #
    #    DELIBERATELY NOT SCOPED ON `source`, unlike every clear above it. Those
    #    rebuild THIS source's projection and must not touch another's; this one
    #    answers "what will a consumer see", and a consumer sees both tables whole.
    #    Today the question does not arise -- db/014 and db/019 CHECK both `source`
    #    columns to 'MED-RT' alone -- but when a second authority lands, a cross-source
    #    collision is exactly as visible to a reader as a within-source one, and
    #    scoping this query would hide it. If a per-source breakdown is ever wanted,
    #    add one; do not narrow this.
    also_contraindicated_pairs = conn.execute(
        "SELECT count(*) FROM (SELECT DISTINCT i.subject_moiety_uuid, "
        "                             i.object_condition_uuid "
        "  FROM drugref.moiety_condition_indication i "
        "  JOIN drugref.moiety_condition_contraindication c "
        "    ON  c.subject_moiety_uuid   = i.subject_moiety_uuid "
        "    AND c.object_condition_uuid = i.object_condition_uuid) x").fetchone()[0]

    # 8. Re-derive the open-question register LAST, for the reason every orchestrator
    #    does: this run rewrote projections the gap views read, and calling it earlier
    #    would read a half-demolished registry.
    questions.register_from_gaps(conn, run_id)

    provenance.finish_run(conn, run_id)
    conn.commit()
    return MeshRelSummary(
        registry=RegistryTally(
            conditions_registered=len(uuid_by_code),
            conditions_added=added,
            condition_parent_edges=parent_edges,
            # Counted off the CLOSURE, which is the set actually registered, and sorted
            # so two runs over one release produce comparable summaries. Descriptors
            # carry no SCRClass and are excluded by the falsy test rather than by a
            # record_kind test: the question is "which published values did this run
            # store", and a None is not one.
            scr_class_counts=tuple(sorted(Counter(
                r.scr_class for r in closure.values() if r.scr_class).items()))),
        contraindications=CiTally(
            condition_rows=ci.condition_rows,
            pair_rows=ci.pair_rows,
            unmatched_subject_rxcuis=len(ci.unmatched_rxcuis),
            withheld_class_objects=len(ci.withheld),
            unregistered_object_substances=len(ci.unregistered),
            self_paired_assertions=ci.self_pairs,
            unresolved_object_codes=unresolved_object_codes,
            non_mesh_objects=parsed.non_mesh_ci_objects),
        indications=IndicationTally(
            indication_rows=ind.indication_rows,
            induced_rows=ind.induced_rows,
            unmatched_subject_rxcuis=len(ind.unmatched_rxcuis),
            class_subject_assertions=parsed.class_subject_indications,
            unresolved_object_codes=unresolved_indication_codes,
            chemical_object_assertions=ind.chemical_object_assertions,
            broadened_object_assertions=ind.broadened_object_assertions),
        also_contraindicated_pairs=also_contraindicated_pairs)
