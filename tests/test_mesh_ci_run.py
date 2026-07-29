"""End-to-end slice-5b ingest against the committed fixtures.

Every number here is a fact about the REAL releases the fixtures were extracted
from (MED-RT 2026.07.06, MeSH 2026), not about anything this suite invented -- see
tests/fixtures/make_mesh_ci_subset.py, which derives the MeSH subset from the MED-RT
subset precisely so the two cannot drift apart and quietly resolve nothing.

Uses an autouse TRUNCATE fixture for the reason the other orchestrator tests do:
ingest_mesh_contraindications commits internally, so it escapes conftest's
rollback-based isolation.
"""
import pathlib

import psycopg
import pytest

from drugref import ids
from drugref.ingest import mesh_ci_run, run

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
UNII_FIX = FIXTURES / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")

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


@pytest.fixture(autouse=True)
def _clean(conn):
    conn.execute(
        "TRUNCATE drugref.moiety_condition_contraindication, "
        "drugref.moiety_contraindication, drugref.ingest_unresolved_ci_object, "
        "drugref.condition_parent, drugref.condition, "
        "drugref.open_question, drugref.class_contraindication, "
        "drugref.class_membership, drugref.class_parent, drugref.substance_class, "
        "drugref.identity_claim, drugref.substance_moiety, drugref.ingest_run "
        "RESTART IDENTITY CASCADE")
    conn.commit()


@pytest.fixture
def seeded_moieties(conn):
    """The slice-1 moiety registry this slice joins against.

    Built by running the real identity ingest over unii_subset.tsv, exactly as
    tests/test_medrt_run.py's `seeded` does, rather than by hand-inserting rows: the
    subject bridge reads RXNORM_IN claims and the object bridge reads UNII claims,
    and both are things ingest_unii produces. Seeding them directly would test the
    orchestrator against a registry no ingest could actually build.

    It carries all four subject RxCUIs the MED-RT subset asserts against except
    ibuprofen (5640), which is deliberately absent so the unmatched-subject path is
    exercised, and it carries pimozide, which is the CI_ChemClass OBJECT.
    """
    run.ingest_unii(conn, unii_path=UNII_FIX,
                    crosswalk_path=DATA / "usan_inn_crosswalk.tsv",
                    allowlist_path=DATA / "legacy_allowlist.tsv",
                    upstream_release="2026-07")
    return conn


def _run(conn):
    return mesh_ci_run.ingest_mesh_contraindications(
        conn,
        medrt_path=FIXTURES / "medrt_subset.xml",
        desc_path=FIXTURES / "mesh_ci_desc_subset.xml",
        supp_path=FIXTURES / "mesh_ci_supp_subset.xml",
        upstream_release="test")


def _condition(conn, source_code):
    return ids.mint_condition_uuid("MeSH", source_code)


def test_ingest_reports_a_summary(conn, seeded_moieties):
    """The acceptance matrix, every number derived from the two real releases.

    The fixture states 16 MeSH-keyed contraindications: 13 CI_with and 3
    CI_ChemClass. Five of the CI_with name ibuprofen, which no moiety carries, so
    seven condition rows survive; of the three CI_ChemClass, one names Pimozide (a
    drug) and two name chemical classes.
    """
    summary = _run(conn)
    # 10 referenced conditions + the 8 tree descendants the fixture samples.
    assert summary.conditions_in_release == 18
    assert summary.conditions_added == 18
    assert summary.condition_parent_edges == 10
    # 272->Poisoning, 161/17767/321988->Drug Hypersensitivity, 161->Liver Diseases,
    # 161->G6PD Deficiency, 17767->Hypotension.
    assert summary.condition_contraindications == 7
    assert summary.moiety_contraindications == 1        # escitalopram -> pimozide
    assert summary.unmatched_subject_rxcuis == 1        # ibuprofen (RxCUI 5640)
    assert summary.withheld_class_objects == 2          # Alkalies, Organic Chemicals
    # Every object code in this fixture is defined by the 2026 MeSH release, and
    # every one is in the MeSH namespace -- so both loss counters are legitimately
    # zero here and the real-release run (not this fixture) is what exercises them.
    assert summary.unresolved_object_codes == 0
    assert summary.non_mesh_objects == 0


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
            (_condition(conn, code),)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 1


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


def test_the_registry_holds_the_descendant_closure(conn, seeded_moieties):
    """A rule names Liver Diseases; the patient is coded Chemical and Drug Induced
    Liver Injury. That descendant is NOT itself a CI object, so a registry scoped to
    referenced objects would leave the read path with nothing to find and the whole
    feature would be inert while appearing to work (spec 5.1).
    """
    _run(conn)
    named, descendant = (_condition(conn, LIVER_DISEASES),
                         _condition(conn, DRUG_INDUCED_LIVER_INJURY))
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
    assert first.condition_contraindications == second.condition_contraindications
    assert first.moiety_contraindications == second.moiety_contraindications
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone()[0] == second.condition_contraindications
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unresolved_ci_object").fetchone()[0] == 2
    # Conditions ACCUMULATE while edges and contraindications are REBUILT, which is
    # why the summary reports the two condition numbers separately.
    assert (second.conditions_in_release, second.conditions_added) == (18, 0)
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition_parent").fetchone()[0] == \
        second.condition_parent_edges


def test_condition_uuids_survive_a_rebuild(conn, seeded_moieties):
    """Immortal by determinism: a rebuild re-derives the same UUIDs, which is what
    lets the projection be dropped safely."""
    _run(conn)
    before = set(conn.execute(
        "SELECT condition_uuid FROM drugref.condition").fetchall())
    _run(conn)
    assert set(conn.execute(
        "SELECT condition_uuid FROM drugref.condition").fetchall()) == before


def test_unmatched_subjects_are_counted(conn, seeded_moieties):
    """22% of CI_with subjects do not join the gated registry. Counted, never
    dropped -- the slice-1/2a no-silent-exclude posture. Ibuprofen (5640) is the
    fixture's, and it is deliberately absent from unii_subset.tsv.
    """
    summary = _run(conn)
    assert summary.unmatched_subject_rxcuis == 1


def test_the_run_does_not_destroy_medrt_runs_worklist(conn, seeded_moieties):
    """THE REASON THIS RUN REPORTS ITS UNMATCHED SUBJECTS INSTEAD OF WRITING THEM.

    ingest_unmatched_ingredient is rebuilt PER SOURCE, and both orchestrators open
    their runs under 'MED-RT' -- so a clear-and-write here would delete medrt_run's
    list. On the real release that is strictly destructive: every one of the 3,757
    CI subjects is also a classified ingredient, so this run's list adds nothing,
    while the clear would drop 14,720 rows nothing else records.

    The marker row stands in for those 14,720: this fixture's two lists are both
    exactly {5640}, so without it the test could not tell a preserved worklist from
    a clobbered one that happens to be rewritten identically.
    """
    from drugref.ingest import medrt_run
    medrt_run.ingest_medrt(conn, medrt_path=FIXTURES / "medrt_subset.xml",
                           upstream_release="2026.07.06")
    medrt_id = conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run WHERE source = 'MED-RT' "
        "ORDER BY ingest_run_id DESC LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '99999', 'marker')",
                 (medrt_id,))
    conn.commit()

    _run(conn)

    assert {r[0] for r in conn.execute(
        "SELECT rxcui FROM drugref.ingest_unmatched_ingredient").fetchall()} == \
        {"5640", "99999"}


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
