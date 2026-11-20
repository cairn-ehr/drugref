"""Every table nothing rebuilds is in the curated export, or says why not (#270).

A release is reconstructable only if `drugref audit replay` sees every row it
must restore. Source projections come back by replaying the archived runs; rows
a curator (or the reviewer-service) wrote come back only from the curated export
`release_archive._EXPORT_TABLES` names. A table carrying an `*_append_only` or
`*_insert_only` trigger is one no rebuild touches, so it must be EITHER exported
OR listed in `release_archive.NOT_EXPORTED` with the reason. Without this guard
the first writer of a new such table makes every later release silently not
reconstructable for it: the replay reports no difference because it never sees
those rows.

DB-gated: the triggers are read from the migrated schema, not restated here.
"""
from drugref import release_archive

#: Trigger names that mark a table as written once and never rebuilt.
_IMMUTABLE_TRIGGERS = (
    "SELECT DISTINCT c.relname FROM pg_trigger g "
    "JOIN pg_class c ON c.oid = g.tgrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'drugref' AND NOT g.tgisinternal "
    "AND (g.tgname LIKE '%\\_append\\_only' OR g.tgname LIKE '%\\_insert\\_only')")


def test_every_append_only_table_is_exported_or_says_why_not(conn):
    immutable = {t for (t,) in conn.execute(_IMMUTABLE_TRIGGERS)}
    assert immutable, "no append-only trigger found: the query is wrong, not the schema"
    exported = set(release_archive._EXPORT_TABLES)
    unclassified = immutable - exported - set(release_archive.NOT_EXPORTED)
    assert unclassified == set(), (
        "add each to _EXPORT_TABLES (and release_replay._TIMED), or to "
        "NOT_EXPORTED with the reason a replay need not restore it")


def test_the_not_exported_ledger_names_real_unexported_tables(conn):
    tables = {t for (t,) in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'drugref'")}
    ledger = set(release_archive.NOT_EXPORTED)
    assert ledger - tables == set(), "a stale entry: the table no longer exists"
    assert ledger & set(release_archive._EXPORT_TABLES) == set()
    assert all(why.strip() for why in release_archive.NOT_EXPORTED.values())
