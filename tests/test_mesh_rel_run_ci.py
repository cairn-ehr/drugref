# tests/test_mesh_rel_run_ci.py
"""End-to-end slice-5b ingest against the committed fixtures.

Every number here is a fact about the REAL releases the fixtures were extracted
from (MED-RT 2026.07.06, MeSH 2026), not about anything this suite invented -- see
tests/fixtures/make_mesh_ci_subset.py, which derives the MeSH subset from the MED-RT
subset precisely so the two cannot drift apart and quietly resolve nothing.

Exercises the CONTRAINDICATION half of the one MeSH-keyed orchestrator
(ingest/mesh_rel_run.py, whose registry machinery is shared, and
ingest/mesh_ci_relations.py, which is the pass itself). The summary is nested for the
reason spec 6.1 gives: the registry figures are one fact about one closure, so they
are stated once under `registry` while each relation family reports its own rows and
losses under its own name. The INDICATION half is next door, in
tests/test_mesh_rel_run_ind.py, which imports this module's entry point, truncate and
seeded registry rather than restating them.

THE REGISTRY FIGURES HERE ARE NOW SHAPED BY BOTH HALVES, and that is the design rather
than a leak: one closure is taken over every MeSH-keyed object at once, so registering
an indication object changes numbers a reader of this file will look for. What must
NOT move is the contraindication half's own rows -- they are what the release asserts
(spec 10) -- and test_ingest_reports_a_summary states which is which.

The setup both modules need -- the truncate, the seeded registry, the entry point --
lives in tests/mesh_rel_fixtures.py, written down once.
"""
from collections import Counter

import psycopg
import pytest

from drugref import ids
from drugref.ingest import medrt, mesh_ci_relations, mesh_concepts, mesh_rel_run
from tests import mesh_rel_fixtures
from tests.mesh_rel_fixtures import FIXTURES
from tests.mesh_rel_fixtures import condition_uuid as _condition
from tests.mesh_rel_fixtures import ingest as _run

# The four moieties whose RxCUIs the MED-RT subset states contraindications for.
PARACETAMOL = "362O9ITL9D"          # RxCUI 161
AMLODIPINE = "1J444QC288"           # RxCUI 17767
ESCITALOPRAM = "4O4S742ANY"         # RxCUI 321988 -- the CI_ChemClass subject
PIMOZIDE = "1HIZ4DL86F"             # MeSH D010868 carries this UNII -- the object

# MeSH records the fixture's assertions land on.
LIVER_DISEASES = "D008107"          # paracetamol's CI_with object
DRUG_INDUCED_LIVER_INJURY = "D056486"   # strictly BELOW it: the expansion case
ALKALIES = "D000468"                # a genuine chemical CLASS: withheld
ORGANIC_CHEMICALS = "D009930"       # ditto
PIMOZIDE_RECORD = "D010868"         # the CI_ChemClass object that IS a drug


@pytest.fixture(autouse=True)
def _clean(conn):
    mesh_rel_fixtures.truncate(conn)


@pytest.fixture
def seeded_moieties(conn):
    return mesh_rel_fixtures.seed_moieties(conn)


