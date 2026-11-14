# tests/test_spl_run_census.py
"""db/057 (issue #186): the per-run SPL census that makes #163's signal readable.

#163's three counts -- records with no interactions field, records with it blank,
labels keyed on openFDA's per-document `id` -- deliberately never refuse a run:
*the ratio moving between releases is the signal*. Before db/057 they reached
only an INFO log line, so there was never an earlier figure to compare with.
`spl_run_census` stores them once per run, and because it is HISTORY (keyed by
the run, not cleared by the per-source rebuild) the previous release's row is
still there when the next one lands.

The end-to-end "the counts arrive unswapped" check for db/057's five lives beside
the summary's own in `tests/test_spl_run.py`; this module pins the history
property, the table's own floor, and (issue #199) the reader and `drugref
status`'s block.

db/072 (#215, #229) widened the row with the class-pass drops and the official-name
counts; their CHECKs, the writer's column-by-column mapping and their end-to-end
store check live here, as do #228's tests of the run's widened `source_checksum`.
"""
import dataclasses
import types

import psycopg
import pytest

from drugref import spl_census
from drugref.ingest import spl_checks, spl_run
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

def _counts(**db057):
    """A stand-in summary for the writer: db/057's five counts, db/072's unmeasured."""
    return types.SimpleNamespace(**(dict.fromkeys(spl_census.CENSUS_COLUMNS) | db057))


def _census(run_id, release, records, labels, absent, blank, id_keyed, **db072):
    """A RunCensus with positional shorthand -- tests only. db/072's counts are
    unmeasured (None) unless given by name."""
    return spl_census.RunCensus(
        ingest_run_id=run_id, upstream_release=release, records_read=records,
        labels=labels, records_field_absent=absent, records_field_blank=blank,
        labels_keyed_on_document_id=id_keyed,
        **(dict.fromkeys(CLASS_DROPS + OFFICIAL) | db072))


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
    # Three lines per run: #163's counts, then db/072's two groups (#235).
    newest, previous = lines[1], lines[4]
    assert "run 12" in newest and "2026-09" in newest
    assert "id-keyed 50 (5.00%)" in newest
    assert "no field 80 (8.00%)" in newest and "blank 20 (2.00%)" in newest
    assert "records 1,000  labels 900" in newest
    assert "id-keyed 5 (0.50%)" in previous
    assert len(lines) == 7


def test_a_census_of_ZERO_records_prints_no_ratio_rather_than_crashing():
    """A run that read nothing has no denominator; dividing by it would kill
    `drugref status` on its last block."""
    from drugref import cli_status
    (_, line, *_) = cli_status.spl_census_lines([_census(1, "r", 0, 0, 0, 0, 0)])
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


def test_a_database_without_db072_gets_the_guards_sentence_not_a_traceback(conn):
    """Every deployment between pulling this code and `drugref migrate` lacks
    the table or (#235) db/072's columns, so the guard names db/072: naming db/057
    would tell a db/057-071 database "NOT a missing migration". Renamed inside the
    rolled-back fixture transaction -- the rename-inside-the-rollback device
    tests/test_ingest_run_duration.py uses for db/054's site, with the same limit
    stated there."""
    from drugref import cli_status
    conn.execute("ALTER TABLE drugref.spl_run_census RENAME TO before_db057")
    # The probe's rollback undoes the rename, so this lands in the "NOT a missing
    # migration" branch: what it pins is THIS site's migration and consequence.
    with pytest.raises(RuntimeError, match=r"(?i)db/072[\s\S]*no SPL release can "
                       r"be compared[\s\S]*spl_run_census"):
        cli_status.print_spl_census_block(conn)


def test_the_reader_maps_every_column_to_its_own_field(conn):
    """The corpus's census is 8/8/0/0/0, so an end-to-end check cannot see two
    count columns swapped; sixteen DISTINCT values can (#235 added db/072's)."""
    run_id = _run(conn)
    spl_census.write_run_census(conn, ingest_run_id=run_id,
                                summary=types.SimpleNamespace(**_DISTINCT))
    assert spl_census.recent_run_census(conn, limit=1) == [spl_census.RunCensus(
        ingest_run_id=run_id, upstream_release="test", **_DISTINCT)]


def test_a_pre_db072_row_reads_back_as_NOT_MEASURED(conn):
    """Every row written before db/072 has NULL there; the reader must hand that
    back as None, never 0 ("measured, none dropped")."""
    run_id = _run(conn)
    spl_census.write_run_census(conn, ingest_run_id=run_id, summary=_counts(
        records_read=1, labels=1, records_field_absent=0, records_field_blank=0,
        labels_keyed_on_document_id=0))
    (got,) = spl_census.recent_run_census(conn, limit=1)
    assert got == _census(run_id, "test", 1, 1, 0, 0, 0)


