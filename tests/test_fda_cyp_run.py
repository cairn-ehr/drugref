# tests/test_fda_cyp_run.py
"""The FDA-CYP orchestrator: DB-gated.

Every test here pins a DECISION from the design, not an implementation detail.
"""
import argparse
import pathlib

import psycopg
import pytest

from drugref import cli, cli_chain, cli_fda_cyp, ids, questions
from drugref.ingest import fda_cyp_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "fda_cyp_table.html"
# The real, checksum-verified page (downloads/ is gitignored). Task 8's CLI
# tests that need a genuine dateModified stamp AND a genuine table -- a full
# end-to-end ingest through the CLI -- are skipped without it, matching the
# idiom tests/test_fda_cyp_parser.py already uses for the same reason.
REAL_PAGE = pathlib.Path("downloads/FDA/fda_cyp_2026-05-29.html")


@pytest.fixture(autouse=True)
def _registry(conn):
    """Seed the real moieties a disposition test needs resolved rather than
    unresolved: bupropion (test_a_qualified_cell_writes_NO_membership) and
    cenobamate (test_a_marker_with_no_page_side_definition_does_not_abort_its_row).

    conftest.py's `_migrated` fixture applies SCHEMA ONLY -- no seed data -- so
    on a fresh connection every substance name in the fixture resolves to
    nothing. Without a registered 'bupropion' moiety its footnoted '2B6
    sensitive substrate' cell would land unresolved_substance rather than
    withheld_qualified, and the section-3 case the whole design is built
    around (a footnote that NEGATES the row it sits on) would never actually
    be exercised -- the test would pass for the wrong reason, on an empty
    registry where nothing resolves regardless of the footnote logic. Without
    a registered 'cenobamate' its CYP3A-inducer cell (markers '4' and the
    undefined letter 'b') would land unresolved_substance too, and the test
    that a marker with no page-side definition still leaves a withheld row
    with real footnote text would be exercising the WRONG disposition path.

    ONLY THESE TWO NAMES. Every other test here is resolution-shape-agnostic:
    S-mephenytoin must stay unresolved whatever else is registered (issue 128),
    and curcumin's own assertion never reads resolved_moiety_uuid, only
    disposition. Seeding more would be data nothing here reads.

    AUTOUSE rather than a `registry` parameter some tests opt into: the test
    bodies above are pinned verbatim to the design's own decisions (this
    module's docstring says so), and adding a fixture parameter to opt in
    would be a change to test code the design does not call for. Running
    before every test is harmless to the tests that do not care.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for unii, name in (("TESTUNII_BUPROPION", "bupropion"),
                       ("TESTUNII_CENOBAMATE", "cenobamate")):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
            (moiety_uuid, name, seed_run))
    conn.commit()


@pytest.fixture(autouse=True)
def _clean(conn):
    """ingest_fda_cyp COMMITS -- twice, in fact: provenance.open_run commits its
    own transaction ("THE COMMIT IS THE FEATURE", per its docstring), and the
    orchestrator's own final conn.commit() lands everything else. Both escape
    the `conn` fixture's rollback, so this module needs its own explicit
    cleanup -- conftest.py's own docstring says exactly that, and
    tests/test_gsrs_run.py's autouse `_clean` fixture is the precedent this
    mirrors.

    FOUR TABLES, NAMED EXPLICITLY, THEN CASCADE FOR THE REST. fda_cyp_assertion,
    class_membership and substance_class are what THIS module writes and what
    every test here reads back; ingest_run is their common provenance parent.
    TRUNCATE ... CASCADE is required (not merely tidy) because ingest_run is
    referenced by more than these four -- open_question's
    first_derived_ingest/last_derived_ingest among them -- and Postgres refuses
    to truncate a table something else still points at unless CASCADE says to
    follow the chain. TRUNCATE, never DELETE: it fires no row-level trigger, so
    it is the only tool that can clear an append-only table's guard-protected
    rows between tests (test_gsrs_run.py's `_clean` makes the same point about
    identity_claim). RESTART IDENTITY keeps ingest_run_id starting from 1 for
    every test, which is what makes summary.classes_in_release and friends
    reproducible run to run rather than drifting with accumulated sequence state.
    """
    yield
    conn.execute(
        "TRUNCATE drugref.fda_cyp_assertion, drugref.class_membership, "
        "drugref.substance_class, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()


def test_the_source_code_is_deterministic_and_lower_case():
    assert fda_cyp_run.source_code("CYP", "3A", "inhibitor", "strong") == "cyp:3a:inhibitor:strong"
    assert fda_cyp_run.source_code("transporter", "P-gp", "substrate", None) == "transporter:pgp:substrate"
    assert fda_cyp_run.source_code("transporter", "MATE2-K", "inhibitor", None) == "transporter:mate2k:inhibitor"


def test_the_class_name_is_source_tagged():
    """So no consumer or UI can mistake it for one of MED-RT's [MoA] classes.
    MED-RT's bracketed suffix is PUBLISHED BY MED-RT; this one is drugref's own
    label and says so.
    """
    assert fda_cyp_run.class_name("CYP", "3A", "inhibitor", "strong") == \
        "CYP3A strong inhibitor [FDA-CYP]"
    assert fda_cyp_run.class_name("transporter", "P-gp", "substrate", None) == \
        "P-gp substrate [FDA-CYP]"


@pytest.mark.usefixtures("conn")
def test_a_qualified_cell_writes_NO_membership(conn):
    """THE SECTION 3 CASE, pinned directly.

    bupropion's row asserts '2B6 sensitive substrate' while its footnote 2 says
    "Bupropion itself is not a sensitive substrate." Promoting it would make
    drugref assert the OPPOSITE of its cited source.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    membership = conn.execute(
        "SELECT count(*) FROM drugref.class_membership m "
        "JOIN drugref.substance_class c ON c.class_uuid = m.class_uuid "
        "JOIN drugref.substance_moiety s ON s.moiety_uuid = m.moiety_uuid "
        "WHERE c.source = 'FDA-CYP' AND lower(s.display_name) = 'bupropion'").fetchone()[0]
    assert membership == 0, "a footnoted cell must not become a membership"

    withheld = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) LIKE 'bupropion%' "
        "  AND disposition = 'withheld_qualified'").fetchone()[0]
    assert withheld > 0, "and it must still be recorded, with its footnote"


