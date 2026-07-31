# tests/test_mesh_rel_run_ind.py
"""End-to-end slice-5b.2 ingest: the INDICATION half of the one MeSH-keyed run.

Every number here is a fact about the REAL releases the fixtures were extracted from
(MED-RT 2026.07.06, MeSH 2026), not about anything this suite invented -- see
tests/fixtures/make_mesh_ci_subset.py, whose wanted set is DERIVED from the MED-RT
subset precisely so the two files cannot drift apart and quietly resolve nothing.

The SIBLING of tests/test_mesh_rel_run_ci.py. One orchestrator (ingest/mesh_rel_run.py)
runs both halves over one condition registry -- the shape spec 6.1 requires, because
`condition_parent` edges are derived by BOTH closures and so cannot be split by a
`reason` discriminator the way ingest_unmatched_ingredient was (#39 one layer deeper).
This module asserts the indication pass (ingest/mesh_ind_relations.py) and what the
SHARED registry did when its closure widened to cover indication objects; the
contraindication half stays next door.

Split into its own file because that module already stands at CLAUDE.md rule 4's
~500-line budget. The entry point, the truncate and the seeded registry come from
tests/mesh_rel_fixtures.py rather than being copied here: one run touches one list of
tables, and a second copy of that list is the "one quantity stated twice" trap db/018's
round is the standing evidence for -- only one copy would learn about the next table.
"""
import pytest

from drugref import classes, conditions, ids
from drugref.ingest import medrt, mesh_concepts, mesh_ind_relations, mesh_rel_run
from tests import mesh_rel_fixtures
from tests.mesh_rel_fixtures import condition_uuid as _condition
from tests.mesh_rel_fixtures import ingest as _run


@pytest.fixture(autouse=True)
def _clean(conn):
    mesh_rel_fixtures.truncate(conn)


@pytest.fixture
def seeded_moieties(conn):
    return mesh_rel_fixtures.seed_moieties(conn)


# The two moieties the MED-RT subset states indications for. BOTH are also CI subjects,
# which is exactly why the unmatched worklist needs a separate `reason` bucket rather
# than a wider clear: one RxCUI can be reported by both halves of one run.
ACTIVATED_CHARCOAL = "2P3VWU3H10"        # RxCUI 272 -- may_treat + may_prevent
HALOTHANE = "UQT9G45D1P"                 # RxCUI 5095 -- induces x2 + may_diagnose

# The MeSH records the fixture's five indication assertions land on. Three of them are
# NEW to the registry, which is what makes this slice widen the shared closure.
POISONING = "D011041"                    # charcoal may_treat; ALSO a CI_with object
ADVERSE_REACTIONS = "D064420"            # charcoal may_prevent -- new
MALIGNANT_HYPERTHERMIA = "D008305"       # halothane may_diagnose -- new
UNCONSCIOUSNESS = "D014474"              # halothane induces -- new
LIVER_FAILURE = "D017093"                # halothane induces; already a CI descendant

# Two records the indication half did NOT name, and that move anyway (spec 3.6).
DRUG_HYPERSENSITIVITY = "D004342"        # a CI_with object -- C25.100.468
DRUG_INDUCED_LIVER_INJURY = "D056486"    # a CI descendant  -- C25.100.562


def _relation_counts(conn) -> tuple[int, ...]:
    """Every row count one ingest of this fixture can move, in one tuple.

    Read as a whole rather than table by table because the question a re-run has to
    answer is "did ANYTHING move", and a per-table assertion answers it only for the
    tables somebody remembered to list at the call site.
    """
    return tuple(conn.execute(f"SELECT count(*) FROM drugref.{table}").fetchone()[0]
                 for table in ("moiety_condition_indication", "moiety_induced_condition",
                               "moiety_condition_contraindication",
                               "moiety_contraindication", "condition",
                               "condition_parent", "ingest_unmatched_ingredient"))


