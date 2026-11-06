# tests/test_spl_run_census.py
"""db/057 (issue #186): the per-run SPL census that makes #163's signal readable.

#163's three counts -- records with no interactions field, records with it blank,
labels keyed on openFDA's per-document `id` -- deliberately never refuse a run:
*the ratio moving between releases is the signal*. Before db/057 they reached
only an INFO log line, so there was never an earlier figure to compare with.
`spl_run_census` stores them once per run, and because it is HISTORY (keyed by
the run, not cleared by the per-source rebuild) the previous release's row is
still there when the next one lands.

The end-to-end "the counts arrive unswapped" check lives beside the summary's
own in `tests/test_spl_run.py`; this module pins the history property and the
table's own floor.
"""
import psycopg
import pytest

from drugref.ingest import spl_run
from tests import test_spl_run as base

# The orchestrator fixtures, reused rather than copied: binding a fixture to a
# module-level name is what registers it here. Assigned, not imported by name,
# so the test parameters that request them are not flagged as redefinitions.
corpus = base.corpus
_clean = base._clean
_ingest = base._ingest
_seed_registry = base._seed_registry


@pytest.mark.usefixtures("_clean")
def test_a_re_ingest_KEEPS_the_previous_runs_census(conn, corpus):
    """The point of the table: two runs, two rows, one per run, and the two
    agree because the corpus did not change. A census cleared with the
    projection would leave one row and nothing to compare against."""
    _seed_registry(conn)
    first = _ingest(conn, corpus)
    _ingest(conn, corpus)
    rows = conn.execute(
        "SELECT c.records_read, c.labels, c.records_field_absent, "
        "       c.records_field_blank, c.labels_keyed_on_document_id "
        "  FROM drugref.spl_run_census c "
        "  JOIN drugref.ingest_run r USING (ingest_run_id) "
        " WHERE r.source = 'SPL' AND r.finished_at IS NOT NULL "
        " ORDER BY c.ingest_run_id").fetchall()
    expected = (first.records_read, first.labels, first.records_field_absent,
                first.records_field_blank, first.labels_keyed_on_document_id)
    assert rows == [expected, expected]


@pytest.mark.usefixtures("_clean")
def test_a_run_refused_by_its_floors_writes_NO_census(conn, corpus):
    """`check_floors` runs before the census is written, so a refused run never
    reaches the INSERT. (That alone says nothing about the transaction -- the
    next test pins that.)"""
    _seed_registry(conn)
    with pytest.raises(ValueError):
        _ingest(conn, corpus, pair_floor=spl_run.MEASURED_PAIR_FLOOR)
    assert conn.execute(
        "SELECT count(*) FROM drugref.spl_run_census").fetchone() == (0,)


@pytest.mark.usefixtures("_clean")
def test_a_run_failing_AFTER_the_census_write_rolls_the_census_back(
        conn, corpus, monkeypatch):
    """The census is written inside the run's transaction, so a run that fails
    after the INSERT (here `finish_run`; in life the deferred quote-budget
    trigger at commit) cannot leave figures behind that a later release would be
    compared against -- they would describe a projection that never existed.
    A census written on its own committed transaction would fail this."""
    def refuse(*_args, **_kwargs):
        raise RuntimeError("finish_run refused (test)")

    _seed_registry(conn)
    monkeypatch.setattr(spl_run.provenance, "finish_run", refuse)
    with pytest.raises(RuntimeError, match="finish_run refused"):
        _ingest(conn, corpus)
    assert conn.execute(
        "SELECT count(*) FROM drugref.spl_run_census").fetchone() == (0,)


def _run(conn) -> int:
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('SPL', 'test', 'test', 'spl_run') RETURNING ingest_run_id"
    ).fetchone()[0]


