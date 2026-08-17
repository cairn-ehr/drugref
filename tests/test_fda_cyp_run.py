# tests/test_fda_cyp_run.py
"""The FDA-CYP orchestrator: DB-gated.

Every test here pins a DECISION from the design, not an implementation detail.
"""
import pathlib

import pytest

from drugref import ids
from drugref.ingest import fda_cyp_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "fda_cyp_table.html"


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
    every test, which is what makes summary.classes_minted and friends
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
    the only way to make the assertion able to fail: if a later change let a
    near name silently promote a row's disposition or attach a moiety, this
    would catch it; today, with no such logic anywhere, it passes because
    nothing here reads the column at all.
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
    assert minted == summary.classes_minted
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
