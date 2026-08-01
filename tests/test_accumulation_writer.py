# tests/test_accumulation_writer.py
"""accumulation.py as the ONLY writer of the curated accumulation tables.

Same single-writer role classes.py, interactions.py and questions.py already play.
What these tests pin is the ORDERING that the overlay forces and that fails only at
COMMIT if it is wrong: insert the new assertion, THEN point the old one at it. Every
one of these calls leaves two rows briefly live, so each test forces the deferred
single-live check rather than trusting a transaction that is never committed.
"""
import pytest
import psycopg

from drugref import accumulation, ids


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGREF', 'curation-1', 'deadbeef', 'curation') RETURNING ingest_run_id"
    ).fetchone()[0]


def _class(conn, run_id, code, concept_type="PE"):
    class_uuid = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (class_uuid, code, f"class {code}", concept_type, run_id))
    return class_uuid


def _moiety(conn, run_id, unii):
    moiety_uuid = ids.mint_moiety_uuid(unii)
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, f"drug {unii}", run_id))
    return moiety_uuid


def _live(conn, table, where, params):
    return conn.execute(
        f"SELECT count(*) FROM drugref.{table} WHERE {where} AND superseded_by IS NULL",
        params).fetchone()[0]


# ---- curate_effect ----------------------------------------------------------


def test_curating_an_effect_twice_leaves_one_live_row(conn):
    run_id = _run(conn)
    cls = _class(conn, run_id, "W001")
    accumulation.curate_effect(conn, cls, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    second = accumulation.curate_effect(conn, cls, run_id, accumulates=True,
                                        threshold_major=2, threshold_total=3,
                                        severity="contraindicated")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert _live(conn, "additive_effect", "effect_class_uuid = %s", (cls,)) == 1
    live = conn.execute(
        "SELECT additive_effect_id, threshold_major, severity FROM drugref.additive_effect "
        "WHERE effect_class_uuid = %s AND superseded_by IS NULL", (cls,)).fetchone()
    assert live == (second, 2, "contraindicated")


def test_the_superseded_effect_survives_as_history(conn):
    """What drugref believed, and when, stays answerable -- which matters most for
    exactly the assertions that already fired an alert."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "W002")
    first = accumulation.curate_effect(conn, cls, run_id, accumulates=True,
                                       threshold_major=1, threshold_total=2,
                                       severity="major")
    accumulation.curate_effect(conn, cls, run_id, accumulates=False)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT severity FROM drugref.additive_effect WHERE additive_effect_id = %s",
        (first,)).fetchone()[0] == "major"


def test_ruling_that_an_effect_does_not_accumulate_needs_no_thresholds(conn):
    run_id = _run(conn)
    cls = _class(conn, run_id, "W003")
    assert accumulation.curate_effect(conn, cls, run_id, accumulates=False)


def test_an_accumulating_effect_without_thresholds_is_refused(conn):
    """The writer does not pre-validate; db/020's CHECK is the single place the rule
    lives, so the failure a caller sees is the schema's own."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "W004")
    with pytest.raises(psycopg.errors.CheckViolation):
        accumulation.curate_effect(conn, cls, run_id, accumulates=True)


# ---- grade_contribution -----------------------------------------------------


def test_regrading_a_contributor_supersedes_the_previous_grade(conn):
    run_id = _run(conn)
    eff, con = _class(conn, run_id, "W010"), _class(conn, run_id, "W011")
    accumulation.grade_contribution(conn, eff, con, "major", run_id)
    accumulation.grade_contribution(conn, eff, con, "minor", run_id)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT magnitude FROM drugref.effect_contribution "
        "WHERE effect_class_uuid = %s AND contributor_class_uuid = %s "
        "AND superseded_by IS NULL", (eff, con)).fetchall() == [("minor",)]


def test_grading_two_different_classes_does_not_supersede_either(conn):
    """The natural key is the PAIR, so a second contributor is a new statement rather
    than a correction of the first. Superseding on effect alone would silently retire
    every other promotion each time a curator graded one more class."""
    run_id = _run(conn)
    eff = _class(conn, run_id, "W012")
    one, two = _class(conn, run_id, "W013"), _class(conn, run_id, "W014")
    accumulation.grade_contribution(conn, eff, one, "major", run_id)
    accumulation.grade_contribution(conn, eff, two, "major", run_id)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert _live(conn, "effect_contribution", "effect_class_uuid = %s", (eff,)) == 2


# ---- groups -----------------------------------------------------------------


def test_register_group_is_idempotent(conn):
    run_id = _run(conn)
    first = accumulation.register_group(conn, "TRIPLE_WHAMMY", run_id)
    second = accumulation.register_group(conn, "TRIPLE_WHAMMY", run_id)
    assert first == second == ids.mint_group_uuid("DRUGREF", "TRIPLE_WHAMMY")
    assert conn.execute("SELECT count(*) FROM drugref.interaction_group").fetchone()[0] == 1


def test_correcting_a_group_assertion_keeps_the_identity(conn):
    run_id = _run(conn)
    grp = accumulation.register_group(conn, "TRIPLE_WHAMMY", run_id)
    accumulation.assert_group(conn, grp, "triple whammy", "major", run_id)
    accumulation.assert_group(conn, grp, "triple whammy (renal)", "contraindicated",
                              run_id)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert _live(conn, "interaction_group_assertion", "group_uuid = %s", (grp,)) == 1
    # the identity every member and external citation points at is untouched
    assert conn.execute(
        "SELECT group_uuid FROM drugref.interaction_group").fetchone()[0] == grp