def test_one_run_ingests_both_halves(conn, seeded_moieties):
    """ONE ingest_run, ONE registry, BOTH relations -- the shape #39 forced.

    Not a formality: two orchestrators would each clear the other's condition_parent
    edges, and no `reason` column can split an edge that both closures derive. So the
    test that matters most about this slice's structure is that a single call produces
    contraindication rows AND indication rows against a single ingest_run.
    """
    summary = _run(conn)
    assert summary.contraindications.condition_rows > 0
    assert summary.indications.indication_rows > 0
    # One run, one provenance row -- not two runs that happened to interleave.
    runs = conn.execute(
        "SELECT count(DISTINCT ingest_run) FROM ("
        "  SELECT ingest_run FROM drugref.moiety_condition_contraindication "
        "  UNION ALL SELECT ingest_run FROM drugref.moiety_condition_indication "
        "  UNION ALL SELECT ingest_run FROM drugref.moiety_induced_condition) x"
    ).fetchone()[0]
    assert runs == 1


def test_the_summary_reports_the_indication_half(conn, seeded_moieties):
    """The indication acceptance matrix, every number derived from the real releases.

    The fixture states EIGHT MeSH-keyed indication assertions, and each is a real row
    of the 2026.07.06 release:
      * activated charcoal (272) may_treat Poisoning        -> an indication row
      * activated charcoal (272) may_treat Diarrhea          -> an indication row
      * activated charcoal (272) may_prevent Drug-Related Side Effects and Adverse
        Reactions                                            -> an indication row
      * mannitol (6628) may_treat Anuria                     -> an indication row
      * mannitol (6628) may_prevent Anuria                   -> an indication row
      * halothane (5095) may_diagnose Malignant Hyperthermia -> an indication row
      * halothane (5095) induces Liver Failure               -> an INDUCED row
      * halothane (5095) induces Unconsciousness             -> an INDUCED row

    MANNITOL'S TWO ARE ONE (SUBJECT, OBJECT) PAIR, which is the whole reason it is in
    the fixture (#53): it is the only subject here whose release states two therapeutic
    predicates against a single object, so it is what makes a PAIR count differ from a
    ROW count -- see test_a_drug_both_indicated_and_contraindicated_is_COUNTED.

    All three subjects are carried by the seeded registry (ibuprofen, the fixture's one
    unregistered subject, states no indication), so nothing is lost here and the
    unmatched path is exercised by the unseeded tests below instead of by a number
    that would silently be zero for the wrong reason.
    """
    ind = _run(conn).indications
    assert ind.indication_rows == 6
    assert ind.induced_rows == 2
    assert ind.unmatched_subject_rxcuis == 0
    # No fixture assertion has a pharmacologic CLASS as its subject. The real release
    # has 193 (may_treat 100, may_prevent 90, may_diagnose 3) and the parser refuses
    # and counts them -- pinned in tests/test_medrt_indication_parser.py, on controlled
    # input, because the subset extractor keeps only RxNorm-subject assertions.
    assert ind.class_subject_assertions == 0
    # Every indication object code in this fixture is defined by the 2026 MeSH release.
    # So is every one in the REAL release: 1,528 of 1,528 resolve, measured, which is
    # why this counter exists as a fact about a release rather than as a guarantee.
    assert ind.unresolved_object_codes == 0
    # No fixture object sits in MeSH's D (Chemicals and Drugs) tree. The real release
    # has 17 such assertions over 13 records; the controlled-input test below is what
    # exercises the counter, because the subset carries none.
    assert ind.chemical_object_assertions == 0


def test_the_registry_reports_its_SCR_classes(conn, seeded_moieties):
    """A COUNT is the drift detector for scr_class, since db/019 gives it no CHECK.

    Empty here, and honestly so: the fixture's closure is 22 descriptors and not one
    supplementary record (the subset's single SCRClass-3 record, C536778, is carried to
    exercise mesh_concepts' supplementary FALLBACK and is named by no assertion). The
    real release registers 34 SCRs -- 29 class '3', 5 class '1' -- and that is what
    slice 9's release run measures this against.

    The assertion is worth having anyway: it pins the field's SHAPE (a sorted tuple of
    pairs, so a summary is comparable and reproducible) and it fails loudly if the
    closure ever starts admitting records the fixture does not contain.
    """
    assert _run(conn).registry.scr_class_counts == ()


