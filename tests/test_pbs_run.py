# tests/test_pbs_run.py
"""DB-gated acceptance matrix for the PBS ingest.

Seeds a small moiety registry with INN claims, then ingests the committed fixture
and asserts on the BRIDGE -- which is the only thing slice 8a is really testing.
"""
import pathlib

import pytest

from drugref import claims, ids
from drugref.ingest import pbs, pbs_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "pbs_items_subset.csv"


@pytest.fixture(autouse=True)
def _clean(conn):
    """ingest_pbs COMMITS internally, so conftest's rollback cannot isolate these
    tests. Truncate first, exactly as test_medrt_run.py does, so counts are
    order-independent.

    NOTE (ROADMAP floor-hardening; corrected, review round finding 9): this
    fixture depends on the very TRUNCATE bypass that item plans to close. This is
    NOT the third module coupled to it -- `grep -l TRUNCATE tests/*.py` finds
    SEVEN: test_chebi.py, test_gap_views.py, test_ingest_run.py,
    test_medrt_run.py, test_mesh_run.py, test_pbs_run.py (this one) and
    test_questions.py. The original "third" count was wrong even before this
    module existed and this branch never re-checked it before repeating the claim
    (see docs/ROADMAP.md, corrected alongside this).
    """
    conn.execute(
        "TRUNCATE drugref.local_product_moiety, drugref.local_unmatched_ingredient, "
        "drugref.local_product, drugref.identity_claim, drugref.substance_moiety, "
        "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield

# INN claims are stored lower-case; PBS publishes Title-case. The fold is what
# makes these meet, so seeding them lower-case mirrors production exactly.
#
# "dimethyl" (bare, no "fumarate") is NOT a real INN -- it is planted here
# adversarially, alongside the real "dimethyl fumarate", so the ordering
# regression test below can actually distinguish the two lookup orders. Without
# it, a stripped-first lookup of "dimethyl fumarate" -> "dimethyl" simply MISSES
# (nothing was seeded under that bare key) and falls through to the exact match
# anyway, landing on the identical result regardless of order -- which is
# exactly the gap the fix-round review caught.
SEED_INNS = ["rifaximin", "abacavir", "lamivudine", "abiraterone",
             "methylprednisolone", "alfuzosin", "dimethyl fumarate", "dimethyl",
             "alendronic acid", "folic acid", "paracetamol", "aspirin"]


@pytest.fixture
def seeded_registry(conn):
    """A moiety per SEED_INN, each carrying its INN identity claim."""
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'seed', 'seed', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    out = {}
    for index, inn in enumerate(SEED_INNS):
        moiety_uuid = ids.mint_moiety_uuid(f"SEEDUNII{index:02d}")
        conn.execute(
            "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
            "first_seen_ingest) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, inn, run_id))
        claims.add_claim(conn, moiety_uuid, "INN", inn, run_id)
        out[inn] = moiety_uuid
    return out


def _bridged_names(conn, drug_name):
    return {row[0] for row in conn.execute(
        "SELECT b.component_name FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = %s", (drug_name,)).fetchall()}


def test_a_moiety_with_no_inn_claim_still_bridges(conn, seeded_registry):
    """The bridge must index drugref's LABEL, not only its INN claims (#26).

    Since #26 the registry admits ~6,850 moieties on a USAN or an RxCUI rather
    than an INN -- amoxicillin, morphine, codeine, doxycycline among them. Those
    moieties get a display_name but NO `INN` identity_claim, because drugref has
    no grounds to assert an INN it cannot source. While the bridge indexed INN
    claims alone they were invisible to it, and measured against the real July
    2026 PBS release that cost 1,256 of 3,140 unmatched components (40%) and
    1,235 products -- i.e. the entire downstream benefit of the gate redesign.

    display_name is a strict superset of the INN claim values: verified against
    the real release, all 12,588 INN claims equal their moiety's display_name
    (both come from gate.inn_display_name), with zero mismatches. So indexing the
    label loses nothing and gains every non-INN moiety.
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'seed2', 'seed2', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    # Exactly how run.py registers amoxicillin: a moiety and a display_name, but
    # no INN claim, because UNII carries no INN_ID for it.
    amox = ids.mint_moiety_uuid("804826J2HU")
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (%s, 'amoxicillin', %s) ON CONFLICT DO NOTHING",
        (amox, run_id))
    assert conn.execute("SELECT count(*) FROM drugref.identity_claim "
                        "WHERE moiety_uuid = %s AND scheme = 'INN'",
                        (amox,)).fetchone()[0] == 0

    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    bridged = {row[0] for row in conn.execute(
        "SELECT component_name FROM drugref.local_product_moiety "
        "WHERE moiety_uuid = %s", (amox,)).fetchall()}
    assert bridged == {"amoxicillin"}      # stored normalised, as every bridge row is


def test_exact_match_bridges(conn, seeded_registry):
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Rifaximin") == {"rifaximin"}


def test_combination_fans_out_to_both_components(conn, seeded_registry):
    """'Abacavir with lamivudine' must produce TWO bridge rows, not one."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Abacavir with lamivudine") == {"abacavir", "lamivudine"}


def test_salt_stripped_match_is_labelled(conn, seeded_registry):
    """'Alfuzosin hydrochloride' matches only after stripping, and the row must
    say so -- otherwise a heuristic masquerades as an exact match."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    method = conn.execute(
        "SELECT b.match_method FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = 'Alfuzosin hydrochloride'").fetchone()[0]
    assert method == "salt_stripped"


def test_unstripped_name_wins_over_the_salt_fallback(conn, seeded_registry):
    """THE REGRESSION. 'Dimethyl fumarate' is itself an INN, and the registry ALSO
    carries a bare 'dimethyl' moiety (a synthetic plant -- see SEED_INNS) so the
    two lookup orders are forced to disagree instead of quietly agreeing:

    * correct order (exact first)  -> the 'dimethyl fumarate' moiety, 'exact'
    * wrong order (strip first)    -> the 'dimethyl' moiety, 'salt_stripped'

    Without the bare 'dimethyl' plant, a stripped-first lookup of 'dimethyl
    fumarate' would simply MISS (nothing seeded under that key) and fall through
    to the exact match anyway -- so both orderings would land on the identical
    row and this test would pass either way, pinning nothing."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    component_name, match_method, moiety_uuid = conn.execute(
        "SELECT b.component_name, b.match_method, b.moiety_uuid "
        "FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = 'Dimethyl fumarate'").fetchone()
    assert (component_name, match_method) == ("dimethyl fumarate", "exact")
    assert moiety_uuid == seeded_registry["dimethyl fumarate"]
    assert moiety_uuid != seeded_registry["dimethyl"]


