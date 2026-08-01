# tests/test_schema_accumulation.py
"""Schema guarantees for the accumulation model (Plan C, db/020).

Four of the five tables here are CURATED CLINICAL ASSERTIONS corrected by OVERLAY --
`additive_effect`, `effect_contribution`, `interaction_group_assertion` and
`interaction_group_member`. Spec 5.0 spells out why that shape is not the obvious
one, and this module is the guard: every one of the four must be keyed on a
SURROGATE, with uniqueness enforced over LIVE rows only.

The test that matters most is the one that would pass under a natural-key primary
key and fail in production -- a correction must INSERT beside the row it supersedes.
With `additive_effect(effect_class_uuid)` as the PK that INSERT is rejected outright
and in-place mutation becomes the only possible implementation, which is precisely
what the overlay exists to prevent. db/001 shipped that defect on identity_claim and
db/005 repaired it; spec 5.0 says these four are the ones most likely to be built
with the natural key "because that is the shape they read as", so all four are
asserted rather than just the first.

The fifth table, `interaction_group`, is deliberately NOT in that set: it holds a
deterministic UUID and its provenance and nothing else, so there is nothing about it
that can be wrong. It is immortal instead -- the same discipline that keeps
moiety_uuid alive while its claims come and go.
"""
import pytest
import psycopg

from drugref import accumulation, ids


def _run(conn, source="DRUGREF"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, code, concept_type="PE", source="MED-RT"):
    """One registered class, for tests that need a live FK target."""
    class_uuid = ids.mint_class_uuid(source, code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (class_uuid, source, code, f"class {code}", concept_type, run_id))
    return class_uuid


def _effect(conn, run_id, class_uuid, major=1, total=2, severity="major"):
    return conn.execute(
        "INSERT INTO drugref.additive_effect (effect_class_uuid, accumulates, "
        "threshold_major, threshold_total, severity, clinical_note, source, ingest_run) "
        "VALUES (%s, true, %s, %s, %s, 'watch it', 'DRUGREF', %s) "
        "RETURNING additive_effect_id",
        (class_uuid, major, total, severity, run_id)).fetchone()[0]


def _group(conn, run_id, code="TRIPLE_WHAMMY"):
    group_uuid = ids.mint_group_uuid("DRUGREF", code)
    conn.execute(
        "INSERT INTO drugref.interaction_group (group_uuid, source, source_code, "
        "first_seen_ingest) VALUES (%s, 'DRUGREF', %s, %s) ON CONFLICT DO NOTHING",
        (group_uuid, code, run_id))
    return group_uuid


# ---- the source trio (spec 6) -----------------------------------------------


def test_drugref_is_admitted_as_an_ingest_run_source(conn):
    """Spec 6 calls this the one that actually stops the migration: every curated row
    below carries `ingest_run`, so without this nothing can be written at all."""
    assert _run(conn, "DRUGREF")


def test_drugref_is_admitted_as_a_class_source(conn):
    """drugref must be able to mint a class where the release names no concept --
    what the 2a.1 source-neutral refactor was for."""
    run_id = _run(conn)
    assert _class(conn, run_id, "NEPHROTOX", source="DRUGREF")


def test_the_source_trio_stays_in_lockstep(conn):
    """Spec 6: substance_class.source, ingest_run.source and ids._SOURCE_CANONICAL are
    a TRIO, not a pair. A future source that extends one CHECK and forgets the others
    fails here rather than in a per-source rebuild that silently deletes nothing."""
    admitted = [r[0] for r in conn.execute(
        "SELECT unnest(enum_or_check_values) FROM ("
        "  SELECT regexp_matches(pg_get_constraintdef(oid), '''([^'']+)''', 'g') "
        "         AS enum_or_check_values "
        "  FROM pg_constraint WHERE conname = 'substance_class_source') s").fetchall()]
    for source in admitted:
        # admitted to substance_class => admitted to ingest_run
        assert _run(conn, source), f"{source} is a class source but not a run source"
        # ...and canonicalises to exactly the spelling that is stored
        assert ids.canonical_source(source) == source, (
            f"{source} is stored under a spelling ids.canonical_source does not return")


# ---- the overlay row shape, on EACH of the four assertion tables (spec 5.0) ---