@pytest.mark.parametrize("row, constraint", [
    # labels + absent + blank must equal records_read: every record is one of the three
    pytest.param((10, 8, 1, 0, 0), "spl_run_census_records_accounted",
                 id="buckets-do-not-sum"),
    # a -1 and a +1 satisfy the sum, which is why each count has its own floor
    pytest.param((10, 11, -1, 0, 0), "spl_run_census_records_field_absent_check",
                 id="negative-absent-hidden-by-the-sum"),
    pytest.param((10, 11, 0, -1, 0), "spl_run_census_records_field_blank_check",
                 id="negative-blank-hidden-by-the-sum"),
    # an id-keyed label is still a label
    pytest.param((10, 8, 1, 1, 9), "spl_run_census_id_keyed_are_labels",
                 id="more-id-keyed-than-labels"),
    pytest.param((10, 8, 1, 1, -1),
                 "spl_run_census_labels_keyed_on_document_id_check",
                 id="negative-id-keyed"),
    # NOT HERE: `labels >= 0` and `records_read >= 0`. Neither can be broken
    # alone -- a negative `labels` also breaks id_keyed_are_labels (or, with a
    # negative id-keyed count, that count's own floor), and `records_read` is
    # the sum of three floored counts, so records_accounted fires first. Both
    # stay as belt-and-braces.
])
def test_the_table_refuses_an_impossible_census(conn, row, constraint):
    """The database's own copy of `SplSummary`'s identities. The Python checks
    guard the one writer; these guard everything else that can INSERT. Each
    case names the ONE constraint it breaks, so a dropped CHECK cannot hide
    behind another that happens to fire on the same row."""
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        conn.execute(
            "INSERT INTO drugref.spl_run_census (ingest_run_id, records_read, "
            " labels, records_field_absent, records_field_blank, "
            " labels_keyed_on_document_id) VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, *row))
    assert refused.value.diag.constraint_name == constraint


def test_the_table_accepts_a_consistent_census(conn):
    """The floor above, shown NOT to refuse everything."""
    run_id = _run(conn)
    conn.execute(
        "INSERT INTO drugref.spl_run_census (ingest_run_id, records_read, "
        " labels, records_field_absent, records_field_blank, "
        " labels_keyed_on_document_id) VALUES (%s, 10, 8, 1, 1, 8)", (run_id,))


# --------------------------------------------------------------------------
# The reader (issue #199): `drugref status` prints the latest two rows
# --------------------------------------------------------------------------

def _census(run_id, release, records, labels, absent, blank, id_keyed):
    from drugref import spl_evidence
    return spl_evidence.RunCensus(
        ingest_run_id=run_id, upstream_release=release, records_read=records,
        labels=labels, records_field_absent=absent, records_field_blank=blank,
        labels_keyed_on_document_id=id_keyed)


def test_the_census_block_says_none_when_no_SPL_run_has_landed():
    """The other `none`-voiced blocks' rule: a bare header reads as cut-off
    output, not as the answer "nothing has been censused yet"."""
    from drugref import cli_status
    assert cli_status.spl_census_lines([]) == ["spl run census: none"]


def test_the_census_block_prints_counts_AND_ratios_latest_first():
    """#199's smallest option: absolute values and per-record ratios, one line
    per run, newest first -- the reader diffs the two lines. No threshold."""
    from drugref import cli_status
    lines = cli_status.spl_census_lines([
        _census(12, "2026-09", 1000, 900, 80, 20, 50),
        _census(9, "2026-08", 1000, 900, 80, 20, 5),
    ])
    assert lines[0].startswith("spl run census")
    newest, previous = lines[1], lines[2]
    assert "run 12" in newest and "2026-09" in newest
    assert "id-keyed 50 (5.00%)" in newest
    assert "no field 80 (8.00%)" in newest and "blank 20 (2.00%)" in newest
    assert "id-keyed 5 (0.50%)" in previous
    assert len(lines) == 3


def test_a_census_of_ZERO_records_prints_no_ratio_rather_than_crashing():
    """A run that read nothing has no denominator; dividing by it would kill
    `drugref status` on its last block."""
    from drugref import cli_status
    (_, line) = cli_status.spl_census_lines([_census(1, "r", 0, 0, 0, 0, 0)])
    assert "id-keyed 0 (n/a)" in line


@pytest.mark.usefixtures("_clean")
def test_the_status_block_reads_the_two_latest_runs_from_the_database(
        conn, corpus, capsys):
    """End to end: three ingests, and the block names exactly the last two,
    newest first -- the comparison #199 asks for, read from db/057's rows."""
    from drugref import cli_status
    _seed_registry(conn)
    runs = [_ingest(conn, corpus, release=f"rel-{i}") for i in range(3)]
    cli_status.print_spl_census_block(conn)
    out = capsys.readouterr().out
    assert "rel-2" in out and "rel-1" in out and "rel-0" not in out
    assert out.index("rel-2") < out.index("rel-1")
    assert f"id-keyed {runs[-1].labels_keyed_on_document_id} (" in out


def test_a_database_without_db057_gets_the_guards_sentence_not_a_traceback(conn):
    """Every deployment between pulling this code and `drugref migrate` lacks
    the table. Renamed inside the rolled-back fixture transaction, the same
    device tests/test_ingest_run_duration.py uses for db/054's site."""
    from drugref import cli_status
    conn.execute("ALTER TABLE drugref.spl_run_census RENAME TO before_db057")
    with pytest.raises(RuntimeError, match="spl_run_census"):
        cli_status.print_spl_census_block(conn)
