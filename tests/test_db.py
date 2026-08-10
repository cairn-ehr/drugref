# tests/test_db.py
"""Focused tests for drugref.db: connect() and apply_migrations() idempotency.

apply_migrations()'s docstring claims the migration SQL is idempotent (safe to
replay on an already-migrated database, mirroring Cairn's connect-and-load
convention). These tests hold that claim to account: a second apply must not
raise, and a fresh connection must be genuinely usable.
"""
import pytest
from drugref import db, ids


def test_connect_without_dsn_raises_clear_error(monkeypatch):
    """No dsn arg and no DRUGREF_DSN env -> a clear RuntimeError, not a bare
    KeyError. Runs anywhere (no database needed)."""
    monkeypatch.delenv("DRUGREF_DSN", raising=False)
    with pytest.raises(RuntimeError, match="DRUGREF_DSN"):
        db.connect()


def test_connect_returns_usable_connection(_dsn):
    """db.connect(dsn) with an explicit DSN opens a connection that can run a query."""
    conn = db.connect(_dsn)
    try:
        row = conn.execute("SELECT 1").fetchone()
        assert row == (1,)
    finally:
        conn.close()


def test_apply_migrations_is_idempotent(conn):
    """Re-running apply_migrations on an already-migrated database must not error,
    and every drugref table must still be present afterwards."""
    # `conn` (from conftest) is already migrated once via the session-scoped
    # `_migrated` fixture. Applying again must be a no-op, not a crash.
    db.apply_migrations(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'drugref'"
        ).fetchall()
    }
    assert tables == {
        # the migration runner's own ledger (created by db.py, not by a db/*.sql
        # file, because it decides whether those files run at all)
        "schema_migration",
        # slice 1: the identity spine
        "ingest_run", "substance_moiety", "identity_claim",
        # slice 2a: the classification DAG
        "substance_class", "class_parent", "class_membership",
        # slice 5a: the interaction projection, plus its read-time pair-expansion
        # VIEW (information_schema.tables lists views too -- an exact inventory that
        # catches any object created by accident, so the view is named explicitly).
        "class_contraindication", "ddi_candidate_pair",
        # the contraindication predicate vocabulary: which CI predicates exist and
        # which membership axis each expands over (db/006)
        "ci_axis",
        # Plan A, db/007: the open-question registry. One rebuildable projection
        # (open_question) and three append-only curated tables keyed off its
        # deterministic UUID -- the split that stops a rebuild erasing curator intent.
        "open_question", "question_state", "question_source_check", "question_evidence",
        # Plan A, db/008: the gap views, the one table a gap view needed (the
        # unmatched RxCUI identities, which the ingest previously discarded), the
        # cost-ladder vocabulary, and the worklist that orders by it.
        "ingest_unmatched_ingredient", "source_tier",
        "gap_unpopulated_contraindication", "gap_unclassified_moiety",
        "gap_unmatched_ingredient", "question_worklist",
        # slice 8a, db/009: the local (AU/PBS) tier -- a rebuildable projection
        # bridged to the global moiety spine, plus its unmatched-ingredient ledger.
        "local_product", "local_product_moiety", "local_unmatched_ingredient",
        # Plan B, db/010: descendant expansion. The policy table is CURATOR DATA --
        # no ingest clears it -- plus the two views that stop it rotting silently:
        # one for a large root nobody has ruled on, one for a ruling whose class the
        # release no longer defines. db/027 (#35) made the table append-only and
        # added the view every reader of it must go through.
        "class_expansion_policy", "class_expansion_policy_current",
        "gap_unreviewed_expansion_root", "expansion_policy_unresolved",
        # db/011 (#26): why each moiety passed the membership gate. A rebuildable
        # projection -- the moiety is immortal, the evidence is per-release.
        "moiety_admission",
        # db/012: the class-DAG descent, hoisted out of the three views that each
        # carried a copy of it. One recursion in the codebase, not three.
        "ci_class_subtree",
        # slice 5b, db/013: the MeSH condition registry -- the object side of a
        # drug-condition contraindication. Not a substance_class: nothing is a
        # MEMBER of pregnancy, so this is its own table pair, not a widened axis.
        "condition", "condition_parent",
        # slice 5b, db/014: the two contraindication relations (drug-condition,
        # drug-drug) plus their vocabularies/worklist. Two relations, not one,
        # because CI_with's object is a condition and CI_ChemClass's moiety arm's
        # object is another moiety -- different kinds of thing, different tables.
        "condition_ci_axis", "moiety_condition_contraindication",
        "moiety_contraindication", "ingest_unresolved_ci_object",
        # slice 5b, db/015: the read path over the condition DAG -- the same
        # recursion shape as db/012's ci_class_subtree, over condition_parent
        # instead of class_parent. Two VIEWs, named explicitly for the same reason
        # ddi_candidate_pair is above: information_schema.tables lists views too.
        "condition_subtree", "condition_contraindication_expanded",
        # slice 5b, db/016: the review gate for the withheld CI_ChemClass class arm --
        # a fourth gap VIEW of db/008's kind, publishing each withheld MeSH object as
        # a citable question instead of dropping it silently.
        "gap_unresolved_ci_object",
        # db/018 (#31): the SIXTH gap view and sixth gap_kind -- contraindications a
        # DENIED expansion root leaves reaching nobody, which
        # gap_unpopulated_contraindication cannot see (it tests the whole subtree) and
        # gap_unreviewed_expansion_root will not ask about (the class HAS been
        # reviewed). Plus the reach measure BOTH dead-rule views filter, hoisted out
        # of the two near-identical CTEs that first carried it -- db/012's move, for
        # db/012's reason: only one of the two copies had learned that a rule's own
        # subject is not a partner, and the views disagreed.
        "ci_rule_partner_reach", "gap_dead_by_expansion_policy",
        # slice 5b.2, db/019: the two indication relations (drug-condition
        # therapeutic, drug-condition induced) plus their vocabulary. Two tables,
        # not one, for the reason db/014 split contraindications: an unfiltered read
        # of each must be one true sentence, "used for" vs "causes".
        "condition_indication_axis", "moiety_condition_indication",
        "moiety_induced_condition",
        # slice 5b.2, db/019 section 5: the read path over the same condition DAG.
        # THE TWO HALVES WALK OPPOSITE WAYS, and that is the slice's central safety
        # distinction rather than a detail: indications_for_condition walks UP from the
        # patient's condition to its ancestors, because walking DOWN from a rule's
        # object would manufacture therapeutic claims the release never made (db/015's
        # contraindication expansion does walk down, soundly, for the opposite reason).
        # condition_indication_reach then walks DOWN from each rule's object to COUNT
        # what that upward walk will find -- the same set, enumerated from the other
        # end, which is why a test pins the two against each other. One VIEW --
        # named explicitly for the same reason as db/015's and db/018's views above;
        # indications_for_condition is a FUNCTION and does not appear in
        # information_schema.tables.
        "condition_indication_reach",
        # slice 5b.2, db/019 section 6: the seventh gap kind -- diseases drugref
        # holds no indication for, direct or generalised. A VIEW, complementary
        # filter on condition_indication_reach rather than a table of its own, named
        # explicitly for the same reason as every other gap_* view above.
        "gap_condition_without_indication",
        # Plan C, db/020: the accumulation model. FIVE tables, all CURATED and all
        # append-only -- the first content drugref asserts on its own authority
        # (source = 'DRUGREF') rather than projecting from an upstream release.
        # Four carry the spec-5.0 overlay shape (surrogate PK + deferred single-live
        # + one-way supersession); interaction_group is the deliberate exception,
        # holding only a deterministic UUID and its provenance, so there is nothing
        # about it that can be wrong and nothing to correct.
        "additive_effect", "effect_contribution", "interaction_group",
        "interaction_group_assertion", "interaction_group_member",
        # Plan C, db/021: the SECOND walk down the class DAG, plus the two views that
        # are spec 8's output contract. class_subtree is unscoped where db/012's
        # ci_class_subtree is scoped to the classes a contraindication names, and they
        # are deliberately NOT merged -- that scoping is what makes the hot pair
        # lookup 3.6 ms instead of 18.8 ms on the real release, for an identical row
        # set. Both new views are named here for the same reason every other view is:
        # information_schema.tables lists views too, so this inventory catches any
        # object created by accident.
        "class_subtree", "additive_effect_contributor",
        "interaction_group_member_moiety",
        # Plan C, db/022: the four curation-dependent gap views -- gap kinds eight
        # through eleven. Unlike the coverage kinds, all four are questions drugref
        # ANSWERS ITSELF by recording a decision, so no source tier orders them.
        "gap_uncurated_additive_effect", "gap_uncurated_threshold",
        "gap_ineffective_contribution", "gap_ungraded_contribution",
        # #16, db/025: the two run-observability views, complementary filters on
        # ingest_run.finished_at. Named here for the same reason every other view
        # is -- information_schema.tables lists views too, so this inventory
        # catches any object created by accident.
        "loaded_release", "ingest_run_incomplete",
        # Slice 3, db/028: the composition tree -- which registered moieties a
        # GSRS substance is composed of. composition_relation is the vocabulary
        # table (db/006's precedent: a FOREIGN KEY, not a second CHECK);
        # substance_composition is the projection; moiety_active_in_composite is
        # the read path (only the ACTIVE component propagates); and
        # gap_unruled_composition_activity is gap kind twelve, populated from day
        # one because unlike the curation-dependent gaps above it needs no
        # curator decision to have rows.
        "composition_relation", "substance_composition",
        "moiety_active_in_composite", "gap_unruled_composition_activity",
        # Slice 5c.1, db/029: the curated overlay's first table -- drugref's own
        # judgement (severity, mechanism, management, evidence grade) on a
        # class-level CI_MoA/CI_PE rule, keyed on the RULE rather than the pair.
        # Ships empty; the floor is db/020's, reused rather than copied.
        "curated_interaction",
        # Slice 5c.1, db/029: the curated overlay's second table -- drugref's
        # ruling on a (drug, condition) PAIR, keyed WITHOUT `relationship` so the
        # 168 pairs MED-RT asserts as both an indication and a contraindication
        # get one row and one judgement rather than two that can disagree. Ships
        # empty; the floor is db/020's, reused rather than copied.
        "curated_condition",
        # Slice 5c.1, db/029 section 3: the two read-path views over the tables
        # above. INNER JOIN throughout -- an ungraded candidate reaches neither
        # view at all, never with a NULL curated column beside it -- and named
        # explicitly here for the same reason every other view in this inventory
        # is: information_schema.tables lists views too.
        "curated_ddi_pair", "curated_condition_ruling",
        # Slice 5c.1, db/029 sections 4-5: the curation WORKLIST. Two gap views --
        # gap kinds thirteen and fourteen, the first two whose answer is a curated
        # row rather than a lookup -- plus one operator check (NOT a gap kind: a
        # vanished candidate is an upstream-change signal, not a clinical question).
        # Named explicitly for the same information_schema.tables reason as above.
        "gap_uncurated_condition_contradiction", "gap_uncurated_interaction_rule",
        "curated_target_unresolved",
        # Slice 5c.4, db/030: the signing tables. Two seeded vocabularies --
        # signing_key_status_kind (the revocation rule as DATA) and
        # signature_target_kind (what a signature may point at, one home for the
        # kind -> table/pk_column/context mapping) -- plus the key registry
        # (signing_key, db/020's overlay floor's EIGHTH table, no new PL/pgSQL),
        # assertion_signature (one canonical-payload signature per row, insert-only
        # under the new forbid_any_rewrite), and the release layer
        # (release_manifest + release_manifest_entry, also insert-only, entries
        # keyed on the natural key rather than the database-local target_id).
        "signing_key_status_kind", "signature_target_kind", "signing_key",
        "assertion_signature", "release_manifest", "release_manifest_entry",
        # Slice 5c.4, db/030 section 7: the read path. curated_signature_status is
        # REGISTRY-LEVEL ONLY (Postgres cannot check an Ed25519 signature) and
        # LEFT-joined into curated_ddi_pair/curated_condition_ruling below, on pain
        # of a key revocation silently withdrawing a contraindication from every
        # consumer -- fewer rows is the harm direction for a contraindication.
        # signature_backdated is an operator signal, not a gap kind. Both are VIEWs,
        # named explicitly for the same information_schema.tables reason as above.
        "curated_signature_status", "signature_backdated",
    }