@pytest.mark.usefixtures("conn")
def test_every_withheld_row_carries_its_footnote_text(conn):
    """Withholding without the reason would be a drop wearing a disposition."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    missing = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE disposition = 'withheld_qualified' "
        "  AND (footnote_text IS NULL OR footnote_markers IS NULL)").fetchone()[0]
    assert missing == 0


@pytest.mark.usefixtures("conn")
def test_the_orchestrator_writes_substance_and_the_two_footnote_scope_columns(conn):
    """db/042. Every row must carry the clean `substance` fda_cyp.CypTuple
    already computed (db/039 never stored it, which is I1's defect), and the
    row-level / cell-level footnote-scope split (I2) rather than only the
    merged footnote_markers db/039 shipped.

    adefovir<sup>1</sup> is the row-level-only case (its one cell, OAT1
    substrate, carries no marker of its own); conivaptan's CYP-Mod-INH cell
    ('3A moderate inhibitor<sup>5</sup>') is the cell-level case, with no
    marker on the name itself.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")

    adefovir = conn.execute(
        "SELECT substance, row_footnote_markers, cell_footnote_markers "
        "FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) LIKE 'adefovir%' LIMIT 1").fetchone()
    assert adefovir == ("adefovir", "1", None)

    conivaptan = conn.execute(
        "SELECT substance, row_footnote_markers, cell_footnote_markers "
        "FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) = 'conivaptan' AND role = 'inhibitor' "
        "LIMIT 1").fetchone()
    assert conivaptan == ("conivaptan", None, "5")


@pytest.mark.usefixtures("conn")
def test_a_marker_with_no_page_side_definition_does_not_abort_its_row(conn):
    """cenobamate's CYP3A-inducer cell carries TWO markers: '4' (row-level, on
    the substance name) and 'b' (cell-level, and per fda_cyp.parse_footnotes's
    own docstring, never defined anywhere in FDA's Footnotes list -- design
    section 2.3's lettered 'second namespace'). The row must still land
    withheld with real footnote text, built from whichever of its markers ARE
    on file ('4'), rather than the whole ingest aborting over the one that
    is not, or the row silently losing its text because one lookup missed.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, footnote_markers, footnote_text "
        "FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) LIKE 'cenobamate%' "
        "  AND column_heading = 'CYP Mod IND' LIMIT 1").fetchone()
    assert row is not None
    disposition, footnote_markers, footnote_text = row
    assert disposition == "withheld_qualified"
    assert "b" in footnote_markers, "the cell-level lettered marker must still be recorded"
    assert footnote_text is not None
    assert "200 mg daily dose" in footnote_text, (
        "footnote 4's text, the one marker on this row that IS on file")


@pytest.mark.usefixtures("conn")
def test_S_mephenytoin_is_unresolved_and_NOT_mapped_to_mephenytoin(conn):
    """Issue 128. S-mephenytoin is the reference CYP2C19 probe substrate, and it
    is the ENANTIOMER that makes it one. Mapping it to the racemate asserts a
    stereochemistry claim FDA did not make, in the direction that ADDS membership.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance ILIKE 'S-mephenytoin%' LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "unresolved_substance"
    assert row[1] is None


@pytest.mark.usefixtures("conn")
def test_the_disposition_never_names_a_cause_drugref_inferred(conn):
    """Spec section 7.1 and the standing rule. Six recognisable categories exist
    in the residue; only the two FDA asserts are stored. Calling R-venlafaxine an
    'enantiomer' would be a chemical relationship inferred from a string prefix --
    issue 122's manufactured-cause defect.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    live = {row[0] for row in conn.execute(
        "SELECT DISTINCT disposition FROM drugref.fda_cyp_assertion").fetchall()}
    assert live <= {"member", "withheld_qualified", "unresolved_substance",
                    "combination_regimen", "non_drug_entity"}


@pytest.mark.usefixtures("conn")
def test_curcumin_resolves_as_a_moiety_AND_is_still_a_non_drug_entity(conn):
    """The independence in section 7, and it inverts the obvious assumption:
    curcumin and diosmin are two of FDA's five declared non-drugs and they DO
    resolve. So the non-drug list must be FDA's own pinned five, read from its
    prose, never inferred from a resolution failure.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) = 'curcumin' LIMIT 1").fetchone()
    assert row[0] == "non_drug_entity"
    assert "curcumin" in fda_cyp_run.NON_DRUG_ENTITIES


@pytest.mark.usefixtures("conn")
def test_grapefruit_juice_is_non_drug_entity_even_though_it_is_footnoted(conn):
    """Ruling 2, pinned on the exact substance the ruling names.

    Grapefruit juice is BOTH one of FDA's own pinned five non-drugs AND
    footnoted (marker 9, "The effect of grapefruit juice varies widely..."), so
    the two categories genuinely overlap on this one row. The disposition order
    -- non_drug_entity checked BEFORE withheld_qualified -- is what keeps it
    non_drug_entity rather than withheld_qualified; a function that checked
    footnote status first would misfile a real, distinct category as a
    plausible-looking wrong one, with no test the wiser.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, footnote_markers FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) LIKE 'grapefruit juice%' LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "non_drug_entity"
    assert row[1] is not None, "grapefruit juice IS footnoted -- the row must still say so"


@pytest.mark.usefixtures("conn")
def test_a_combination_regimen_is_never_exploded_into_its_components(conn):
    """FDA reports the role FOR THE REGIMEN. Assigning it to atazanavir or to
    ritonavir individually is an inference FDA did not make.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    rows = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance ILIKE 'atazanavir and ritonavir%'").fetchall()
    assert rows
    for disposition, moiety in rows:
        assert disposition == "combination_regimen"
        assert moiety is None


@pytest.mark.usefixtures("conn")
def test_a_near_name_never_upgrades_a_rows_disposition(conn):
    """registry_near_name is EVIDENCE, never coverage (design section 7.1).
    Near-name DETECTION is deliberately NOT implemented in this slice -- it is
    filed as issue 129 -- so ingest_fda_cyp never writes a non-NULL value into
    the column at all, and the column ships NULL throughout.

    THIS TEST CONSTRUCTS ITS OWN ROW rather than asking the ingest to produce
    one, and that is the point, not a shortcut: the PREVIOUS version of this
    test filtered `registry_near_name IS NOT NULL AND disposition = 'member'`,
    which can never match while the ingest writes the column unconditionally
    NULL -- a green test asserting the inverse of its own name, and this repo
    has already lost a round to exactly that shape. Inserting a row that DOES
    carry a near name -- something issue 129's future detector will do -- is
    the only way to make the assertion able to fail.

    WHAT THIS ACTUALLY PINS, stated precisely rather than aspirationally: the
    SCHEMA and the gap view treat `registry_near_name` as inert evidence.
    Nothing in this test ever calls `_classify` or any other ingest code on
    the hand-inserted row -- it is written directly with `conn.execute`, after
    the one real ingest above it has already finished -- so a FUTURE change to
    `_classify`'s own logic could not be exercised by this test at all; it
    would need its own test against a real fixture row. The two assertions
    this one DOES protect are narrower and real: (1) a roundtrip check that
    inserting a row with disposition='unresolved_substance' and a near name
    reads back unchanged (protects nothing on its own -- nothing between the
    INSERT and the SELECT could have altered it -- but pins the query used to
    read it back), and (2) the actually load-bearing assertion: the gap view
    still SURFACES a near-name-bearing row on the worklist rather than
    filtering it out because the column is non-NULL. That second assertion
    would catch a future gap-view predicate that mistook "has a near name" for
    "has been looked at" and dropped the row from
    gap_fda_cyp_unadjudicated -- which is a real, specific regression this
    test can actually detect.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    run_id = conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run "
        "WHERE source = 'FDA-CYP' LIMIT 1").fetchone()[0]
    a_class_uuid = conn.execute(
        "SELECT class_uuid FROM drugref.substance_class "
        "WHERE source = 'FDA-CYP' LIMIT 1").fetchone()[0]

    # row_ordinal 9999 cannot collide with a real row (the fixture's rows are
    # numbered from 1); the rest is deliberately synthetic too -- this row
    # asserts nothing about any real FDA substance, only about how the schema
    # and gap view treat registry_near_name in isolation.
    conn.execute(
        "INSERT INTO drugref.fda_cyp_assertion "
        "(ingest_run, source, row_ordinal, raw_substance, resolved_moiety_uuid, "
        " column_heading, raw_cell, system, pathway, role, potency, class_uuid, "
        " footnote_markers, footnote_text, registry_near_name, disposition) "
        "VALUES (%s, 'FDA-CYP', 9999, 'testonly nearname substance', NULL, "
        " 'CYP Strg INH', '3A strong inhibitor', 'CYP', '3A', 'inhibitor', "
        " 'strong', %s, NULL, NULL, 'testonly registry candidate', "
        " 'unresolved_substance')",
        (run_id, a_class_uuid))

    row = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance = 'testonly nearname substance'").fetchone()
    assert row == ("unresolved_substance", None), (
        "a row carrying a near name must stay exactly as unresolved as one "
        "without -- a near name is evidence, never a resolution")

    on_worklist = conn.execute(
        "SELECT registry_near_name FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE raw_substance = 'testonly nearname substance'").fetchone()
    assert on_worklist is not None, (
        "the row must still raise its question -- a near name must not "
        "quietly remove a row from the unadjudicated worklist")
    assert on_worklist[0] == "testonly registry candidate"


@pytest.mark.usefixtures("conn")
def test_all_classes_are_minted_even_when_every_member_is_withheld(conn):
    """Spec section 4.2. A class whose only members are withheld still exists, so
    a withheld row can name the class it WOULD have joined, and a zero-member
    class is distinguishable from a band FDA never defined.
    """
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                         upstream_release="2026-05-29T14:00")
    minted = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source = 'FDA-CYP'").fetchone()[0]
    assert minted == summary.classes_in_release
    orphaned = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE disposition = 'withheld_qualified' AND class_uuid IS NULL").fetchone()[0]
    assert orphaned == 0, "a withheld row must still name the class it would have joined"


@pytest.mark.usefixtures("conn")
def test_no_class_parent_edge_is_written(conn):
    """FDA publishes no hierarchy; inventing one and inheriting advice along it
    is the rejected alternative in section 4.2.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    edges = conn.execute(
        "SELECT count(*) FROM drugref.class_parent p "
        "JOIN drugref.substance_class c ON c.class_uuid = p.child_class_uuid "
        "WHERE c.source = 'FDA-CYP'").fetchone()[0]
    assert edges == 0