def test_the_registry_widened_to_cover_the_indication_objects(conn, seeded_moieties):
    """WITHOUT THIS THE INDICATION HALF HAS NO OBJECTS TO POINT AT.

    An indication's object is always a condition, so unlike CI_ChemClass there is no
    second arm to fall back on: a registry scoped to CI_with objects would resolve
    Drug-Related Side Effects and Adverse Reactions to a MeSH record and then find no
    condition row for it, and the assertion would vanish through the pass's
    "not a registered condition" exit while the ingest reported success.
    """
    _run(conn)
    for code in (ADVERSE_REACTIONS, MALIGNANT_HYPERTHERMIA, UNCONSCIOUSNESS):
        assert conn.execute("SELECT count(*) FROM drugref.condition "
                            "WHERE source_code = %s", (code,)).fetchone()[0] == 1


def test_widening_the_registry_COMPLETES_edges_the_CI_closure_could_not_see(
        conn, seeded_moieties):
    """SPEC 3.6, ON THE FIXTURE. Do not read a new edge here as scope creep.

    A condition bears SEVERAL tree numbers and mesh.tree_parent_edges writes an edge
    only when BOTH endpoints are registered. Drug Hypersensitivity (C25.100.468) is a
    CI_with object and Chemical and Drug Induced Liver Injury (C25.100.562) is a CI
    descendant -- both were already registered, and both have a tree PARENT at C25.100,
    Drug-Related Side Effects and Adverse Reactions, which only the indication half
    names. The edges were missing because the registry was too narrow to see them, not
    because MeSH does not assert them.

    On the real releases this is 10 of 641 CI roots and grows condition_subtree
    11,512 -> 11,605 in the RECALL-SAFE direction (the spec's 11 of 677 / 12,311 ->
    12,415 counted the roots the RELEASE references, before the moiety gate). On this
    fixture nothing moves for the contraindication half, because the newly-registered
    parent is not itself below any CI root -- so the completion is visible here as
    edges and not as expansion.
    """
    _run(conn)
    parent = _condition(ADVERSE_REACTIONS)
    for child_code in (DRUG_HYPERSENSITIVITY, DRUG_INDUCED_LIVER_INJURY):
        assert conn.execute(
            "SELECT count(*) FROM drugref.condition_parent "
            "WHERE child_condition_uuid = %s AND parent_condition_uuid = %s",
            (_condition(child_code), parent)).fetchone()[0] == 1