def test_ingest_reports_a_summary(conn, seeded_moieties):
    """The acceptance matrix, every number derived from the two real releases.

    The fixture states 17 MeSH-keyed contraindications: 14 CI_with and 3
    CI_ChemClass. SIX of the CI_with name ibuprofen, which no moiety carries
    (161 x3, 17767 x2, 272 x1, 321988 x1, 5095 x1, 5640 x6 = 14), so eight
    condition rows survive; of the three CI_ChemClass, one names Pimozide (a drug)
    and two name chemical classes.

    Halothane (slice 5b.2, RxCUI 5095) adds the 14th CI_with, but NOT a new
    referenced CI condition: its object is MeSH M0006829 (Drug Hypersensitivity),
    which 161/17767/321988 already name.

    conditions_registered had already moved once (18 -> 19) BEFORE this orchestrator
    read an indication predicate at all, and that history is kept because it is the
    easiest number here to misattribute. make_mesh_ci_subset.py's wanted set was
    widened to cover all six MeSH-keyed predicates (5b.2 review fix), which pulled
    halothane's `induces` object "Liver Failure" (MeSH M0025970) into
    mesh_ci_desc_subset.xml. Liver Failure independently turns out to be a genuine
    MeSH-tree DESCENDANT of "Liver Diseases" (161's real CI_with object) -- so the
    CI-only closure walk (_condition_closure -> mesh_concepts.descriptors_under, which
    scans every record the desc file happens to contain for tree-number containment,
    not only the ones added for CI's sake) discovered it, plus its own two sampled
    children (End Stage Liver Disease, Hepatic Encephalopathy). That was +3.
    Meanwhile the SAME regeneration's descendant-closure sample (shared cap,
    MAX_CHILDREN_TOTAL=8, now spread over 14 referenced conditions instead of 10)
    displaced two conditions that used to fit the sample -- Cough-Variant Asthma and
    Rhinitis, Allergic no longer appear in the fixture at all. That was -2. Net +1,
    verified against the actual registered condition NAMES (not just the count) by
    diffing this fixture's ingest output against the pre-fix commit's.
    """
    summary = _run(conn)
    # THE REGISTRY IS ONE FACT ABOUT ONE CLOSURE, so it is asserted once, under its
    # own tally, rather than once per relation family (spec 6.1). And since 5b.2 the
    # closure is taken over EVERY MeSH-keyed object, so these three numbers answer for
    # both halves -- which is why the contraindication figures below are the ones that
    # must not move (spec 10).
    #
    # 19 -> 22 (+3), and each one is a named indication OBJECT the CI closure had no
    # reason to hold: Drug-Related Side Effects and Adverse Reactions (D064420,
    # charcoal may_prevent), Malignant Hyperthermia (D008305, halothane may_diagnose)
    # and Unconsciousness (D014474, halothane induces). The other two indication
    # objects were already registered -- Poisoning is itself a CI_with object, and
    # Liver Failure arrived as the descendant described above -- and the three new
    # records contribute no descendants the fixture's desc subset contains.
    assert summary.registry.conditions_registered == 22
    assert summary.registry.conditions_added == 22
    # 10 -> 12 (+2), and NEITHER new edge hangs off a new leaf: both are edges MeSH
    # already asserted between records the CI closure ALREADY held, which could not be
    # written because their shared PARENT was unregistered. Drug Hypersensitivity
    # (C25.100.468, a CI_with object) and Chemical and Drug Induced Liver Injury
    # (C25.100.562, a CI descendant) both sit under C25.100 = D064420, which only the
    # indication half names. This is spec 3.6's completion -- on the real releases 11
    # of 677 CI roots gain reach and condition_subtree goes 12,311 -> 12,415, in the
    # recall-safe direction. On THIS fixture the completion is visible as edges only:
    # measured before and after, condition_subtree over CI_with roots stays 9 and
    # condition_contraindication_expanded stays 15, because D064420 is not itself
    # below any CI_with root here.
    #
    # The 10 that was there before was a coincidence worth keeping recorded: -2 edges
    # (Asthma->Cough-Variant Asthma, Rhinitis->Rhinitis, Allergic, displaced from the
    # descendant sample) +2 edges (Liver Failure->its own two sampled children) netted
    # to zero. Verified by diffing the actual (parent, child) pairs, not assumed.
    assert summary.registry.condition_parent_edges == 12
    ci = summary.contraindications
    # NOT ONE OF THE EIGHT ASSERTIONS BELOW MOVED WHEN THE REGISTRY WIDENED, which is
    # spec 10's criterion and the reason this block is worth reading as a block: the
    # DIRECT rows are what the release asserts, so a widened closure that changed one
    # of them would mean the closure had taken in more than the referenced objects and
    # their descendants.
    #
    # 272->Poisoning, 161/17767/321988/5095->Drug Hypersensitivity, 161->Liver
    # Diseases, 161->G6PD Deficiency, 17767->Hypotension.
    assert ci.condition_rows == 8
    assert ci.pair_rows == 1                            # escitalopram -> pimozide
    assert ci.unmatched_subject_rxcuis == 1             # ibuprofen (RxCUI 5640)
    assert ci.withheld_class_objects == 2               # Alkalies, Organic Chemicals
    # Pimozide resolves against this seeded registry, so it is an ingested pair and
    # not an unregistered object; the empty-registry case below is what moves this.
    assert ci.unregistered_object_substances == 0
    # No fixture assertion names a drug as its own CI_ChemClass object.
    assert ci.self_paired_assertions == 0
    # Every object code in this fixture is defined by the 2026 MeSH release, and
    # every one is in the MeSH namespace -- so both loss counters are legitimately
    # zero here and the real-release run (not this fixture) is what exercises them.
    assert ci.unresolved_object_codes == 0
    assert ci.non_mesh_objects == 0