def test_acid_names_match_exactly(conn, seeded_registry):
    """'Alendronic acid' and 'Folic acid' must match whole -- 'acid' is part of
    the INN, not a salt token."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Alendronic acid") == {"alendronic acid"}
    assert _bridged_names(conn, "Folic acid") == {"folic acid"}


def test_partial_combination_is_recorded_honestly(conn, seeded_registry):
    """'Amoxicillin with clavulanic acid': neither component is seeded, so both
    must land in the unmatched list rather than the product silently vanishing."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    unmatched = {row[0] for row in conn.execute(
        "SELECT component_name FROM drugref.local_unmatched_ingredient").fetchall()}
    assert "amoxicillin" in unmatched
    assert "clavulanic acid" in unmatched


def test_null_sentinel_row_uses_the_drug_name_fallback(conn, seeded_registry):
    """The NULLCASE_1 fixture row has li_drug_name='null' and drug_name='Aspirin'."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Aspirin") == {"aspirin"}
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product WHERE drug_name = 'null'"
    ).fetchone()[0] == 0


def test_summary_counts_are_consistent(conn, seeded_registry):
    """products_written must be a MEASUREMENT of what actually landed in
    local_product, not an assertion echoing items_read (review round, finding
    3) -- comparing a field to itself is a tautology that can never fail, even
    if a repeated li_item_id upstream collapsed two rows onto one product."""
    summary = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    total_products = conn.execute(
        "SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert summary.products_written == total_products
    assert summary.products_bridged <= summary.products_written
    assert summary.bridge_rows_salt_stripped >= 1
    assert summary.combination_products >= 2


def test_products_written_counts_distinct_products_not_rows_read(conn, seeded_registry, tmp_path):
    """THE REGRESSION (review round, finding 3). Two CSV rows sharing one
    li_item_id must collapse onto ONE local_product row (product identity is
    keyed on that value), so products_written -- the slice's headline
    match-rate denominator -- must report 1, not 2. Before this fix,
    products_written was simply set to items_read, so a duplicate upstream row
    would have silently inflated it to match items_read while the real
    database only ever gained one row: exactly the drift this test pins."""
    path = tmp_path / "items.csv"
    path.write_text(
        "li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,"
        "program_code,benefit_type_code\n"
        "DUP_1,X,Brand A,Rifaximin,Rifaximin,Tab,GE,A\n"
        "DUP_1,X,Brand B,Rifaximin,Rifaximin,Tab,GE,A\n",
        encoding="utf-8-sig")
    summary = pbs_run.ingest_pbs(conn, path, "2026-07-01", "testsum")
    assert summary.items_read == 2
    assert summary.products_written == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product").fetchone()[0] == 1
    # THE OTHER HALF OF THE SAME DEFECT (fix round, finding 1). Fixing only the
    # denominator left the NUMERATOR counting per CSV row, so this duplicate
    # scored products_bridged=2 against products_written=1 -- a 200% match rate,
    # the slice's headline figure reading as nonsense. The invariant below is
    # asserted HERE, on the only fixture that contains a duplicate: asserting it
    # in test_summary_counts_are_consistent (which ingests the duplicate-free
    # subset) is a check that cannot fail no matter how badly the counter drifts.
    assert summary.products_bridged == 1
    assert summary.products_bridged <= summary.products_written


def test_combination_count_is_per_product_not_per_row(conn, seeded_registry, tmp_path):
    """combination_products drifted exactly like products_bridged (fix round,
    finding 1): counted per CSV row, so one duplicated combination item reported
    two combinations while the database held one product. Every count in
    PbsSummary that describes PRODUCTS must be keyed on the product UUID."""
    path = tmp_path / "items.csv"
    path.write_text(
        "li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,"
        "program_code,benefit_type_code\n"
        "DUP_2,X,Brand A,Abacavir with lamivudine,Abacavir + lamivudine,Tab,CA,S\n"
        "DUP_2,X,Brand B,Abacavir with lamivudine,Abacavir + lamivudine,Tab,CA,S\n",
        encoding="utf-8-sig")
    summary = pbs_run.ingest_pbs(conn, path, "2026-07-01", "testsum")
    assert summary.items_read == 2
    assert summary.products_written == 1
    assert summary.combination_products == 1


def test_rows_without_identity_are_counted_not_silently_dropped(conn, seeded_registry, tmp_path):
    """THE ROW-LEVEL COUNTER (review round, finding 1). A row with no li_item_id
    cannot be keyed and must not be written -- but it must be COUNTED, not just
    quietly absorbed into a lower items_read-vs-database-rows gap with no
    number anyone could query (mirrors ingest/run.py's rows_without_unii)."""
    path = tmp_path / "items.csv"
    path.write_text(
        "li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,"
        "program_code,benefit_type_code\n"
        ",X,B,Aspirin,Aspirin,Tab,GE,U\n"
        "X_2,Y,B,Ibuprofen,Ibuprofen,Tab,GE,U\n",
        encoding="utf-8-sig")
    summary = pbs_run.ingest_pbs(conn, path, "2026-07-01", "testsum")
    assert summary.items_read == 2
    assert summary.rows_without_identity == 1
    assert summary.products_written == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product").fetchone()[0] == 1


def test_column_drift_raises_instead_of_silently_wiping_the_projection(conn, seeded_registry, tmp_path):
    """THE COLUMN-DRIFT GUARD, end to end (review round, finding 1). If a future
    release renames li_item_id, the OLD behaviour would let every row hit the
    per-row skip, parse_items would yield nothing usable, and ingest_pbs would
    commit an EMPTY local tier with no error -- after clear_source_products had
    already deleted the previous release's rows. Seed one row via the real
    fixture first, so there is something to lose, then re-ingest a CSV missing
    the key column and confirm the whole ingest raises and the transaction rolls
    back rather than silently emptying local_product."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    before = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert before > 0

    broken = tmp_path / "items.csv"
    broken.write_text(
        "pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,"
        "benefit_type_code\n"
        "10001J,Xifaxan,Rifaximin,Rifaximin,Tablet 550 mg,GE,A\n",
        encoding="utf-8-sig")
    with pytest.raises(ValueError, match="li_item_id"):
        pbs_run.ingest_pbs(conn, broken, "2026-08-01", "testsum2")

    # The failed run must not have left the previous release's rows deleted:
    # ingest_pbs rolls back on any exception, and clear_source_products ran
    # inside that same transaction.
    after = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert after == before


def test_nameless_item_is_recorded_as_unmatched_not_dropped(conn, seeded_registry, tmp_path):
    """THE NAMELESS-ITEM GUARD (review round, finding 2). A row where BOTH
    li_drug_name and drug_name are absent/'null' has no component to bridge --
    but the product must still surface in local_unmatched_ingredient under the
    sentinel name, rather than silently vanishing from both the bridge and the
    residual worklist with no queryable trace (spec section 7)."""
    path = tmp_path / "items.csv"
    path.write_text(
        "li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,"
        "program_code,benefit_type_code\n"
        "NONAME_1,X,SomeBrand,null,null,Tab,GE,U\n",
        encoding="utf-8-sig")
    summary = pbs_run.ingest_pbs(conn, path, "2026-07-01", "testsum")
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product").fetchone()[0] == 1
    unmatched = {row[0] for row in conn.execute(
        "SELECT component_name FROM drugref.local_unmatched_ingredient").fetchall()}
    assert unmatched == {pbs.NO_DRUG_NAME_SENTINEL}
    assert summary.products_bridged == 0


def test_re_ingest_is_idempotent(conn, seeded_registry):
    first = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    second = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert first.products_written == second.products_written
    total = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert total == second.products_written


# The canary values make_pbs_subset.py plants in the fixture's atc_code/amt_code
# columns. They are NOT upstream -- the fixture adds them precisely so this test
# proves drugref DISCARDS them, instead of passing merely because they were absent.
ATC_CANARY = "ZZZ_ATC_CANARY"
AMT_CANARY = "ZZZ_AMT_CANARY"


def _all_text_columns(conn):
    """Every text-ish column in the drugref schema, for an exhaustive sweep."""
    return conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND data_type IN ('text','character varying') "
        "ORDER BY table_name, column_name").fetchall()


def test_no_encumbered_value_reaches_any_drugref_table(conn, seeded_registry):
    """THE LICENCE GUARANTEE, EXECUTABLE (spec section 6).

    ATC codes are WHO-owned (NonCommercial + NoDerivatives) and AMT/SNOMED CT-AU
    is NCTS-licensed; neither may ever enter drugref -- that is a licence
    constraint, not a style preference, and a breach here is a legal problem for
    every downstream user of the data, not a bug report.

    The committed fixture (tests/fixtures/pbs_items_subset.csv) carries a planted
    ATC_CANARY/AMT_CANARY value in every row's atc_code/amt_code column. Those
    columns do not exist in the real upstream items.csv -- make_pbs_subset.py
    adds them on purpose, precisely so this test cannot pass just because the
    encumbered columns happened to be absent. It only passes if drugref actively
    reads items.csv WITHOUT those columns and never carries their values into any
    table, which is what the licence actually requires.

    This test sweeps every text/varchar column in the whole drugref schema
    (not just the PBS tables) after a full ingest, so a leak via an unexpected
    path -- a future column, a copy-paste into the wrong table -- is caught too.

    The match is a SUBSTRING one, not equality (fix round, finding 5). Equality
    only catches a canary copied whole into its own column; it misses the leak
    that is actually harder to spot by eye -- an encumbered value CONCATENATED
    into a longer string, e.g. an ATC code appended to a display name or folded
    into a provenance note. A licence breach is a legal problem for every
    downstream user, so the guard should cost nothing to widen and does.
    """
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    offenders = []
    for table, column in _all_text_columns(conn):
        hits = conn.execute(
            f'SELECT count(*) FROM drugref."{table}" '
            f'WHERE "{column}" LIKE %s OR "{column}" LIKE %s',
            (f"%{ATC_CANARY}%", f"%{AMT_CANARY}%")).fetchone()[0]
        if hits:
            offenders.append(f"{table}.{column}")
    assert offenders == [], f"encumbered value leaked into: {offenders}"


def test_rebuild_is_scoped_to_pbs(conn, seeded_registry):
    """A PBS re-ingest must not touch another source's projection.

    Two things must survive, and only one of them was actually at risk (review
    round, finding 4). The registry seeded by the UNII run above (identity_claim,
    substance_moiety) is unreachable by ANY bug in clear_source_products, because
    that function only ever DELETEs the three local_* tables -- so asserting on
    the registry alone could never fail no matter how badly the scoping broke.
    The property genuinely at risk is a local_product row belonging to a
    DIFFERENT source's ingest_run: clear_source_products scopes its DELETE via
    `ingest_run IN (SELECT ingest_run_id FROM ingest_run WHERE source = %s)`, and
    only a row seeded exactly that way can catch a regression in that scoping.
    """
    before = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'INN'").fetchone()[0]

    other_run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'other', 'other', 'medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    foreign_uuid = ids.mint_local_product_uuid("AU", "PBS", "NOT_A_PBS_INGEST_RUN")
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, "
        "source, source_code, ingest_run) VALUES (%s, 'AU', 'PBS', "
        "'NOT_A_PBS_INGEST_RUN', %s)", (foreign_uuid, other_run_id))

    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-08-01", "testsum2")

    after = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'INN'").fetchone()[0]
    assert after == before
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_moiety").fetchone()[0] == len(SEED_INNS)
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product WHERE local_product_uuid = %s",
        (foreign_uuid,)).fetchone()[0] == 1


def test_rebuild_drops_a_delisted_item(conn, seeded_registry, tmp_path):
    """The projection must SHRINK when upstream does -- the property that makes
    delete-and-rebuild the right model and an append-only floor the wrong one."""
    import csv as _csv
    rows = list(_csv.DictReader(open(FIXTURE, newline="", encoding="utf-8-sig")))
    smaller = tmp_path / "items.csv"
    with open(smaller, "w", newline="", encoding="utf-8-sig") as fh:
        writer = _csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows[:2])
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "a")
    full = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    pbs_run.ingest_pbs(conn, smaller, "2026-08-01", "b")
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product").fetchone()[0] == 2 < full