def test_the_census_block_prints_db072s_counts_on_their_own_lines():
    """#235: the class-pass drops and the official-name counts, by name, so the
    operator sees them drift between two runs as #163's already do."""
    from drugref import cli_status
    (_, _, drops, names) = cli_status.spl_census_lines([
        spl_census.RunCensus(ingest_run_id=1, upstream_release="r", **_DISTINCT)])
    assert drops == ("    class pass dropped: at a boundary 101  on a non-drug head 102"
                     "  shadowed by moieties 103  shadowed by official names 111")
    assert names == ("    official names: adopted 104  already named 105  shadowed 106"
                     "  ambiguous 107  excluded 108  unfoldable 109"
                     "  exclusions unused 110")


def test_an_unmeasured_group_prints_NOT_MEASURED_never_zero():
    """A run without a UNII_Names file measured the class pass but not the
    official names (nor R19/P1's drop); a pre-db/072 row measured neither. The
    measured drops are ZERO: a measured 0 prints `0`, even beside a None."""
    from drugref import cli_status
    (_, _, drops, names, _, old_drops, old_names) = cli_status.spl_census_lines([
        _census(2, "r", 1, 1, 0, 0, 0, **dict.fromkeys(CLASS_DROPS, 0)),
        _census(1, "q", 1, 1, 0, 0, 0)])
    assert drops == ("    class pass dropped: at a boundary 0  on a non-drug head 0"
                     "  shadowed by moieties 0  shadowed by official names "
                     "not measured")
    assert names == "    official names: not measured"
    assert old_drops == "    class pass dropped: not measured"
    assert old_names == "    official names: not measured"


def test_the_census_block_prints_every_db072_column():
    """A column added to CENSUS_COLUMNS (and so stored and read) but not to
    `_DB072_GROUPS` would never reach the operator; nothing else would fail."""
    from drugref import cli_status
    printed = [column for _, group in cli_status._DB072_GROUPS for column, _ in group]
    assert sorted(printed) == sorted(CLASS_DROPS + OFFICIAL)
    assert set(printed) | set(spl_census.CENSUS_COLUMNS[:5]) == set(
        spl_census.CENSUS_COLUMNS)


def test_newest_means_last_FINISHED_as_the_loaded_release_block_reads_it(conn):
    """Block 1 (db/054's `loaded_release`) orders by `finished_at`; ordering this
    block by run id would disagree with it whenever two ingests commit out of the
    order they opened in."""
    from drugref import provenance, spl_census
    opened_first, opened_second = _run(conn), _run(conn)
    for run_id in (opened_first, opened_second):
        spl_census.write_run_census(conn, ingest_run_id=run_id, summary=_counts(
            records_read=1, labels=1, records_field_absent=0, records_field_blank=0,
            labels_keyed_on_document_id=0))
    provenance.finish_run(conn, opened_second)
    provenance.finish_run(conn, opened_first)
    got = [c.ingest_run_id for c in spl_census.recent_run_census(conn)]
    assert got == [opened_first, opened_second]


# --------------------------------------------------------------------------
# db/072 (#215, #229): the class-pass drops and the official-name adoption
# counts, stored beside #163's -- printed-only figures cannot be compared
# --------------------------------------------------------------------------

#: The db/072 columns, named as on SplSummary (the writer relies on that).
CLASS_DROPS = ("class_spans_dropped_at_a_boundary",
               "class_spans_dropped_on_a_non_drug_head",
               "class_keys_shadowed_by_moieties")
OFFICIAL = ("official_names_adopted", "official_names_already_named",
            "official_names_shadowed", "official_names_ambiguous",
            "official_names_excluded", "official_names_unfoldable",
            "official_name_exclusions_unused",
            "class_keys_shadowed_by_official_names")


def _stored(conn, columns):
    return conn.execute(
        f"SELECT {', '.join(columns)} FROM drugref.spl_run_census c "
        "  JOIN drugref.ingest_run r USING (ingest_run_id) "
        " WHERE r.source = 'SPL'").fetchall()


@pytest.mark.usefixtures("_clean")
def test_the_class_drops_and_adoption_counts_are_STORED_unswapped(
        conn, corpus, tmp_path):
    """#215/#229 end to end: R19/P1's fixture (one class key that is also a UNII
    official name, so it is dropped from the class pass AND adopted by the drug
    pass), and the census row carries the summary's own figures."""
    _seed_registry(conn)
    base._seed_class(conn)
    names = tmp_path / "UNII_Names.txt"
    names.write_text("Name\tTYPE\tUNII\tDisplay Name\n"
                     f"RIFAMPIN INDUCERS\tof\t{sorted(base._fixture_uniis())[0]}\tX\n")
    summary = _ingest(conn, base._text_corpus(corpus), unii_names=names)
    columns = CLASS_DROPS + OFFICIAL
    assert _stored(conn, columns) == [
        tuple(getattr(summary, c) for c in columns)]
    # Two of these are not zero, so the equality reaches past 0 == 0 -- but most are
    # 0 here: the writer test below pins every column with a distinct value.
    assert summary.official_names_adopted == 1
    assert summary.class_keys_shadowed_by_official_names == 1


