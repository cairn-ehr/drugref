# tests/test_medrt_run.py
"""DB-gated acceptance matrix for the slice-2a MED-RT ingest.

The expected numbers come from the real MED-RT release the fixture was extracted
from, so they assert against upstream reality rather than against a fixture that
merely agrees with our assumptions.
"""
import logging
import pathlib

import pytest

from drugref import ids
from drugref.ingest import medrt_run, run

MEDRT_FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"
UNII_FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"

PARACETAMOL = "362O9ITL9D"
AMLODIPINE = "1J444QC288"
MAGNESIUM_SULFATE = "DE08037SAB"
ESCITALOPRAM = "4O4S742ANY"


@pytest.fixture(autouse=True)
def _clean(conn):
    # Both orchestrators commit internally, so the conn fixture's rollback cannot
    # isolate these tests; truncate first so counts are order-independent.
    conn.execute("TRUNCATE drugref.class_contraindication, drugref.class_membership, "
                 "drugref.class_parent, drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


@pytest.fixture
def seeded(conn):
    """Classification needs the slice-1 moiety registry to join against."""
    run.ingest_unii(conn, unii_path=UNII_FIX, crosswalk_path=XW,
                    allowlist_path=AL, upstream_release="2026-07")
    return conn


def _ingest(conn, release="2026.07.06"):
    return medrt_run.ingest_medrt(conn, medrt_path=MEDRT_FIX, upstream_release=release)


def _classes_of(conn, unii, relationship=None):
    """Return {class_name: relationship} for one moiety."""
    sql = ("SELECT c.class_name, m.relationship FROM drugref.class_membership m "
           "JOIN drugref.substance_class c USING (class_uuid) WHERE m.moiety_uuid = %s")
    params = [ids.mint_moiety_uuid(unii)]
    if relationship:
        sql += " AND m.relationship = %s"
        params.append(relationship)
    return dict(conn.execute(sql, params).fetchall())


# ---- classes ---------------------------------------------------------------


def test_registers_every_ingested_class(seeded):
    summary = _ingest(seeded)
    # 75 + 8 (slice 5b.2): halothane's own has_MoA/has_PE/has_TC classes (4) plus
    # their 2-level MED-RT ancestors the extractor pulls in alongside them (4).
    assert (summary.classes_in_release, summary.classes_added) == (83, 83)
    # Nothing in the real release is retired or unidentified; if either ever fires
    # it is a shape change upstream, reported rather than silently absorbed.
    assert (summary.inactive_concepts, summary.unidentified_concepts) == (0, 0)
    # Codes are unique in the real release. If two concepts ever publish one
    # code, edges through it are refused rather than misfiled -- reported here.
    assert summary.ambiguous_codes == 0
    types = dict(seeded.execute(
        "SELECT concept_type, count(*) FROM drugref.substance_class GROUP BY 1").fetchall())
    # +2 MoA (halothane's own class N0000009915 plus its ancestor N0000000223),
    # +5 PE (halothane's two, N0000008501/N0000175975, plus three ancestors),
    # +1 TC (halothane's own N0000193810). EPC/PK/APC untouched: halothane carries
    # none of those axes.
    assert types == {"MoA": 22, "PE": 36, "EPC": 4, "TC": 9, "PK": 6, "APC": 6}


def test_a_failed_ingest_leaves_the_connection_usable(seeded, monkeypatch):
    """An orchestrator owns the transaction it opens, so it must also clean it up.

    Without this, a mid-run failure left the caller's connection in Postgres's
    aborted-transaction state: every following statement fails with "current
    transaction is aborted", including the unrelated ingest_run INSERT that a
    subsequent ingest_mesh would start with. These three orchestrators are
    deliberately mirror-shaped for exactly that kind of pipeline, so one feed's bad
    row must not take the next feed down with it.
    """
    import psycopg
    from drugref import classes as class_writer

    def boom(conn, *args, **kwargs):
        # A real database error, not a Python one: that is what puts the
        # transaction into Postgres's aborted state, where every subsequent
        # statement fails until someone rolls back.
        conn.execute("SELECT no_such_function_exists()")

    monkeypatch.setattr(class_writer, "add_membership", boom)
    with pytest.raises(psycopg.Error):
        _ingest(seeded)
    # Without the orchestrator's rollback this raises InFailedSqlTransaction.
    assert seeded.execute("SELECT 1").fetchone() == (1,)
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_membership").fetchone()[0] == 0


def test_classes_added_never_exceeds_classes_in_release(seeded, tmp_path):
    """`classes_added` summed a per-row flag over a list the parser never
    deduplicates, while `classes_in_release` is a dict length. A concept repeated in
    one release therefore counted as "new" twice and the summary reported more
    classes added than the release contains -- which the docstring says cannot
    happen, and which is exactly the number an operator checks a release against.
    """
    path = tmp_path / "dup_concept.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n<terminology>\n'
        '<namespace><name>MED-RT</name><version>test</version></namespace>\n'
        + 2 * ('<concept><namespace>MED-RT</namespace><name>Alpha [MoA]</name>'
               '<code>N-DUP-1</code><status>A</status>'
               '<property><name>CTY</name><value>MoA</value></property>'
               '<property><name>NUI</name><value>N-DUP-1</value></property></concept>\n')
        + '</terminology>\n', encoding="utf-8")
    summary = medrt_run.ingest_medrt(seeded, medrt_path=path, upstream_release="dup")
    assert summary.classes_in_release == 1
    assert summary.classes_added == 1


