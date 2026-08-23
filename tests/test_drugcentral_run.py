# tests/test_drugcentral_run.py
"""The orchestrator: it reconciles, and it is the only writer.

Issue 71's standing rule, re-learned by curate_onchigh and again by the
re-measurement's Measurement guard: a summary whose buckets do not sum is a
number that cannot be checked, and every row must land in exactly one of them.
"""
import gzip
import pathlib
import uuid

import pytest

from drugref.ingest import drugcentral, drugcentral_run

FIXTURE = pathlib.Path("tests/fixtures/drugcentral_ddi_subset.sql.gz")


def _forged_dump(tmp_path, name, *replacements):
    """Write a copy of FIXTURE into *tmp_path* with literal substitutions applied.

    Every test that needs a MALFORMED dump forges it from the committed fixture
    rather than hand-writing one, so the forgery differs from a real dump in
    exactly the one way the test is about and in no other. Each `(old, new)` pair
    is asserted present first: a fixture regeneration that renamed the text being
    substituted must fail loudly here, not silently produce an unchanged dump the
    test then "passes" against.

    Both handles are opened in `with` blocks. The read used to be a bare
    `gzip.open(...).read()`, which leaks the handle and raises ResourceWarning.
    """
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        text = handle.read()
    for old, new in replacements:
        assert old in text, f"the fixture no longer contains {old!r}"
        text = text.replace(old, new)
    path = tmp_path / name
    with gzip.open(path, "wt", encoding="utf-8") as out:
        out.write(text)
    return path

# Two moieties DELIBERATELY sharing the display_name 'gatifloxacin', so
# load_registry's first_wins must pick one deterministically -- fixed, ordered
# UUIDs (rather than gen_random_uuid()) so the test can assert WHICH one wins
# without re-deriving it. _GATIFLOXACIN_LOW sorts before _GATIFLOXACIN_HIGH
# under both PostgreSQL's native uuid ordering and plain text comparison, so
# there is no ambiguity about which one `ORDER BY display_name, moiety_uuid`
# visits first.
_GATIFLOXACIN_LOW = uuid.UUID("00000000-0000-0000-0000-000000000001")
_GATIFLOXACIN_HIGH = uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffe")
_PIOGLITAZONE = uuid.UUID("00000000-0000-0000-0000-000000000002")
# ONE moiety carries BOTH names below -- see _seed_registry's docstring for
# why that is what makes ddi row 870 a genuine self-pair.
_ACETAMINOPHEN_AND_SULFINPYRAZONE = uuid.UUID("00000000-0000-0000-0000-000000000003")


