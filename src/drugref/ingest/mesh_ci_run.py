"""Orchestrate one slice-5b ingest: MED-RT's MeSH-keyed contraindications.

Reads TWO authorities and joins them: MED-RT states the contraindication, MeSH
defines its object. Mirrors medrt_run/mesh_run (open an ingest_run for provenance,
do the work, stamp finished_at, commit) with two genuinely new pieces:

  1. M-CODE RESOLUTION. MED-RT's MeSH endpoint is a ConceptUI, so every object is
     resolved against the MeSH release (ingest/mesh_concepts.py). 99.81% of this
     slice's objects resolve; the rest are counted.
  2. THE TWO ARMS OF CI_ChemClass. Its object is usually a SPECIFIC DRUG (Pimozide,
     Cisapride, Ritonavir), so it is first resolved against the moiety registry via
     slice 2b's two-key UNII->CAS bridge. When it resolves, the assertion is an exact
     drug-drug pair. When it does not, the object is a genuine chemical CLASS, and
     the assertion is WITHHELD and recorded as a question -- expanding it over MeSH's
     structural tree would make a rule on Sulfonamides reach bendroflumethiazide
     (see db/014 and db/016).

Order matters:
  1. parse MED-RT (pure) -> the set of MeSH codes to resolve;
  2. resolve those codes, then walk their tree positions for the DESCENDANT CLOSURE,
     without which a rule on Epilepsy has nothing to expand into;
  3. upsert conditions, then clear this source's edges and contraindications, then
     write the DAG and the two relations;
  4. rebuild the open-question register LAST, before the commit.

WORKLIST NUMBERS, NOT SILENT DROPS -- four distinct losses, each counted separately
so they stay legible (spec 7). Two of them are also PERSISTED by identity, because a
count cannot be worked: the withheld objects (a curator rules on each by name) and
the unmatched subjects. The latter is written but never CLEARED here, which is the
one place this orchestrator does not mirror medrt_run -- see step 6 of _ingest for
the measurement behind that, and its caveats.
"""
import hashlib
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field

import psycopg

from drugref import classes as class_writer
from drugref import conditions as condition_writer
from drugref import interactions, questions
from drugref.ingest import medrt, mesh_concepts

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

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeshCiSummary:
    """What one slice-5b run did -- returned so a caller or test can assert on it.

    Conditions ACCUMULATE while edges and contraindications are REBUILT, so the two
    condition numbers are reported separately rather than as one ambiguous count.

    `conditions_registered` is what THIS RUN put in the registry -- the referenced
    objects plus their descendant closure -- and is deliberately NOT called
    "in_release": MeSH 2026 defines ~30,000 descriptors, of which this slice
    registers the 5,190 that a contraindication can reach. Naming it after the
    release would invite a reader to check it against MeSH's own record count and
    conclude the ingest had lost 25,000 records.

    The four worklist numbers are reported, never swallowed:
      * unmatched_subject_rxcuis  -- the rule's subject is carried by no moiety
      * withheld_class_objects    -- CI_ChemClass objects that name a CLASS
      * unresolved_object_codes   -- M-codes MeSH no longer defines
      * non_mesh_objects          -- objects outside the MeSH namespace (MED-RT EXT)
    """
    conditions_registered: int
    conditions_added: int
    condition_parent_edges: int
    condition_contraindications: int
    moiety_contraindications: int
    unmatched_subject_rxcuis: int
    withheld_class_objects: int
    unresolved_object_codes: int
    non_mesh_objects: int


@dataclass
class _Relations:
    """The tally of one pass over the assertions (see _write_relations).

    A mutable scratch record rather than five loose locals: the pass produces two
    row counts and three worklists, and returning them as one named thing is what
    keeps _ingest readable and stops a caller pairing them up in the wrong order.
    """
    condition_rows: int = 0
    pair_rows: int = 0
    unmatched_rxcuis: set[str] = field(default_factory=set)
    # object record_ui -> how many assertions were withheld on it, and its name.
    # Counted per OBJECT because the curator's decision is per object: "should a
    # rule naming this class expand over MeSH's structural tree?" (db/016).
    withheld: Counter[str] = field(default_factory=Counter)
    withheld_names: dict[str, str] = field(default_factory=dict)