def test_every_migration_is_recorded_in_the_ledger(conn):
    """Without a ledger, 'has this migration run?' has to be reverse-engineered from
    the catalog by hand-written guards in each file -- which is why db/003 carries
    three different DO-block idioms. The ledger answers it directly."""
    recorded = {row[0] for row in conn.execute(
        "SELECT filename FROM drugref.schema_migration").fetchall()}
    on_disk = {p.name for p in db.migration_dir().glob("*.sql")}
    assert recorded == on_disk


def test_editing_an_already_applied_migration_is_refused_loudly(conn):
    """The failure this ledger exists to catch. db/003's own comment tells the next
    author to extend its source CHECK in place, but its guard only asks 'does this
    constraint exist', so an in-place edit silently never reaches a database that
    already ran the file -- fresh and migrated databases then diverge with no error.
    A checksum mismatch must stop the run instead.

    apply_migrations commits, so this restores the ledger explicitly rather than
    relying on the conn fixture's rollback.
    """
    target = "001_schema_drugref.sql"
    original = conn.execute(
        "SELECT checksum FROM drugref.schema_migration WHERE filename = %s",
        (target,)).fetchone()[0]
    conn.execute("UPDATE drugref.schema_migration SET checksum = 'tampered' "
                 "WHERE filename = %s", (target,))
    conn.commit()
    try:
        with pytest.raises(RuntimeError, match=target):
            db.apply_migrations(conn)
    finally:
        conn.rollback()
        conn.execute("UPDATE drugref.schema_migration SET checksum = %s "
                     "WHERE filename = %s", (original, target))
        conn.commit()


