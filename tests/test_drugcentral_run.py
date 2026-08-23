# tests/test_drugcentral_run.py
"""The orchestrator: it reconciles, and it is the only writer.

Issue 71's standing rule, re-learned by curate_onchigh and again by the
re-measurement's Measurement guard: a summary whose buckets do not sum is a
number that cannot be checked, and every row must land in exactly one of them.
"""
import gzip
import pathlib
import uuid

import psycopg
import pytest

from drugref.ingest import drugcentral, drugcentral_run
from drugref.ingest.checksum import checksum

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


def _summary(**overrides):
    fields = dict(rows_read=10, rows_excluded_by_reference=2, rows_bundleable=8,
                  rows_resolved=7, rows_self_pair=0, rows_unresolved=1,
                  rows_blank_endpoint=0, pairs=7, duplicate_keys=0)
    return drugcentral_run.DrugCentralSummary(**(fields | overrides))


def test_the_summary_refuses_to_exist_unless_its_buckets_sum():
    with pytest.raises(ValueError, match="do not sum"):
        _summary(rows_resolved=5)               # 5 + 0 + 1 + 0 = 6, not 8


def test_the_summary_accepts_buckets_that_sum():
    assert _summary().rows_bundleable == 8


def test_the_blank_endpoint_bucket_counts_toward_the_identity():
    """A fourth bucket that did not have to sum would be a place to lose rows."""
    assert _summary(rows_resolved=6, rows_blank_endpoint=1,
                    pairs=6).rows_bundleable == 8
    with pytest.raises(ValueError, match="blank-endpoint"):
        _summary(rows_blank_endpoint=1)          # 7 + 0 + 1 + 1 = 9, not 8


def test_the_summary_refuses_a_read_count_that_excludes_more_than_it_read():
    with pytest.raises(ValueError, match="do not sum"):
        _summary(rows_excluded_by_reference=3, rows_resolved=8, rows_unresolved=0)


def test_the_summary_refuses_more_pairs_than_resolved_rows():
    """The ONE check here that is not tautological at the call site.

    Both bucket identities are satisfied by construction where the summary is
    built -- `rows_excluded` is computed as `read - bundleable`, and the counting
    loop dispatches on a TOTAL enum. `pairs` is the only field read back out of
    the database and it was checked by nothing. Each resolved row yields at most
    one distinct unordered pair, so exceeding that means the count came from the
    wrong relation or another run's rows are still resident.
    """
    with pytest.raises(ValueError, match="exceeds resolved rows"):
        _summary(pairs=8)


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
    # EXACT FIGURES, not `> 0`. The committed fixture is deterministic -- 8 ddi
    # rows, 4 under ref 2, 2 under ref 1 (Stockley's) and 2 under ref 3 (Lexicomp)
    # -- so these pin the rule-6 filter's ARITHMETIC, where `> 0` pinned only that
    # it did something. A filter that admitted three of the four, or excluded one
    # ref-2 row, passed the old assertions unchanged.
    assert (summary.rows_read, summary.rows_excluded_by_reference,
            summary.rows_bundleable) == (8, 4, 4)
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
def test_ONE_blank_source_id_aborts_rather_than_publishing_an_unciteable_key(
        conn, tmp_path):
    """One blank is already a defect. It used to take two to be noticed.

    `resolve_row` falls back to `""` when `source_id` is NULL, and the read-back
    reconciliation compares COUNTS -- so a single blank was a perfectly valid row
    as far as it could tell, and the ingest published a row keyed by the empty
    string and reported clean success. db/049 calls this column "the upstream
    AUTHORITY's identifier ... which is what a key anything downstream might cite
    has to be", and drugcentral_ddi_pair uses it as the total-order tie-break that
    makes most-severe-wins reproducible: the empty string sorts before every real
    key, so a blank silently wins every tie it takes part in.

    db/050's CHECK moves the refusal to the FIRST blank, inside the write loop,
    where the rollback still undoes everything.
    """
    forged = _forged_dump(tmp_path, "one-blank-key.sql.gz",
                          ("\tC23303775029507\n", "\t\\N\n"))   # ddi row 870
    with pytest.raises(psycopg.errors.CheckViolation,
                       match="drugcentral_ddi_assertion_key_present"):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=forged, release="11012023")
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (0,)


