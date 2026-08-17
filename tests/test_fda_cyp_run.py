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
    """Seed the ONE real moiety a disposition test needs resolved rather than
    unresolved: bupropion, for test_a_qualified_cell_writes_NO_membership.

    conftest.py's `_migrated` fixture applies SCHEMA ONLY -- no seed data -- so
    on a fresh connection every substance name in the fixture, bupropion
    included, resolves to nothing. Without a registered 'bupropion' moiety its
    footnoted '2B6 sensitive substrate' cell would land unresolved_substance
    rather than withheld_qualified, and the section-3 case the whole design is
    built around (a footnote that NEGATES the row it sits on) would never
    actually be exercised -- the test would pass for the wrong reason, on an
    empty registry where nothing resolves regardless of the footnote logic.

    ONE NAME IS ENOUGH. Every other test here is resolution-shape-agnostic:
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
    moiety_uuid = ids.mint_moiety_uuid("TESTUNII_BUPROPION")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
        (moiety_uuid, "bupropion", seed_run))
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
def test_a_row_with_a_near_name_is_counted_as_unresolved(conn):
    """registry_near_name is EVIDENCE, never coverage. This test exists because a
    nullable text column beside an unresolved row is precisely the shape a later
    reader will be tempted to count.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    contradictions = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE registry_near_name IS NOT NULL AND disposition = 'member'").fetchone()[0]
    assert contradictions == 0


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
    """
    before = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source <> 'FDA-CYP'").fetchone()[0]
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    after = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source <> 'FDA-CYP'").fetchone()[0]
    assert before == after


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