def test_hc_navigation_bins_never_become_classes(seeded):
    """'A [Preparations]' is an alphabetical bin, not a pharmacologic class."""
    _ingest(seeded)
    names = [r[0] for r in seeded.execute(
        "SELECT class_name FROM drugref.substance_class WHERE class_name LIKE '%[Preparations]%'"
    ).fetchall()]
    assert names == []


# ---- the DAG ---------------------------------------------------------------


def test_builds_the_dag_the_right_way_up(seeded):
    """The broad APC must come out ABOVE the specific EPC, not below it."""
    _ingest(seeded)
    parent, child = ids.mint_class_uuid("MED-RT", "N0000193892"), ids.mint_class_uuid("MED-RT", "N0000175421")
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (child, parent)).fetchone()[0] == 1
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (parent, child)).fetchone()[0] == 0


def test_a_class_keeps_both_of_its_parents(seeded):
    # 59 + 8: halothane's 4 new classes each contribute at least one Parent Of
    # edge into the DAG built in step 3 above.
    assert _ingest(seeded).parent_edges == 67
    child = ids.mint_class_uuid("MED-RT", "N0000193892")
    parents = {r[0] for r in seeded.execute(
        "SELECT parent_class_uuid FROM drugref.class_parent WHERE child_class_uuid = %s",
        (child,)).fetchall()}
    assert parents == {ids.mint_class_uuid("MED-RT", "N0000193904"), ids.mint_class_uuid("MED-RT", "N0000193893")}


# ---- membership ------------------------------------------------------------


def test_links_paracetamol_on_the_right_axes(seeded):
    _ingest(seeded)
    assert _classes_of(seeded, PARACETAMOL, "has_MoA") == {
        "Prostaglandin Receptor Antagonists [MoA]": "has_MoA"}
    assert _classes_of(seeded, PARACETAMOL, "has_TC") == {
        "Anti-inflammatory Agent [TC]": "has_TC"}
    assert len(_classes_of(seeded, PARACETAMOL)) == 8


def test_amlodipine_gets_both_of_its_epc_classes(seeded):
    """EPC membership is hierarchical in MED-RT, not an association -- this is the
    clinically recognisable axis, so it is pinned explicitly."""
    _ingest(seeded)
    assert set(_classes_of(seeded, AMLODIPINE, "has_EPC")) == {
        "Dihydropyridine Calcium Channel Blocker [EPC]", "Calcium Channel Blocker [EPC]"}