def test_a_drug_both_indicated_and_contraindicated_is_COUNTED(conn, seeded_moieties):
    """THE RELEASE SAYS BOTH, 168 TIMES, AND THE ONLY HONEST PROPERTY IS THAT WE SEE IT.

    This test replaces one called `test_indications_and_contraindications_do_not_mix`,
    which asserted the opposite and could not fail. It joined the two tables on
    `c.relationship = i.relationship`, and the two vocabularies are DISJOINT BY
    CONSTRUCTION -- condition_ci_axis holds only CI_with, condition_indication_axis only
    the three therapeutic predicates -- so the join was provably empty for every input,
    including inputs that contradicted its name. This very fixture contains the case it
    claimed to exclude.

    What the 2026.07.06 release actually asserts, measured through this ingest: **168
    distinct (subject_moiety, object_condition) pairs** carry an indication AND a
    contraindication, from 175 indication rows (7 pairs hold two therapeutic
    predicates) over 154 moieties and 40 conditions -- may_treat/CI_with 140,
    may_prevent/CI_with 32, may_diagnose/CI_with 3. Carvedilol, atenolol, bisoprolol
    and metoprolol are all may_treat AND CI_with for Heart Failure; alteplase for
    Stroke; budesonide for Asthma; activated charcoal for Poisoning.
    Those are not upstream errors -- they are the distinction MeSH's descriptor grain
    cannot carry (beta-blockers treat stable chronic HFrEF and are contraindicated in
    acute decompensation) and MED-RT states both flatly, with no qualifier. #51.

    SO THIS TEST FAILS IF THE COUNTER STOPS COUNTING, which is the property worth
    pinning: the summary figure is checked against a direct query over both tables
    rather than against a literal, so a counter that silently returned 0 -- or that
    drifted to a different grain -- disagrees with the database and fails here.
    The literal is asserted too, so a run that stopped STORING the overlap (by
    withholding one side) also fails rather than passing with 0 == 0.

    THE GRAIN HALF OF THAT CLAIM ONCE OUTRAN THE FIXTURE, and closing that gap is why
    mannitol is in it (#53). Until then the fixture held exactly ONE overlapping row
    and ONE overlapping pair, so `count(DISTINCT ...)` and `count(*)` were
    indistinguishable on it: mutating the production query's `SELECT DISTINCT` away
    left this test PASSING, and only the *stops counting* half was really pinned.
    The fixture now holds 2 pairs across 3 rows -- mannitol states may_treat AND
    may_prevent against Anuria while also being CI_with it -- so the two grains
    disagree and the mutation fails here. Do not reduce the fixture to one
    therapeutic predicate per overlapping pair; that silently restores the blind spot.
    """
    summary = _run(conn)
    overlap = conn.execute(
        "SELECT count(*) FROM (SELECT DISTINCT i.subject_moiety_uuid, "
        "                             i.object_condition_uuid "
        "  FROM drugref.moiety_condition_indication i "
        "  JOIN drugref.moiety_condition_contraindication c "
        "    ON  c.subject_moiety_uuid   = i.subject_moiety_uuid "
        "    AND c.object_condition_uuid = i.object_condition_uuid) x").fetchone()[0]
    assert summary.also_contraindicated_pairs == overlap
    # Activated charcoal / Poisoning and mannitol / Anuria, both REAL upstream
    # statements rather than wiring accidents: the fixture carries MED-RT's own
    # `may_treat` + `CI_with` for RxCUI 272 against M0017099, and its `may_treat` +
    # `may_prevent` + `CI_with` for RxCUI 6628 against M0001524, all extracted from
    # the release.
    assert overlap == 2
    # AND THE TWO GRAINS GENUINELY DIFFER HERE, asserted rather than left implicit:
    # 3 joined ROWS collapse to the 2 PAIRS above, because mannitol / Anuria carries
    # two therapeutic predicates. This is the assertion that fails if the production
    # query drops its DISTINCT, so it is what makes the docstring's claim true.
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication i "
        "JOIN drugref.moiety_condition_contraindication c "
        "  ON  c.subject_moiety_uuid   = i.subject_moiety_uuid "
        "  AND c.object_condition_uuid = i.object_condition_uuid").fetchone()[0] == 3
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication i "
        "JOIN drugref.moiety_condition_contraindication c "
        "  ON  c.subject_moiety_uuid   = i.subject_moiety_uuid "
        "  AND c.object_condition_uuid = i.object_condition_uuid "
        "WHERE i.subject_moiety_uuid = %s AND i.object_condition_uuid = %s "
        "AND   i.relationship = 'may_treat' AND c.relationship = 'CI_with'",
        (ids.mint_moiety_uuid(ACTIVATED_CHARCOAL),
         _condition(POISONING))).fetchone()[0] == 1