def _checksum(*paths) -> str:
    """One checksum over every input file, in a fixed order, so the run's provenance
    changes if ANY input changes. Chunked: the MeSH files are large and slurping them
    would undo the streaming parser's bounded memory."""
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


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
    (spec 5.1). Measured on the real release: 664 referenced descriptors -> 5,190.

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
    becomes a row, or lands in `unmatched_rxcuis` (no moiety carries the subject),
    or in `withheld` (CI_ChemClass on a chemical class), or was already counted as an
    unresolved object code by the caller. Nothing falls off the end.

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

    ON THE OBJECT COUNT, for whoever checks this against spec 7: the worklist is
    keyed on the MeSH RECORD ui, so the release's 108 withheld ConceptUIs collapse
    into 103 curator questions. Five records are named by two concepts each (D010406
    by both "Penicillins" and "Penicillin", plus D001569, D020902, D006993, D000701),
    and one record is one decision. 103 is correct; do not "fix" it by keying the
    worklist on the concept, which is the split mesh_concepts.py exists to prevent.
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
                # The CLASS arm: withheld pending curator review (db/014, db/016).
                out.withheld[record.record_ui] += 1
                out.withheld_names[record.record_ui] = record.name

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
         _checksum(medrt_path, desc_path, supp_path))).fetchone()[0]

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
    #    THE UNMATCHED SUBJECTS ARE WRITTEN BUT NEVER CLEARED, and that asymmetry is
    #    the one place this orchestrator does not mirror medrt_run. Both open their
    #    runs under source 'MED-RT', and ingest_unmatched_ingredient is rebuilt PER
    #    SOURCE, so the two cannot both own it:
    #
    #      * CLEARING would destroy medrt_run's list. Measured on the real
    #        2026.07.06 release through the real gate: MED-RT classifies 6,012
    #        ingredients and states contraindications for 3,757, and 2,271 of the
    #        classified are not CI subjects at all. Those 2,271 rows are medrt_run's
    #        alone, and a clear here would drop them.
    #      * NOT WRITING would lose real rules. 16 CI subjects are never classified
    #        by MED-RT, so medrt_run -- which builds its list from MEMBERSHIP
    #        assertions -- can never record them. Three (221083 sulfur colloidal,
    #        5924 inulin, 89767 colloid sulfur) are also outside the moiety registry,
    #        one CI_with rule each. Counting those in a summary and a log line is
    #        exactly the "number that vanishes when the process exits" that spec 7
    #        exists to prevent.
    #
    #    So: write, deduped, and leave the clearing to medrt_run. THE HONEST CAVEAT,
    #    because a half-fix documented as complete is worse than no fix:
    #      * ORDER-DEPENDENT. medrt_run's clear is scoped by source, so it removes
    #        these rows too, and cannot re-add them (they are not classified). Those
    #        3 rules are therefore absent from the gap view between a medrt_run and
    #        the next run of this orchestrator.
    #      * Rows ACCUMULATE across consecutive runs of this orchestrator, since each
    #        run inserts under its own ingest_run id and only medrt_run collects the
    #        garbage. gap_unmatched_ingredient is DISTINCT ON (rxcui), so the view a
    #        curator reads is unaffected; the table is not.
    #    Issue #39 tracks the real fix -- a discriminator, so each writer can rebuild
    #    its own rows without touching the other's.
    class_writer.add_unmatched_ingredients(conn, sorted(rel.unmatched_rxcuis), run_id)
    interactions.record_unresolved_ci_objects(
        conn,
        [(SOURCE, PAIR_PREDICATE, OBJECT_SOURCE, code, rel.withheld_names[code], count)
         for code, count in sorted(rel.withheld.items())],
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
        unresolved_object_codes=unresolved_object_codes,
        non_mesh_objects=parsed.non_mesh_ci_objects)