@pytest.mark.usefixtures("conn")
def test_a_second_run_rebuilds_rather_than_duplicating(conn):
    first = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    second = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    assert first.memberships_written == second.memberships_written
    rows = conn.execute("SELECT count(*) FROM drugref.fda_cyp_assertion").fetchone()[0]
    assert rows == second.assertions_written


@pytest.mark.usefixtures("conn")
def test_clearing_FDA_CYP_touches_no_other_sources_classes(conn):
    """Per-source rebuild safety, pinned rather than argued. class_membership has
    no source column of its own, so the clear is scoped through ingest_run.

    A MED-RT class AND a MED-RT class_membership edge are seeded FIRST, and
    that seed is what makes this test able to fail -- on a schema-only test
    database that never loads any other source, 'before' and 'after' were
    both 0 regardless of whether the clear was correctly scoped, so the
    comparison held even if fda_cyp_run's clear touched every source's rows,
    not only its own.

    BOTH A CLASS AND A MEMBERSHIP, because they are not equally at risk.
    substance_class rows are never DELETEd by anything in this codebase --
    class_uuid is immortal and classes.upsert_class only ever INSERTs or
    refreshes one (classes.py's own module docstring) -- so a substance_class
    count alone cannot distinguish correct scoping from a scoping bug: it
    would read the same either way. class_membership is what
    classes.clear_source_edges actually DELETEs on every re-ingest, so it is
    where a wrong source string would actually show up. Verified directly
    (not merely argued): temporarily rewriting the clear call to scope on
    'MED-RT' instead of 'FDA-CYP' left the substance_class assertion below
    passing unchanged, while the class_membership assertion caught it --
    the seeded edge dropped from 1 row to 0.
    """
    from tests.test_curated_overlay import _a_class

    other_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'test', 'test', 'medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    other_class = _a_class(conn, other_run)
    other_moiety = ids.mint_moiety_uuid("TESTUNII_OTHER_SOURCE_SCOPE")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
        (other_moiety, "scopetestdrug", other_run))
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, 'has_MoA', %s)", (other_moiety, other_class, other_run))
    conn.commit()

    before_classes = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source <> 'FDA-CYP'").fetchone()[0]
    before_membership = conn.execute(
        "SELECT count(*) FROM drugref.class_membership "
        "WHERE class_uuid = %s", (other_class,)).fetchone()[0]
    assert (before_classes, before_membership) == (1, 1), (
        "the seed above must be the only non-FDA-CYP class/membership present")

    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")

    after_classes = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source <> 'FDA-CYP'").fetchone()[0]
    after_membership = conn.execute(
        "SELECT count(*) FROM drugref.class_membership "
        "WHERE class_uuid = %s", (other_class,)).fetchone()[0]
    assert before_classes == after_classes
    assert after_membership == 1, (
        "the MED-RT membership edge must survive two FDA-CYP re-ingests "
        "untouched -- this is the assertion a scoping regression actually breaks")


@pytest.mark.usefixtures("conn")
def test_this_slice_creates_no_interaction_content(conn):
    """Section 9's refusal, checked rather than trusted: 20 strong CYP3A
    inhibitors x 40 sensitive CYP3A substrates would be 800 pairs no source
    asserts.
    """
    before = conn.execute("SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()[0]
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    after = conn.execute("SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()[0]
    assert before == after
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_contraindication "
        "WHERE source = 'FDA-CYP'").fetchone()[0] == 0


# ---- Task 7: the gap view wired into the question register -----------------


@pytest.mark.usefixtures("conn")
def test_every_unadjudicated_tuple_raises_exactly_one_question(conn):
    """The view's grain IS the gap_key's grain (#41). Grouping coarser folds two
    independent facts onto one immortal question_uuid; finer mints two questions
    for one fact.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    gaps = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated").fetchone()[0]
    questions_ = conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated'").fetchone()[0]
    assert gaps > 0
    assert questions_ == gaps


@pytest.mark.usefixtures("conn")
def test_a_members_row_raises_no_question(conn):
    """A membership drugref already wrote asks nobody anything."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    leaked = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE disposition = 'member'").fetchone()[0]
    assert leaked == 0