def test_an_object_reached_through_a_SUBORDINATE_concept_is_counted(conn,
                                                                    seeded_moieties):
    """MED-RT NAMES A ConceptUI; drugref KEYS ON THE RECORD. That collapse is measured.

    A MeSH record owns several concepts, exactly one preferred. When MED-RT names a
    SUBORDINATE one, the concept can be NARROWER than the record, and the assertion is
    stored against the BROADER record -- for an indication that is the UNSAFE direction,
    because a patient coded at the broader record (or anywhere below it, since the walk
    goes up) is now offered a drug the release never named for them.

    Measured on the real releases, and RELEASE-GRAIN rather than a row count (see
    test_the_widening_counters_are_release_grain_not_row_grain, which pins that): 422 of
    18,314 assertions (2.30%) -- may_treat 340, may_prevent 80, induces 2 -- arrive
    through 90 non-preferred ConceptUIs and collapse onto 85 broader records, whether or
    not a moiety carries the subject. Most are benign synonymy ('Breast Cancer' -> Breast
    Neoplasms); a minority is genuine narrowing collapsed upward, of which the sharpest
    is M0335931 'Seizures, Focal' -> D012640 'Seizures' for eslicarbazepine, a drug
    licensed for focal-onset seizures that can AGGRAVATE generalised myoclonic and
    absence seizures. #52 tracks storing the concept_ui so a consumer can detect it.

    THE FIXTURE CARRIES A REAL ONE, which is why this needs no invented input: MED-RT
    asserts `may_prevent` for activated charcoal against M0006855 'Drug Toxicity', and
    desc2026 marks that concept PreferredConceptYN="N" on D064420 'Drug-Related Side
    Effects and Adverse Reactions' -- MeSH's own ConceptRelation calls it NRW (narrower).
    """
    ind = _run(conn).indications
    assert ind.broadened_object_assertions == 1
    # STORED, NOT WITHHELD -- the count exists so the collapse is visible, not so the
    # row is dropped. It lands on the RECORD, which is the whole grain decision.
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication i "
        "JOIN drugref.condition c ON c.condition_uuid = i.object_condition_uuid "
        "WHERE i.subject_moiety_uuid = %s AND i.relationship = 'may_prevent' "
        "AND   c.source_code = %s",
        (ids.mint_moiety_uuid(ACTIVATED_CHARCOAL),
         ADVERSE_REACTIONS)).fetchone()[0] == 1


def test_induced_states_land_in_their_own_table(conn, seeded_moieties):
    """db/019's structural split, asserted end to end.

    `induces` says the drug CAUSES the state. A consumer who forgets a `relationship`
    filter on a shared table would read "halothane treats Liver Failure" off it, which
    is why the split is a table and not a WHERE clause (spec 5.1). Halothane's two
    induces assertions are the fixture's, and both are real rows of the release.
    """
    _run(conn)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication "
        "WHERE relationship = 'induces'").fetchone()[0] == 0
    assert {r[0] for r in conn.execute(
        "SELECT c.source_code FROM drugref.moiety_induced_condition m "
        "JOIN drugref.condition c ON c.condition_uuid = m.object_condition_uuid "
        "WHERE m.subject_moiety_uuid = %s",
        (ids.mint_moiety_uuid(HALOTHANE),)).fetchall()} == {LIVER_FAILURE,
                                                            UNCONSCIOUSNESS}
    # And it licenses NO WALK: condition_indication_reach excludes induces, so an
    # induced state is not reported as something a drug is indicated for.
    assert conn.execute(
        "SELECT direct_indication_rules + generalised_indication_rules "
        "FROM drugref.condition_indication_reach WHERE condition_uuid = %s",
        (_condition(UNCONSCIOUSNESS),)).fetchone()[0] == 0


def test_may_diagnose_reaches_the_table_under_its_own_name(conn, seeded_moieties):
    """PINNED BY OBJECT CODE, because the aggregate count cannot pin it.

    `indication_rows == 3` sums three predicates, so dropping may_diagnose from
    medrt.INDICATION_RELATIONSHIPS while any other therapeutic assertion gained a row
    would leave that assertion -- and the whole suite -- green. Its sibling `induces` is
    pinned by object code below; this is the same shape applied to the predicate that
    was missing it.

    Halothane states exactly one therapeutic assertion in the fixture, and it is a real
    row of the 2026.07.06 release: may_diagnose Malignant Hyperthermia (D008305).
    Asserted as a SET rather than a count so a spurious extra predicate fails too.
    """
    _run(conn)
    assert {tuple(r) for r in conn.execute(
        "SELECT i.relationship, c.source_code "
        "FROM   drugref.moiety_condition_indication i "
        "JOIN   drugref.condition c ON c.condition_uuid = i.object_condition_uuid "
        "WHERE  i.subject_moiety_uuid = %s",
        (ids.mint_moiety_uuid(HALOTHANE),)).fetchall()} == {
        ("may_diagnose", MALIGNANT_HYPERTHERMIA)}