def test_additive_effect_is_keyed_on_a_surrogate(conn):
    run_id = _run(conn)
    cls = _class(conn, run_id, "N0001")
    first = _effect(conn, run_id, cls)
    # The correction: insert beside, THEN point the old row at it. Under a
    # natural-key PK this INSERT is what fails.
    second = _effect(conn, run_id, cls, major=2, total=3)
    assert second != first
    conn.execute("UPDATE drugref.additive_effect SET superseded_by = %s "
                 "WHERE additive_effect_id = %s", (second, first))
    # Force the deferred single-live check NOW. Without this the test would pass on a
    # transaction that could never commit, which is worse than no test at all.
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    live = conn.execute(
        "SELECT additive_effect_id FROM drugref.additive_effect "
        "WHERE effect_class_uuid = %s AND superseded_by IS NULL", (cls,)).fetchall()
    assert [r[0] for r in live] == [second]
    # ...and the superseded row is still THERE. What drugref believed, and when,
    # stays answerable.
    assert conn.execute(
        "SELECT count(*) FROM drugref.additive_effect WHERE effect_class_uuid = %s",
        (cls,)).fetchone()[0] == 2


def test_two_live_additive_effects_for_one_class_are_refused(conn):
    """The invariant the DEFERRAL must not weaken: leaving both rows live is a
    contradiction (which threshold is the effect on?) and fails when the constraint is
    checked -- at commit in production, forced here without committing.

    Deferred rather than a partial unique index, which is what spec 5.0 asks for and
    what this test was originally written against. The index cannot work: a correction
    here keeps the SAME natural key, so both rows are live for the instant between the
    INSERT and the UPDATE that points the old one at the new, and an immediate check
    rejects the only sequence that can express a correction. db/007 met this on
    question_state first; see db/020's comment."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "N0002")
    _effect(conn, run_id, cls)
    _effect(conn, run_id, cls)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_effect_contribution_is_keyed_on_a_surrogate(conn):
    run_id = _run(conn)
    eff = _class(conn, run_id, "N0003")
    con = _class(conn, run_id, "N0004")
    ids_ = []
    for magnitude in ("minor", "major"):
        ids_.append(conn.execute(
            "INSERT INTO drugref.effect_contribution (effect_class_uuid, "
            "contributor_class_uuid, magnitude, source, ingest_run) "
            "VALUES (%s, %s, %s, 'DRUGREF', %s) RETURNING effect_contribution_id",
            (eff, con, magnitude, run_id)).fetchone()[0])
    conn.execute("UPDATE drugref.effect_contribution SET superseded_by = %s "
                 "WHERE effect_contribution_id = %s", (ids_[1], ids_[0]))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT magnitude FROM drugref.effect_contribution WHERE superseded_by IS NULL "
        "AND effect_class_uuid = %s", (eff,)).fetchone()[0] == "major"


def test_interaction_group_assertion_is_keyed_on_a_surrogate(conn):
    run_id = _run(conn)
    grp = _group(conn, run_id)
    ids_ = []
    for name in ("triple whammy", "triple whammy (revised)"):
        ids_.append(conn.execute(
            "INSERT INTO drugref.interaction_group_assertion (group_uuid, name, "
            "severity, clinical_note, applies, source, ingest_run) "
            "VALUES (%s, %s, 'major', 'AKI risk', true, 'DRUGREF', %s) "
            "RETURNING interaction_group_assertion_id", (grp, name, run_id)).fetchone()[0])
    conn.execute("UPDATE drugref.interaction_group_assertion SET superseded_by = %s "
                 "WHERE interaction_group_assertion_id = %s", (ids_[1], ids_[0]))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT name FROM drugref.interaction_group_assertion "
        "WHERE group_uuid = %s AND superseded_by IS NULL", (grp,)).fetchone()[0] == \
        "triple whammy (revised)"


def test_interaction_group_member_is_keyed_on_a_surrogate(conn):
    """Spec 5.3: the first draft left THIS table a bare natural-key one, so the part
    that actually determines whether a group FIRES was mutable in place while the
    header was append-only."""
    run_id = _run(conn)
    grp = _group(conn, run_id)
    cls = _class(conn, run_id, "N0005", concept_type="EPC")
    first = conn.execute(
        "INSERT INTO drugref.interaction_group_member (group_uuid, role, class_uuid, "
        "satisfies_role, source, ingest_run) "
        "VALUES (%s, 'diuretic', %s, true, 'DRUGREF', %s) "
        "RETURNING interaction_group_member_id", (grp, cls, run_id)).fetchone()[0]
    second = conn.execute(
        "INSERT INTO drugref.interaction_group_member (group_uuid, role, class_uuid, "
        "satisfies_role, source, ingest_run) "
        "VALUES (%s, 'diuretic', %s, true, 'DRUGREF', %s) "
        "RETURNING interaction_group_member_id", (grp, cls, run_id)).fetchone()[0]
    conn.execute("UPDATE drugref.interaction_group_member SET superseded_by = %s "
                 "WHERE interaction_group_member_id = %s", (second, first))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.interaction_group_member WHERE group_uuid = %s",
        (grp,)).fetchone()[0] == 2


# ---- supersession is one-way, on every one of the four (spec 5.0) ------------


ASSERTION_TABLES = [
    ("additive_effect", "additive_effect_id"),
    ("effect_contribution", "effect_contribution_id"),
    ("interaction_group_assertion", "interaction_group_assertion_id"),
    ("interaction_group_member", "interaction_group_member_id"),
]


@pytest.fixture
def two_rows(conn, request):
    """A (table, pk, first_id, second_id) tuple for whichever table is parametrised."""
    table, pk = request.param
    run_id = _run(conn)
    if table == "additive_effect":
        cls = _class(conn, run_id, "S001")
        return table, pk, _effect(conn, run_id, cls), _effect(conn, run_id, cls)
    if table == "effect_contribution":
        eff, con = _class(conn, run_id, "S002"), _class(conn, run_id, "S003")
        made = [conn.execute(
            "INSERT INTO drugref.effect_contribution (effect_class_uuid, "
            "contributor_class_uuid, magnitude, source, ingest_run) "
            "VALUES (%s, %s, 'minor', 'DRUGREF', %s) RETURNING effect_contribution_id",
            (eff, con, run_id)).fetchone()[0] for _ in range(2)]
        return table, pk, made[0], made[1]
    grp = _group(conn, run_id)
    if table == "interaction_group_assertion":
        made = [conn.execute(
            "INSERT INTO drugref.interaction_group_assertion (group_uuid, name, "
            "severity, applies, source, ingest_run) "
            "VALUES (%s, 'g', 'major', true, 'DRUGREF', %s) "
            "RETURNING interaction_group_assertion_id", (grp, run_id)).fetchone()[0]
            for _ in range(2)]
        return table, pk, made[0], made[1]
    cls = _class(conn, run_id, "S004")
    made = [conn.execute(
        "INSERT INTO drugref.interaction_group_member (group_uuid, role, class_uuid, "
        "satisfies_role, source, ingest_run) "
        "VALUES (%s, 'nsaid', %s, true, 'DRUGREF', %s) "
        "RETURNING interaction_group_member_id", (grp, cls, run_id)).fetchone()[0]
        for _ in range(2)]
    return table, pk, made[0], made[1]


@pytest.mark.parametrize("two_rows", ASSERTION_TABLES, indirect=True,
                         ids=[t for t, _ in ASSERTION_TABLES])
def test_delete_is_forbidden(conn, two_rows):
    table, pk, first, _second = two_rows
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(f"DELETE FROM drugref.{table} WHERE {pk} = %s", (first,))


@pytest.mark.parametrize("two_rows", ASSERTION_TABLES, indirect=True,
                         ids=[t for t, _ in ASSERTION_TABLES])
def test_only_superseded_by_may_change(conn, two_rows):
    table, pk, first, _second = two_rows
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(f"UPDATE drugref.{table} SET source = 'MED-RT' WHERE {pk} = %s",
                     (first,))


@pytest.mark.parametrize("two_rows", ASSERTION_TABLES, indirect=True,
                         ids=[t for t, _ in ASSERTION_TABLES])
def test_supersession_cannot_be_unset(conn, two_rows):
    table, pk, first, second = two_rows
    conn.execute(f"UPDATE drugref.{table} SET superseded_by = %s WHERE {pk} = %s",
                 (second, first))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(f"UPDATE drugref.{table} SET superseded_by = NULL WHERE {pk} = %s",
                     (first,))


@pytest.mark.parametrize("two_rows", ASSERTION_TABLES, indirect=True,
                         ids=[t for t, _ in ASSERTION_TABLES])
def test_supersession_must_point_forward(conn, two_rows):
    """A monotonically increasing surrogate is what makes a CYCLE unrepresentable --
    the reason spec 5.0 needs the surrogate and not just the partial index."""
    table, pk, first, second = two_rows
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(f"UPDATE drugref.{table} SET superseded_by = %s WHERE {pk} = %s",
                     (first, second))


def test_a_correction_must_keep_the_same_natural_key(conn):
    """db/005's same-moiety rule, generalised: a correction replaces a statement about
    THIS effect, never a different one. Pointing across subjects is not a correction,
    it is a merge, and the model has no merge semantics."""
    run_id = _run(conn)
    one, two = _class(conn, run_id, "S010"), _class(conn, run_id, "S011")
    first, other = _effect(conn, run_id, one), _effect(conn, run_id, two)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.additive_effect SET superseded_by = %s "
                     "WHERE additive_effect_id = %s", (other, first))


# ---- interaction_group: identity, deliberately NOT superseded (spec 5.4) -----


def test_interaction_group_has_no_superseded_by(conn):
    """It holds a deterministic UUID and its provenance, so there is nothing about it
    that CAN be wrong. Retiring a group supersedes its assertion, never its identity."""
    cols = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'interaction_group'").fetchall()]
    # Assert the table was FOUND before asserting what it lacks: an absent table
    # returns no columns, and "superseded_by not in []" is true of nothing at all.
    assert "group_uuid" in cols, "interaction_group is missing entirely"
    assert "superseded_by" not in cols


def test_interaction_group_identity_is_immortal(conn):
    run_id = _run(conn)
    grp = _group(conn, run_id)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.interaction_group WHERE group_uuid = %s", (grp,))


def test_interaction_group_uuid_is_the_minted_one(conn):
    run_id = _run(conn)
    assert _group(conn, run_id, "TRIPLE_WHAMMY") == \
        ids.mint_group_uuid("DRUGREF", "TRIPLE_WHAMMY")


# ---- thresholds: the CHECKs spec 5.1 asks for -------------------------------


def test_threshold_total_must_be_at_least_one(conn):
    """(0,0) would fire on a regimen containing NO contributor at all."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "T001")
    with pytest.raises(psycopg.errors.CheckViolation):
        _effect(conn, run_id, cls, major=0, total=0)


