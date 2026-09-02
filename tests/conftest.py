# tests/conftest.py
"""Shared DB fixtures. DB-gated tests are SKIPPED unless DRUGREF_TEST_DSN is set,
so unit tests still run anywhere. The `conn` fixture rolls back after each test,
which isolates tests whose code under test never commits. Isolation is
rollback-based, not transaction-enforced: code under test that calls
conn.commit() itself (e.g. an orchestrator that commits per run) escapes the
rollback, so a test module exercising such code needs its own explicit
cleanup (see tests/test_ingest_run.py's autouse truncate fixture)."""
import os
import pytest
import psycopg
from drugref import db
from tests.suite_count import COLLECTED_TESTS


def dsn_verdict(dsn, in_ci):
    """PURE: what to do about the DSN. Returns ("use"|"fail"|"skip", detail).

    Split out of the fixture below so the CI branch can be TESTED. It could not be
    before: it never runs locally (the DSN is set) and never runs in CI (the DSN is set
    there too, by ci.yml), so the one gate standing between this suite and a vacuous
    green had never been observed firing -- the same shape as the three gates issue 74,
    66 and 76 were filed about. tests/test_dsn_verdict.py drives all three verdicts
    without a database or an environment.
    """
    if dsn:
        return "use", dsn
    if in_ci:
        return "fail", (
            "DRUGREF_TEST_DSN is not set. Most of this suite is DB-gated, so a "
            "CI run without a database would pass while testing none of it.")
    return "skip", "DRUGREF_TEST_DSN not set — skipping DB-gated test"


# --- issue 146: how many tests this run collected ---------------------------------
#
# The suite count in docs/PROJECT-NOTES.md § "How to run / test" calls itself THE ONE
# HOME FOR THIS NUMBER and has drifted from the real suite NINE times, against a comment
# rewritten three times to prevent it. tests/test_suite_count.py turns it into a gate;
# these two hooks are what give that gate something to measure.
#
# THE COUNT IS TAKEN IN-PROCESS rather than from a `--collect-only` subprocess (issue
# 146 rejected that as slow and fragile), so it cannot disagree with the run it belongs
# to.

#: Deselected items in THIS collection, accumulated across every plugin that deselects
#: any. A module-level counter because `pytest_deselected` is not handed the `Config`;
#: it is folded into the stash below, which is where anything else reads it from, and
#: it is reset at the start of every collection (see `pytest_collection`).
_deselected = 0


def pytest_collection(session):
    """Reset the deselection counter at the START of each collection.

    ⇒ WITHOUT THIS THE COUNTER IS MONOTONIC FOR THE LIFE OF THE INTERPRETER, because
    it is a module global. A second in-process run -- which
    `test_suite_count.py`'s two `pytester` tests are -- would read the first run's
    deselections into its own total and report a number no invocation produced.

    `pytest_collection` is a FIRSTRESULT hook: returning None (as this does) means
    "carry on", and pytest's own implementation then does the collecting. Returning
    anything else here would replace collection entirely.
    """
    global _deselected
    _deselected = 0


def pytest_deselected(items):
    """Count what -k, -m, --deselect and --sw took out of the collection.

    ⇒ WHY THESE ARE ADDED BACK. CI runs `uv run pytest -q -m "not livepage"`, which
    deselects exactly one test, so the number CI could compare against would be one
    lower than the number a local `uv run pytest` produces -- and PROJECT-NOTES can
    only state one of them. Counting deselected items back in makes both runs measure
    the same thing: pytest's own "collected N items" figure, before any exclusion.

    `--lf` IS NOT IN THAT LIST, and an earlier version of this docstring wrongly said
    it was. `--lf` prunes collection via `LFPluginCollSkipfiles.pytest_ignore_collect`
    -- the items never exist to be deselected -- which is why it is handled the other
    way, as a ledgered narrowing option in tests/suite_count.py.
    """
    global _deselected
    _deselected += len(items)


def pytest_collection_finish(session):
    """Publish the run's collected total for tests/test_suite_count.py to read.

    `pytest_collection_finish` runs AFTER `pytest_collection_modifyitems`, which is
    where every deselection happens, so `len(session.items)` here is the selected
    count and the counter above holds the rest.
    """
    session.config.stash[COLLECTED_TESTS] = len(session.items) + _deselected