@pytest.mark.usefixtures("_clean")
def test_a_run_WITHOUT_a_names_file_stores_NULL_not_zero_for_official_names(
        conn, corpus):
    """No UNII_Names file means #213 and R19/P1 never ran. A 0 would read as
    "measured, none adopted" in the next release's comparison; NULL says "not
    measured". The class-pass drops were measured, so they are not NULL."""
    _seed_registry(conn)
    _ingest(conn, corpus)
    ((*drops, ), ) = _stored(conn, CLASS_DROPS)
    assert None not in drops
    assert _stored(conn, OFFICIAL) == [(None,) * len(OFFICIAL)]


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
    # Every count's own floor: the group CHECKs do not see a negative.
    *(pytest.param(dict.fromkeys(group, 0) | {column: -1},
                   f"spl_run_census_{column}_check", id=f"negative-{column}")
      for group in (CLASS_DROPS, OFFICIAL) for column in group),
])
def test_db072_refuses_a_half_measured_or_negative_census(conn, values, constraint):
    """A group is measured whole or not at all (NULL = not measured), and every
    count has its own floor (#187's lesson)."""
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        _insert_072(conn, **values)
    assert refused.value.diag.constraint_name == constraint


def test_db072_accepts_a_measured_census_and_one_without_official_names(conn):
    _insert_072(conn, **dict.fromkeys(CLASS_DROPS + OFFICIAL, 0))
    _insert_072(conn, **dict.fromkeys(CLASS_DROPS, 0))


def test_CENSUS_COLUMNS_is_every_census_column_and_every_one_a_summary_field(conn):
    """A column a later migration adds but the writer's list misses would stay
    NULL, which reads as "not measured": silent. And a name SplSummary lacks would
    fail only at the census write, after the whole scan."""
    table = {name for (name,) in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        " WHERE table_schema = 'drugref' AND table_name = 'spl_run_census'")}
    assert set(spl_census.CENSUS_COLUMNS) == table - {"ingest_run_id"}
    assert set(spl_census.CENSUS_COLUMNS) <= {
        f.name for f in dataclasses.fields(spl_checks.SplSummary)}


#: A consistent census (db/057's identities hold) with every value distinct.
_DISTINCT = dict(records_read=20, labels=12, records_field_absent=5,
                 records_field_blank=3, labels_keyed_on_document_id=7) | {
    column: 101 + i for i, column in enumerate(CLASS_DROPS + OFFICIAL)}


def test_the_writer_stores_each_count_in_its_OWN_column(conn):
    """Sixteen same-typed counts: distinct values, so a column filled from its
    neighbour's field fails here even where the end-to-end fixture holds zeros."""
    spl_census.write_run_census(conn, ingest_run_id=_run(conn),
                                summary=types.SimpleNamespace(**_DISTINCT))
    assert _stored(conn, spl_census.CENSUS_COLUMNS) == [
        tuple(_DISTINCT[c] for c in spl_census.CENSUS_COLUMNS)]


def test_the_writer_does_not_paper_over_HALF_an_official_name_group(conn):
    """A measured R19/P1 count beside an unmeasured adoption group is a caller's
    bug; the writer passes it through so db/072's CHECK refuses it, rather than
    nulling the measured value to make the row look consistent."""
    half = _DISTINCT | dict.fromkeys(OFFICIAL[:7])
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        spl_census.write_run_census(conn, ingest_run_id=_run(conn),
                                    summary=types.SimpleNamespace(**half))
    assert (refused.value.diag.constraint_name
            == "spl_run_census_official_names_together")


# --------------------------------------------------------------------------
# #228: the run's checksum covers every input that can move the pairs
# --------------------------------------------------------------------------

def test_the_checksum_covers_UNII_Names_and_both_shipped_data_files(tmp_path):
    """Since #213 the published pairs also depend on the UNII_Names release and
    on two shipped lists; a checksum over the corpora alone lets two runs with
    one checksum publish different pairs."""
    names = tmp_path / "UNII_Names.txt"
    got = [p.name for p in spl_run.checksummed_inputs(
        [tmp_path / "drug-label-1.json.zip"], [tmp_path / "dm_part1.zip"], names)]
    assert got == ["UNII_Names.txt", "dm_part1.zip", "drug-label-1.json.zip",
                   "spl_suppression_terms.txt", "spl_official_name_exclusions.txt"]
    without = [p.name for p in spl_run.checksummed_inputs([], [], None)]
    assert "UNII_Names.txt" not in without


@pytest.mark.usefixtures("_clean")
def test_two_UNII_Names_releases_give_two_checksums(conn, corpus, tmp_path):
    _seed_registry(conn)
    digests = []
    for text in ("A", "B"):
        names = tmp_path / f"UNII_Names_{text}.txt"
        names.write_text("Name\tTYPE\tUNII\tDisplay Name\n"
                         f"{text}\tof\t{sorted(base._fixture_uniis())[0]}\tX\n")
        _ingest(conn, corpus, unii_names=names)
        digests += conn.execute(
            "SELECT source_checksum FROM drugref.ingest_run WHERE source = 'SPL' "
            "ORDER BY ingest_run_id DESC LIMIT 1").fetchone()
    assert digests[0] != digests[1]