@pytest.mark.usefixtures("conn")
def test_the_question_text_states_the_actual_reason(conn):
    """Four dispositions reach this view and they are four different questions.
    A single text asserting one reason would be #122's defect again -- a message
    asserting a cause it has not confirmed.

    ALL FOUR branches pinned, not two: the review of this task's first pass
    found the CASE's `ELSE` branch (non_drug_entity) was untested by name, which
    is exactly how it went unnoticed that `ELSE` itself contradicted this file's
    own no-ELSE rule (see questions.py's `fda_cyp_unadjudicated` entry, and
    unresolved_ci_object's comment above it). A wording assertion per branch is
    what would have caught a branch silently swallowed by the wrong one.

    JOINED ON DISPOSITION, not four independent `any(...)` scans over the WHOLE
    text list. The previous version of this test only checked that each phrase
    appeared SOMEWHERE among all the texts, which cannot tell "this disposition's
    branch says the right thing" from "some OTHER branch's wording leaked onto
    this disposition's row" -- a branch silently emitting another branch's text
    (the CASE matching the wrong WHEN, or two branches accidentally sharing a
    string) would still pass every `any(...)` here. Joining question_text back to
    the gap view's own `disposition` column (via the same gap_key format
    questions.py's key_sql builds -- pinned directly, on
    test_the_subject_grain_collapses_repeated_cells_for_one_substance's own
    precedent of matching against `gap_key LIKE 'FDACYP:...'`) makes the
    assertion fail if a phrase shows up under the wrong disposition's own rows.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    rows = conn.execute(
        "SELECT g.disposition, q.question_text "
        "FROM drugref.gap_fda_cyp_unadjudicated g "
        "JOIN drugref.open_question q "
        "  ON q.gap_kind = 'fda_cyp_unadjudicated' "
        " AND q.gap_key = 'FDACYP:' || g.substance || '|' || "
        "     COALESCE(g.column_heading, '') || '|' || COALESCE(g.pathway, '')"
    ).fetchall()
    texts_by_disposition: dict[str, list[str]] = {}
    for disposition, text in rows:
        texts_by_disposition.setdefault(disposition, []).append(text.lower())

    for disposition, phrase in (
            ("withheld_qualified", "footnote"),
            ("unresolved_substance", "moiety"),
            ("combination_regimen", "regimen"),
            ("non_drug_entity", "not drugs")):
        texts = texts_by_disposition.get(disposition, [])
        assert texts, f"no gap row found for disposition {disposition!r}"
        assert any(phrase in t for t in texts), (
            f"{disposition}'s OWN question text must contain {phrase!r}; "
            f"got {texts!r}")


@pytest.mark.usefixtures("conn")
def test_the_gap_key_and_text_quote_the_clean_substance_not_footnote_markers(conn):
    """I1. db/039's INSERT never stored fda_cyp.CypTuple's clean `substance`, so
    the gap_key and question text were built from raw_substance -- FDA's PRINTED
    form, footnote markers and all. 'ritonavir 14, 15, 16' produced the gap_key
    'FDACYP:ritonavir 14, 15, 16||' and the text "...is FDA's ritonavir 14, 15,
    16?" -- the exact defect that gave this slice its headline case, reproduced
    in the human-readable output db/039 shipped. Worse than cosmetic:
    question_uuid = uuid5(gap_kind, gap_key) is IMMORTAL, so keying on FDA's own
    footnote NUMBERING means a footnote renumbering changes a question's identity
    for a reason unrelated to the substance.

    ritonavir (unresolved -- not seeded) and grapefruit juice (non_drug_entity
    AND footnoted, marker 9) both pin it: neither the key nor the text may carry
    trailing digits.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    rows = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated' "
        "  AND (gap_key LIKE 'FDACYP:ritonavir%' "
        "    OR gap_key LIKE 'FDACYP:grapefruit juice%')").fetchall()
    assert len(rows) == 2, "both ritonavir and grapefruit juice must raise a question"
    for gap_key, text in rows:
        assert gap_key in ("FDACYP:ritonavir||", "FDACYP:grapefruit juice||"), (
            f"gap_key must quote the clean name, no footnote digits: {gap_key!r}")
        assert "14" not in text and "15" not in text and "16" not in text, (
            f"ritonavir's text must not carry its footnote markers: {text!r}")
        assert " 9" not in text and "juice 9" not in text, (
            f"grapefruit juice's text must not carry its footnote marker: {text!r}")


@pytest.mark.usefixtures("conn")
def test_the_withheld_text_only_asserts_cell_attachment_when_the_marker_is_cell_level(conn):
    """I2. cenobamate carries BOTH footnote positions on ONE row: <sup>4</sup>
    glued to the NAME (row-level -- a claim about cenobamate, not about any one
    cell) and the lettered <sup>b</sup> attached INSIDE the CYP-Mod-IND cell
    (cell-level -- a claim about that cell specifically). db/039's merged
    footnote_markers could not tell them apart, so BOTH of cenobamate's withheld
    cells got the same wording: "Does FDA's footnote on cenobamate (COLUMN,
    PATHWAY) narrow or NEGATE the membership its row states?" -- asserting a
    cell-specific attachment FDA never made for the CYP-Mod-INH cell, whose only
    footnote is glued to the name.

    * CYP Mod INH / 2C19 (row-marker 4 only, no cell-level marker on THIS cell)
      must get the NAME-level wording: it does not claim the footnote is
      specifically about this cell.
    * CYP Mod IND / 3A (cell-level marker 'b' present) must get wording that
      DOES assert cell attachment, because here the attachment is real.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    rows = dict(conn.execute(
        "SELECT column_heading, question_text FROM drugref.gap_fda_cyp_unadjudicated g "
        "JOIN drugref.open_question q "
        "  ON q.gap_kind = 'fda_cyp_unadjudicated' "
        " AND q.gap_key = 'FDACYP:' || g.substance || '|' || "
        "     COALESCE(g.column_heading, '') || '|' || COALESCE(g.pathway, '') "
        "WHERE g.substance = 'cenobamate'").fetchall())
    assert set(rows) == {"CYP Mod INH", "CYP Mod IND"}

    name_level_text = rows["CYP Mod INH"].lower()
    assert "carries footnote" in name_level_text, (
        f"the row-marker-only cell must use the name-level wording: {name_level_text!r}")
    assert "cell for cenobamate" not in name_level_text

    cell_level_text = rows["CYP Mod IND"].lower()
    assert "cell for cenobamate" in cell_level_text, (
        f"the cell-marker cell must assert real attachment: {cell_level_text!r}")


@pytest.mark.usefixtures("conn")
def test_the_subject_grain_collapses_repeated_cells_for_one_substance(conn):
    """db/040. Three of the four dispositions are graded per SUBJECT (source,
    raw_substance, disposition), not per cell -- db/039 originally grouped all
    four the same way, and issue 41's review measured that as 71 questions
    minted on the real page for 55 actual facts. This is the collapse, pinned
    directly against fixture rows that already exercise it without a synthetic
    INSERT:

    * rifampin is unseeded (unresolved_substance) and appears against EIGHT
      distinct (column_heading, pathway) cells in the fixture -- two CYP
      Strg IND pathways, four CYP Mod IND pathways, and two TRNSP INH
      pathways. Under the old per-cell grain that is eight questions asking
      "which moiety is rifampin?" under eight different UUIDs; the subject
      grain must raise exactly ONE.
    * 'atazanavir and ritonavir' (combination_regimen) appears against TWO
      pathways (OATP1B1, OATP1B3) of the SAME column_heading. Same collapse,
      smaller fixture footprint.

    bupropion is the CONTRAST, included so this test cannot pass by a grain
    fix that accidentally collapses EVERYTHING: its disposition is
    withheld_qualified, which keeps the per-CELL grain (db/040's header
    explains why -- each footnoted cell is its own adjudication), and it
    carries two footnoted cells (CYP Strg INH/2D6, CYP SENS SUB/2B6) that
    must stay two separate gap rows and two separate questions.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")

    rifampin_cells = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) = 'rifampin'").fetchone()[0]
    assert rifampin_cells > 1, "the fixture must still exercise more than one cell"
    rifampin_gaps = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE lower(raw_substance) = 'rifampin'").fetchone()[0]
    assert rifampin_gaps == 1, "one substance, one question -- not one per cell"
    rifampin_questions = conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated' "
        "  AND gap_key LIKE 'FDACYP:rifampin|%'").fetchone()[0]
    assert rifampin_questions == 1

    regimen_cells = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance = 'atazanavir and ritonavir'").fetchone()[0]
    assert regimen_cells > 1
    regimen_gaps = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE raw_substance = 'atazanavir and ritonavir'").fetchone()[0]
    assert regimen_gaps == 1

    bupropion_cells = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) LIKE 'bupropion%' "
        "  AND disposition = 'withheld_qualified'").fetchone()[0]
    assert bupropion_cells > 1, "the fixture must still exercise more than one cell"
    bupropion_gaps = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE lower(raw_substance) LIKE 'bupropion%'").fetchone()[0]
    assert bupropion_gaps == bupropion_cells, (
        "withheld_qualified keeps the per-CELL grain -- it must NOT collapse "
        "the way the other three dispositions do")