def test_a_moiety_medrt_says_nothing_about_is_left_unclassified(seeded):
    """A registered moiety this partial fixture says nothing about must come out
    unclassified: the ingest classifies from what MED-RT asserts, never by inference.

    Note the fixture's own [HC]-bin-only ingredient is RxCUI 6853, which is
    METHOXAMINE and not magnesium sulfate -- make_medrt_subset.py carried that
    mislabel until slice 5b. Magnesium sulfate is RxCUI 6585, which the release
    classifies richly; it is simply absent from this fixture, which is why it lands
    here with nothing. The 'filed under M would be worse than unclassified' rule the
    fixture demonstrates is real, it just belongs to 6853."""
    _ingest(seeded)
    assert _classes_of(seeded, MAGNESIUM_SULFATE) == {}


def test_unmatched_ingredient_is_skipped_and_counted_not_silently_dropped(seeded):
    """Ibuprofen is classified upstream but absent from our registry; it must be
    reported as a worklist number rather than vanishing."""
    summary = _ingest(seeded)
    # paracetamol 8 + amlodipine 9 + activated charcoal 5 + escitalopram 4
    # + halothane 4 (1 has_MoA + 2 has_PE + 1 has_TC) + methoxamine 0
    assert summary.memberships == 30
    assert summary.unmatched_rxcuis == 1      # ibuprofen (RxCUI 5640)


def test_every_moiety_claiming_an_rxcui_gets_classified(seeded):
    """Nothing stops two moieties from carrying the same RXNORM_IN claim, so the
    membership join must classify BOTH. Picking one arbitrarily would silently
    leave a real moiety unclassified -- and, being an unordered read, might leave a
    different one unclassified on the next run."""
    run_id = seeded.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII', 'test', 'deadbeef') RETURNING ingest_run_id").fetchone()[0]
    twin = ids.mint_moiety_uuid("TWIN-OF-PARACETAMOL")
    seeded.execute("INSERT INTO drugref.substance_moiety "
                   "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                   (twin, "paracetamol twin", run_id))
    seeded.execute("INSERT INTO drugref.identity_claim "
                   "(moiety_uuid, scheme, value, ingest_run) "
                   "VALUES (%s, 'RXNORM_IN', '161', %s)", (twin, run_id))
    seeded.commit()

    summary = _ingest(seeded)
    # Paracetamol's 8 memberships are now written for both claimants.
    assert summary.memberships == 38          # 30 + a second set of paracetamol's 8
    assert len(_classes_of(seeded, PARACETAMOL)) == 8
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_membership WHERE moiety_uuid = %s",
        (twin,)).fetchone()[0] == 8


def test_no_membership_points_outside_the_registry(seeded):
    _ingest(seeded)
    orphans = seeded.execute(
        "SELECT count(*) FROM drugref.class_membership m "
        "LEFT JOIN drugref.substance_moiety s USING (moiety_uuid) WHERE s.moiety_uuid IS NULL"
    ).fetchone()[0]
    assert orphans == 0


# ---- rebuild + provenance --------------------------------------------------


def test_reingest_rebuilds_edges_without_duplicating(seeded):
    """A second release REPLACES the previous edges; class UUIDs are unchanged."""
    first = _ingest(seeded)
    second = _ingest(seeded, release="2026.08.03")
    assert (second.classes_in_release, second.parent_edges, second.memberships) == \
           (first.classes_in_release, first.parent_edges, first.memberships)
    # ...but the second run ADDED nothing: classes accumulate, edges are rebuilt.
    # Reporting one number for both would have hidden exactly this distinction.
    assert (first.classes_added, second.classes_added) == (83, 0)
    counts = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.substance_class), "
        "       (SELECT count(*) FROM drugref.class_parent), "
        "       (SELECT count(*) FROM drugref.class_membership)").fetchone()
    assert counts == (83, 67, 30)