def test_the_class_arm_is_counted_not_ingested(conn, seeded_moieties):
    """THE GUARD AGAINST THE SULFONAMIDE HAZARD. A CI_ChemClass naming a class must
    produce a worklist row and ZERO contraindication rows. Do not delete this test.

    The fixture's two class-arm objects are Alkalies (D000468) and Organic Chemicals
    (D009930) -- activated charcoal's real CI_ChemClass objects. Neither carries a
    UNII or a CAS in MeSH at all, which is exactly why neither can bridge to a
    moiety, and expanding either over MeSH's STRUCTURAL tree would make a rule on
    charcoal reach every organic compound in the registry (db/014, db/016).
    """
    _run(conn)
    withheld = dict(conn.execute(
        "SELECT object_code, object_name FROM drugref.ingest_unresolved_ci_object "
        "WHERE relationship = 'CI_ChemClass'").fetchall())
    assert withheld == {ALKALIES: "Alkalies", ORGANIC_CHEMICALS: "Organic Chemicals"}
    # Nothing was ingested for either: not as a condition-contraindication (they are
    # chemicals, not patient states) and not as a drug-drug pair.
    for code in (ALKALIES, ORGANIC_CHEMICALS):
        assert conn.execute(
            "SELECT count(*) FROM drugref.moiety_condition_contraindication "
            "WHERE object_condition_uuid = %s",
            (_condition(code),)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 1


def test_a_class_object_is_withheld_even_when_no_subject_resolves(conn):
    """THE OBJECT QUESTION DOES NOT DEPEND ON THE SUBJECT.

    Deliberately runs with NO seeded registry, so every subject is unmatched. The
    curator's question -- "should a contraindication naming this class expand over
    MeSH's structural tree?" -- is about the OBJECT, so it must still be asked. When
    the subject test came first, a class object all of whose subjects were
    unregistered vanished from the worklist entirely: on the real 2026.07.06 release
    that was 370 assertions over 99 objects instead of 405 over 103, with D000963,
    D003911, D050256 and D056747 dropped outright.

    The two worklists are separate axes, not a partition: these assertions are
    counted as withheld objects AND as unmatched subjects, because both are true.

    All THREE objects reach the worklist here, Pimozide included -- but NOT all three
    as the same KIND, and that distinction is the subject of the next test.
    """
    summary = _run(conn)
    assert {r[0] for r in conn.execute(
        "SELECT object_code FROM drugref.ingest_unresolved_ci_object").fetchall()} == \
        {ALKALIES, ORGANIC_CHEMICALS, PIMOZIDE_RECORD}
    # Nothing could be ingested, and nothing was invented to compensate.
    assert (summary.contraindications.condition_rows,
            summary.contraindications.pair_rows) == (0, 0)
    # All six subject RxCUIs in the fixture (slice 5b.2 adds halothane, 5095,
    # alongside 161/17767/272/321988/5640), including the two that reach only a
    # withheld object.
    assert summary.contraindications.unmatched_subject_rxcuis == 6


def test_an_unresolved_object_is_classified_by_the_record_not_the_failure(conn):
    """A SUBSTANCE DRUGREF DOES NOT CARRY IS NOT A CHEMICAL CLASS. Do not delete.

    Runs against an EMPTY registry, so all three CI_ChemClass objects fail to bridge
    to a moiety. The earlier design read that single failure as "therefore a class"
    and filed all three identically -- which asked a curator whether contraindications
    naming Pimozide should "be expanded to the drugs beneath it in MeSH's structural
    tree". Pimozide (D010868) is a leaf drug descriptor at D03.633.100.103.732. There
    is nothing beneath it. The question was a category error, and it also hid the real
    gap, which is that the registry carries no moiety for it.

    The discriminator was always available and simply was not consulted: the MeSH
    RECORD says which it is. Alkalies and Organic Chemicals carry only MeSH's '0'
    placeholder, which mesh.registry_keys discards, so they name no substance at all;
    Pimozide's record carries UNII 1HIZ4DL86F. Nothing about the registry decides it,
    which is why this holds even here, where the registry is empty.
    """
    summary = _run(conn)
    ci = summary.contraindications
    assert ci.withheld_class_objects == 2               # Alkalies, Organic Chemicals
    assert ci.unregistered_object_substances == 1       # Pimozide

    kinds = dict(conn.execute(
        "SELECT object_code, object_kind FROM drugref.ingest_unresolved_ci_object"
    ).fetchall())
    assert kinds == {ALKALIES: "CHEMICAL_CLASS",
                     ORGANIC_CHEMICALS: "CHEMICAL_CLASS",
                     PIMOZIDE_RECORD: "UNREGISTERED_SUBSTANCE"}

    # And the curator is asked the RIGHT question about each -- the whole point of
    # keeping the kinds apart rather than merely counting them apart.
    texts = dict(conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ci_object'").fetchall())
    assert "structural tree" in texts[f"MESH:{ALKALIES}"]
    assert "registers no moiety" in texts[f"MESH:{PIMOZIDE_RECORD}"]
    assert "be expanded to the drugs beneath it" not in texts[f"MESH:{PIMOZIDE_RECORD}"]


def test_the_moiety_arm_is_ingested_as_an_exact_pair(conn, seeded_moieties):
    """THE OTHER HALF OF THE SPLIT, and the reason the split is worth having.

    MED-RT's CI_ChemClass usually names a SPECIFIC DRUG, not a class. Escitalopram's
    real object is MeSH M0016871 = D010868 Pimozide, whose MeSH record carries UNII
    1HIZ4DL86F -- so slice 2b's UNII bridge resolves it to a registered moiety and
    the assertion becomes an EXACT drug-drug pair with nothing expanded. Without
    this test a split that withheld everything would pass the class-arm test above
    and look correct.
    """
    _run(conn)
    rows = conn.execute(
        "SELECT relationship, source FROM drugref.moiety_contraindication "
        "WHERE subject_moiety_uuid = %s AND object_moiety_uuid = %s",
        (ids.mint_moiety_uuid(ESCITALOPRAM), ids.mint_moiety_uuid(PIMOZIDE))).fetchall()
    assert rows == [("CI_ChemClass", "MED-RT")]
    # DIRECTIONAL: MED-RT states which drug the rule is about and does not assert
    # the converse, so the reverse pair must NOT have been invented.
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication "
        "WHERE subject_moiety_uuid = %s", (ids.mint_moiety_uuid(PIMOZIDE),)
    ).fetchone()[0] == 0
    # ...and it was NOT also filed as a condition: pimozide is a drug, not a state.
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition WHERE source_code = 'D010868'"
    ).fetchone()[0] == 0


