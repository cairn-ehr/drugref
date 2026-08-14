# tests/test_class_subject_schema.py
"""The class-subject rule's floor and completeness rules (db/032, the class-subject
round of slice 5c.2 -- spec section 14).

WHY THIS TABLE PAIR EXISTS AT ALL. Tasks 1-8 built the moiety x class grain: a drug
contraindicated with a co-administered member of a drug class. Retrieving the ONC
list (Phansalkar 2012, Table 2) showed that shape covers only 4 of 15 entries -- 8
are class x class (SSRIs x MAOIs, statins x CYP3A4 inhibitors, ...) and 1 is a class
self-pair (QT-prolonging x QT-prolonging). db/032 adds a second grain: a rule whose
SUBJECT is a class, not a moiety.

Two tables, mirroring db/029's curated_interaction section exactly, for the reason
spec section 14.3 records and this file exists to hold to account:

  1. class_pair_contraindication -- the CANDIDATE tier, mirroring class_contraindication
     (db/004/006): rebuildable, source-scoped, PK includes `source`, FK to ingest_run.
     Gets NO append-only floor -- it is a projection, dropped and rebuilt per source.

  2. curated_class_interaction -- the OVERLAY tier, mirroring curated_interaction
     (db/029): append-only, deferred single-live guard, `superseded_by` self-FK, the
     db/027 provenance triple, nullable question_uuid with ON DELETE CASCADE.

TWO TABLES, NOT A POLYMORPHIC SUBJECT COLUMN. Making subject_moiety_uuid nullable
beside a new subject_class_uuid on the EXISTING tables would break the overlay floor:
forbid_multiple_live_assertions (db/023) compares natural-key columns with EQUALITY
predicates for speed (a jsonb-containment comparison was measured quadratic -- 5,773 ms
against 42 ms at 2,000 rows), and NULL = NULL is never true in SQL, so the single-live
guard would silently stop guarding a mixed-grain table. Slice 5b set the precedent
already: `condition` turned out not to be a `substance_class`, and the fix was two
relations because the endpoints are different kinds of thing, not one relation with an
optional column.

A CLASS SELF-PAIR IS LEGAL HERE, UNLIKE A MOIETY SELF-PAIR. db/014's
moiety_contraindication_not_self forbids a moiety pairing with itself (a drug cannot
be a co-administration partner of itself), and that stays true -- the read-path
expansion (Task 11) excludes identical moieties from a class-subject rule's pair
output. But "every member of this class interacts with every other member" (QT-
prolonging agents x QT-prolonging agents, a real ONC entry) is a different claim from
"this drug interacts with itself", so class_pair_contraindication carries no such
CHECK.
"""

import psycopg
import pytest

from tests.test_curated_overlay import _a_class