def test_migration_dir_prefers_the_packaged_copy(tmp_path, monkeypatch):
    """An INSTALLED drugref reads the .sql files the wheel shipped, not a directory
    three parents up from site-packages that it does not own. Both candidates exist
    here, so this pins the ORDER rather than merely that something is found."""
    packaged, source = tmp_path / "packaged", tmp_path / "source"
    for d in (packaged, source):
        d.mkdir()
        (d / "001_x.sql").write_text("SELECT 1;")
    monkeypatch.setattr(db, "_PACKAGED_MIGRATIONS", packaged)
    monkeypatch.setattr(db, "_SOURCE_MIGRATIONS", source)

    assert db.migration_dir() == packaged


def test_migration_dir_falls_back_to_the_source_checkout(tmp_path, monkeypatch):
    """The dev layout: src/drugref/migrations/ does not exist in a checkout (the
    force-include only builds it into a wheel), so `uv run drugref migrate` must keep
    reading db/ at the repository root."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "001_x.sql").write_text("SELECT 1;")
    monkeypatch.setattr(db, "_PACKAGED_MIGRATIONS", tmp_path / "absent")
    monkeypatch.setattr(db, "_SOURCE_MIGRATIONS", source)

    assert db.migration_dir() == source


def test_a_directory_holding_no_sql_is_refused_like_a_missing_one(tmp_path, monkeypatch):
    """AN EMPTY DIRECTORY IS THE SAME CATASTROPHE AS A MISSING ONE -- zero files
    applied -- and Path.glob reports neither. The wheel-install bug this guards
    against is exactly that: the directory question answered silently."""
    present_but_empty = tmp_path / "empty"
    present_but_empty.mkdir()
    monkeypatch.setattr(db, "_PACKAGED_MIGRATIONS", present_but_empty)
    monkeypatch.setattr(db, "_SOURCE_MIGRATIONS", tmp_path / "absent")

    with pytest.raises(db.MissingMigrationsError, match="no migration SQL found"):
        db.migration_dir()


def test_apply_migrations_cannot_report_success_having_applied_nothing(tmp_path, monkeypatch):
    """THE DEFECT IN ONE TEST. `drugref migrate` from a wheel that shipped no .sql
    created the ledger, applied nothing, committed, and returned normally -- so the
    CLI printed "migrations applied" and the very next command died with
    UndefinedTable on a database the operator had just been told was migrated.

    The stand-in connection REFUSES EVERY STATEMENT, which is what pins the ordering
    as well as the failure: the directory is resolved BEFORE the ledger DDL runs, so a
    broken install leaves the database exactly as it found it rather than holding an
    empty schema_migration that makes the next run look partially complete. A real
    connection could not show that -- the shared test database already has a ledger.
    """
    class _RefusingConn:
        def execute(self, *args, **kwargs):
            raise AssertionError("apply_migrations touched the database before it "
                                 "knew it had anything to apply")

    monkeypatch.setattr(db, "_PACKAGED_MIGRATIONS", tmp_path / "absent-packaged")
    monkeypatch.setattr(db, "_SOURCE_MIGRATIONS", tmp_path / "absent-source")

    with pytest.raises(db.MissingMigrationsError):
        db.apply_migrations(_RefusingConn())


def test_replaying_migrations_preserves_existing_classes(conn):
    """db/003 generalises the class registry by RENAMING columns, which is the
    riskiest kind of migration to replay: a guard that misfires either errors on
    the second pass or silently rebuilds the table and drops every class row --
    taking the class_uuids that class_parent and class_membership join on with it.

    apply_migrations() commits, so this test cannot rely on the `conn` fixture's
    rollback and cleans up after itself explicitly (the same reason the
    orchestrator test modules carry their own cleanup -- see conftest).
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'replay-test', 'deadbeef', 'medrt_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    class_uuid = ids.mint_class_uuid("MED-RT", "N0000999999")
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, 'MED-RT', 'N0000999999', 'N0000999999', "
        " 'Replay Probe [MoA]', 'MoA', %s)", (class_uuid, run_id))
    conn.commit()
    try:
        db.apply_migrations(conn)
        assert conn.execute(
            "SELECT source, source_code, class_name FROM drugref.substance_class "
            "WHERE class_uuid = %s", (class_uuid,)).fetchone() == (
                "MED-RT", "N0000999999", "Replay Probe [MoA]")
    finally:
        conn.execute("DELETE FROM drugref.substance_class WHERE class_uuid = %s", (class_uuid,))
        conn.execute("DELETE FROM drugref.ingest_run WHERE ingest_run_id = %s", (run_id,))
        conn.commit()