def test_retiring_a_member_is_an_explicit_false(conn):
    run_id = _run(conn)
    grp = accumulation.register_group(conn, "TRIPLE_WHAMMY", run_id)
    cls = _class(conn, run_id, "W020", concept_type="EPC")
    drug = _moiety(conn, run_id, "W020U")
    conn.execute(
        "INSERT INTO drugref.class_membership (moiety_uuid, class_uuid, relationship, "
        "ingest_run) VALUES (%s, %s, 'has_EPC', %s)", (drug, cls, run_id))

    accumulation.set_group_member(conn, grp, "diuretic", cls, True, run_id)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.interaction_group_member_moiety "
        "WHERE group_uuid = %s", (grp,)).fetchone()[0] == 1

    # SET CONSTRAINTS ALL IMMEDIATE changes the mode for the REST of the transaction,
    # so without this the retiring INSERT below would be checked before the UPDATE
    # that supersedes the old row can run -- a test artefact, not a defect: in
    # production the check happens at COMMIT, by which time both statements have run.
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    accumulation.set_group_member(conn, grp, "diuretic", cls, False, run_id)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.interaction_group_member_moiety "
        "WHERE group_uuid = %s", (grp,)).fetchone()[0] == 0
    # ...and the history of what was believed is still there
    assert conn.execute(
        "SELECT count(*) FROM drugref.interaction_group_member WHERE group_uuid = %s",
        (grp,)).fetchone()[0] == 2


# ---- effect_counts ----------------------------------------------------------


def test_effect_counts_counts_drugs_not_rows(conn):
    """The convenience over the contract's uniqueness guarantee: a moiety promoted
    through two classes must count ONCE, because the difference between one and two is
    the difference between firing and not firing at threshold_total = 2."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "W030")
    promo_a = _class(conn, run_id, "W031", concept_type="EPC")
    promo_b = _class(conn, run_id, "W032", concept_type="EPC")
    drug = _moiety(conn, run_id, "W030U")
    for cls, rel in ((effect, "has_PE"), (promo_a, "has_EPC"), (promo_b, "has_EPC")):
        conn.execute(
            "INSERT INTO drugref.class_membership (moiety_uuid, class_uuid, "
            "relationship, ingest_run) VALUES (%s, %s, %s, %s)", (drug, cls, rel, run_id))

    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    accumulation.grade_contribution(conn, effect, promo_a, "major", run_id)
    accumulation.grade_contribution(conn, effect, promo_b, "major", run_id)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    assert accumulation.effect_counts(conn, effect, [drug]) == (1, 1)
    assert not accumulation.fires(*accumulation.effect_counts(conn, effect, [drug]), 1, 2)


def test_a_role_containing_quotes_is_stored_and_checked_intact(conn):
    """`role` is FREE TEXT a curator types, and since db/023 it is the one value that
    reaches composed SQL: the single-live trigger builds `t.role = <literal>` rather
    than comparing a jsonb projection, because only the equality form can use an index.
    format's %L is what makes that safe, so this pins both halves -- the value survives
    a round trip unmangled, AND the single-live rule still fires on it."""
    run_id = _run(conn)
    grp = accumulation.register_group(conn, "QUOTED_ROLE", run_id)
    cls = _class(conn, run_id, "W060", concept_type="EPC")
    role = "O'Brien's ''role''; DROP TABLE drugref.additive_effect; --"

    accumulation.set_group_member(conn, grp, role, cls, True, run_id)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT role FROM drugref.interaction_group_member WHERE group_uuid = %s",
        (grp,)).fetchone()[0] == role
    assert conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'drugref' "
        "AND table_name = 'additive_effect'").fetchone()[0] == 1

    # ...and the check the quoting exists to serve still rejects two live rows
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    conn.execute(
        "INSERT INTO drugref.interaction_group_member (group_uuid, role, class_uuid, "
        "satisfies_role, source, ingest_run) VALUES (%s, %s, %s, true, 'DRUGREF', %s)",
        (grp, role, cls, run_id))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_effect_counts_of_an_empty_regimen_is_zero(conn):
    """A patient on nothing is an ordinary call, not an edge case to be guarded against
    by the caller -- and an empty list is the one array argument whose adaptation can
    fail at the driver rather than returning no rows."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "W050")
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert accumulation.effect_counts(conn, effect, []) == (0, 0)


def test_effect_counts_ignores_drugs_outside_the_regimen(conn):
    run_id = _run(conn)
    effect = _class(conn, run_id, "W040")
    on_it = _moiety(conn, run_id, "W040A")
    not_on_it = _moiety(conn, run_id, "W040B")
    for drug in (on_it, not_on_it):
        conn.execute(
            "INSERT INTO drugref.class_membership (moiety_uuid, class_uuid, "
            "relationship, ingest_run) VALUES (%s, %s, 'has_PE', %s)",
            (drug, effect, run_id))
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=0, threshold_total=2, severity="moderate")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert accumulation.effect_counts(conn, effect, [on_it]) == (0, 1)
    assert accumulation.effect_counts(conn, effect, [on_it, not_on_it]) == (0, 2)