@pytest.mark.usefixtures("_clean")
def test_the_orchestrator_ROLLS_BACK_a_failed_run_without_the_caller_helping(
        conn, tmp_path):
    """The rollback under test is the ORCHESTRATOR's, not the test's.

    Its sibling above used to call `conn.rollback()` itself before counting, so
    the assertion proved only that the test had rolled back -- deleting
    `conn.rollback()` from `ingest_drugcentral`'s `except` clause left the whole
    suite green. Here the caller does NOT roll back: if the orchestrator did not,
    the connection would still be in an aborted transaction and the count below
    would raise InFailedSqlTransaction instead of returning 0.
    """
    forged = _forged_dump(tmp_path, "rollback-probe.sql.gz",
                          ("\tC23303775029507\n", "\t\\N\n"))
    with pytest.raises(psycopg.errors.CheckViolation):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=forged, release="11012023")
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (0,)


@pytest.mark.usefixtures("_clean")
def test_a_blank_ENDPOINT_lands_in_its_own_bucket_rather_than_beside_real_misses(
        conn, tmp_path):
    """A malformed row must be visible SOMEWHERE, and this is the only place left.

    It is excluded from drugcentral_ddi_pair by the NULL-uuid filter and from
    gap_unresolved_ddi_endpoint by the `<> ''` filter that has to be there (a blank
    is not a question anyone can answer). Before the route existed it resolved to
    `not_a_substance` -- documented as "A CORRECT miss" -- and was summed into
    rows_unresolved beside genuine misses, so nothing at any layer could report it.
    """
    forged = _forged_dump(tmp_path, "blank-endpoint.sql.gz",
                          ("870\tacetaminophen\t", "870\t\\N\t"))
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=forged, release="11012023")
    assert summary.rows_blank_endpoint == 1
    assert summary.rows_unresolved == 3, "a blank must not be counted as a miss"
    assert conn.execute(
        "SELECT route_1 FROM drugref.drugcentral_ddi_assertion "
        "WHERE endpoint_1_name = ''").fetchone() == ("blank_endpoint",)
    # ... and it is still correctly absent from the curator worklist.
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unresolved_ddi_endpoint "
        "WHERE endpoint_name = ''").fetchone() == (0,)


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


@pytest.mark.usefixtures("_clean")
def test_a_real_ingest_MINTS_the_questions_its_gap_view_derives(conn):
    """`register_from_gaps` was deletable from the orchestrator, suite still green.

    The eighteenth question kind was only ever driven by tests calling
    `questions.register_from_gaps` DIRECTLY against hand-inserted rows, so nothing
    proved a real ingest publishes anything to the review gate. Both sibling
    orchestrators assert this (test_onchigh_run, test_fda_cyp_run); this one did
    not. The failure it hides is quiet: the gap VIEW keeps returning rows, so a
    curator sees an empty worklist and no error anywhere.
    """
    _seed_registry(conn)
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    # ddi row 1288 is cortisone/rifabutin, and _seed_registry deliberately
    # registers NEITHER -- both are absent from the fixture's own structures and
    # synonyms too, so they are honest misses and are the questions this run owes.
    assert conn.execute(
        "SELECT gap_key FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ddi_endpoint' ORDER BY gap_key"
    ).fetchall() == [("DRUGCENTRAL:ENDPOINT:cortisone",),
                     ("DRUGCENTRAL:ENDPOINT:rifabutin",)]