def test_threshold_total_may_not_be_below_threshold_major(conn):
    """majors >= 2 AND contributors >= 1 is unsatisfiable-by-construction nonsense:
    every major IS a contributor, so total can never be the smaller number."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "T002")
    with pytest.raises(psycopg.errors.CheckViolation):
        _effect(conn, run_id, cls, major=2, total=1)


def test_threshold_major_may_be_zero(conn):
    """LEGAL BUT LOAD-BEARING (spec 5.1 / tension A): (0,2) is the correct encoding
    for a fully curated effect where every member really does count. The schema must
    NOT forbid it -- gap_uncurated_threshold surfaces the risky case instead."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "T003")
    assert _effect(conn, run_id, cls, major=0, total=2)


def test_severity_vocabulary_is_constrained(conn):
    """CHECK-constrained rather than free text so it cannot drift per curator."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "T004")
    with pytest.raises(psycopg.errors.CheckViolation):
        _effect(conn, run_id, cls, severity="quite bad")


def test_magnitude_vocabulary_is_two_values(conn):
    """Tension E: two values deliberately. A finer scale invites precision the
    evidence cannot support, and dose is unavailable until slice 4."""
    run_id = _run(conn)
    eff, con = _class(conn, run_id, "T005"), _class(conn, run_id, "T006")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.effect_contribution (effect_class_uuid, "
            "contributor_class_uuid, magnitude, source, ingest_run) "
            "VALUES (%s, %s, 'moderate', 'DRUGREF', %s)", (eff, con, run_id))


# ---- rulings: how a curator says NO (db/020's addition to spec 5) ------------


def test_a_non_accumulating_ruling_carries_no_thresholds(conn):
    """`accumulates = false` is a real answer, and it must not carry filler numbers:
    a threshold on a ruling that says the effect does not add up is meaningless data
    in a clinical table."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "R001")
    assert conn.execute(
        "INSERT INTO drugref.additive_effect (effect_class_uuid, accumulates, source, "
        "ingest_run) VALUES (%s, false, 'DRUGREF', %s) RETURNING additive_effect_id",
        (cls, run_id)).fetchone()[0]