@pytest.mark.usefixtures("conn")
def test_the_subject_half_does_not_attribute_an_arbitrary_cells_text_to_the_whole_substance(
        conn):
    """db/041's subject half (unresolved_substance / combination_regimen /
    non_drug_entity) projected `max(raw_cell)` and `max(footnote_text)` across
    EVERY row sharing a substance -- for rifampin (8 cells) that picks ONE
    arbitrary cell's raw text via whichever value happens to sort highest and
    attributes it to the whole substance, in columns a curator might reasonably
    read as meaning something. Neither column is read by any of the three
    subject-half branches in questions.py's CASE, so db/042 projects NULL for
    both there -- matching db/040's own reasoning for why column_heading and
    pathway are NULL in this half: the honest value for a fact this half is
    not asking about is NULL, not an arbitrary survivor of max().

    registry_near_name is the CONTRAST: unresolved_substance's own branch DOES
    read it (test_a_near_name_never_upgrades_a_rows_disposition depends on the
    view still surfacing it), so it must NOT be nulled the same way.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")

    subject_half = conn.execute(
        "SELECT raw_cell, footnote_text FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE lower(raw_substance) = 'rifampin'").fetchone()
    assert subject_half == (None, None), (
        "the subject half must not attribute an arbitrary cell's raw_cell/"
        f"footnote_text to the whole substance -- got {subject_half!r}")

    cell_half = conn.execute(
        "SELECT raw_cell, footnote_text FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE lower(raw_substance) LIKE 'bupropion%' LIMIT 1").fetchone()
    assert cell_half[0] is not None and cell_half[1] is not None, (
        "the CELL half (withheld_qualified) is about one specific cell, so its "
        "raw_cell/footnote_text stay real, unlike the subject half's")


@pytest.mark.usefixtures("conn")
def test_an_unrecognised_disposition_aborts_loudly_rather_than_vanishing(conn):
    """db/041, and the whole reason it exists.

    db/040's first version of the gap view named the subject half's
    dispositions POSITIVELY (`IN ('unresolved_substance', 'combination_regimen',
    'non_drug_entity')`). A future sixth disposition -- foreseeable, not
    hypothetical: db/035 added a whole gap kind mid-plan, and
    db/035 added a whole gap kind mid-plan -- would have matched neither that
    list nor the cell half's `= 'withheld_qualified'`, so it would have produced
    ZERO gap-view rows. SILENCE would have been the failure: `drugref ingest
    fda-cyp` reporting success, the view's own "ABSENCE OF A ROW IS NOT
    COVERAGE" comment being quietly false for that row, and questions.py's CASE
    comment ("aborts the ingest loudly") never actually being exercised --
    issues 74/66/76's gate-that-never-fires, beside issue 122's comment
    asserting a property the code did not have.

    db/041's negative predicate (`NOT IN ('member', 'withheld_qualified')`)
    means an unrecognised disposition now reaches the subject half, then
    questions.py's CASE (which has no ELSE, on unresolved_ci_object's own
    precedent), evaluates to SQL NULL, and trips open_question.question_text's
    NOT NULL constraint -- loud and specific, not silent.

    The CHECK is dropped for this test only, inside the rollback-scoped `conn`
    fixture's own transaction (ingest_fda_cyp's two commits happen first and
    are cleaned up by this module's autouse `_clean` truncate, same as every
    other test here; the DROP CONSTRAINT and the bad INSERT that follows are
    never committed, so no other test or session ever sees a relaxed
    constraint).
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    run_id = conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run "
        "WHERE source = 'FDA-CYP' LIMIT 1").fetchone()[0]
    a_class_uuid = conn.execute(
        "SELECT class_uuid FROM drugref.substance_class "
        "WHERE source = 'FDA-CYP' LIMIT 1").fetchone()[0]

    # Dropped, not widened: a widen-and-reword of the CHECK would itself need
    # verifying against the live catalog (ids.py's own lesson, and db/039's own
    # "copied VERBATIM" discipline), which is machinery this test does not need
    # -- it only needs ONE row the CHECK would otherwise refuse to exist,
    # inside a transaction nothing here ever commits.
    conn.execute(
        "ALTER TABLE drugref.fda_cyp_assertion "
        "DROP CONSTRAINT fda_cyp_assertion_disposition")
    conn.execute(
        "INSERT INTO drugref.fda_cyp_assertion "
        "(ingest_run, source, row_ordinal, raw_substance, resolved_moiety_uuid, "
        " column_heading, raw_cell, system, pathway, role, potency, class_uuid, "
        " footnote_markers, footnote_text, registry_near_name, disposition) "
        "VALUES (%s, 'FDA-CYP', 9998, 'testonly sixth disposition', NULL, "
        " 'CYP Strg INH', '3A strong inhibitor', 'CYP', '3A', 'inhibitor', "
        " 'strong', %s, NULL, NULL, NULL, 'testonly_sixth_disposition')",
        (run_id, a_class_uuid))

    with pytest.raises(psycopg.errors.NotNullViolation):
        questions.register_from_gaps(conn, run_id)

    # Postgres aborts the WHOLE transaction on that error, refusing any further
    # statement until it sees a ROLLBACK -- this module's own autouse `_clean`
    # fixture runs a TRUNCATE in its teardown on this same connection, so without
    # this the teardown itself would fail with InFailedSqlTransaction. Rolling
    # back here only undoes the uncommitted DROP CONSTRAINT and INSERT above;
    # ingest_fda_cyp's two commits already landed the fixture's real rows, which
    # is exactly what `_clean` still needs to find and truncate.
    conn.rollback()


# ---- Task 8: the CLI subcommand ---------------------------------------------
#
# `drugref ingest fda-cyp --page <path> [--release <upstream_release>]`.
# UNLIKE every other source, --release is OPTIONAL: fda-cyp's release comes
# from the page's own dateModified stamp (fda_cyp.parse_release) unless the
# operator overrides it, and an override that disagrees with the page is
# refused rather than silently preferred. cli.STEPS' generic loop makes
# --release required for every entry (test_ingest_subcommand_requires_a_release
# in tests/test_cli.py pins that for `unii`), which is exactly why fda-cyp is
# NOT a STEPS entry and gets its own subparser instead -- see cli.py's
# build_parser and _handle_fda_cyp.


def test_the_fda_cyp_subcommand_makes_release_optional_but_requires_page():
    """--page has no way to be derived, so it stays required like every other
    source's input flag. --release does have a derivation (the page's own
    stamp), so, unlike every STEPS entry, it must not be mandatory.
    """
    args = cli.build_parser().parse_args(
        ["ingest", "fda-cyp", "--page", "somepage.html"])
    assert args.release is None
    assert args.page == pathlib.Path("somepage.html")

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["ingest", "fda-cyp", "--release", "2026-05-29T14:00"])