def test_a_self_pair_is_counted_not_silently_skipped(conn, a_moiety, ingest_run_id):
    """MED-RT states a drug is contraindicated with ITSELF when a salt and its parent
    moiety collapse to one drugref identity. db/014's CHECK forbids storing that, so
    the orchestrator has to skip it -- but a skip nobody counts is exactly the silent
    drop spec 7 forbids, and this branch was uncounted and untested until now.

    Driven through write_contraindications directly rather than through a fixture,
    because the fixture is machine-extracted from the real release (it must not be
    hand-edited to invent an assertion) and the real release's self-pairs involve
    ingredients the subset does not carry.

    Removing the guard does not merely change this number: the insert reaches
    moiety_contraindication_not_self and takes the whole ingest down with it.
    """
    record = mesh_concepts.MeshRecord(
        concept_ui="M0016871", record_ui="D010868", record_kind="DESCRIPTOR",
        name="Pimozide", tree_numbers=(), unii=frozenset({"1HIZ4DL86F"}),
        cas=frozenset(), is_preferred_concept=True)
    assertion = medrt.MeshObjectAssertion(
        rxcui="321988", mesh_code="M0016871", relationship="CI_ChemClass")
    # Subject and object are the SAME moiety: the RxCUI resolves to it, and so does
    # the object record's UNII.
    indexes = ({"321988": [a_moiety]}, {"1HIZ4DL86F": [a_moiety]}, {})

    rel = mesh_ci_relations.write_contraindications(
        conn, [assertion], {"M0016871": record}, {}, indexes, mesh_rel_run.SOURCE,
        ingest_run_id)

    assert rel.self_pairs == 1
    assert rel.pair_rows == 0
    # Counted as a self-pair and nothing else: the object DID resolve, so it is
    # neither withheld nor unregistered, and the subject DID resolve, so it is not
    # an unmatched RxCUI either.
    assert (rel.withheld, rel.unregistered, rel.unmatched_rxcuis) == (
        Counter(), Counter(), set())
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 0