@pytest.fixture(scope="session")
def _dsn():
    # Skipping locally is a convenience; skipping in CI is a trap. Over half
    # the suite is DB-gated -- every schema, trigger, floor, writer and
    # orchestrator test -- and pytest exits 0 on a run that skipped all of
    # them, so an unset DSN would report green on a completely unexercised
    # database layer. In CI that is a failure, not a skip.
    #
    # PRESENCE of CI, not its truthiness: some runners export CI="", which
    # `os.environ.get("CI")` reads as falsy and would quietly downgrade the failure
    # back to a skip -- reintroducing the vacuous green this branch exists to prevent.
    # The asymmetry decides it: a local developer who happens to export CI="" gets a
    # loud, self-explaining failure, whereas the other way round CI reports success on
    # an untested database layer.
    verdict, detail = dsn_verdict(os.environ.get("DRUGREF_TEST_DSN"), "CI" in os.environ)
    if verdict == "fail":
        pytest.fail(detail)
    if verdict == "skip":
        pytest.skip(detail)
    return detail


@pytest.fixture(scope="session")
def _migrated(_dsn):
    """Drop and recreate the drugref schema once, then apply migrations."""
    with psycopg.connect(_dsn) as conn:
        conn.execute("DROP SCHEMA IF EXISTS drugref CASCADE")
        conn.commit()
        db.apply_migrations(conn)
    return _dsn


@pytest.fixture
def conn(_migrated):
    """A connection whose work is rolled back after each test."""
    with psycopg.connect(_migrated) as c:
        yield c
        c.rollback()


@pytest.fixture
def an_uningested_registry(conn, _migrated):
    """A registry with nothing in it -- ESTABLISHED, not inherited from the run order.

    ⇒ WHY THIS EXISTS. Two tests assert a GLOBAL precondition -- that the registry holds
    no moieties -- which neither of them creates:
    `test_registry_read.py::test_registry_is_empty_on_a_migrated_but_uningested_database`
    and `test_cli_interactions.py::test_an_empty_registry_is_not_blamed_on_the_operators_typing`.
    Half this suite's orchestrator tests commit -- `test_cli.py::test_ingest_unii_end_to_end`
    registers real moieties on its own connection with an explicit commit -- so the only
    reason either assertion held was that other modules TRUNCATE in an autouse fixture
    and these two files happen to sort after some of them. (HOW MANY OTHER MODULES IS
    NOT WRITTEN DOWN HERE. `grep -l TRUNCATE tests/*.py` answers it, and ROADMAP §
    "Floor hardening" records that the same count, restated in prose, was wrong four
    times running.) Both accidents reproduce in about two seconds ON A TREE WITHOUT
    THIS FIXTURE:

        uv run pytest tests/test_cli.py tests/test_registry_read.py
        uv run pytest tests/test_cli.py tests/test_cli_interactions.py

    In conftest rather than in one of those modules because pytest resolves conftest
    fixtures BY NAME with no import at all, and a cross-file fixture import couples two
    suites for no benefit -- the precedent recorded for the curated-overlay fixtures
    below. The first version of this fixture lived in test_registry_read.py and the
    review that read it found the identical bug one file away, which is the argument.

    **THE TRUNCATE IS NOT COMMITTED, which is what makes it safe here.** TRUNCATE is
    transactional in PostgreSQL -- rows AND the `RESTART IDENTITY` sequence reset are
    both undone -- and the `conn` fixture rolls back after every test, so every
    committed row this suite has accumulated is still there for the next module. That
    is the difference between this fixture and the autouse ones in test_ingest_run.py
    and friends, which commit because the code THEY exercise commits.

    **CASCADE REACHES MOST OF THE SCHEMA, not the three tables named below**, because
    everything FKs `ingest_run` -- 43 of 66 tables when this was measured, and the
    direction of travel is upward. That is harmless only for as long as the transaction
    is rolled back, which is why the teardown below stops being prose and checks.

    It has to be TRUNCATE rather than DELETE: the append-only floor's row-level
    triggers refuse a DELETE on `substance_moiety` and `identity_claim` outright (the
    third table, `ingest_run`, carries no such trigger and is held only by the FKs), and
    not covering TRUNCATE is precisely the documented bypass (ROADMAP § "Floor
    hardening").
    """
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    # The transaction id of the wipe, so the teardown can ask the server what became of
    # it. Taken AFTER the TRUNCATE, which has already forced an xid to be assigned.
    wipe_xid = conn.execute("SELECT pg_current_xact_id()").fetchone()[0]
    yield conn
    # ⇒ THE SAFETY ARGUMENT, AS A GATE RATHER THAN AS A PARAGRAPH. Everything above
    # rests on one line in the `conn` fixture (`c.rollback()`), and psycopg's `with`
    # block COMMITS on a clean exit -- so a future test in either module that calls
    # `conn.commit()`, or a wrapper like tests/test_cli_signing.py's that lets a
    # handler's own commit through, would make this wipe durable and take the whole
    # schema's committed cross-module state with it. That failure would surface as order-
    # dependent breakage somewhere else entirely, which is the exact diagnosis-
    # resistant shape this fixture was added to eliminate. So end our own transaction
    # and ask the server, on a second connection, whether it committed.
    conn.rollback()
    with psycopg.connect(_migrated) as probe:
        status = probe.execute("SELECT pg_xact_status(%s)", (wipe_xid,)).fetchone()[0]
    assert status == "aborted", (
        f"the TRUNCATE in an_uningested_registry was {status}, not rolled back. It "
        f"CASCADEs across most of the drugref schema, so a committed wipe destroys "
        f"every row the orchestrator tests have accumulated and breaks unrelated "
        f"modules later in the run -- somewhere else entirely, which is the hardest "
        f"kind of failure to diagnose and the exact one this fixture exists to stop. "
        f"Whatever committed this connection must not use this fixture.")