def test_a_release_that_disagrees_with_the_page_is_refused_before_any_db_write(
        tmp_path):
    """THE POINT OF ALLOWING --release AT ALL. A release typed on the command
    line that disagrees with the page's own stamp means the operator believes
    they are ingesting different bytes than they actually are, and ingest_run
    is history -- a wrong tag cannot be corrected afterwards. Reuses
    cli_chain.ReleaseError, whose own docstring states exactly this failure
    mode for the chain's release flags.

    The stub `conn` raises if `execute` is ever called, so this test fails
    for the right reason if a future change let the mismatch slip past
    validation and reach the orchestrator: not just "wrong exception", but
    "started writing before refusing".

    Only a synthetic page carrying a dateModified stamp is needed -- no table,
    no footnotes -- because the mismatch must be caught before parse_table or
    parse_footnotes ever run.
    """
    page = tmp_path / "fake_fda_cyp.html"
    page.write_text(
        '<meta property="article:modified_time" content="Fri, 05/29/2026 - 14:00" />',
        encoding="utf-8")
    args = argparse.Namespace(page=page, release="1999-01-01T00:00",
                          allow_shrink=False)

    class _NoDBConn:
        def execute(self, *a, **kw):
            raise AssertionError(
                "a release mismatch must be caught before any DB write")

    with pytest.raises(cli_chain.ReleaseError) as excinfo:
        cli_fda_cyp.handle_fda_cyp(_NoDBConn(), args)
    # BOTH VALUES NAMED, per the brief: an operator staring at this error needs
    # to see what they typed AND what the page actually says, not just one.
    assert "1999-01-01T00:00" in str(excinfo.value)
    assert "2026-05-29T14:00" in str(excinfo.value)


def test_a_matching_release_passes_validation_and_the_ingest_proceeds(
        tmp_path, monkeypatch):
    """The other half: a --release that DOES match the page's stamp must not
    be refused. `ingest_fda_cyp` itself is stubbed out so this test is about
    the CLI's validation and wiring alone -- the orchestrator's own behaviour
    is already covered by every other test in this module.
    """
    page = tmp_path / "fake_fda_cyp.html"
    page.write_text(
        '<meta property="article:modified_time" content="Fri, 05/29/2026 - 14:00" />',
        encoding="utf-8")
    args = argparse.Namespace(page=page, release="2026-05-29T14:00",
                          allow_shrink=False)

    calls = []

    def _stub_ingest(conn, *, page_path, upstream_release=None,
                     allow_shrink=False):
        calls.append((page_path, upstream_release))
        return fda_cyp_run.FdaCypSummary(
            upstream_release="2026-05-29T14:00", classes_in_release=0, classes_added=0,
            memberships_written=0, assertions_written=0, withheld_qualified=0,
            unresolved_substances=0, combination_regimens=0,
            non_drug_entities=0, questions_registered=0)

    monkeypatch.setattr(cli_fda_cyp.fda_cyp_run, "ingest_fda_cyp", _stub_ingest)
    assert cli_fda_cyp.handle_fda_cyp(object(), args) == 0
    # upstream_release is None here, NOT "2026-05-29T14:00": once --release
    # has been validated to agree with the page, the page's own stamp still
    # governs what actually gets recorded -- there is exactly one source of
    # truth for that value, and --release is a confirmation gate on it, never
    # a second route to the same fact.
    assert calls == [(page, None)]


def test_an_omitted_release_also_proceeds_straight_to_the_orchestrator(
        tmp_path, monkeypatch):
    """No --release at all is the ordinary case (design section 13): the page's
    own stamp governs with no operator involvement, and validation is skipped
    entirely rather than comparing None against anything.
    """
    page = tmp_path / "fake_fda_cyp.html"
    args = argparse.Namespace(page=page, release=None, allow_shrink=False)

    calls = []

    def _stub_ingest(conn, *, page_path, upstream_release=None,
                     allow_shrink=False):
        calls.append((page_path, upstream_release))
        return fda_cyp_run.FdaCypSummary(
            upstream_release="2026-05-29T14:00", classes_in_release=0, classes_added=0,
            memberships_written=0, assertions_written=0, withheld_qualified=0,
            unresolved_substances=0, combination_regimens=0,
            non_drug_entities=0, questions_registered=0)

    monkeypatch.setattr(cli_fda_cyp.fda_cyp_run, "ingest_fda_cyp", _stub_ingest)
    assert cli_fda_cyp.handle_fda_cyp(object(), args) == 0
    assert calls == [(page, None)]


def test_a_release_mismatch_reported_through_main_names_both_values(
        tmp_path, _migrated, monkeypatch, capsys):
    """End to end through cli.main -- what an operator actually runs -- rather
    than the handler directly: exit code 2, and the printed error names both
    the value they typed and what the page actually says. No real page is
    needed (only its dateModified stamp matters here), but a live DSN is,
    because `main` opens a connection before dispatching to any handler.
    """
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    page = tmp_path / "fake_fda_cyp.html"
    page.write_text(
        '<meta property="article:modified_time" content="Fri, 05/29/2026 - 14:00" />',
        encoding="utf-8")
    assert cli.main(
        ["ingest", "fda-cyp", "--page", str(page),
         "--release", "1999-01-01T00:00"]) == 2
    err = capsys.readouterr().err
    assert "1999-01-01T00:00" in err
    assert "2026-05-29T14:00" in err


@pytest.mark.skipif(not REAL_PAGE.exists(), reason="live page not downloaded")
def test_ingest_fda_cyp_via_the_cli_end_to_end_records_the_pages_own_release(
        _migrated, monkeypatch):
    """One real ingest through the CLI (#16's precedent for `unii`), against
    the real pinned page, with --release omitted -- the ordinary invocation.
    """
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    assert cli.main(["ingest", "fda-cyp", "--page", str(REAL_PAGE)]) == 0
    with psycopg.connect(_migrated) as c:
        row = c.execute(
            "SELECT source, writer, upstream_release FROM drugref.loaded_release "
            "WHERE source = 'FDA-CYP'").fetchone()
    assert row == ("FDA-CYP", "fda_cyp_run", "2026-05-29T14:00")


@pytest.mark.skipif(not REAL_PAGE.exists(), reason="live page not downloaded")
def test_a_release_flag_matching_the_real_page_succeeds_via_the_cli(
        _migrated, monkeypatch):
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    assert cli.main(
        ["ingest", "fda-cyp", "--page", str(REAL_PAGE),
         "--release", "2026-05-29T14:00"]) == 0


# ---------------------------------------------------------------------------
# Review 5c.2g: the paths the autouse `_registry` fixture made untestable.
# ---------------------------------------------------------------------------