def _an_onc_run(conn, source="ONCHIGH", writer="onchigh_run"):
    """A fresh ingest_run for a given (source, writer) pair -- both already admitted
    by db/031's widened CHECKs. Separate from the session-standard `ingest_run_id`
    fixture (which is always source='PBS') because
    test_the_candidate_key_includes_source needs two runs from two DIFFERENT
    authorities to prove the primary key really keeps both rows apart."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'test', 'test', %s) RETURNING ingest_run_id",
        (source, writer),
    ).fetchone()[0]


def _two_classes(conn, ingest_run_id):
    """Two distinct MED-RT classes -- the subject and object of a class-subject
    rule. Distinct codes so the minted UUIDs (and therefore the rows) differ."""
    subject = _a_class(conn, ingest_run_id, code="N0000000201", name="Subject Class [MoA]")
    obj = _a_class(conn, ingest_run_id, code="N0000000202", name="Object Class [MoA]")
    return subject, obj


def _insert_class_pair(conn, subject_class, object_class, ingest_run_id, *,
                        relationship="CI_MoA", source="ONCHIGH"):
    """INSERT one class_pair_contraindication candidate row."""
    conn.execute(
        "INSERT INTO drugref.class_pair_contraindication "
        "(subject_class_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s)",
        (subject_class, object_class, relationship, source, ingest_run_id),
    )


def _assert_class_interaction(conn, subject_class, object_class, *,
                               relationship="CI_MoA", **over):
    """INSERT one curated_class_interaction row, returning its id. Defaults assert.
    Mirrors test_curated_overlay._assert_interaction exactly, one grain over."""
    cols = dict(
        applies=True,
        severity="major",
        mechanism="additive QT prolongation",
        management="avoid the combination; if unavoidable, monitor ECG",
        evidence_grade="established",
        source="DRUGREF",
        reviewed_by="test",
        reviewed_against="Phansalkar 2012",
    )
    cols.update(over)
    names = ", ".join(cols)
    holes = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO drugref.curated_class_interaction "
        f"(subject_class_uuid, object_class_uuid, relationship, {names}) "
        f"VALUES (%s, %s, %s, {holes}) RETURNING curated_class_interaction_id",
        (subject_class, object_class, relationship, *cols.values()),
    ).fetchone()[0]


# ============================================================================
# 1. class_pair_contraindication -- the candidate tier
# ============================================================================

def test_a_class_pair_candidate_can_be_written(conn, ingest_run_id):
    """The basic shape: a class-subject candidate row, mirroring
    class_contraindication exactly (subject, object, relationship, source,
    ingest_run) but with a CLASS on both ends instead of a moiety and a class."""
    subject, obj = _two_classes(conn, ingest_run_id)
    _insert_class_pair(conn, subject, obj, ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_pair_contraindication "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s",
        (subject, obj),
    ).fetchone() == (1,)


def test_the_candidate_key_includes_source(conn):
    """Two authorities may assert the same class pair; both rows coexist, exactly
    as class_contraindication already allows for the moiety grain (db/006's fix,
    which put `source` into the primary key precisely so a second authority's row
    is never swallowed by the first)."""
    medrt_run = _an_onc_run(conn, source="MED-RT", writer="medrt_run")
    onchigh_run = _an_onc_run(conn, source="ONCHIGH", writer="onchigh_run")
    subject, obj = _two_classes(conn, medrt_run)
    _insert_class_pair(conn, subject, obj, medrt_run, source="MED-RT")
    _insert_class_pair(conn, subject, obj, onchigh_run, source="ONCHIGH")
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_pair_contraindication "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s",
        (subject, obj),
    ).fetchone() == (2,)


def test_a_self_pair_is_permitted_at_the_class_grain(conn, ingest_run_id):
    """QT-prolonging x QT-prolonging is a real ONC entry. db/014 forbids a MOIETY
    pairing with itself, and that stays true -- the expansion (Task 11) excludes
    identical moieties. A class contraindicated with its own members is not the
    same claim, so class_pair_contraindication carries no such CHECK."""
    klass = _a_class(conn, ingest_run_id, code="N0000000203", name="QT-prolonging [MoA]")
    _insert_class_pair(conn, klass, klass, ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_pair_contraindication "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s",
        (klass, klass),
    ).fetchone() == (1,)


def test_an_unresolvable_source_is_refused_by_the_check(conn, ingest_run_id):
    """Mirrors class_contraindication_source: only the two admitted candidate
    authorities may write here."""
    subject, obj = _two_classes(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.CheckViolation, match="class_pair_contraindication_source"
    ):
        _insert_class_pair(conn, subject, obj, ingest_run_id, source="MADE-UP")


def test_an_unknown_relationship_is_refused_by_the_foreign_key(conn, ingest_run_id):
    """`relationship` is a FOREIGN KEY into drugref.ci_axis, NOT a hardcoded CHECK
    -- db/006's finding 1, mirrored rather than re-learned: a CHECK duplicates a
    vocabulary the read path also names, and widening only one of the two silently
    produces rows that expand to nothing once Task 11 builds the read path."""
    subject, obj = _two_classes(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.ForeignKeyViolation,
        match="class_pair_contraindication_relationship",
    ):
        _insert_class_pair(conn, subject, obj, ingest_run_id, relationship="CI_XYZ")


# ============================================================================
# 2. curated_class_interaction -- the overlay tier
# ============================================================================

def test_curated_class_interaction_refuses_an_update(conn, ingest_run_id):
    """The append-only floor, on the new table. Without this the append-only
    guarantee is a guarantee about one table out of two."""
    subject, obj = _two_classes(conn, ingest_run_id)
    row_id = _assert_class_interaction(conn, subject, obj)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "UPDATE drugref.curated_class_interaction SET severity = 'minor' "
            "WHERE curated_class_interaction_id = %s",
            (row_id,),
        )
    conn.rollback()
    # conn.rollback() undoes the WHOLE open transaction, including this test's own
    # fixture inserts on the same conn (neither ingest_run_id nor _a_class commits)
    # -- the identical trap test_curated_overlay.py's
    # test_the_row_cannot_be_updated_or_deleted documents and works around. Fresh
    # rows are seeded rather than reusing ids the rollback already erased.
    run_id = _an_onc_run(conn)
    subject, obj = _two_classes(conn, run_id)
    row_id = _assert_class_interaction(conn, subject, obj)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "DELETE FROM drugref.curated_class_interaction "
            "WHERE curated_class_interaction_id = %s",
            (row_id,),
        )


def test_curated_class_interaction_refuses_two_live_rows_for_one_key(
    conn, ingest_run_id
):
    """The deferred single-live guard. It compares natural-key columns by
    EQUALITY (db/023), which is why this table exists rather than a nullable
    subject_moiety_uuid beside a nullable subject_class_uuid on curated_interaction
    -- NULL = NULL is not true, and the guard would have silently stopped
    guarding. A TEST THAT NEVER COMMITS PROVES NOTHING about a DEFERRED
    constraint, so this forces the check with SET CONSTRAINTS ALL IMMEDIATE."""
    subject, obj = _two_classes(conn, ingest_run_id)
    _assert_class_interaction(conn, subject, obj)
    _assert_class_interaction(conn, subject, obj, severity="minor")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_two_live_rows_differing_only_by_relationship_may_coexist(
    conn, ingest_run_id
):
    """The same mutation test_curated_overlay.py carries for curated_interaction,
    one grain over: relationship is part of the natural key the deferred
    single-live check compares, so CI_MoA and CI_PE against the same class pair
    are two different rules and must be allowed to stand together."""
    subject, obj = _two_classes(conn, ingest_run_id)
    _assert_class_interaction(conn, subject, obj, relationship="CI_MoA")
    _assert_class_interaction(conn, subject, obj, relationship="CI_PE")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")   # must NOT raise -- two real rules
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_class_interaction "
        "WHERE superseded_by IS NULL"
    ).fetchone() == (2,)


def test_a_correction_supersedes_and_the_old_row_survives(conn, ingest_run_id):
    """Correction-by-overlay: INSERT the new row, then point the old one at it.
    The old row must survive (never deleted, never rewritten except for
    superseded_by), and after the correction exactly one row on the natural key
    is live."""
    subject, obj = _two_classes(conn, ingest_run_id)
    original_id = _assert_class_interaction(conn, subject, obj, severity="moderate")
    corrected_id = _assert_class_interaction(conn, subject, obj, severity="major")
    conn.execute(
        "UPDATE drugref.curated_class_interaction SET superseded_by = %s "
        "WHERE curated_class_interaction_id = %s",
        (corrected_id, original_id),
    )
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")  # must NOT raise: one live row now
    row = conn.execute(
        "SELECT severity, superseded_by FROM drugref.curated_class_interaction "
        "WHERE curated_class_interaction_id = %s",
        (original_id,),
    ).fetchone()
    assert row == ("moderate", corrected_id)  # the old row's own data is untouched
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_class_interaction "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s "
        "AND relationship = 'CI_MoA' AND superseded_by IS NULL",
        (subject, obj),
    ).fetchone() == (1,)


def test_the_completeness_check_is_enforced_here_too(conn, ingest_run_id):
    """applies AND severity AND evidence_grade, or NOT applies AND neither --
    'real but ungraded' and 'not real but graded major' stay unrepresentable, the
    same CHECK shape as curated_interaction_ruling_is_complete."""
    subject, obj = _two_classes(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.CheckViolation,
        match="curated_class_interaction_ruling_is_complete",
    ):
        _assert_class_interaction(conn, subject, obj, severity=None)
    conn.rollback()
    run_id = _an_onc_run(conn)
    subject, obj = _two_classes(conn, run_id)
    with pytest.raises(
        psycopg.errors.CheckViolation,
        match="curated_class_interaction_ruling_is_complete",
    ):
        _assert_class_interaction(conn, subject, obj, applies=False)


def test_a_non_applying_row_with_no_grading_is_accepted(conn, ingest_run_id):
    """`applies = false` is a real answer here too -- "a curator looked and this
    class-pair rule is not a real interaction" -- and needs no severity or grade."""
    subject, obj = _two_classes(conn, ingest_run_id)
    row_id = _assert_class_interaction(
        conn, subject, obj, applies=False, severity=None, evidence_grade=None
    )
    assert row_id is not None


