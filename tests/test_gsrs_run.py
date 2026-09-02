# tests/test_gsrs_run.py
"""The orchestrator: one transaction, one run record, worklist numbers not drops."""
import pathlib

import pytest

from drugref import ids
from drugref.ingest import gsrs_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gsrs_subset.gsrs"


@pytest.fixture(autouse=True)
def _clean(conn):
    """ingest_gsrs COMMITS, so it escapes the conn fixture's rollback. Same pattern
    as tests/test_ingest_run.py's autouse truncate.

    IT CLEANS THE SEED TOO, not just what the ingest wrote. `registry` below also
    commits -- it has to, because the orchestrator opens its own transaction and
    cannot see uncommitted rows -- so its moieties, claims and seed run outlived
    this file just as surely as the GSRS rows did. Nothing broke only because
    tests/test_ingest_run.py happens to sort later and truncates those three tables
    before each of its own tests; that is an accident of alphabetical ordering, not
    isolation. The leaked moieties carry no has_PE membership, so until that file
    ran they showed up in gap_unclassified_moiety for every test in between.

    TRUNCATE AND NOT DELETE, and that is forced by the architecture rather than
    chosen for speed: substance_moiety and identity_claim sit on slice 1's
    append-only floor (db/001, db/005), whose row-level triggers RAISE on DELETE --
    "drugref.identity_claim is append-only: DELETE forbidden". A committed seed
    therefore cannot be unpicked row by row at all, which is precisely why
    tests/test_ingest_run.py reaches for TRUNCATE too. TRUNCATE fires no row-level
    DELETE trigger, so it is the only tool that clears these tables, and CASCADE is
    required because ingest_run is the provenance parent of every projection.

    NEVER NARROW THIS TO gap_kind = 'unruled_composition_activity', which an earlier
    teardown here did and which the CASCADE now makes structurally impossible to get
    wrong again. register_from_gaps refreshes last_derived_ingest for EVERY currently
    open gap on every call, not only the ones this ingest caused: `registry` below
    registers bare moieties with no has_PE membership, so every GSRS run also
    re-derives gap_unclassified_moiety and stamps those rows with the GSRS run's id.
    Clearing only this slice's gap_kind left those other-kind rows pointing at a run
    the teardown was about to remove, and open_question.first_derived_ingest and
    .last_derived_ingest are both NOT NULL FKs into ingest_run -- so it raised. The
    lesson generalises past this file: a teardown scoped by the thing the test was
    ABOUT will miss whatever the code under test also touched on the way past.
    """
    yield
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()