@pytest.fixture
def _wider_registry(conn):
    """Seed the names the MEMBER path and the independence claims need.

    OPT-IN, not autouse, and deliberately so: the autouse `_registry` above
    seeds only bupropion and cenobamate, and BOTH are footnoted, so every DB
    test in this module ran against a registry where no row could reach
    `member` at all. `memberships_written` was 0 everywhere,
    `classes.add_membership` and `RELATIONSHIP` were executed by nothing, and
    test_a_members_row_raises_no_question counted `member` rows in a table
    that had none -- an assertion that could not fail. Keeping this separate
    from `_registry` means the existing count assertions above stay pinned to
    the registry they were measured against.

    abiraterone is the member case: one tuple, no footnote marker, so it is
    the shortest path from "FDA states a role" to "drugref asserts it".
    mephenytoin and venlafaxine make the two enantiomer tests FALSIFIABLE --
    without the racemate registered, "S-mephenytoin was not mapped to
    mephenytoin" is unfalsifiable, because there is nothing to map it TO.
    curcumin makes the independence claim real: it must resolve AND still be
    non_drug_entity. The two alprazolam spellings are the ambiguity case.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test-wider', 'test-wider', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for unii, name in (("TESTUNII_ABIRATERONE", "abiraterone"),
                       ("TESTUNII_MEPHENYTOIN", "mephenytoin"),
                       ("TESTUNII_VENLAFAXINE", "venlafaxine"),
                       ("TESTUNII_CURCUMIN", "curcumin"),
                       # TWO moieties, ONE case-folded display name.
                       ("TESTUNII_ALPRAZOLAM_A", "alprazolam"),
                       ("TESTUNII_ALPRAZOLAM_B", "Alprazolam")):
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
            (ids.mint_moiety_uuid(unii), name, seed_run))
    conn.commit()


@pytest.mark.usefixtures("conn", "_wider_registry")
def test_a_clean_resolvable_cell_becomes_a_membership(conn):
    """The path the ingest EXISTS to walk, and nothing exercised it.

    abiraterone's cell carries no footnote marker and its name resolves to one
    moiety, so _classify returns `member` and the orchestrator must write a
    real class_membership edge under FDA-CYP's own relationship.
    """
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                         upstream_release="2026-05-29T14:00")
    assert summary.memberships_written > 0, "no row reached the member path at all"
    row = conn.execute(
        "SELECT m.relationship, c.source FROM drugref.class_membership m "
        "JOIN drugref.substance_class c ON c.class_uuid = m.class_uuid "
        "JOIN drugref.substance_moiety s ON s.moiety_uuid = m.moiety_uuid "
        "WHERE lower(s.display_name) = 'abiraterone' AND c.source = 'FDA-CYP'").fetchone()
    assert row is not None, "abiraterone resolved and was unfootnoted; it must be a member"
    assert row[0] == fda_cyp_run.RELATIONSHIP == "has_PK"
    disposition = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) = 'abiraterone'").fetchone()
    assert disposition[0] == "member"
    assert disposition[1] is not None


@pytest.mark.usefixtures("conn", "_wider_registry")
def test_a_second_run_leaves_exactly_one_FDA_CYP_membership_edge(conn):
    """Per-source rebuild safety for THIS source's OWN edges.

    The existing rebuild test seeds a MED-RT edge and checks FDA-CYP's clear
    does not take it -- the complementary half (that FDA-CYP's own edges are
    rebuilt rather than duplicated) could not be tested while no FDA-CYP edge
    was ever written.
    """
    first = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                       upstream_release="2026-05-29T14:00")
    second = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                        upstream_release="2026-05-29T14:00")
    assert first.memberships_written == second.memberships_written > 0
    edges = conn.execute(
        "SELECT count(*) FROM drugref.class_membership m "
        "JOIN drugref.substance_class c ON c.class_uuid = m.class_uuid "
        "WHERE c.source = 'FDA-CYP'").fetchone()[0]
    assert edges == second.memberships_written


@pytest.mark.usefixtures("conn", "_wider_registry")
def test_an_ambiguous_display_name_stays_unresolved_rather_than_picking_the_first(conn):
    """`single = candidates[0] if len(candidates) == 1 else None`.

    Two moieties share the case-folded name 'alprazolam'. Rewriting that line
    as `candidates[0] if candidates else None` would silently assert a
    membership on whichever moiety the index happened to list first -- a wrong
    membership on a REAL moiety, which is the worst shape available here.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) = 'alprazolam' LIMIT 1").fetchone()
    assert row[0] == "unresolved_substance"
    assert row[1] is None


@pytest.mark.usefixtures("conn", "_wider_registry")
def test_S_mephenytoin_stays_unresolved_WITH_the_racemate_registered(conn):
    """Issue 128, made falsifiable. The sibling test above runs on a registry
    with no 'mephenytoin' in it at all, so it cannot detect a prefix bridge --
    there is nothing to bridge to. Here the racemate IS registered.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance ILIKE 'S-mephenytoin%' LIMIT 1").fetchone()
    assert row[0] == "unresolved_substance"
    assert row[1] is None


@pytest.mark.usefixtures("conn", "_wider_registry")
def test_R_venlafaxine_stays_unresolved_WITH_venlafaxine_registered(conn):
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance ILIKE 'R-venlafaxine%' LIMIT 1").fetchone()
    assert row[0] == "unresolved_substance"
    assert row[1] is None


@pytest.mark.usefixtures("conn", "_wider_registry")
def test_curcumin_RESOLVES_and_is_still_a_non_drug_entity(conn):
    """The independence, actually exercised: the sibling test never seeds
    curcumin, so it proves only the disposition, not that resolution and
    non-drug status are orthogonal. `_classify` threads `single` through the
    non_drug_entity branch unchanged, and nothing checked that.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) = 'curcumin' LIMIT 1").fetchone()
    assert row[0] == "non_drug_entity"
    assert row[1] is not None, "curcumin resolves; the branch must not discard that"


def _truncated_page(tmp_path, keep_rows):
    """The fixture with all but `keep_rows` data rows removed, still well-formed.

    A partial download, a CMS pagination change or a `</table>` emitted early
    all produce exactly this: rows that are individually PERFECT (still
    EXPECTED_COLUMNS wide, still a closed-vocabulary pathway), just far fewer
    of them. That is why no per-row guard can catch it.
    """
    import re
    page = FIXTURE.read_text(encoding="utf-8")
    table = re.findall(r"<table.*?</table>", page, re.S)[0]
    rows = re.findall(r"<tr.*?</tr>", table, re.S)
    trimmed = table.split(rows[0])[0] + rows[0] + "".join(rows[1:1 + keep_rows]) + "</table>"
    path = tmp_path / "truncated.html"
    path.write_text(page.replace(table, trimmed), encoding="utf-8")
    return path


@pytest.mark.usefixtures("conn")
def test_a_truncated_page_refuses_to_replace_the_projection(conn, tmp_path):
    """THE GATE THE MODULE DOCSTRING CLAIMED AND DID NOT HAVE.

    fda_cyp.py's own "why a regex parse is defensible" argument said "the row
    and cell COUNTS are asserted (245 x 11 exactly)"; only the CELL count ever
    existed. Measured on the real page: truncating the data table to six <tr>
    yields 5 tuples instead of 419, with NO error -- and because the
    orchestrator clears class_membership and fda_cyp_assertion BEFORE writing,
    that run deletes 240 substances' rows and commits, exit 0. The only trace
    is a smaller number in a printed dataclass nobody has a baseline for.

    The guard compares against what is ALREADY STORED rather than a pinned 245,
    so it needs no constant to bump when FDA grows the table, and it cannot
    fire on a first ingest -- which is right, because a first ingest destroys
    nothing.
    """
    full = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                      upstream_release="2026-05-29T14:00")
    assert full.assertions_written > 10
    with pytest.raises(fda_cyp_run.FdaCypShrinkError, match="refusing"):
        fda_cyp_run.ingest_fda_cyp(conn, page_path=_truncated_page(tmp_path, 2),
                                   upstream_release="2026-05-29T14:01")
    # The refusal must leave the FULL projection intact, not a half-cleared one.
    conn.rollback()
    remaining = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion").fetchone()[0]
    assert remaining == full.assertions_written