def test_applies_has_no_default(conn, ingest_run_id):
    """Same mutation test_curated_overlay.py guards against for curated_interaction:
    a DEFAULT on `applies` would turn an unstated ruling into a stated one."""
    subject, obj = _two_classes(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.curated_class_interaction "
            "(subject_class_uuid, object_class_uuid, relationship, source, "
            " reviewed_by, reviewed_against) "
            "VALUES (%s, %s, 'CI_MoA', 'DRUGREF', 'test', 'Phansalkar 2012')",
            (subject, obj),
        )


def test_the_severity_vocabulary_lives_in_the_database(conn, ingest_run_id):
    """A severity outside Plan C's four levels must be refused by the DATABASE, not
    by a Python list nobody keeps in step with it.

    A FOREIGN KEY SINCE db/035, not a CHECK, and the exception class is the whole
    observable difference -- exactly as the `relationship` test below already argues
    one column over. The four levels were five identical CHECKs (db/020 x2, db/029 x2,
    db/032); #97 needed an ORDER over them as well as a vocabulary, an order has to
    live in a table, and a vocabulary in a table plus a CHECK restating it is db/006's
    finding for the fifth time. Asserting ForeignKeyViolation is what proves the key is
    live rather than a stale CHECK still doing the work.
    """
    subject, obj = _two_classes(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.ForeignKeyViolation, match="curated_class_interaction_severity"
    ):
        _assert_class_interaction(conn, subject, obj, severity="catastrophic")