def test_rebuild_drops_edges_that_vanished_upstream(seeded, tmp_path):
    """The point of a rebuildable projection: a class that lost a parent upstream
    must lose it here too, which an insert-only merge could never express."""
    _ingest(seeded)
    shrunk = tmp_path / "medrt_shrunk.xml"
    # A CLEARLY SYNTHETIC code (N-LONE-1, not NNNNNNNNNN), not a real MED-RT NUI --
    # this test needs one that is guaranteed ABSENT from medrt_subset.xml, and a
    # real code cannot promise that release to release. It used to borrow the real
    # root N0000000223 ("Cellular or Molecular Interactions [MoA]"), which slice
    # 5b.2 broke: that root is now one of halothane's own 2-level MED-RT ancestors,
    # so it is already IN the fixture's 83 classes, and reusing it here would test
    # "re-assert a known class" rather than "add a genuinely new one" -- the wrong
    # behaviour for a test named for what happens when a class VANISHES upstream.
    shrunk.write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n<terminology>\n'
        "\t<namespace><name>MED-RT</name><version>x</version></namespace>\n"
        "\t<concept><namespace>MED-RT</namespace><name>Lone Class [MoA]</name>"
        "<code>N-LONE-1</code><status>A</status>"
        "<property><namespace>MED-RT</namespace><name>CTY</name><value>MoA</value></property>"
        "<property><namespace>MED-RT</namespace><name>NUI</name><value>N-LONE-1</value></property>"
        "</concept>\n</terminology>\n", encoding="utf-8")
    summary = medrt_run.ingest_medrt(seeded, medrt_path=shrunk, upstream_release="2026.09.07")
    assert summary.parent_edges == 0 and summary.memberships == 0
    counts = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.class_parent), "
        "       (SELECT count(*) FROM drugref.class_membership)").fetchone()
    assert counts == (0, 0)
    # Edges rebuild, but class IDENTITY is immortal: every class from the earlier
    # release is still present with its UUID intact, and the release's one new
    # class is simply added alongside. Classes accumulate; they are never deleted.
    surviving = seeded.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE class_uuid = ANY(%s)",
        ([ids.mint_class_uuid("MED-RT", n) for n in ("N0000175421", "N0000175566", "N0000193892")],)
    ).fetchone()[0]
    assert surviving == 3
    # 83 (the real fixture, slice 5b.2) + 1 (N-LONE-1, genuinely new this release).
    assert seeded.execute("SELECT count(*) FROM drugref.substance_class").fetchone()[0] == 84


# ---- contraindications (slice 5a) ------------------------------------------


def test_contraindications_are_written_from_the_real_fixture(seeded):
    """The fixture carries amlodipine's real CI_PE edge (17767 -> N0000178477) and
    escitalopram's real CI_MoA edge (321988 -> N0000000184, MAO inhibitors).
    Ingest resolves each subject to its moiety and each object to its ingested
    class, writing one row per edge -- direction pinned on real data: subject is the
    drug the statement is about, object is the co-administered drug's class.

    BOTH AXES, not one: ci_axis admits CI_MoA and CI_PE, and until slice 5b widened
    the fixture the release gave these ingredients only a CI_PE, so an ingest that
    silently dropped every CI_MoA would have passed this file.
    """
    summary = _ingest(seeded)
    assert summary.contraindications == 2
    assert summary.unmatched_ci_rxcuis == 0
    rows = dict(seeded.execute(
        "SELECT subject_moiety_uuid, relationship FROM drugref.class_contraindication "
        "WHERE source = 'MED-RT'").fetchall())
    assert rows == {ids.mint_moiety_uuid(AMLODIPINE): "CI_PE",
                    ids.mint_moiety_uuid(ESCITALOPRAM): "CI_MoA"}
    row = seeded.execute(
        "SELECT relationship, source FROM drugref.class_contraindication "
        "WHERE subject_moiety_uuid = %s AND object_class_uuid = %s",
        (ids.mint_moiety_uuid(AMLODIPINE), ids.mint_class_uuid("MED-RT", "N0000178477"))
    ).fetchone()
    assert row == ("CI_PE", "MED-RT")


def test_a_contraindication_shares_its_runs_provenance(seeded):
    """One MED-RT file, one ingest_run: the contraindication carries the same run id
    as the classes and edges written beside it."""
    _ingest(seeded)
    run_id = seeded.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run WHERE source = 'MED-RT' "
        "ORDER BY ingest_run_id DESC LIMIT 1").fetchone()[0]
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_contraindication WHERE ingest_run = %s",
        (run_id,)).fetchone()[0] == 2