@pytest.mark.usefixtures("conn")
def test_a_first_ingest_is_never_blocked_by_the_shrink_guard(conn, tmp_path):
    """Nothing is stored, so nothing can be destroyed."""
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=_truncated_page(tmp_path, 2),
                                         upstream_release="2026-05-29T14:00")
    assert summary.assertions_written > 0


@pytest.mark.usefixtures("conn")
def test_a_deliberate_shrink_can_be_authorised_explicitly(conn, tmp_path):
    """FDA genuinely shrinking the table is a real event; it just must be a
    DECISION rather than a silent default."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=_truncated_page(tmp_path, 2),
                                         upstream_release="2026-05-29T14:01",
                                         allow_shrink=True)
    assert summary.assertions_written > 0


@pytest.mark.usefixtures("conn", "_wider_registry")
def test_classes_added_and_classes_in_release_are_different_numbers(conn):
    """`classes_minted` was `len(class_cache)` -- the classes SEEN this release,
    not the ones this run added -- with upsert_class's `is_new` discarded.

    On every re-ingest it therefore printed the same figure while zero classes
    were minted, which is the one number an operator would quote to answer
    "did this release change anything?". MedrtSummary splits these two for
    exactly this reason.
    """
    first = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                       upstream_release="2026-05-29T14:00")
    second = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                        upstream_release="2026-05-29T14:00")
    assert first.classes_in_release == second.classes_in_release > 0
    assert first.classes_added == first.classes_in_release
    assert second.classes_added == 0, "a re-ingest mints nothing and must say so"


@pytest.mark.usefixtures("conn")
def test_an_unrecognised_disposition_is_counted_by_nothing_and_so_must_raise(
        conn, monkeypatch):
    """The Python half of the hole db/041 fixed in SQL.

    The if/elif chain over the five dispositions had no terminal `else`, so a
    sixth value -- which db/041's own header calls "a real, foreseeable event"
    -- would be counted into NONE of the five counters while
    `assertions_written` still included it. No counter sums to
    assertions_written anywhere, so nothing downstream could notice.
    """
    monkeypatch.setattr(fda_cyp_run, "_classify",
                        lambda substance, single, markers: ("testonly_sixth", None))
    with pytest.raises(fda_cyp_run.FdaCypDispositionError, match="testonly_sixth"):
        fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                   upstream_release="2026-05-29T14:00")
    conn.rollback()


def test_an_ascii_apostrophe_still_matches_FDAs_curly_one():
    """NON_DRUG_ENTITIES pinned "st. john<U+2019>s wort" with FDA's curly
    apostrophe hardcoded.

    If the CMS ever emits &#39; or a plain ASCII apostrophe, the membership
    test silently fails and St. John's wort -- a dietary supplement FDA
    explicitly carved out -- falls through to a DIFFERENT VALID disposition, or
    to a membership if a moiety ever carries that display name. A one-codepoint
    change flipping a clinical classification with no error is the exact shape
    _classify's own ordering docstring exists to prevent.
    """
    assert fda_cyp_run._is_non_drug_entity("St. John's wort")      # ASCII
    assert fda_cyp_run._is_non_drug_entity("St. John’s wort")  # FDA's curly
    assert fda_cyp_run._is_non_drug_entity("GRAPEFRUIT JUICE")
    assert not fda_cyp_run._is_non_drug_entity("bupropion")


@pytest.mark.usefixtures("conn")
def test_a_pre_db042_withheld_row_does_not_abort_an_unrelated_sources_ingest(conn):
    """THE CROSS-SOURCE ABORT, pinned.

    db/042 added `substance`, `row_footnote_markers` and `cell_footnote_markers`
    nullable with NO backfill, so a database that applied it and has not yet
    re-run `drugref ingest fda-cyp` carries withheld rows with all three NULL.
    questions.py's ELSE arm concatenated `row_footnote_markers` unguarded; SQL's
    `||` yields NULL if any operand is NULL, and open_question.question_text is
    NOT NULL.

    Because register_from_gaps runs at the END OF EVERY INGEST OF EVERY SOURCE,
    the blast radius was cross-source: the next MeSH, GSRS or PBS ingest would
    die on FDA-CYP's residue, with an error naming a column in open_question and
    nothing about FDA-CYP or db/042.

    Simulated exactly as the migration leaves it -- the columns NULLed on a real
    withheld row -- rather than by mocking the view.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    conn.execute(
        "UPDATE drugref.fda_cyp_assertion "
        "SET substance = NULL, row_footnote_markers = NULL, "
        "    cell_footnote_markers = NULL "
        "WHERE disposition = 'withheld_qualified'")
    withheld = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE disposition = 'withheld_qualified'").fetchone()[0]
    assert withheld > 0, "the fixture must carry withheld rows for this to mean anything"

    run_id = conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run "
        "WHERE source = 'FDA-CYP' LIMIT 1").fetchone()[0]
    counts = questions.register_from_gaps(conn, run_id)
    assert counts["fda_cyp_unadjudicated"] > 0

    blank = conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated' AND question_text IS NULL").fetchone()[0]
    assert blank == 0
    conn.rollback()


@pytest.mark.usefixtures("conn")
def test_two_printed_forms_of_one_name_raise_ONE_question_not_two(conn):
    """db/043: the view's grain must equal the grain its gap_key uses.

    db/042 moved key_sql onto COALESCE(substance, raw_substance) while both
    halves of the view still grouped by raw_substance -- a strictly finer
    grain. Two printed forms of one name ('aprepitant 3' and 'aprepitant')
    therefore produced TWO view rows carrying ONE gap_key, and
    register_from_gaps' ON CONFLICT DO UPDATE over an UNORDERED view silently
    overwrote the first row's question_text with the second's. Which one won
    was not deterministic, for a question_uuid that is immortal and externally
    citable.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    run_id = conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run "
        "WHERE source = 'FDA-CYP' LIMIT 1").fetchone()[0]
    # One substance, two printed forms, same subject disposition -- exactly what
    # a footnote marker on one of FDA's two aprepitant rows would produce.
    conn.execute(
        "INSERT INTO drugref.fda_cyp_assertion "
        "(ingest_run, source, row_ordinal, raw_substance, substance, column_heading, "
        " raw_cell, system, pathway, role, potency, disposition) VALUES "
        "(%s, 'FDA-CYP', 9001, 'probeamide 7', 'probeamide', 'CYP Mod INH', "
        " '3A moderate inhibitor', 'CYP', '3A', 'inhibitor', 'moderate', "
        " 'unresolved_substance'), "
        "(%s, 'FDA-CYP', 9002, 'probeamide', 'probeamide', 'CYP Strg IND', "
        " '3A strong inducer', 'CYP', '3A', 'inducer', 'strong', "
        " 'unresolved_substance')",
        (run_id, run_id))

    rows = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE substance = 'probeamide'").fetchone()[0]
    assert rows == 1, "one substance, one subject-grain question -- not one per printed form"

    questions.register_from_gaps(conn, run_id)
    minted = conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated' AND gap_key LIKE '%probeamide%'").fetchone()[0]
    assert minted == 1
    conn.rollback()