@pytest.fixture
def assert_live_key_index(conn):
    """Assert that a single-live natural-key index has ALL FOUR properties the
    append-only overlay depends on. Shared here rather than imported across test
    files, following the precedent set when the curated-overlay fixtures were moved
    into conftest: a cross-file test import couples two suites for no benefit.

    FOUR, not three. Earlier drafts of this docstring counted the first three and then
    asserted the column list as well, and the prose said "three" in four separate files
    while the code checked four things -- in the one fixture whose whole job is stopping
    a load-bearing clause from being silently dropped. The column list IS a property, it
    is the one that survived 936 green tests, and it is numbered below with the rest.

    Seven tables now carry one of these indexes and every one of them needs the same
    four things to be true, so the property lives in exactly one place:

    1. **It exists, by name.** Nothing but the single-live trigger ever reads it, so
       it looks unused to a catalog sweep and only a test stops it being dropped.
       db/023 measured the cost of its absence: the deferred check at COMMIT becomes
       a sequential scan per row, and a 2,000-row load went 42 ms -> 5,773 ms.
    2. **It is PARTIAL over live rows.** A full index answers a different question
       than the trigger asks.
    3. **It is NON-UNIQUE.** This is the one an existence check cannot see, and the
       reason issue 74 was filed. A correction is briefly TWO live rows on the same
       natural key -- INSERT the new row, then UPDATE the old one to point at it --
       and a partial index cannot be declared DEFERRABLE, so a UNIQUE one would be
       enforced at statement time and reject every correction the overlay exists to
       make. The schema would still be green on every other test in this suite.

    4. **It indexes exactly the right COLUMNS.** Dropping a column from an index
       (matching a drop from the trigger's argument list) leaves properties 1-3 all
       true while silently indexing the wrong key -- that exact mutation survived 936
       green tests during slice 5c.1 and was caught by review, not by the suite.

    `indexdef` is read rather than `pg_index.indisunique`/`indpred` because it is the
    one place all four properties are visible in a single string.
    """
    def _assert(index_name, table, columns):
        row = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'drugref' "
            "AND tablename = %s AND indexname = %s", (table, index_name)).fetchone()
        assert row is not None, f"{index_name} is missing from drugref.{table}"
        (indexdef,) = row
        assert "WHERE (superseded_by IS NULL)" in indexdef, (
            f"{index_name} must be PARTIAL over live rows; a full index answers a "
            f"different question than the trigger asks. Got: {indexdef}")
        assert "UNIQUE" not in indexdef, (
            f"{index_name} must be NON-UNIQUE: a correction is briefly two live rows "
            f"on the same natural key, and a partial index cannot be DEFERRABLE, so "
            f"UNIQUE here forbids every correction. Got: {indexdef}")
        assert f"({columns})" in indexdef, (
            f"{index_name} must index exactly ({columns}) -- the trigger's natural "
            f"key. A shorter list is still partial and still non-unique while "
            f"indexing the wrong key. Got: {indexdef}")
    return _assert