@pytest.mark.usefixtures("_clean")
def test_a_SUPERSEDED_identity_claim_must_not_resurrect_a_resolution(conn):
    """Deleting `superseded_by IS NULL` from the registry read left the suite green.

    `load_registry`'s docstring states the invariant and `Registry`'s restates it
    twice; nothing executed it, because no fixture ever seeded a superseded claim.
    The consequence is the worst in this slice after the licence guard: a WRONG
    MOIETY on a contraindication pair, derived from an identifier drugref has
    already retracted.

    Here sulfinpyrazone's InChIKey is claimed TWICE -- once live against the
    moiety that also owns 'acetaminophen' (making ddi row 870 a genuine
    self-pair), and once by a decoy moiety with `superseded_by` set. If the filter
    is gone the decoy is a second live claim for the same key, first_wins reports a
    collision that should not exist, and the row can resolve to the decoy instead.
    """
    seed_run = _seed_registry(conn)
    decoy = uuid.UUID("0000000f-0000-0000-0000-00000000000f")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
        (decoy, "retracted sulfinpyrazone", seed_run))
    # The claim to be RETRACTED: the decoy also claims sulfinpyrazone's InChIKey.
    retracted = conn.execute(
        "INSERT INTO drugref.identity_claim "
        "(moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'INCHIKEY', 'MBGGBVCUIVRRBF-UHFFFAOYSA-N', %s) "
        "RETURNING identity_claim_id", (decoy, seed_run)).fetchone()[0]
    # The claim that SUPERSEDES it -- a corrected identifier for the same moiety.
    # It must be a LATER claim (forbid_claim_rewrite enforces exactly that), and it
    # carries a different value so it competes for nothing the fixture looks up.
    correction = conn.execute(
        "INSERT INTO drugref.identity_claim "
        "(moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'INCHIKEY', 'AAAAAAAAAAAAAA-BBBBBBBBBB-C', %s) "
        "RETURNING identity_claim_id", (decoy, seed_run)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s "
                 "WHERE identity_claim_id = %s", (correction, retracted))
    conn.commit()

    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    # The retracted claim is INVISIBLE: no collision, and row 870 is still the
    # self-pair the live claim makes it.
    assert summary.duplicate_keys == 1, "only the display_name collision is real"
    assert summary.rows_self_pair == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion "
        "WHERE moiety_1_uuid = %s OR moiety_2_uuid = %s", (decoy, decoy)
    ).fetchone() == (0,)


@pytest.mark.usefixtures("_clean")
def test_the_run_row_records_the_release_the_checksum_and_the_WRITER(conn):
    """Four separate mutations survived here, all of them silent downstream.

    `upstream_release` and `source_checksum` are two strings in a matched pair, so
    transposing them at the `open_run` call typechecks and runs; `writer` could
    become 'unattributed', which provenance.py says "no code should ever write";
    and `finish_run` could be deleted entirely, leaving `finished_at` NULL forever
    -- which BOTH published views select as `ingested_at`. Nothing asserted any of
    it against a run this orchestrator actually opened.
    """
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    source, release, digest, writer, finished = conn.execute(
        "SELECT source, upstream_release, source_checksum, writer, finished_at "
        "FROM drugref.ingest_run WHERE source = 'DRUGCENTRAL'").fetchone()
    assert (source, release, writer) == ("DRUGCENTRAL", "11012023", "drugcentral_run")
    assert digest == checksum(FIXTURE), "the recorded checksum must be the dump's"
    assert release != digest, "release and checksum must not have been transposed"
    assert finished is not None, "an unstamped run makes ingested_at NULL in both views"
    assert summary.rows_bundleable == 4


@pytest.mark.usefixtures("_clean")
def test_the_summary_reports_the_PAIR_count_not_the_row_count(conn):
    """`summary.pairs` was read from the database and asserted nowhere.

    Pointing that count at `drugcentral_ddi_assertion` instead of
    `drugcentral_ddi_pair` left the suite green. The fixture makes the distinction
    free and it is the distinction that matters: rows, pairs and DISTINCT pairs are
    three different units, which PROJECT-NOTES records as having been quoted
    interchangeably before. Here two resolved rows -- the fixture's both-order,
    disagreeing-severity gatifloxacin/pioglitazone entries -- are ONE pair.
    """
    _seed_registry(conn)
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    assert summary.rows_resolved == 2
    assert summary.pairs == 1


@pytest.mark.usefixtures("_clean")
def test_the_read_back_is_SCOPED_to_this_run(conn):
    """No test ever had two DrugCentral runs' rows resident at once.

    Replacing the read-back's `WHERE ingest_run = %s` with a whole-table count left
    the suite green, so the scoping its own comment argues for ("a concurrent run's
    rows could never mask or manufacture a discrepancy") was unverified. A second
    ingest whose clear is defeated leaves the previous run's rows in place; an
    unscoped count would then see 8 where 4 were written and abort a correct run.
    """
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    stale = conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run "
        "WHERE source = 'DRUGCENTRAL'").fetchone()[0]
    # Re-parent this run's rows onto a DIFFERENT source so the per-source clear
    # cannot reach them: they stay in the table across the next ingest.
    other = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    conn.execute("UPDATE drugref.drugcentral_ddi_assertion SET ingest_run = %s "
                 "WHERE ingest_run = %s", (other, stale))
    conn.commit()

    # A correct ingest, with four foreign rows resident. Scoped, this reconciles.
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    assert summary.rows_bundleable == 4
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (8,)