def test_reingest_rebuilds_contraindications_without_duplicating_or_touching_edges(seeded):
    """A rebuildable projection: a second release replaces the prior release's
    contraindications, and clearing them leaves membership and the DAG untouched
    (the clear is scoped to class_contraindication alone)."""
    first = _ingest(seeded)
    second = _ingest(seeded, release="2026.08.03")
    assert first.contraindications == second.contraindications == 2
    assert seeded.execute(
        "SELECT count(*) FROM drugref.class_contraindication").fetchone()[0] == 2
    assert seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.class_membership), "
        "       (SELECT count(*) FROM drugref.class_parent)").fetchone() == (30, 67)


def test_a_contraindication_on_an_unregistered_ingredient_is_skipped_and_counted(seeded, tmp_path):
    """The subject-side gate, mirroring membership's unmatched_rxcuis: a CI whose
    ingredient our registry does not carry is a worklist number, never dropped in
    silence."""
    synthetic = tmp_path / "medrt_ci.xml"
    synthetic.write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n<terminology>\n'
        "\t<namespace><name>MED-RT</name><version>x</version></namespace>\n"
        "\t<concept><namespace>MED-RT</namespace><name>A Mechanism [MoA]</name>"
        "<code>N0000000401</code><status>A</status>"
        "<property><namespace>MED-RT</namespace><name>CTY</name><value>MoA</value></property>"
        "<property><namespace>MED-RT</namespace><name>NUI</name><value>N0000000401</value></property>"
        "</concept>\n"
        "\t<association><namespace>MED-RT</namespace><name>CI_MoA</name>"
        "<from_namespace>RxNorm</from_namespace><from_name>x</from_name>"
        "<from_code>9999999</from_code>"  # no such ingredient in the registry
        "<to_namespace>MED-RT</to_namespace><to_name>y</to_name>"
        "<to_code>N0000000401</to_code></association>\n</terminology>\n", encoding="utf-8")
    summary = medrt_run.ingest_medrt(seeded, medrt_path=synthetic, upstream_release="2026.10.05")
    assert summary.contraindications == 0
    assert summary.unmatched_ci_rxcuis == 1


def test_ingest_run_provenance_is_recorded(seeded):
    _ingest(seeded)
    source, release, finished = seeded.execute(
        "SELECT source, upstream_release, finished_at FROM drugref.ingest_run "
        "WHERE source = 'MED-RT' ORDER BY ingest_run_id DESC LIMIT 1").fetchone()
    assert (source, release) == ("MED-RT", "2026.07.06")
    assert finished is not None


def test_unmatched_ingredient_identities_are_persisted_not_just_counted(seeded):
    """The count says HOW MANY drugs drugref cannot speak about; only the identities
    say WHICH, and the identities are what a worklist needs. They were built locally
    and discarded on return until Plan A, so gap_unmatched_ingredient had nothing to
    query."""
    _ingest(seeded)
    rows = seeded.execute(
        "SELECT rxcui FROM drugref.ingest_unmatched_ingredient ORDER BY rxcui").fetchall()
    assert rows == [("5640",)]                # ibuprofen, the one the gate excludes


def test_unmatched_ingredients_are_replaced_on_re_ingest(seeded):
    """A rebuildable projection: re-ingesting the same release must not accumulate a
    second copy, or the worklist would grow by its own length every run."""
    _ingest(seeded)
    _ingest(seeded)
    assert seeded.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient").fetchone()[0] == 1


def test_the_unmatched_ingredient_reaches_the_gap_view(seeded):
    """End to end: ingest -> persisted row -> gap view, with no moiety carrying it."""
    _ingest(seeded)
    assert seeded.execute(
        "SELECT rxcui FROM drugref.gap_unmatched_ingredient").fetchall() == [("5640",)]