def test_a_non_accumulating_ruling_may_not_state_a_threshold(conn):
    run_id = _run(conn)
    cls = _class(conn, run_id, "R002")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.additive_effect (effect_class_uuid, accumulates, "
            "threshold_major, threshold_total, severity, source, ingest_run) "
            "VALUES (%s, false, 1, 2, 'major', 'DRUGREF', %s)", (cls, run_id))


def test_an_accumulating_effect_must_state_all_three_judgements(conn):
    """An effect that accumulates with no threshold would fire on nothing or on
    everything depending on how a consumer reads a NULL -- so it is unrepresentable
    rather than left to a convention nobody enforces."""
    run_id = _run(conn)
    cls = _class(conn, run_id, "R003")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.additive_effect (effect_class_uuid, accumulates, "
            "threshold_major, source, ingest_run) "
            "VALUES (%s, true, 1, 'DRUGREF', %s)", (cls, run_id))


def test_satisfies_role_has_no_default(conn):
    """db/014's discipline: a later curator must STATE whether the class satisfies the
    role, never inherit a guess. A DEFAULT would answer the question silently."""
    run_id = _run(conn)
    grp = _group(conn, run_id)
    cls = _class(conn, run_id, "R004", concept_type="EPC")
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.interaction_group_member (group_uuid, role, "
            "class_uuid, source, ingest_run) VALUES (%s, 'diuretic', %s, 'DRUGREF', %s)",
            (grp, cls, run_id))


