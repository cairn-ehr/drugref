"""Orchestrate MED-RT's MeSH-keyed relations over ONE condition registry.

Reads TWO authorities and joins them: MED-RT states the relation, MeSH defines its
object. Mirrors medrt_run/mesh_run (open an ingest_run for provenance, do the work,
stamp finished_at, commit) with two genuinely new pieces:

  1. M-CODE RESOLUTION. MED-RT's MeSH endpoint is a ConceptUI, so every object is
     resolved against the MeSH release (ingest/mesh_concepts.py). 1,051 of THIS
     SLICE'S 1,053 object codes resolve (99.81%); the rest are counted. Do not
     reconcile that with the 99.88% in mesh_concepts.py -- that figure is
     2,471/2,474 over every MeSH endpoint in the release, a wider denominator, and
     both are right about different populations.
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
is the two contraindication predicates; the shape is what lets a second family be a
second pass rather than a second writer of the same tables.

Order matters:
  1. parse MED-RT (pure) -> the set of MeSH codes to resolve;
  2. resolve those codes, then walk their tree positions for the DESCENDANT CLOSURE,
     without which a rule on Epilepsy has nothing to expand into;
  3. upsert conditions, then clear this source's edges and contraindications, then
     write the DAG and the two relations;
  4. rebuild the open-question register LAST, before the commit.

WORKLIST NUMBERS, NOT SILENT DROPS -- six distinct losses, each counted separately
so they stay legible (spec 7). Three of them are also PERSISTED by identity, because
a count cannot be worked: the withheld class objects and the unregistered-substance
objects (a curator works each by name, and is asked a DIFFERENT question about each)
and the unmatched subjects. The last shares a table with medrt_run's own unmatched
list, so both writers scope their clear on `reason` as well as source (#39, db/018) --
see step 6 of _ingest for the measurement behind that.
"""
import logging
import uuid
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref import conditions as condition_writer
from drugref import interactions, questions
from drugref.ingest import medrt, mesh_ci_relations, mesh_concepts
from drugref.ingest.checksum import checksum

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

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegistryTally:
    """What one run did to the CONDITION REGISTRY, stated ONCE for the whole run.

    Conditions ACCUMULATE while edges and contraindications are REBUILT, so the two
    condition numbers are reported separately rather than as one ambiguous count.

    `conditions_registered` counts CONDITION ROWS this run put in the registry -- the
    referenced objects plus their descendant closure -- and is deliberately NOT called
    "in_release". Measured on the real 2026 releases: desc2026 holds 31,110 descriptor
    records, of which this slice registers 5,203 conditions (5,190 descriptors plus 13
    supplementary records). Naming it after the release would invite a reader to check
    it against MeSH's own record count and conclude the ingest had lost 25,000 records.

    Do not confuse it with the closure's DESCRIPTOR count (5,190) quoted in
    _condition_closure and mesh_concepts.descriptors_under: SCRs bear no tree numbers,
    so they never enter the closure and appear only as themselves. Both figures are
    right about different things, which is why each says which it is counting.

    ONE TALLY FOR THE WHOLE RUN, not one per relation family, because it IS one fact
    about one closure over one registry (spec 6.1). Reporting `conditions_registered`
    under each family would be one quantity stated twice, and db/018's round is the
    standing evidence for what happens next: only one of the two copies learns the
    next correction.
    """
    conditions_registered: int
    conditions_added: int
    condition_parent_edges: int


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


