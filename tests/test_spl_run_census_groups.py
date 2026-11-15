# tests/test_spl_run_census_groups.py
"""db/072, db/077, db/078 (#215, #229, #214, #248, #249): the spl_run_census group CHECKs.

A group of census counts is measured whole or not at all (NULL = not measured),
and every count has its own floor. db/078's whole-class-group CHECK is NOT VALID
so the honest pre-db/078 rows on disk stand; the last test here proves those
rows exist. Moved verbatim out of `tests/test_spl_run_census.py` (#251), whose
column-group constants and `_run` helper these tests share.
"""
import psycopg
import pytest

from tests.test_spl_run_census import (
    CLASS_DROPS, CLASS_EXCLUSIONS, CLASS_GROUP, NON_DROP, OFFICIAL, _run)


def _insert_072(conn, **values):
    run_id = _run(conn)
    cols = ", ".join(values)
    conn.execute(
        "INSERT INTO drugref.spl_run_census (ingest_run_id, records_read, "
        " labels, records_field_absent, records_field_blank, "
        f" labels_keyed_on_document_id, {cols}) VALUES (%s, 1, 1, 0, 0, 0, "
        f"{', '.join(['%s'] * len(values))})", (run_id, *values.values()))


@pytest.mark.parametrize("values, constraint", [
    pytest.param(dict.fromkeys(CLASS_DROPS[:2], 0),
                 "spl_run_census_class_drops_together", id="class-drop-half-null"),
    pytest.param(dict.fromkeys(OFFICIAL[:7], 0),
                 "spl_run_census_official_names_together",
                 id="official-names-half-null"),
    # #249: db/072's three measured beside db/077's or db/078's column unmeasured.
    *(pytest.param(dict.fromkeys(CLASS_GROUP, 0) | {column: None},
                   "spl_run_census_class_group_whole", id=f"{column}-null")
      for column in NON_DROP + CLASS_EXCLUSIONS),
    pytest.param(dict.fromkeys(NON_DROP + CLASS_EXCLUSIONS, 0),
                 "spl_run_census_class_group_whole", id="db072-drops-null"),
    # Every count's own floor: the group CHECKs do not see a negative.
    *(pytest.param(dict.fromkeys(group, 0) | {column: -1},
                   f"spl_run_census_{column}_check", id=f"negative-{column}")
      for group in (CLASS_GROUP, OFFICIAL) for column in group),
])
def test_db072_refuses_a_half_measured_or_negative_census(conn, values, constraint):
    """A group is measured whole or not at all (NULL = not measured), and every
    count has its own floor (#187's lesson)."""
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        _insert_072(conn, **values)
    assert refused.value.diag.constraint_name == constraint


def test_db072_accepts_a_measured_census_and_one_without_official_names(conn):
    _insert_072(conn, **dict.fromkeys(CLASS_GROUP + OFFICIAL, 0))
    _insert_072(conn, **dict.fromkeys(CLASS_GROUP, 0))


@pytest.mark.parametrize("measured", [
    pytest.param(CLASS_DROPS, id="db072-to-db076-row"),
    pytest.param(CLASS_DROPS + NON_DROP, id="db077-row")])
def test_db078_leaves_the_db072_to_db077_rows_on_disk_standing(conn, measured):
    """#249's carve-out: a row written after db/072 but before db/078 has db/072's
    drops measured and db/078's column NULL (db/077's too, if before db/077), which
    db/078's CHECK now refuses. It is created NOT VALID, so rows already on disk
    stand (each is an honest "not measured") while every new INSERT is checked.
    Validating the constraint here is what a mistaken `VALIDATE CONSTRAINT` would
    do: it must fail on that row, proving the rows exist that NOT VALID was chosen
    for. The constraint is re-added from its own live definition, so it cannot
    drift from db/078."""
    (validated, definition) = conn.execute(
        "SELECT convalidated, pg_get_constraintdef(oid) FROM pg_constraint "
        " WHERE conname = 'spl_run_census_class_group_whole'").fetchone()
    assert validated is False
    conn.execute("ALTER TABLE drugref.spl_run_census "
                 " DROP CONSTRAINT spl_run_census_class_group_whole")
    _insert_072(conn, **dict.fromkeys(measured, 0))  # a row from before db/078
    conn.execute("ALTER TABLE drugref.spl_run_census ADD CONSTRAINT "
                 f" spl_run_census_class_group_whole {definition}")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("ALTER TABLE drugref.spl_run_census "
                     " VALIDATE CONSTRAINT spl_run_census_class_group_whole")