def test_migration_003_renames_populated_columns_and_keeps_edges(conn):
    """The test above replays 003 over a table ALREADY in the target shape, so it
    exercises the guard's "already renamed" branch. This one exercises the branch
    that actually does the rename -- 002's medrt_nui/medrt_code columns present and
    populated, no source column yet -- because a RENAME of a column with rows in it,
    joined to by a foreign key, is the exact operation the whole migration risks.

    It reconstructs the pre-003 shape on the (empty) live table, populates it with a
    class_parent edge, applies ONLY db/003, and proves the edge still joins its two
    class rows. All DDL here is transactional, so the `conn` fixture's rollback
    restores the shared schema -- nothing is committed.
    """
    # Reconstruct db/002's column shape by reversing 003 on the empty table.
    conn.execute("ALTER TABLE drugref.substance_class DROP COLUMN source CASCADE")
    conn.execute("ALTER TABLE drugref.substance_class RENAME COLUMN source_code TO medrt_nui")
    conn.execute("ALTER TABLE drugref.substance_class RENAME COLUMN published_code TO medrt_code")

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'rename-test', 'deadbeef', 'medrt_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    parent = ids.mint_class_uuid("MED-RT", "N0000000010")
    child = ids.mint_class_uuid("MED-RT", "N0000000011")
    for cu, nui, name in ((parent, "N0000000010", "Parent [APC]"),
                          (child, "N0000000011", "Child [EPC]")):
        conn.execute(
            "INSERT INTO drugref.substance_class "
            "(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (cu, nui, nui, name, "APC" if cu == parent else "EPC", run_id))
    conn.execute(
        "INSERT INTO drugref.class_parent (child_class_uuid, parent_class_uuid, ingest_run) "
        "VALUES (%s, %s, %s)", (child, parent, run_id))

    # Apply ONLY db/003 over that populated, pre-rename table.
    conn.execute((db.migration_dir() / "003_class_registry_source_neutral.sql").read_text())

    # The rows survived the rename with their UUIDs intact (a rebuilt table would
    # have dropped them and broken the class_parent foreign key), the columns are
    # renamed, and source was backfilled.
    assert conn.execute(
        "SELECT source, source_code, published_code, class_name FROM drugref.substance_class "
        "WHERE class_uuid = %s", (child,)).fetchone() == (
            "MED-RT", "N0000000011", "N0000000011", "Child [EPC]")
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_parent "
        "WHERE child_class_uuid = %s AND parent_class_uuid = %s", (child, parent)).fetchone()[0] == 1