def _condition_closure(desc_path, records: dict[str, mesh_concepts.MeshRecord],
                       condition_codes: set[str]) -> dict[str, mesh_concepts.MeshRecord]:
    """The conditions this run registers: those NAMED, plus everything BELOW them.

    The closure is what a rule expands INTO. The descendants are not themselves CI
    objects, so a registry scoped to referenced objects would leave the read path
    with nothing to find and the feature would be inert while appearing to work
    (spec 5.1).

    Measured on the real 2026.07.06 release, and stated as two numbers because this
    function returns records of two KINDS: 677 referenced records (664 descriptors +
    13 SCRs) come in, and 5,203 conditions (5,190 descriptors + the same 13 SCRs)
    come out. The 5,190 is the figure mesh_concepts.descriptors_under is measured on,
    because only descriptors carry tree numbers -- an SCR contributes no prefix and
    gains no descendant, so it appears in the closure only as itself.

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

    TRANSACTION OWNERSHIP: as for medrt_run/mesh_run -- this owns `conn`'s
    transaction, commits on success, and rolls back before re-raising on failure so
    the caller never receives a connection stuck in the aborted-transaction state.
    """
    log.info("MeSH-keyed relation ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest(conn, medrt_path, desc_path, supp_path, upstream_release)
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
    return summary


def _ingest(conn, medrt_path, desc_path, supp_path,
            upstream_release) -> MeshRelSummary:
    """The body of one MeSH-keyed relation ingest (see ingest_mesh_relations)."""
    parsed = medrt.parse(medrt_path)
    ci_assertions = parsed.mesh_contraindications

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release,
         checksum(medrt_path, desc_path, supp_path))).fetchone()[0]

    # 1. Resolve every referenced MeSH code, then take the descendant closure of the
    #    condition objects (see _condition_closure).
    wanted = {a.mesh_code for a in ci_assertions}
    records = mesh_concepts.resolve_concepts(desc_path, supp_path, wanted)
    unresolved_object_codes = len(wanted - set(records))
    # The registry covers the objects named as CONDITIONS, and this expression is the
    # whole statement of which those are -- one set, built here, closed over below.
    condition_codes = {a.mesh_code for a in ci_assertions
                       if a.relationship == mesh_ci_relations.CONDITION_PREDICATE}
    closure = _condition_closure(desc_path, records, condition_codes)

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
    condition_writer.clear_source_condition_edges(conn, SOURCE)
    interactions.clear_source_mesh_contraindications(conn, SOURCE)

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
    #    then hand the same three to every pass.
    indexes = (class_writer.moieties_by_rxcui(conn),
               class_writer.moieties_by_scheme(conn, "UNII"),
               class_writer.moieties_by_scheme(conn, "CAS"))
    #    The pass is TOLD which run it writes for -- SOURCE and run_id travel
    #    together, because both are provenance of the ingest_run opened above.
    ci = mesh_ci_relations.write_contraindications(conn, ci_assertions, records,
                                                  uuid_by_code, indexes, SOURCE,
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
    interactions.record_unresolved_ci_objects(
        conn,
        [(SOURCE, mesh_ci_relations.PAIR_PREDICATE, OBJECT_SOURCE, code,
          ci.object_names[code], kind, count)
         for kind, counter in ((mesh_ci_relations.CHEMICAL_CLASS, ci.withheld),
                               (mesh_ci_relations.UNREGISTERED_SUBSTANCE,
                                ci.unregistered))
         for code, count in sorted(counter.items())],
        run_id)

    # 7. Re-derive the open-question register LAST, for the reason every orchestrator
    #    does: this run rewrote projections the gap views read, and calling it earlier
    #    would read a half-demolished registry.
    questions.register_from_gaps(conn, run_id)

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() "
                 "WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return MeshRelSummary(
        registry=RegistryTally(conditions_registered=len(uuid_by_code),
                               conditions_added=added,
                               condition_parent_edges=parent_edges),
        contraindications=CiTally(
            condition_rows=ci.condition_rows,
            pair_rows=ci.pair_rows,
            unmatched_subject_rxcuis=len(ci.unmatched_rxcuis),
            withheld_class_objects=len(ci.withheld),
            unregistered_object_substances=len(ci.unregistered),
            self_paired_assertions=ci.self_pairs,
            unresolved_object_codes=unresolved_object_codes,
            non_mesh_objects=parsed.non_mesh_ci_objects))
