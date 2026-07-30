"""Orchestrate one slice-5b ingest: MED-RT's MeSH-keyed contraindications.

Reads TWO authorities and joins them: MED-RT states the contraindication, MeSH
defines its object. Mirrors medrt_run/mesh_run (open an ingest_run for provenance,
do the work, stamp finished_at, commit) with two genuinely new pieces:

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
from collections import Counter
from dataclasses import dataclass, field

import psycopg

from drugref import classes as class_writer
from drugref import conditions as condition_writer
from drugref import interactions, questions
from drugref.ingest import medrt, mesh_concepts
from drugref.ingest.checksum import checksum

# The authority that STATES the contraindications, and therefore the source this
# run's ingest_run is opened under. Every per-source rebuild in this module scopes
# on it, because every row this module writes hangs off this run.
SOURCE = "MED-RT"
# The authority that DEFINES the objects. It is the condition registry's source
# (a condition_uuid is minted from 'MeSH' + a DescriptorUI), which is a different
# question from who asserted the rule -- hence two constants, not one.
OBJECT_SOURCE = "MeSH"
CONDITION_PREDICATE = "CI_with"
PAIR_PREDICATE = "CI_ChemClass"

# Why a CI_ChemClass object was not ingested (db/014's object_kind vocabulary). Kept
# in lockstep with that migration's CHECK, and named here rather than spelled inline
# so the two writers below cannot disagree about the spelling.
CHEMICAL_CLASS = "CHEMICAL_CLASS"                  # the record carries no registry key
UNREGISTERED_SUBSTANCE = "UNREGISTERED_SUBSTANCE"  # a real UNII/CAS, but no moiety

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeshCiSummary:
    """What one slice-5b run did -- returned so a caller or test can assert on it.

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

    The six worklist numbers are reported, never swallowed:
      * unmatched_subject_rxcuis      -- the rule's subject is carried by no moiety
      * withheld_class_objects        -- CI_ChemClass objects that name a CLASS
      * unregistered_object_substances -- CI_ChemClass objects that name a SUBSTANCE
                                         drugref's registry does not carry
      * self_paired_assertions        -- CI_ChemClass rules whose two ends collapse
                                         to one moiety, which db/014 forbids storing
      * unresolved_object_codes       -- M-codes MeSH no longer defines
      * non_mesh_objects              -- objects outside the MeSH namespace (MED-RT EXT)

    THE TWO OBJECT NUMBERS ARE NOT ONE NUMBER, and separating them is the point of
    db/014's object_kind. Both are CI_ChemClass objects that failed to bridge, but the
    reasons differ and so do the remedies: a CLASS (no registry key on the MeSH
    record at all) is withheld pending a curator ruling on structural-tree expansion,
    while an UNREGISTERED SUBSTANCE (a real UNII or CAS, no moiety) is a registry
    coverage gap. Reporting them as one figure is what let a leaf drug descriptor be
    asked whether it should expand over the drugs beneath it.
    """
    conditions_registered: int
    conditions_added: int
    condition_parent_edges: int
    condition_contraindications: int
    moiety_contraindications: int
    unmatched_subject_rxcuis: int
    withheld_class_objects: int
    unregistered_object_substances: int
    self_paired_assertions: int
    unresolved_object_codes: int
    non_mesh_objects: int


@dataclass
class _Relations:
    """The tally of one pass over the assertions (see _write_relations).

    A mutable scratch record rather than a handful of loose locals: the pass produces
    two row counts and four worklists, and returning them as one named thing is what
    keeps _ingest readable and stops a caller pairing them up in the wrong order.
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
    # merely skipped -- see _write_relations.
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
    """
    prefixes = frozenset(tree for code in condition_codes if code in records
                         for tree in records[code].tree_numbers)
    closure = {r.record_ui: r for r in mesh_concepts.descriptors_under(desc_path,
                                                                      prefixes)}
    for code in condition_codes:
        if code in records:
            closure[records[code].record_ui] = records[code]
    return closure


def _write_relations(conn, assertions, records, uuid_by_code, indexes,
                     run_id: int) -> _Relations:
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
    out = _Relations()
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


def ingest_mesh_contraindications(conn: psycopg.Connection, *, medrt_path,
                                  desc_path, supp_path,
                                  upstream_release: str) -> MeshCiSummary:
    """Ingest MED-RT's MeSH-keyed contraindications. Idempotent.

    TRANSACTION OWNERSHIP: as for medrt_run/mesh_run -- this owns `conn`'s
    transaction, commits on success, and rolls back before re-raising on failure so
    the caller never receives a connection stuck in the aborted-transaction state.
    """
    log.info("MeSH CI ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest(conn, medrt_path, desc_path, supp_path, upstream_release)
    except Exception:
        conn.rollback()
        log.exception("MeSH CI ingest failed (release=%s); rolled back",
                      upstream_release)
        raise
    log.info("MeSH CI ingest finished (release=%s): %s", upstream_release, summary)
    if summary.withheld_class_objects:
        # WARNING, not an error: withholding is the designed behaviour, but the
        # operator's next move is to look at those exact rows, so the number is put
        # where they will see it -- the same posture medrt_run takes for
        # unresolved_expansion_policy.
        log.warning("%d contraindication object(s) withheld pending review; see "
                    "drugref.gap_unresolved_ci_object", summary.withheld_class_objects)
    if summary.unregistered_object_substances:
        # A DIFFERENT operator action from the line above, which is why it is a
        # different line: these objects name real substances drugref's registry does
        # not carry, so the remedy is to widen the registry, never to rule on tree
        # expansion (db/014).
        log.warning("%d contraindication object(s) name a substance no moiety "
                    "carries, so their rules were not ingested; see "
                    "drugref.gap_unresolved_ci_object",
                    summary.unregistered_object_substances)
    if summary.unmatched_subject_rxcuis:
        log.warning("%d contraindication subject RxCUI(s) are carried by no moiety, "
                    "so their rules were not ingested; see "
                    "drugref.gap_unmatched_ingredient",
                    summary.unmatched_subject_rxcuis)
    return summary


def _ingest(conn, medrt_path, desc_path, supp_path, upstream_release) -> MeshCiSummary:
    """The body of one slice-5b ingest (see ingest_mesh_contraindications)."""
    parsed = medrt.parse(medrt_path)
    assertions = parsed.mesh_contraindications

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release,
         checksum(medrt_path, desc_path, supp_path))).fetchone()[0]

    # 1. Resolve every referenced MeSH code, then take the descendant closure of the
    #    condition objects (see _condition_closure).
    wanted = {a.mesh_code for a in assertions}
    records = mesh_concepts.resolve_concepts(desc_path, supp_path, wanted)
    unresolved_object_codes = len(wanted - set(records))
    closure = _condition_closure(
        desc_path, records,
        {a.mesh_code for a in assertions if a.relationship == CONDITION_PREDICATE})

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

    # 5. The two relations. Read every index ONCE -- a subject appears in many
    #    assertions, so a per-assertion lookup re-asks an answered question.
    indexes = (class_writer.moieties_by_rxcui(conn),
               class_writer.moieties_by_scheme(conn, "UNII"),
               class_writer.moieties_by_scheme(conn, "CAS"))
    rel = _write_relations(conn, assertions, records, uuid_by_code, indexes, run_id)

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
    class_writer.add_unmatched_ingredients(conn, sorted(rel.unmatched_rxcuis), run_id,
                                           class_writer.CONTRAINDICATION)
    interactions.record_unresolved_ci_objects(
        conn,
        [(SOURCE, PAIR_PREDICATE, OBJECT_SOURCE, code, rel.object_names[code],
          kind, count)
         for kind, counter in ((CHEMICAL_CLASS, rel.withheld),
                               (UNREGISTERED_SUBSTANCE, rel.unregistered))
         for code, count in sorted(counter.items())],
        run_id)

    # 7. Re-derive the open-question register LAST, for the reason every orchestrator
    #    does: this run rewrote projections the gap views read, and calling it earlier
    #    would read a half-demolished registry.
    questions.register_from_gaps(conn, run_id)

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() "
                 "WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return MeshCiSummary(
        conditions_registered=len(uuid_by_code), conditions_added=added,
        condition_parent_edges=parent_edges,
        condition_contraindications=rel.condition_rows,
        moiety_contraindications=rel.pair_rows,
        unmatched_subject_rxcuis=len(rel.unmatched_rxcuis),
        withheld_class_objects=len(rel.withheld),
        unregistered_object_substances=len(rel.unregistered),
        self_paired_assertions=rel.self_pairs,
        unresolved_object_codes=unresolved_object_codes,
        non_mesh_objects=parsed.non_mesh_ci_objects)
