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
        # release no longer defines.
        "class_expansion_policy", "gap_unreviewed_expansion_root",
        "expansion_policy_unresolved",
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
        # slice 5b.2, db/019 section 5: the read path over the same condition DAG,
        # walked UPWARD instead of down (db/015's expansion would manufacture claims
        # an indication rule never made). One VIEW -- condition_indication_reach --
        # named explicitly for the same reason as db/015's and db/018's views above;
        # indications_for_condition is a FUNCTION and does not appear in
        # information_schema.tables.
        "condition_indication_reach",
    }


def test_every_migration_is_recorded_in_the_ledger(conn):
    """Without a ledger, 'has this migration run?' has to be reverse-engineered from
    the catalog by hand-written guards in each file -- which is why db/003 carries
    three different DO-block idioms. The ledger answers it directly."""
    recorded = {row[0] for row in conn.execute(
        "SELECT filename FROM drugref.schema_migration").fetchall()}
    on_disk = {p.name for p in db._DB_DIR.glob("*.sql")}
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
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('MED-RT', 'replay-test', 'deadbeef') RETURNING ingest_run_id").fetchone()[0]
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
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('MED-RT', 'rename-test', 'deadbeef') RETURNING ingest_run_id").fetchone()[0]
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
    conn.execute((db._DB_DIR / "003_class_registry_source_neutral.sql").read_text())

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