def test_constraint_definition_quotes_the_check_rather_than_restating_it(conn):
    """The one way an error message can tell an operator what a CHECK accepts without
    becoming a second copy of it (db/006's lesson). What it returns IS the constraint,
    so it cannot go stale the way a hand-written "one of deny, allow, withdrawn" would.

    Asserted on the VALUES rather than on the whole rendered string: postgres decides
    whether to print `= ANY (ARRAY[...])` or an IN list, and pinning that spelling here
    would be a test of postgres's formatter.
    """
    definition = db.constraint_definition(
        conn, "class_expansion_policy", "class_expansion_policy_decision")
    assert definition is not None
    assert all(v in definition for v in ("deny", "allow", "withdrawn"))


def test_constraint_definition_is_none_for_a_constraint_that_does_not_exist(conn):
    """None rather than a raise, and the caller depends on it: cli_policy._write is
    already reporting a failure when it calls this, and a message that could not be
    improved is not a reason to lose the message it was improving."""
    assert db.constraint_definition(
        conn, "class_expansion_policy", "no_such_constraint") is None


def test_constraint_definition_does_not_match_another_tables_constraint(conn):
    """conname is unique per TABLE, not per schema, so the lookup is scoped to both.
    An unqualified `WHERE conname = %s` would happily return some other table's rule
    and quote it at an operator as the one they just tripped."""
    assert db.constraint_definition(
        conn, "ingest_run", "class_expansion_policy_decision") is None