def test_accumulates_has_no_default(conn):
    run_id = _run(conn)
    cls = _class(conn, run_id, "R005")
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.additive_effect (effect_class_uuid, source, ingest_run) "
            "VALUES (%s, 'DRUGREF', %s)", (cls, run_id))


def test_applies_has_no_default(conn):
    """The third table that needed a retirement column (db/023). `additive_effect` and
    `interaction_group_member` each got one in db/020 for the same reason -- supersession
    must point at a later row carrying the SAME natural key, so a group would otherwise
    always keep exactly one live assertion and could never be withdrawn as a whole."""
    run_id = _run(conn)
    grp = _group(conn, run_id, "R006")
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.interaction_group_assertion (group_uuid, name, "
            "severity, source, ingest_run) VALUES (%s, 'g', 'major', 'DRUGREF', %s)",
            (grp, run_id))


def test_a_group_can_be_retired_as_a_whole(conn):
    """Retiring a GROUP is an insert of `applies = false` that supersedes the true row,
    exactly as retiring a member is an insert of `satisfies_role = false`. Before db/023
    the only route was retiring every member one at a time, which left a live assertion
    still claiming a severity for a group that could never fire."""
    run_id = _run(conn)
    grp = _group(conn, run_id, "R007")
    accumulation.assert_group(conn, grp, "triple whammy", "major", run_id)
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    accumulation.assert_group(conn, grp, "triple whammy", "major", run_id, applies=False)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT applies FROM drugref.interaction_group_assertion "
        "WHERE group_uuid = %s AND superseded_by IS NULL", (grp,)).fetchone()[0] is False
    # ...and the identity every member and external citation points at is untouched
    assert conn.execute(
        "SELECT count(*) FROM drugref.interaction_group WHERE group_uuid = %s",
        (grp,)).fetchone()[0] == 1


# ---- the single-live check must stay INDEXABLE (db/023) ----------------------


@pytest.mark.parametrize("index_name,table", [
    ("additive_effect_live_key", "additive_effect"),
    ("effect_contribution_live_key", "effect_contribution"),
    ("interaction_group_assertion_live_key", "interaction_group_assertion"),
    ("interaction_group_member_live_key", "interaction_group_member"),
])
def test_every_assertion_table_has_a_live_natural_key_index(conn, index_name, table):
    """db/020's first single-live trigger asked `to_jsonb(t) @> $1`, which no index can
    serve -- so the deferred check at COMMIT was a SEQUENTIAL SCAN PER ROW, measured at
    5.8 s to load 2,000 promotions and rising quadratically. db/023 rewrote it to
    equality predicates; these partial indexes are what make that rewrite pay, and this
    test is what stops one being dropped as "unused" (nothing but the trigger reads it).
    """
    definition = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND tablename = %s AND indexname = %s", (table, index_name)).fetchone()
    assert definition is not None, f"{index_name} is missing from drugref.{table}"
    assert "superseded_by IS NULL" in definition[0], (
        "the index must be PARTIAL over live rows -- a full index would not answer "
        "the trigger's question, and a UNIQUE one is what this whole design cannot use")