def test_the_read_path_reaches_a_patient_coded_below_the_rule(conn, seeded_moieties):
    """THE WHOLE POINT, END TO END, and it needs the widened registry to work.

    A patient coded Chemical and Drug Induced Liver Injury is coded FINER than any rule
    MED-RT wrote -- the common case, 3,719 conditions against 1,305 on the real release
    -- so the sound statement is the WEAKER one, found by walking UP:
    "activated charcoal is indicated for a more general form of this diagnosis".

    BOTH of charcoal's rules reach it, up two different tree numbers, and the pair is
    the point:
      * may_treat Poisoning (C25.723) -- DILI is C25.723.260, an edge slice 5b's
        registry already held;
      * may_prevent Drug-Related Side Effects and Adverse Reactions (C25.100) -- DILI
        is also C25.100.562, and THAT edge exists only because this slice widened the
        closure. Before it, the parent was unregistered and the edge unwritable.

    is_direct = false on both is the label the whole slice turns on: a weaker claim,
    never a wider one.
    """
    _run(conn)
    assert set(conn.execute(
        "SELECT object_condition, is_direct, relationship FROM "
        "drugref.indications_for_condition(%s) WHERE subject_moiety = %s",
        (_condition(DRUG_INDUCED_LIVER_INJURY),
         ids.mint_moiety_uuid(ACTIVATED_CHARCOAL))).fetchall()) == {
        (_condition(POISONING), False, "may_treat"),
        (_condition(ADVERSE_REACTIONS), False, "may_prevent")}


def test_a_rerun_changes_nothing(conn, seeded_moieties):
    """Rebuildable projection: re-ingesting the same release is idempotent.

    The clear that makes this true has to run BEFORE the pass, not after it -- a clear
    placed after the write deletes the rows the write just produced, and the failure
    would look like an empty table rather than like a doubled one.
    """
    _run(conn)
    before = _relation_counts(conn)
    _run(conn)
    assert _relation_counts(conn) == before


def test_unmatched_indication_subjects_are_persisted_under_their_own_reason(conn):
    """A THIRD BUCKET, NEVER A SHARED ONE (db/018's one-writer-per-(source, reason)).

    Deliberately runs with NO seeded registry, because all three of the fixture's
    indication subjects ARE carried by the seeded one -- so this is the only way the
    fixture can exercise the path at all, and asserting zero against the seeded
    registry would be a number that is right for a reason nobody checked.

    All three RxCUIs (272, 5095 and, since #53, 6628) land in BOTH buckets here, which
    is the case the discriminator exists for: they state contraindications and
    indications alike, and db/018 keyed the table (run, reason, rxcui) precisely so the
    second writer's row is not swallowed by ON CONFLICT DO NOTHING.
    """
    summary = _run(conn)
    assert summary.indications.unmatched_subject_rxcuis == 3
    assert {r[0] for r in conn.execute(
        "SELECT rxcui FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'indication'").fetchall()} == {"272", "5095", "6628"}
    # The identity is the point, not the count: gap_unmatched_ingredient is a query
    # over these rows, and a summary field does not survive the process.
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unmatched_ingredient "
        "WHERE rxcui IN ('272', '5095', '6628')").fetchone()[0] == 3


def test_consecutive_runs_rebuild_the_indication_bucket_rather_than_accumulate(conn):
    """#39 IN THIS SLICE'S TERMS. A writer that writes without clearing grows forever.

    The clear and the first write of this bucket landed in the same commit for exactly
    this reason: ingest_unmatched_ingredient is keyed (ingest_run, reason, rxcui), so a
    second run under a new ingest_run id inserts a second copy of every row unless the
    writer collected its own garbage first.
    """
    _run(conn)
    _run(conn)
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'indication'").fetchone()[0] == 3