def test_the_registry_holds_the_descendant_closure(conn, seeded_moieties):
    """A rule names Liver Diseases; the patient is coded Chemical and Drug Induced
    Liver Injury. That descendant is NOT itself a CI object, so a registry scoped to
    referenced objects would leave the read path with nothing to find and the whole
    feature would be inert while appearing to work (spec 5.1).
    """
    _run(conn)
    named, descendant = (_condition(LIVER_DISEASES),
                         _condition(DRUG_INDUCED_LIVER_INJURY))
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition_parent "
        "WHERE child_condition_uuid = %s AND parent_condition_uuid = %s",
        (descendant, named)).fetchone()[0] == 1
    # The whole point, end to end: paracetamol's rule reaches the patient's code.
    expanded = conn.execute(
        "SELECT is_direct FROM drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s AND member_condition = %s",
        (ids.mint_moiety_uuid(PARACETAMOL), descendant)).fetchall()
    assert expanded == [(False,)]


def test_rerunning_replaces_rather_than_duplicates(conn, seeded_moieties):
    """Per-source rebuild: a second run must leave the same row count, not double it."""
    first = _run(conn)
    second = _run(conn)
    assert first.contraindications.condition_rows == second.contraindications.condition_rows
    assert first.contraindications.pair_rows == second.contraindications.pair_rows
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone()[0] == second.contraindications.condition_rows
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unresolved_ci_object").fetchone()[0] == 2
    # Conditions ACCUMULATE while edges and contraindications are REBUILT, which is
    # why the registry tally reports the two condition numbers separately. 22 since
    # the closure widened to cover indication objects (see test_ingest_reports_a_
    # summary for the +3); the 0 is the number that carries this test's argument, and
    # it is unmoved.
    assert (second.registry.conditions_registered,
            second.registry.conditions_added) == (22, 0)
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition_parent").fetchone()[0] == \
        second.registry.condition_parent_edges


def test_condition_uuids_survive_a_rebuild(conn, seeded_moieties):
    """Immortal by determinism: a rebuild re-derives the same UUIDs, which is what
    lets the projection be dropped safely."""
    _run(conn)
    before = set(conn.execute(
        "SELECT condition_uuid FROM drugref.condition").fetchall())
    _run(conn)
    assert set(conn.execute(
        "SELECT condition_uuid FROM drugref.condition").fetchall()) == before


def test_unmatched_subjects_are_recorded_not_only_counted(conn, seeded_moieties):
    """22% of CI_with subjects do not join the gated registry. Counted AND kept by
    identity, never dropped -- the slice-1/2a no-silent-exclude posture. Ibuprofen
    (5640) is the fixture's, and it is deliberately absent from unii_subset.tsv.

    The IDENTITY is the point. medrt_run builds its list from MEMBERSHIP assertions,
    so the 16 CI subjects the real release never classifies -- three of which are
    also outside the moiety registry -- can never reach that table by any other
    route. A summary field and a log line do not survive the process.
    """
    summary = _run(conn)
    assert summary.contraindications.unmatched_subject_rxcuis == 1
    assert [r[0] for r in conn.execute(
        "SELECT rxcui FROM drugref.ingest_unmatched_ingredient").fetchall()] == ["5640"]
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unmatched_ingredient "
        "WHERE rxcui = '5640'").fetchone()[0] == 1


def test_consecutive_runs_rebuild_rather_than_accumulate(conn, seeded_moieties):
    """#39 FIXED. This orchestrator now CLEARS its own rows before writing them.

    It could not before: ingest_unmatched_ingredient was rebuilt per SOURCE and both
    orchestrators open their runs under 'MED-RT', so a clear here would have destroyed
    medrt_run's rows. The price was that consecutive runs of this one each inserted
    under their own ingest_run id and nothing collected them until medrt_run next ran.
    db/018's `reason` gives each writer its own bucket, so the clear is safe and the
    table stops growing by its own length -- which is what "rebuildable projection"
    is supposed to mean.
    """
    _run(conn)
    _run(conn)
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient "
        "WHERE rxcui = '5640'").fetchone()[0] == 1