def test_an_autocommit_connection_is_REFUSED_rather_than_silently_downgraded(conn):
    """PostgreSQL only whispers about this, so the ingest has to shout.

    Under autocommit every statement is its own transaction: `conn.rollback()` in
    the `except` clause rolls back nothing, and a failure between the per-source
    clear and `finish_run` leaves the projection cleared and half-rewritten -- the
    "worse than none" outcome this module says it refuses. Measured before the
    guard: the server answered the mis-placed SET TRANSACTION with a NOTICE rather
    than an error, psycopg discarded it, and the ingest reported success having
    lost its atomicity.
    """
    conn.commit()
    conn.autocommit = True
    try:
        with pytest.raises(ValueError, match="must not be handed an autocommit"):
            drugcentral_run.ingest_drugcentral(
                conn, dump_path=FIXTURE, release="11012023")
    finally:
        conn.autocommit = False


@pytest.mark.usefixtures("_clean")
def test_a_dump_this_code_cannot_read_leaves_the_PREVIOUS_projection_standing(
        conn, tmp_path):
    """The finding this whole round turned on, end to end.

    Renaming ONE column -- exactly what a re-publication is free to do -- used to
    take the projection from 4 rows to 0, report "0 bundleable of 8 rows (8
    excluded by rule 6)", and exit 0. Every guard passed vacuously: both bucket
    identities hold at 0 = 0 + 0, and the read-back holds because stored (0) equals
    len(bundleable) (0). The summary BLAMED RULE 6 for a loss rule 6 had no part in.
    """
    good = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    assert good.rows_bundleable == 4

    renamed = _forged_dump(tmp_path, "renamed-column.sql.gz",
                           ("ddi_ref_id, ddi_risk", "reference_id, ddi_risk"))
    with pytest.raises(drugcentral.DumpShapeError, match="ddi_ref_id"):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=renamed, release="11012024")
    conn.rollback()
    # The refusal left the database EXACTLY as it was -- rows and run row alike.
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (4,)
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_run "
        "WHERE source = 'DRUGCENTRAL'").fetchone() == (1,)


@pytest.mark.usefixtures("_clean")
def test_a_dump_with_nothing_bundleable_does_not_clear_what_the_last_one_published(
        conn, tmp_path):
    """A release that dropped NDF-RT is well-formed, and still must not run.

    Distinct from the shape refusal above and told apart from it, because they are
    different problems: this dump is perfectly readable and simply cites no
    reference rule 6 admits.
    """
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    # Every ref-2 row becomes ref-3 (Lexicomp), so nothing is bundleable.
    all_lexicomp = _forged_dump(tmp_path, "no-ndfrt.sql.gz", ("\t2\t", "\t3\t"))
    with pytest.raises(drugcentral.DumpShapeError, match="would publish nothing"):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=all_lexicomp, release="11012024")
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (4,)


@pytest.mark.usefixtures("_clean")
def test_the_SYNONYM_leg_of_the_cascade_resolves_end_to_end(conn):
    """`read_tables` could discard every `synonyms` row with the suite still green.

    The fixture's only synonym-routed name is 'acetaminophen' (struct 52, whose
    `structures.name` is 'paracetamol'), and _seed_registry registers
    'acetaminophen' as a DISPLAY_NAME -- so it resolved on route 1 and the synonym
    index was never consulted by any DB-gated test. `synonyms` is one of the two
    reasons WANTED_TABLES exists.

    Here 'acetaminophen' is registered ONLY by paracetamol's InChIKey, so the only
    way the endpoint can reach a moiety is: endpoint text -> synonyms -> struct_id
    -> InChIKey -> identity_claim. Route `inchikey`, not `display_name`.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    paracetamol = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'paracetamol-by-key-only', %s)", (paracetamol, seed_run))
    # Paracetamol's InChIKey, read straight from the fixture's `structures` table.
    conn.execute(
        "INSERT INTO drugref.identity_claim "
        "(moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'INCHIKEY', 'RZVAJINKPMORJF-UHFFFAOYSA-N', %s)",
        (paracetamol, seed_run))
    conn.commit()

    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    assert conn.execute(
        "SELECT route_1, moiety_1_uuid FROM drugref.drugcentral_ddi_assertion "
        "WHERE endpoint_1_name = 'acetaminophen'").fetchone() == (
        "inchikey", paracetamol)