def test_a_later_contraindication_clear_leaves_indication_rows_standing(conn):
    """The #39 defect, asserted in this slice's terms: clearing ONE bucket must not
    take the other's rows with it.

    Both buckets are written by one orchestrator, so this is not the two-orchestrator
    collision #39 was -- but the clear is still scoped by `reason`, and a clear that
    lost the scope would silently delete a list nothing rebuilds until the next run.
    """
    _run(conn)
    before = conn.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'indication'").fetchone()[0]
    assert before > 0
    classes.clear_source_unmatched_ingredients(conn, "MED-RT",
                                               classes.CONTRAINDICATION)
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'indication'").fetchone()[0] == before


def test_the_seventh_gap_kind_reaches_the_register(conn, seeded_moieties):
    """Diseases nothing treats are PUBLISHED, and the run's last step is what does it.

    register_from_gaps still runs LAST, after both passes: it now derives a kind that
    reads tables this very run rewrote, so calling it any earlier would register
    questions against a half-demolished registry. Poisoning carries a direct
    may_treat rule and must therefore be ABSENT from the gap; Hypotension (a C-tree
    disease this fixture's release states no indication for) must be present.
    """
    _run(conn)
    keys = {r[0] for r in conn.execute(
        "SELECT gap_key FROM drugref.open_question "
        "WHERE gap_kind = 'condition_without_indication' AND is_current").fetchall()}
    assert f"CONDITION:{_condition(POISONING)}" not in keys
    assert f"CONDITION:{_condition('D007022')}" in keys      # Hypotension
    # The register is the worklist's only source, so a kind that reaches one and not
    # the other would be a question nobody is ever shown.
    assert conn.execute(
        "SELECT count(*) FROM drugref.question_worklist "
        "WHERE gap_kind = 'condition_without_indication'").fetchone()[0] == len(keys)


def test_a_D_tree_chemical_object_is_ingested_but_counted(conn, a_moiety,
                                                          ingest_run_id):
    """0.09% OF THE THERAPEUTIC ASSERTIONS NAME A CHEMICAL, NOT A PATIENT STATE.

    17 of the 18,144 -- 14 may_treat and 3 may_prevent -- over 13 records in the
    2026.07.06 release. The denominator is every therapeutic assertion, not may_treat
    alone: 17/15,319 would be 0.11%, and the 17 are not all may_treat. LDL Cholesterol 2,
    Antioxidants 2, Prostate-Specific Antigen 2, Analgesics, Antiemetics,
    Antiparkinson Agents, Deodorants... Some are defensible treatment targets ("a
    statin may_treat LDL cholesterol") and some are upstream category errors
    ("may_treat Analgesics"), and MED-RT does not distinguish them. drugref ingests
    them -- condition.tree_numbers lets a consumer scope on the leading letter, and 5b
    already registered 18 such CI_with objects -- but REPORTS the number, because
    that split is a fact an operator should see rather than discover.

    Driven through write_indications directly rather than through a fixture, exactly as
    the self-pair test is: the subset is machine-extracted from the real release and
    must not be hand-edited to invent an assertion, and none of the 13 records is
    reachable from the subset's five assertions.

    The record is real: MED-RT's M0012591 is MeSH D008078 "Cholesterol, LDL", and both
    of its may_treat assertions (RxCUIs 2588243 and 2588259) are in the release.
    """
    record = mesh_concepts.MeshRecord(
        concept_ui="M0012591", record_ui="D008078", record_kind="DESCRIPTOR",
        name="Cholesterol, LDL",
        tree_numbers=("D04.210.500.247.808.197.244", "D10.532.515.500",
                      "D10.570.938.208.275", "D12.776.521.550.500"),
        unii=frozenset(), cas=frozenset(), is_preferred_concept=True)
    condition_uuid, _ = conditions.upsert_condition(conn, record, ingest_run_id, "MeSH")
    assertion = medrt.MeshObjectAssertion(rxcui="2588243", mesh_code="M0012591",
                                          relationship="may_treat")

    rel = mesh_ind_relations.write_indications(
        conn, [assertion], {"M0012591": record}, {"D008078": condition_uuid},
        {"2588243": [a_moiety]}, mesh_rel_run.SOURCE, ingest_run_id)

    # INGESTED -- the row exists -- AND counted. Both, not either.
    assert (rel.indication_rows, rel.chemical_object_assertions) == (1, 1)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication "
        "WHERE object_condition_uuid = %s", (condition_uuid,)).fetchone()[0] == 1


