"""Every table nothing rebuilds is in the curated export, or says why not (#270).

A release is reconstructable only if `drugref audit replay` sees every row it
must restore. Source projections come back by replaying the archived runs; rows
a curator (or the reviewer-service) wrote come back only from the curated export
`release_archive._EXPORT_TABLES` names. A table no rebuild touches must be EITHER
exported OR listed in `release_archive.NOT_EXPORTED` with the reason. Without
this guard the first writer of a new such table makes every later release
silently not reconstructable for it: the replay reports no difference because
it never sees those rows.

"No rebuild touches it" is detected two ways, because neither alone is enough:
- a write-once trigger named `*_append_only`, `*_insert_only` or `*_immortal`
  (curated tables such as `question_state` still carry a provenance foreign key
  to `ingest_run`, so the second test alone would miss them);
- no foreign key to `ingest_run` at all: every source projection has one, so a
  table without it (e.g. the trigger-less `question_source_check`) is curated,
  a seeded vocabulary or bookkeeping, and must be classified.
`forbid_*` triggers are deliberately not matched: they guard ingest-written
tables (`identity_claim`, `substance_moiety`) that the replay does rebuild.

DB-gated: both are read from the migrated schema, not restated here.
"""
from drugref import release_archive

#: Trigger names that mark a table as written once and never rebuilt.
_IMMUTABLE_TRIGGERS = (
    "SELECT DISTINCT c.relname FROM pg_trigger g "
    "JOIN pg_class c ON c.oid = g.tgrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'drugref' AND NOT g.tgisinternal "
    "AND (g.tgname LIKE '%\\_append\\_only' OR g.tgname LIKE '%\\_insert\\_only' "
    "OR g.tgname LIKE '%\\_immortal')")

#: Tables with no foreign key to ingest_run: nothing a source run wrote.
_NO_RUN_FOREIGN_KEY = (
    "SELECT t.tablename FROM pg_tables t WHERE t.schemaname = 'drugref' "
    "AND NOT EXISTS (SELECT 1 FROM pg_constraint k "
    "JOIN pg_class s ON s.oid = k.conrelid "
    "JOIN pg_namespace n ON n.oid = s.relnamespace "
    "WHERE k.contype = 'f' AND n.nspname = 'drugref' AND s.relname = t.tablename "
    "AND k.confrelid = 'drugref.ingest_run'::regclass)")

_HINT = ("add each to _EXPORT_TABLES (and replay_loader._TIMED), or to "
         "NOT_EXPORTED with the reason a replay need not restore it")


def _unclassified(tables: set[str]) -> set[str]:
    return tables - set(release_archive._EXPORT_TABLES) - set(release_archive.NOT_EXPORTED)


def test_every_append_only_table_is_exported_or_says_why_not(conn):
    immutable = {t for (t,) in conn.execute(_IMMUTABLE_TRIGGERS)}
    assert immutable, "no append-only trigger found: the query is wrong, not the schema"
    assert _unclassified(immutable) == set(), _HINT


def test_every_table_no_run_wrote_is_exported_or_says_why_not(conn):
    orphans = {t for (t,) in conn.execute(_NO_RUN_FOREIGN_KEY)}
    assert "ingest_run" in orphans, "the query is wrong, not the schema"
    assert _unclassified(orphans) == set(), _HINT


def test_the_not_exported_ledger_names_real_unexported_tables(conn):
    tables = {t for (t,) in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'drugref'")}
    ledger = set(release_archive.NOT_EXPORTED)
    assert ledger - tables == set(), "a stale entry: the table no longer exists"
    assert ledger & set(release_archive._EXPORT_TABLES) == set()
    assert all(why.strip() for why in release_archive.NOT_EXPORTED.values())