@pytest.fixture
def ingest_run_id(conn):
    """A committed-in-transaction ingest_run row for provenance FKs.

    Says which writer it stands in for (db/025): `writer` is NOT NULL with no
    DEFAULT, so every insert must name one, and naming a real one keeps the fixture
    honest about what it is simulating.
    """
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('PBS', 'test', 'test', 'pbs_run') RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def a_moiety(conn, ingest_run_id):
    """One registered moiety, for tests that need a live FK target."""
    from drugref import ids
    moiety_uuid = ids.mint_moiety_uuid("TESTUNII01")
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'testdrug', %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, ingest_run_id))
    return moiety_uuid


# ---- shared across the curated-overlay test modules (db/029, slice 5c.1) ----------
# Both fixtures below were originally defined in tests/test_curated_read_path.py and
# moved here once tests/test_curated_gap_views.py needed the same setup: pytest
# resolves conftest fixtures BY NAME with no import at all, so this is the
# established way to share a fixture across modules in this repo (a_moiety and
# ingest_run_id above are the standing example). Importing a fixture function across
# test modules instead is a workaround this repo has already rejected -- see
# tests/test_gap_views.py's test_the_condition_views_grain_is_the_gap_keys_grain,
# which renames rather than re-imports a colliding test, with a comment saying why.


@pytest.fixture
def a_graded_rule(conn, a_moiety, ingest_run_id):
    """One CI_MoA rule with a member on its axis, and drugref's grade on the rule.

    NOTE: the name is historical. It sets up a rule and its membership but
    deliberately does NOT grade it -- grading is left to the calling test -- which is
    exactly what makes it usable, unmodified, as the "uncurated rule" fixture in
    tests/test_curated_gap_views.py as well as the "graded once curated" fixture
    here.
    """
    from drugref import ids
    from tests.test_curated_overlay import _a_class
    klass = _a_class(conn, ingest_run_id)
    partner = ids.mint_moiety_uuid("TESTUNII02")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'partnerdrug', %s) ON CONFLICT DO NOTHING",
        (partner, ingest_run_id))
    # NOTE class_membership has NO `source` column -- unlike class_contraindication
    # below, and unlike every moiety_condition_* table. db/003 made the class registry
    # source-neutral; adding one here fails with UndefinedColumn.
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, 'has_MoA', %s) ON CONFLICT DO NOTHING",
        (partner, klass, ingest_run_id))
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (a_moiety, klass, ingest_run_id))
    return {"subject": a_moiety, "partner": partner, "class": klass}


@pytest.fixture
def a_contradicted_pair(conn, a_moiety, ingest_run_id):
    """Issue 51's shape: one pair asserted as BOTH may_treat and CI_with."""
    from drugref import interactions
    from tests.test_curated_overlay import _a_condition
    condition = _a_condition(conn, ingest_run_id)
    interactions.add_condition_contraindication(
        conn, a_moiety, condition, "CI_with", "MED-RT", ingest_run_id)
    conn.execute(
        "INSERT INTO drugref.moiety_condition_indication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'may_treat', 'MED-RT', %s)",
        (a_moiety, condition, ingest_run_id))
    return {"moiety": a_moiety, "condition": condition}


def clean_scan(**overrides):
    """A `ScanResult` with every counter at zero, in ONE home.

    ⇒ THIS HELPER WAS WRITTEN TWICE, and the two copies had to be edited in step
    every time a counter was added -- the same two-homes shape `ScanResult`'s own
    module argues against for vocabularies. It lives here so a new counter is
    spelled once.

    Every field is spelled and none is defaulted, because `ScanResult` refuses
    defaults on purpose: a counter added to the type must break every
    construction site loudly rather than quietly reading zero. That is the
    property this helper must not undo, so it does not use `dataclasses.fields`
    to fill the gaps -- doing so would restore exactly the silence the type
    exists to prevent.
    """
    from drugref.ingest import spl_release

    fields = dict(
        documents_read=10, found={}, dropped_no_set_id_bytes=0,
        dropped_unreadable=0, dropped_prefilter_disagreed=0,
        dropped_no_xml_member=0, dropped_several_xml_members=0,
        dropped_unreadable_member_zip=0, skipped_not_a_member_zip=0,
        dropped_untrustworthy_prefilter=0, dropped_junk_version=0,
        dropped_unknown_class_code_unii=0, skipped_unknown_class_code=0,
        unknown_class_codes=frozenset())
    return spl_release.ScanResult(**(fields | overrides))
