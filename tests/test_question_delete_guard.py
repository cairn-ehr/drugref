"""register_from_gaps must never DELETE a question a curated row cites (#294 review).

Every curated table's question_uuid is ON DELETE CASCADE into a table whose floor
refuses DELETE, so a table missing from register_from_gaps' NOT EXISTS guard turns
the first ruling that cites its question into an aborted ingest for every source.
The list is derived from the CATALOG here, so the next table with that FK cannot
be forgotten the way curated_lab_effect was.
"""
import inspect

from drugref import questions


def test_every_table_citing_a_question_is_in_the_delete_guard(conn):
    citing = {t for (t,) in conn.execute(
        "SELECT c.conrelid::regclass::text FROM pg_constraint c "
        "WHERE c.contype = 'f' AND c.confrelid = 'drugref.open_question'::regclass")}
    assert citing, "no FK into open_question found -- this test would pass vacuously"
    source = inspect.getsource(questions.register_from_gaps)
    missing = {t for t in citing if f"FROM {t} x" not in source}
    assert missing == set()