def test_the_widening_counters_are_release_grain_not_row_grain(conn, ingest_run_id):
    """BOTH WIDENING COUNTERS SIT ABOVE THE MOIETY GATE, so neither counts stored rows.

    The placement is deliberate (see write_indications: a widening is a property of the
    two VOCABULARIES, and a figure that moved with drugref's registry coverage could not
    be checked against MED-RT's own totals). It was never asserted, though, and every
    other test that touches these counters hands them a subject the registry carries --
    so the suite passed identically whether the increments sat above the gate or below
    it. Meanwhile the docstrings, the inline comments, the run's log line, db/019's
    COMMENT ON and the living record all described the numbers as counts of rows that
    were STORED, which the code has never done. Nothing could fail; the prose simply
    drifted, and it drifted in the direction that overstates what drugref holds.

    This is that missing pin, and it is deliberately the UNMATCHED-SUBJECT case: an empty
    rxcui_index means no assertion here can become a row, so a counter that still reports
    1 can only be release-grain. Move either increment below the `continue` and this test
    fails rather than the documentation quietly becoming false again.

    Both records are real. M0012591 is D008078 'Cholesterol, LDL' (a D-tree chemical,
    preferred concept); M0006855 'Drug Toxicity' is a NON-preferred concept on D064420,
    which desc2026 marks PreferredConceptYN="N" -- the same pair the subordinate-concept
    test above drives through the fixture.
    """
    chemical = mesh_concepts.MeshRecord(
        concept_ui="M0012591", record_ui="D008078", record_kind="DESCRIPTOR",
        name="Cholesterol, LDL", tree_numbers=("D10.532.515.500",),
        unii=frozenset(), cas=frozenset(), is_preferred_concept=True)
    subordinate = mesh_concepts.MeshRecord(
        concept_ui="M0006855", record_ui="D064420", record_kind="DESCRIPTOR",
        name="Drug-Related Side Effects and Adverse Reactions",
        tree_numbers=("C25.100",), unii=frozenset(), cas=frozenset(),
        is_preferred_concept=False)
    uuid_by_code = {}
    for record in (chemical, subordinate):
        uuid_by_code[record.record_ui], _ = conditions.upsert_condition(
            conn, record, ingest_run_id, "MeSH")

    rel = mesh_ind_relations.write_indications(
        conn,
        [medrt.MeshObjectAssertion(rxcui="2588243", mesh_code="M0012591",
                                   relationship="may_treat"),
         medrt.MeshObjectAssertion(rxcui="272", mesh_code="M0006855",
                                   relationship="may_prevent")],
        {"M0012591": chemical, "M0006855": subordinate},
        uuid_by_code,
        {},                                  # NO moiety carries either subject
        mesh_rel_run.SOURCE, ingest_run_id)

    # Counted, though not one row exists to be counted.
    assert rel.chemical_object_assertions == 1
    assert rel.broadened_object_assertions == 1
    # And the assertions are still reported as the loss they are -- a pre-gate counter
    # must not swallow the gate's own worklist, which is the whole no-silent-drops
    # posture (spec 7).
    assert rel.unmatched_rxcuis == {"2588243", "272"}
    assert (rel.indication_rows, rel.induced_rows) == (0, 0)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication").fetchone()[0] == 0
