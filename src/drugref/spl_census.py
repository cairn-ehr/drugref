# src/drugref/spl_census.py
"""db/057's `spl_run_census`: each SPL run's issue-163 counts, kept as HISTORY.

Split out of `spl_evidence` (#216, rule 4). The writer is called only by
`spl_run.ingest_spl`, inside its transaction; the reader feeds `drugref status`.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

import psycopg


#: db/057's table, named once: the writer, the reader and the reader's migration guard.
RUN_CENSUS_TABLE = "drugref.spl_run_census"


def write_run_census(
    conn: psycopg.Connection, *, ingest_run_id: int, records_read: int,
    labels: int, records_field_absent: int, records_field_blank: int,
    labels_keyed_on_document_id: int,
) -> None:
    """Store one run's issue-163 counts in db/057's `spl_run_census` (issue 186).

    HISTORY, not projection: the table is NOT in `SPL_TABLES`, so the next run's
    `clear_source_spl` leaves this row standing for that run to be compared with.
    Keyword-only for the module's usual reason -- five same-typed counts, and
    swapping absent with blank would store cleanly. The table's CHECKs refuse an
    impossible census; the caller's transaction decides whether it lands at all.
    """
    conn.execute(
        f"INSERT INTO {RUN_CENSUS_TABLE} (ingest_run_id, records_read, labels, "
        " records_field_absent, records_field_blank, labels_keyed_on_document_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (ingest_run_id, records_read, labels, records_field_absent,
         records_field_blank, labels_keyed_on_document_id))


@dataclass(frozen=True, kw_only=True)
class RunCensus:
    """One `spl_run_census` row plus the release it censused (issue 199's reader)."""
    ingest_run_id: int
    upstream_release: str
    records_read: int
    labels: int
    records_field_absent: int
    records_field_blank: int
    labels_keyed_on_document_id: int


def recent_run_census(conn: psycopg.Connection, *, limit: int = 2) -> list[RunCensus]:
    """The newest `limit` censuses, newest first: what #163's counts exist to compare.

    "NEWEST" IS LAST FINISHED, as db/054's `loaded_release` (status block 1) orders it,
    so the two blocks cannot disagree when two ingests commit out of the order they
    opened in. The only writer, `spl_run.ingest_spl`, commits the census together with
    `finish_run`, so its rows all have a `finished_at`; a hand-written INSERT need
    not, and `NULLS LAST` puts such a row after every finished one rather than first.
    """
    # Column order is RunCensus's field order, so each row zips onto its field names.
    # `strict=True` catches a wrong COUNT only; two swapped int columns would zip
    # cleanly, which is what test_the_reader_maps_every_column_to_its_own_field pins.
    rows = conn.execute(
        "SELECT c.ingest_run_id, r.upstream_release, c.records_read, c.labels, "
        "       c.records_field_absent, c.records_field_blank, "
        "       c.labels_keyed_on_document_id "
        f" FROM {RUN_CENSUS_TABLE} c JOIN drugref.ingest_run r USING (ingest_run_id) "
        " ORDER BY r.finished_at DESC NULLS LAST, c.ingest_run_id DESC LIMIT %s",
        (limit,)).fetchall()
    names = [f.name for f in fields(RunCensus)]
    return [RunCensus(**dict(zip(names, row, strict=True))) for row in rows]