def test_the_ingest_registers_the_open_questions(seeded):
    """THE test this slice was missing. Every gap view worked, every curator API
    worked, and register_from_gaps was called by nothing but its own unit tests -- so
    on a real database open_question and question_worklist were permanently EMPTY
    while three documents and a table comment all said "re-derived every ingest".
    A register nothing populates is not a register. Asserted against the real
    orchestrator, because that is the only place the wiring exists to be checked.

    Asserted on the kind only THIS run can derive, not on a bare non-empty count:
    the seeded fixture's UNII ingest registers unclassified_moiety questions of its
    own, so `count(*) > 0` passes with this orchestrator's rebuild deleted -- which
    is exactly the bug, still green. Verified by removing the call and watching this
    fail."""
    before = seeded.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unmatched_ingredient'").fetchone()[0]
    assert before == 0

    _ingest(seeded)

    assert seeded.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unmatched_ingredient'").fetchone()[0] == 1
    # and it reaches the read path a consumer actually queries
    assert seeded.execute(
        "SELECT count(*) FROM drugref.question_worklist "
        "WHERE gap_kind = 'unmatched_ingredient'").fetchone()[0] == 1


def test_the_unmatched_ingredient_becomes_a_citable_question(seeded):
    """The full chain the slice promises: ingest -> persisted identity -> gap view ->
    a question under the deterministic UUID an external tool can hold."""
    _ingest(seeded)
    assert seeded.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unmatched_ingredient'").fetchall() == [
            (ids.mint_question_uuid("unmatched_ingredient", "RXNORM_IN:5640"),)]


def test_re_ingesting_does_not_duplicate_the_register(seeded):
    """The register is a rebuildable projection driven by an idempotent ingest, so a
    second run must leave it the same size -- not twice the size."""
    _ingest(seeded)
    first = seeded.execute("SELECT count(*) FROM drugref.open_question").fetchone()[0]
    _ingest(seeded)
    assert seeded.execute(
        "SELECT count(*) FROM drugref.open_question").fetchone()[0] == first


# ---- the expansion policy's other rot direction (db/012) ---------------------


def test_the_ingest_reports_expansion_decisions_the_release_no_longer_resolves(seeded):
    """db/010 wrote `expansion_policy_unresolved` for exactly the right reason -- "a
    deny that matches nothing looks exactly like a deny that is working" -- and then
    gave it no consumer: it is not a gap_kind, no orchestrator read it, and no test
    asserted on it. A detector nobody runs is the failure mode it was built to catch,
    so the ingest that could invalidate a decision is the thing that has to report it.

    Asserted against the view rather than against a literal, because WHICH of the 14
    seeded roots resolve is a property of the fixture, not of this wiring; `> 0` is
    what proves the orchestrator reads the view instead of reporting a hardcoded 0
    (the partial fixture defines only two of the seeded classes)."""
    summary = _ingest(seeded)
    from_view = seeded.execute(
        "SELECT count(*) FROM drugref.expansion_policy_unresolved "
        "WHERE source = 'MED-RT'").fetchone()[0]
    assert summary.unresolved_expansion_policy == from_view > 0


def test_an_unresolved_expansion_decision_is_logged_not_only_counted(seeded, caplog):
    """The count lands in the summary, which is logged at INFO as one line -- fine for
    a number that is usually zero, useless for acting on. The identities go out at
    WARNING, naming the codes, because the operator's next move is to look at those
    specific decisions."""
    with caplog.at_level(logging.WARNING, logger="drugref.ingest.medrt_run"):
        _ingest(seeded)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]

    # WHICH codes go unresolved is a property of the fixture, so the assertion asks
    # the view rather than naming one -- a literal NUI here would break the day the
    # fixture happens to define that class.
    unresolved = [r[0] for r in seeded.execute(
        "SELECT source_code FROM drugref.expansion_policy_unresolved "
        "WHERE source = 'MED-RT'").fetchall()]
    assert unresolved, "fixture is meant to be partial, so some decisions must dangle"
    assert any("expansion" in m and any(c in m for c in unresolved) for m in warnings), \
        warnings