def test_an_unknown_relationship_is_refused_on_the_curated_table_too(
    conn, ingest_run_id
):
    """Same FK-not-CHECK shape as class_pair_contraindication above, on the
    overlay's own relationship column."""
    subject, obj = _two_classes(conn, ingest_run_id)
    with pytest.raises(
        psycopg.errors.ForeignKeyViolation,
        match="curated_class_interaction_relationship",
    ):
        _assert_class_interaction(conn, subject, obj, relationship="CI_XYZ")


def test_the_live_key_index_exists_by_name(assert_live_key_index):
    """Nothing but the deferred trigger reads this index, so nothing but a test
    protects it -- db/023 measured the cost of its absence at 5,773 ms against
    42 ms for a 2,000-row load. PARTIAL, NON-UNIQUE, and over exactly
    (subject_class_uuid, object_class_uuid, relationship) -- the trigger's own
    natural key, matched exactly as db/029's fixture already requires for its
    six sibling tables."""
    assert_live_key_index(
        "curated_class_interaction_live_key", "curated_class_interaction",
        "subject_class_uuid, object_class_uuid, relationship")


def test_the_question_is_indexed(conn):
    """`question_uuid` is a FOREIGN KEY with ON DELETE CASCADE, and Postgres
    indexes neither side of that automatically -- the referencing column is
    exactly the one it leaves bare. Mirrors curated_interaction_by_question
    (db/029) for the identical reason: register_from_gaps will probe this table
    with NOT EXISTS once per gap kind, and the cascade itself must find this
    table's rows before the append-only trigger can refuse the delete."""
    row = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND indexname = 'curated_class_interaction_by_question'"
    ).fetchone()
    assert row is not None, "curated_class_interaction_by_question is missing"
    assert "(question_uuid)" in row[0]