def test_the_run_does_not_destroy_medrt_runs_worklist(conn, seeded_moieties):
    """Half of #39's contract: this run clears `contraindication` and nothing else.

    On the real 2026.07.06 release 2,271 of MED-RT's 6,012 classified ingredients are
    not CI subjects at all -- rows this run could never rewrite, because it never sees
    those ingredients. The marker row stands in for them: this fixture's two lists are
    both exactly {5640}, so without it the test could not tell a preserved worklist
    from a clobbered one that happens to be rewritten identically.
    """
    from drugref.ingest import medrt_run
    medrt_run.ingest_medrt(conn, medrt_path=FIXTURES / "medrt_subset.xml",
                           upstream_release="2026.07.06")
    medrt_id = conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run WHERE source = 'MED-RT' "
        "ORDER BY ingest_run_id DESC LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name, reason) "
                 "VALUES (%s, '99999', 'marker', 'classification')", (medrt_id,))
    conn.commit()

    _run(conn)

    assert {r[0] for r in conn.execute(
        "SELECT rxcui FROM drugref.ingest_unmatched_ingredient").fetchall()} == \
        {"5640", "99999"}


def test_a_LATER_medrt_run_no_longer_destroys_this_runs_rows(conn, seeded_moieties):
    """THE OTHER HALF, AND THE ORDER-DEPENDENCE #39 WAS FILED FOR.

    medrt_run's clear used to be scoped by source alone, so it removed this run's rows
    too -- and could not re-add them, because it builds its list from MEMBERSHIP
    assertions and 16 CI subjects in the real release are never classified. Three of
    those (221083 sulfur colloidal, 5924 inulin, 89767 colloid sulfur) carry a
    CI_with rule each and are outside the registry, so they simply vanished from the
    worklist between a medrt_run and the next run of this orchestrator.

    Now each writer clears only its own `reason`, and the answer no longer depends on
    which orchestrator ran last.
    """
    from drugref.ingest import medrt_run
    _run(conn)
    assert ("contraindication", "5640") in conn.execute(
        "SELECT reason, rxcui FROM drugref.ingest_unmatched_ingredient").fetchall()

    medrt_run.ingest_medrt(conn, medrt_path=FIXTURES / "medrt_subset.xml",
                           upstream_release="2026.07.06")

    assert ("contraindication", "5640") in conn.execute(
        "SELECT reason, rxcui FROM drugref.ingest_unmatched_ingredient").fetchall()
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unmatched_ingredient "
        "WHERE rxcui = '5640'").fetchone()[0] == 1


def test_the_question_register_is_rebuilt(conn, seeded_moieties):
    """Every orchestrator rebuilds the register as its LAST step before commit.

    Called any earlier it would read a half-demolished registry: this run deletes
    and re-inserts the very projections the gap views select from.
    """
    _run(conn)
    keys = {r[0] for r in conn.execute(
        "SELECT gap_key FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ci_object' AND is_current").fetchall()}
    assert keys == {f"MESH:{ALKALIES}", f"MESH:{ORGANIC_CHEMICALS}"}


def test_a_failed_ingest_leaves_the_connection_usable(conn, seeded_moieties,
                                                      monkeypatch):
    """An orchestrator owns the transaction it opens, so it must also clean it up.

    Mirrors tests/test_medrt_run.py's identical test, and for the same reason: these
    orchestrators are meant to run one after another in a pipeline, so a mid-run
    failure that left the caller's connection in Postgres's aborted-transaction
    state would take the NEXT feed down with it.
    """
    from drugref import interactions

    def boom(conn, *args, **kwargs):
        # A real database error, not a Python one: only that puts the transaction
        # into the aborted state this test is about.
        conn.execute("SELECT no_such_function_exists()")

    monkeypatch.setattr(interactions, "add_condition_contraindication", boom)
    with pytest.raises(psycopg.Error):
        _run(conn)
    assert conn.execute("SELECT 1").fetchone() == (1,)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone()[0] == 0