def _seed_registry(conn) -> int:
    """Seed a small, real registry so the fixture's rows resolve for real.

    conftest's `_migrated` fixture applies SCHEMA ONLY, so every other
    DB-gated test in this module runs `ingest_drugcentral` against an EMPTY
    registry: every endpoint is honestly unresolved, `resolved == self_pair
    == 0` on every run, and the `elif record.resolved` branch of the counting
    loop is never taken at all (review round, Finding 1). This fixture buys
    three things the rest of the module cannot exercise:

    1. A REAL resolved, non-self pair: 'gatifloxacin' and 'pioglitazone' are
       registered as two DIFFERENT moieties, so ddi rows 15 and 2890 (the
       fixture's own both-order, disagreeing-severity pair -- see
       tests/fixtures/make_drugcentral_subset.py's selection notes) resolve
       to two distinct moiety_uuids and exercise drugcentral_ddi_pair's
       orientation-collapse and most-severe-wins tie-break for real.
    2. A REAL self-pair: 'acetaminophen' is registered as the display_name
       for _ACETAMINOPHEN_AND_SULFINPYRAZONE, and a SECOND identity_claim
       carries sulfinpyrazone's own InChIKey
       ('MBGGBVCUIVRRBF-UHFFFAOYSA-N', read straight from the fixture's
       `structures` table) against that SAME moiety. So 'acetaminophen'
       resolves via the display_name route and 'sulfinpyrazone' resolves via
       the InChIKey route, and both land on ONE moiety -- ddi row 870
       becomes a genuine self-pair. This is the ONLY thing that can catch a
       regression that swaps the counting loop's `if record.self_pair: ...
       elif record.resolved: ...` order (self_pair is a STRICT SUBSET of
       resolved, per AssertionRecord.self_pair's own docstring): with the
       checks swapped, a self-pair row is `resolved` too, so it would be
       silently folded into the `resolved` bucket and `rows_self_pair` would
       read 0 no matter how many genuine self-pairs the run produced.
    3. A REAL duplicate registry key: TWO 'gatifloxacin' moieties are
       registered (_GATIFLOXACIN_LOW, _GATIFLOXACIN_HIGH, HIGH inserted
       FIRST -- see the loop below for why that order is itself part of the
       test). load_registry's `ORDER BY display_name, moiety_uuid` +
       `first_wins` must resolve every 'gatifloxacin' endpoint to the LOWER
       uuid, deterministically, and report the collision through
       `duplicate_keys` rather than silently dropping it -- exercising the
       live SQL, not just first_wins's own pure unit tests
       (tests/test_drugcentral_resolve.py). Only the display_name path is
       exercised here; this fixture adds no duplicate InChIKey or CAS claim,
       so the same over-determination risk does not arise for those two
       routes -- there is nothing here for it to apply to.

    'cortisone' (ddi row 1288's other endpoint) is deliberately left
    unregistered: it is absent from the fixture's own `structures`/`synonyms`
    tables too, so that row stays an honest, unresolved miss -- the fourth
    bucket every other DB-gated test in this module already covers.

    Returns the seed run's ingest_run_id, in case a future test needs it.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    # ORDER IS LOAD-BEARING: _GATIFLOXACIN_HIGH is inserted BEFORE
    # _GATIFLOXACIN_LOW, deliberately opposing the winner the test expects.
    # first_wins is a FIFO fold over whatever order the SQL hands it back, so
    # if load_registry's `ORDER BY display_name, moiety_uuid` ever lost its
    # `, moiety_uuid` tiebreak, `ORDER BY display_name` alone would fall back
    # to some unspecified (commonly insertion) order -- and on this tiny,
    # freshly-populated table that unspecified order tends to match insertion
    # order, so a test that seeded LOW first would keep passing for the wrong
    # reason: insertion order, not the SQL tiebreak, would be what "won".
    # Seeding HIGH first means the two can only agree if `, moiety_uuid` is
    # genuinely doing the work -- verified directly in the fix report by
    # removing that clause and confirming this exact test then fails.
    for moiety_uuid, name in (
            (_GATIFLOXACIN_HIGH, "gatifloxacin"),
            (_GATIFLOXACIN_LOW, "gatifloxacin"),
            (_PIOGLITAZONE, "pioglitazone"),
            (_ACETAMINOPHEN_AND_SULFINPYRAZONE, "acetaminophen")):
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
            (moiety_uuid, name, seed_run))
    conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'INCHIKEY', 'MBGGBVCUIVRRBF-UHFFFAOYSA-N', %s)",
        (_ACETAMINOPHEN_AND_SULFINPYRAZONE, seed_run))
    conn.commit()
    return seed_run


def test_the_summary_refuses_to_exist_unless_its_buckets_sum():
    with pytest.raises(ValueError, match="do not sum"):
        drugcentral_run.DrugCentralSummary(
            rows_read=10, rows_excluded_by_reference=2, rows_bundleable=8,
            rows_resolved=5, rows_self_pair=0, rows_unresolved=1,  # 6, not 8
            pairs=5, duplicate_keys=0)


def test_the_summary_accepts_buckets_that_sum():
    summary = drugcentral_run.DrugCentralSummary(
        rows_read=10, rows_excluded_by_reference=2, rows_bundleable=8,
        rows_resolved=7, rows_self_pair=0, rows_unresolved=1,
        pairs=7, duplicate_keys=0)
    assert summary.rows_bundleable == 8


def test_the_summary_refuses_a_read_count_that_excludes_more_than_it_read():
    with pytest.raises(ValueError, match="do not sum"):
        drugcentral_run.DrugCentralSummary(
            rows_read=10, rows_excluded_by_reference=3, rows_bundleable=8,
            rows_resolved=8, rows_self_pair=0, rows_unresolved=0,
            pairs=8, duplicate_keys=0)


@pytest.fixture
def _clean(conn):
    """ingest_drugcentral COMMITS, so the conn fixture's rollback cannot undo it.
    Same shape as tests/test_ingest_run.py's autouse truncate fixture.

    Also truncates substance_moiety and identity_claim, EXPLICITLY rather than
    relying on the CASCADE from ingest_run alone: the registry-seeded test
    below (test_a_seeded_registry_...) commits real moiety and identity_claim
    rows via _seed_registry so the resolution cascade fires for real, and
    those must not survive to the next test file any more than the
    DrugCentral rows do (review round, "make sure cleanup cannot leak
    seeded moieties"). Listed explicitly, matching test_ingest_run.py's own
    style, so a reader does not have to trace the FK graph to see what this
    clears."""
    yield
    conn.execute("TRUNCATE drugref.drugcentral_ddi_assertion, "
                 "drugref.open_question, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run CASCADE")
    conn.commit()


@pytest.mark.usefixtures("_clean")
def test_the_fixture_dump_ingests_and_reconciles(conn):
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    assert summary.rows_excluded_by_reference > 0, "the rule-6 filter did nothing"
    assert summary.rows_bundleable > 0
    stored = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone()[0]
    assert stored == summary.rows_bundleable


@pytest.mark.usefixtures("_clean")
def test_no_excluded_row_reaches_the_database(conn):
    """Rule 6 enforced by EXECUTION, not by reading the filter."""
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    leaked = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion "
        "WHERE upstream_label LIKE '%redacted%'").fetchone()[0]
    assert leaked == 0


@pytest.mark.usefixtures("_clean")
def test_a_second_ingest_replaces_rather_than_accumulates(conn):
    first = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    stored = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone()[0]
    assert stored == first.rows_bundleable


@pytest.mark.usefixtures("_clean")
def test_a_renumbered_reference_writes_nothing_at_all(conn, tmp_path):
    """The refusal must leave the database exactly as it was -- including no
    ingest_run row, which is why the guard runs before open_run."""
    forged = _forged_dump(tmp_path, "forged.sql.gz",
                          ("Veterans Health Administration", "Lexicomp"))
    before = conn.execute("SELECT count(*) FROM drugref.ingest_run").fetchone()[0]
    with pytest.raises(drugcentral.ReferenceIdentityError):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=forged, release="11012023")
    after = conn.execute("SELECT count(*) FROM drugref.ingest_run").fetchone()[0]
    assert after == before


@pytest.mark.usefixtures("_clean")
def test_two_blank_source_ids_abort_rather_than_silently_storing_fewer_rows(
        conn, tmp_path):
    """The stored count must be reconciled against the reported one, not assumed.

    `resolve_row` falls back to `""` when `source_id` is NULL, and the writer's
    ON CONFLICT DO NOTHING return value is deliberately ignored, so two rows with
    a NULL `source_id` in one dump collide on the empty upstream_key: the second
    insert is skipped, the table ends up holding ONE FEWER row than
    `rows_bundleable` says, and every Python-side identity in DrugCentralSummary
    still sums perfectly because all of them count records the loop MADE rather
    than rows the table KEPT.

    This forgery blanks the `source_id` of ddi rows 870 and 1288 -- both ref-2,
    both bundleable -- so the collision happens between two rows of this same
    run, which is the only way the PRIMARY KEY (ingest_run, source, upstream_key)
    can be hit at all.

    The trigger matters as much as the failure: the orchestrator's comment used
    to scope the repeated-key risk to widening BUNDLEABLE_REF_IDS or changing
    upstream_key's source column, and a blank `source_id` is NEITHER of those.
    That is why the guard reads the table rather than relying on the comment
    being revisited.
    """
    forged = _forged_dump(
        tmp_path, "blank-keys.sql.gz",
        ("\tC23303775029507\n", "\t\\N\n"),   # ddi row 870
        ("\tC23304976819400\n", "\t\\N\n"))   # ddi row 1288
    with pytest.raises(ValueError, match="does not hold what the summary"):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=forged, release="11012023")
    # ABORTED, not half-written: the raise happens inside the work transaction,
    # so the rollback leaves no assertion row behind at all.
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (0,)


@pytest.mark.usefixtures("_clean")
def test_a_seeded_registry_resolves_collapses_and_picks_the_lower_uuid(conn):
    """The ONE test in this module that runs the resolution cascade for real.

    Review round, Finding 1: every OTHER DB-gated test here ingests against
    an empty registry, so `resolved == self_pair == 0` on every row and the
    orchestrator's `elif record.resolved` branch is never taken -- a
    regression that swapped the self_pair/resolved check order would pass
    the whole suite. _seed_registry's docstring explains exactly how this
    test's four seeded rows turn the fixture's four bundleable ddi rows into
    one real resolved pair, one real self-pair, one real duplicate registry
    key, and one honest miss -- all four buckets, all exercised through
    `ingest_drugcentral` itself rather than through the pure resolver's own
    isolated unit tests.
    """
    _seed_registry(conn)
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")

    # ddi rows 15 and 2890: gatifloxacin/pioglitazone, both orders, resolve to
    # TWO DIFFERENT moieties -- two genuinely resolved (non-self-pair) rows.
    assert summary.rows_resolved == 2
    # ddi row 870: acetaminophen/sulfinpyrazone resolve to ONE moiety -- the
    # self-pair _seed_registry constructs. This is the assertion a swapped
    # self_pair/resolved check order breaks: see the mutation check in the
    # fix report for the reviewer.
    assert summary.rows_self_pair == 1
    # ddi row 1288: cortisone never resolves (registered nowhere, and absent
    # from the fixture's own structures/synonyms tables), so the whole row
    # stays an honest miss.
    assert summary.rows_unresolved == 1

    # Finding 1's own request: drugcentral_ddi_pair collapses the both-order
    # pair to ONE row, and the more severe orientation (row 15, 'Critical')
    # wins over the reverse orientation (row 2890, 'Significant').
    pair_rows = conn.execute(
        "SELECT severity FROM drugref.drugcentral_ddi_pair "
        "WHERE moiety_lo = least(%s, %s) AND moiety_hi = greatest(%s, %s)",
        (_GATIFLOXACIN_LOW, _PIOGLITAZONE,
         _GATIFLOXACIN_LOW, _PIOGLITAZONE)).fetchall()
    assert len(pair_rows) == 1, "the both-order pair must collapse to ONE row"
    assert pair_rows[0][0] == "contraindicated", (
        "Critical (row 15) must win over Significant (row 2890)")

    # A self-pair asserts nothing about an interaction between two drugs, so
    # it must be ABSENT from drugcentral_ddi_pair, not merely un-severe.
    self_paired = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_pair "
        "WHERE moiety_lo = %s AND moiety_hi = %s",
        (_ACETAMINOPHEN_AND_SULFINPYRAZONE,
         _ACETAMINOPHEN_AND_SULFINPYRAZONE)).fetchone()[0]
    assert self_paired == 0

    # Finding 2: the duplicate 'gatifloxacin' display_name is counted, and
    # resolves deterministically to the LOWER of the two registered uuids.
    assert summary.duplicate_keys >= 1
    row_15 = conn.execute(
        "SELECT moiety_1_uuid, moiety_2_uuid FROM drugref.drugcentral_ddi_assertion "
        "WHERE upstream_key = 'C23308143128526'").fetchone()
    assert _GATIFLOXACIN_LOW in row_15
    assert _GATIFLOXACIN_HIGH not in row_15