@pytest.fixture
def registry(conn):
    """Register the components the fixture's composites resolve to.

    ZINC CATION and Chlortetracycline are moieties; the counterions deliberately
    are NOT, so the run has something to COUNT as unresolved rather than drop.

    FYTIC ACID IS HERE TO MAKE FIXTURE ROLE 6 REACHABLE. make_gsrs_subset.py cut
    PHYTATE SODIUM into the fixture as the genuine gap case -- a real composition
    edge with no ACTIVE MOIETY ruling anywhere -- and kept 7IGF0S7R8I alongside it
    "so the gap-view edge resolves against the registry". That was only true of the
    parser: with 7IGF0S7R8I unregistered the orchestrator dropped the edge as
    unresolved and wrote no row, so the case the fixture was cut for never reached
    the gap view here at all. Registering it is what makes the claim true.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for unii, name in (("13S1S8SF37", "ZINC CATION"),
                       ("WCK1KIQ23Q", "Chlortetracycline"),
                       ("ML30MJ2U7I", "Magnesium sulfate anhydrous"),
                       ("7IGF0S7R8I", "FYTIC ACID")):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING", (moiety_uuid, name, seed_run))
        conn.execute(
            "INSERT INTO drugref.identity_claim "
            "(moiety_uuid, scheme, value, ingest_run) VALUES (%s, 'UNII', %s, %s) "
            "ON CONFLICT DO NOTHING", (moiety_uuid, unii, seed_run))
    conn.commit()


def test_ingest_writes_composition_rows(conn, registry):
    summary = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert summary.rows_written > 0
    rows = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0]
    assert rows == summary.rows_written


def test_zinc_glycinate_citrate_attaches_only_its_REGISTERED_component(conn, registry):
    """Three components upstream; only ZINC CATION is a moiety here. The other two
    are COUNTED, never silently dropped."""
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    components = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition "
        "WHERE substance_unii = 'H3472PJ7YA'").fetchone()[0]
    assert components == 1


def test_unresolved_components_are_counted_not_dropped(conn, registry):
    summary = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert summary.components_not_in_registry > 0


def test_the_unruled_count_matches_the_gap_view_row_for_row(conn, registry):
    """THE OTHER WORKLIST NUMBER. `unruled_composites` had no assertion at all until
    this test: replacing it with a literal 0 left all 895 tests green, even though
    the module docstring, the dataclass docstring and the commit message all present
    it as half of what this orchestrator exists to report.

    It is asserted AGAINST THE GAP VIEW rather than against a literal, because the
    two are independent implementations of one rule -- Python's `values == {None}`
    and SQL's `bool_and(is_active_component IS NULL)` -- and two implementations of
    one rule that nothing compares is exactly the drift db/006 was written to
    prevent. A literal would pin the count without pinning the agreement.
    """
    summary = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    from_view = conn.execute(
        "SELECT count(*) FROM drugref.gap_unruled_composition_activity").fetchone()[0]
    assert summary.unruled_composites == from_view
    # NON-VACUOUS: with no unruled composite in the fixture both sides would be 0
    # and the assertion above would hold against an implementation that always
    # returns 0 -- which is the mutation this test exists to kill.
    assert summary.unruled_composites > 0


def test_phytate_sodium_is_the_designed_unruled_composite(conn, registry):
    """Fixture role 6, exercised end to end on real release bytes.

    GSRS gives 88496G1ERL one composition edge and makes NO ACTIVE MOIETY ruling
    for it, so the row must land NULL -- unruled, never false -- and the composite
    must reach gap kind 12 as a citable question. This is the whole read-path trade
    in one record: slice 3 propagates nothing to this substance, and that is only
    defensible because the shortfall is queued rather than hidden.
    """
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition "
        "WHERE substance_unii = '88496G1ERL'").fetchone()[0] is None
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unruled_composition_activity "
        "WHERE substance_unii = '88496G1ERL'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity' "
        "AND gap_key = 'SUBSTANCE:88496G1ERL'").fetchone()[0] == 1


def test_the_active_component_is_marked_true(conn, registry):
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    active = conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition "
        "WHERE substance_unii = 'H3472PJ7YA'").fetchone()[0]
    assert active is True


def test_a_ruling_seen_first_survives_a_later_none_from_the_mirror_encoding(
        conn, registry):
    """DO NOT 'simplify' this back onto H3472PJ7YA -- the row choice is load-bearing.

    H3472PJ7YA's own record (which carries the ruling) happens to sit AFTER
    13S1S8SF37's mirror record in the fixture, so for that row the merge writes
    None first and True second -- which passes whether or not the
    `if key not in edges or edges[key] is None` guard in gsrs_run.py is even
    there, since a naive unconditional overwrite also ends on the last-write
    value, True. It cannot distinguish "the guard works" from "there is no
    guard".

    1D06KZ672I / WCK1KIQ23Q is the other order: 1D06KZ672I's own record (record
    #2 in the fixture) rules WCK1KIQ23Q active (True) BEFORE WCK1KIQ23Q's mirror
    record (record #4) contributes the unruled encoding (None) for the same
    edge. An unconditional overwrite would let that later None clobber the
    earlier True, silently turning a ruled composition into an unruled one.
    Only the guard -- "a ruling beats a None, whichever end it arrives from" --
    keeps it True. WCK1KIQ23Q is registered as a moiety by the `registry`
    fixture (as Chlortetracycline), so the composition row exists to assert on.
    """
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    active = conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition "
        "WHERE substance_unii = '1D06KZ672I'").fetchone()[0]
    assert active is True


def test_the_run_is_recorded_and_finished(conn, registry):
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    row = conn.execute(
        "SELECT source, writer, upstream_release, finished_at IS NOT NULL "
        "FROM drugref.ingest_run WHERE source = 'GSRS'").fetchone()
    assert row[0] == "GSRS"
    assert row[1] == "gsrs_run"
    assert row[2] == "2026-02-26"
    assert row[3] is True


def test_re_ingest_replaces_rather_than_accumulates(conn, registry):
    """The projection contract: running twice must not double the rows."""
    first = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    second = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert first.rows_written == second.rows_written
    total = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0]
    assert total == second.rows_written


def test_a_failure_in_the_writes_rolls_back_and_names_the_release(conn, registry,
                                                                  monkeypatch, caplog):
    """GSRS WAS THE ONE ORCHESTRATOR OF ELEVEN WITH NO `except` AT ALL.

    The gap was harmless while `finish_run` was a bare `UPDATE ... = now()` that could
    not fail. db/053 gave it a CHECK to trip, so the last statement before the commit
    can now raise -- and without a handler a programmatic caller got back a connection
    in an ABORTED transaction, with nothing in the log saying which source or release
    died. fda_cyp_run's own comment records paying exactly that cost as the previous
    last-orchestrator-to-be-fixed.

    The run row must SURVIVE the rollback -- that is open_run's early commit doing its
    job -- while the work must not, which is the partition ingest_run_incomplete
    exists to report.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure in the writes")

    monkeypatch.setattr("drugref.questions.register_from_gaps", boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")

    assert "GSRS" in caplog.text and "2026-02-26" in caplog.text
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0] == 0
    # SCOPED TO THIS WRITER: the `registry` fixture opens a run of its own, so a bare
    # count would be asserting about somebody else's row.
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_run_incomplete "
        "WHERE writer = 'gsrs_run'").fetchone()[0] == 1


def test_gsrs_is_a_declared_writer_and_source():
    from drugref import ids as ids_module
    from drugref import provenance
    assert "gsrs_run" in provenance.WRITERS
    assert ids_module.canonical_source("GSRS") == "GSRS"
