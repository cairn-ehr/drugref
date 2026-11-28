"""Tiny row builders for the lab-effect tests (#294, db/091 storage).

Each helper inserts the MINIMUM valid row for the SPL_LAB tables, so the view tests
can build a label reading in four lines. Registry rows (moieties, classes) come from
tests/class_profile_world.py. Nothing here commits; the `conn` fixture rolls back.
"""


def lab_run(conn) -> int:
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('SPL_LAB', 'test', 'deadbeef', 'spl_lab_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def label(conn, run, set_id, moiety, *, version="1") -> None:
    """A label and ONE resolved subject (moiety may be None: an unresolved subject)."""
    conn.execute("INSERT INTO drugref.spl_lab_label (ingest_run, source, set_id, version) "
                 "VALUES (%s, 'SPL_LAB', %s, %s)", (run, set_id, version))
    conn.execute(
        "INSERT INTO drugref.spl_lab_label_subject "
        "(ingest_run, source, set_id, version, moiety_uuid, resolution) "
        "VALUES (%s, 'SPL_LAB', %s, %s, %s, %s)",
        (run, set_id, version, moiety, "moiety" if moiety else "unresolved"))


def section(conn, run, set_id, section_name, text_key, *, version="1") -> None:
    """A wording (if new) and the label section carrying it."""
    conn.execute("INSERT INTO drugref.spl_lab_wording (ingest_run, source, text_key, "
                 "char_count) VALUES (%s, 'SPL_LAB', %s, 1000) ON CONFLICT DO NOTHING",
                 (run, text_key))
    conn.execute(
        "INSERT INTO drugref.spl_lab_label_section "
        "(ingest_run, source, set_id, version, section, text_key) "
        "VALUES (%s, 'SPL_LAB', %s, %s, %s, %s)",
        (run, set_id, version, section_name, text_key))


def reading(conn, run, text_key, term_id, *, role_read="causes", direction_read=None,
            char_start=0, co_medication_moiety_uuid=None) -> None:
    """One machine reading of `term_id` in the wording; direction defaults to the
    term's own, and role_rule is 'test.rule' unless nothing was read."""
    conn.execute(
        "INSERT INTO drugref.spl_lab_effect_occurrence "
        "(ingest_run, source, text_key, char_start, char_end, term_id, direction_read, "
        " role_read, role_rule, co_medication_moiety_uuid, scope, scope_start, "
        " scope_end) "
        "SELECT %s, 'SPL_LAB', %s, %s, %s + 10, t.term_id, "
        "       COALESCE(%s, t.direction), %s, %s, %s, 'sentence', %s, %s + 50 "
        "FROM drugref.lab_effect_term t WHERE t.term_id = %s",
        (run, text_key, char_start, char_start, direction_read, role_read,
         None if role_read == "not_read" else "test.rule", co_medication_moiety_uuid,
         char_start, char_start, term_id))
