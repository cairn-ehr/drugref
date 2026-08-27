# tests/test_spl_schema.py
"""db/051's shape: the source trio, the five tables, and the quote budget.

WHY A SCHEMA TEST AT ALL, when the orchestrator exercises the same objects: a new
source spelling is not a one-line change. It has to land in the database CHECK,
in `ids._SOURCE_CANONICAL` and in `provenance.WRITERS` **in the same commit**, and
the failure mode when it does not is SILENT -- `canonical_source` folds the source
to a spelling the CHECK does not admit, and a per-source rebuild then deletes
nothing and reports success.

**AND BECAUSE db/050's FINDING WAS THAT EVERY GUARD IN A SLICE PASSED VACUOUSLY.**
The quote budget is the one this slice most needs shown: its failure mode is
silent, additive and visible only in aggregate. So the tests below construct a
wording whose windows exceed the budget and assert the database REFUSES it. A
budget nobody demonstrated rejecting anything is that finding waiting to recur.
"""
import uuid

import psycopg
import pytest

from drugref import ids, provenance, spl_evidence
from drugref.ingest import spl, spl_quote, spl_subject

SOURCE = spl.SOURCE
KEY_A = "a" * 64
KEY_B = "b" * 64


# --------------------------------------------------------------------------
# Fixtures: a run, a moiety, and a wording to hang rows off
# --------------------------------------------------------------------------

@pytest.fixture
def run_id(conn):
    """An open SPL run. Not committed -- the `conn` fixture rolls it back."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, '2026-08-22', 'deadbeef', %s) RETURNING ingest_run_id",
        (SOURCE, spl.WRITER)).fetchone()[0]


@pytest.fixture
def moiety(conn, run_id):
    """One registry moiety to point occurrences and subjects at."""
    return _moiety(conn, run_id, "warfarin")


def _moiety(conn, run_id, display_name):
    """Register one moiety. `first_seen_ingest` is NOT NULL, so it needs a run."""
    moiety_uuid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
        (moiety_uuid, display_name, run_id))
    return moiety_uuid


def _wording(conn, run_id, *, text_key=KEY_A, char_length=1_000, label_count=1):
    spl_evidence.write_wordings(
        conn,
        [spl_evidence.WordingRow(text_key=text_key, char_length=char_length,
                                 label_count=label_count)],
        ingest_run_id=run_id, source=SOURCE)
    return text_key


def _label(conn, run_id, *, set_id="SET-1", version="1", text_key=KEY_A):
    spl_evidence.write_labels(
        conn,
        [spl_evidence.LabelRow(set_id=set_id, version=version,
                               effective_time="20260101", product_type=None,
                               text_key=text_key)],
        ingest_run_id=run_id, source=SOURCE)
    return set_id, version


# --------------------------------------------------------------------------
# 1. The source-admission TRIO
# --------------------------------------------------------------------------

def test_spl_is_a_canonical_source_spelling():
    """Listed EXPLICITLY though the upper-case fall-through produces the same.

    `ids.py`'s own docstring warns by name against leaning on that fall-through:
    'openFDA-SPL' folds to 'OPENFDA-SPL', which a mixed-case CHECK would never
    match. 'SPL' survives by luck, and the entry records that the luck was
    CHECKED.
    """
    assert ids.canonical_source("SPL") == "SPL"
    assert ids.canonical_source("spl") == "SPL"
    assert ids.canonical_source("  Spl  ") == "SPL"


def test_spl_run_is_a_declared_writer():
    assert "spl_run" in provenance.WRITERS


def test_ingest_run_admits_the_spl_source_and_writer(conn):
    conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('SPL', '2026-08-22', 'deadbeef', 'spl_run')")


def test_ingest_run_still_refuses_a_misspelled_spl_source(conn):
    """'OPENFDA-SPL' is the spelling ids.py names as the trap.

    It would insert cleanly under an unconstrained column and then match nothing,
    ever.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.ingest_run "
            "(source, upstream_release, source_checksum, writer) "
            "VALUES ('OPENFDA-SPL', '2026-08-22', 'deadbeef', 'spl_run')")


def test_the_severity_and_class_source_checks_are_NOT_widened(conn):
    """SPL writes no class rule, no moiety contraindication AND NO SEVERITY.

    Grading prose would be the relation extraction this slice refuses, so a
    widened CHECK would admit a row no writer in this project produces -- which is
    how a vocabulary grows a value nothing means.
    """
    for constraint in ("class_contraindication_source",
                       "moiety_contraindication_source",
                       "ddi_source_severity_source"):
        (definition,) = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = %s", (constraint,)).fetchone()
        assert "SPL" not in definition, constraint


# --------------------------------------------------------------------------
# 2. The route vocabulary's SECOND HOME
# --------------------------------------------------------------------------

def test_the_route_check_matches_the_python_vocabulary(conn):
    """Second homes are admitted only when PINNED.

    A value the resolver can produce and the CHECK does not admit aborts an
    ingest at whichever row happens to carry it first -- 68,550 labels in.
    """
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'spl_label_subject_route'").fetchone()
    for route in spl_subject.SUBJECT_ROUTES:
        assert f"'{route}'" in definition, route
    # And nothing the Python vocabulary does not know: a route admitted by the
    # database and produced by nobody is a bucket that can never be reported on.
    quoted = definition.count("'")
    assert quoted == 2 * len(spl_subject.SUBJECT_ROUTES)


def test_the_completeness_check_makes_BOTH_malformed_states_unrepresentable(
        conn, run_id, moiety):
    """'Resolved but no uuid' and 'a uuid on an unresolved route'.

    db/049's `..._endpoint_1_complete` shape: ONE check per pairing, not two
    nullable columns nobody cross-checks.
    """
    _wording(conn, run_id)
    _label(conn, run_id)
    for route, value in (("openfda_unii", None), ("unresolved", moiety)):
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                spl_evidence.write_label_subjects(
                    conn,
                    [spl_evidence.SubjectRow(
                        set_id="SET-1", version="1", subject_ordinal=0,
                        moiety_uuid=value, route=route)],
                    ingest_run_id=run_id, source=SOURCE)


def test_a_label_may_carry_SEVERAL_subjects_because_combinations_are_ordinary(
        conn, run_id, moiety):
    second = _moiety(conn, run_id, "aspirin")
    _wording(conn, run_id)
    _label(conn, run_id)
    spl_evidence.write_label_subjects(
        conn,
        [spl_evidence.SubjectRow(set_id="SET-1", version="1", subject_ordinal=0,
                                 moiety_uuid=moiety, route="openfda_unii"),
         spl_evidence.SubjectRow(set_id="SET-1", version="1", subject_ordinal=1,
                                 moiety_uuid=second, route="openfda_unii")],
        ingest_run_id=run_id, source=SOURCE)


def test_one_label_may_NOT_name_one_moiety_twice(conn, run_id, moiety):
    """A repeat would double that pair's evidence rows with nothing to say so."""
    _wording(conn, run_id)
    _label(conn, run_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        spl_evidence.write_label_subjects(
            conn,
            [spl_evidence.SubjectRow(set_id="SET-1", version="1",
                                     subject_ordinal=0, moiety_uuid=moiety,
                                     route="openfda_unii"),
             spl_evidence.SubjectRow(set_id="SET-1", version="1",
                                     subject_ordinal=1, moiety_uuid=moiety,
                                     route="openfda_unii")],
            ingest_run_id=run_id, source=SOURCE)


def test_an_unresolved_label_gets_exactly_ONE_row(conn, run_id):
    """'No subject' is a single statement about a label.

    Two of them would double-count it in the recovery register, which is the
    population every future recovery route is sized against.
    """
    _wording(conn, run_id)
    _label(conn, run_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        spl_evidence.write_label_subjects(
            conn,
            [spl_evidence.SubjectRow(set_id="SET-1", version="1",
                                     subject_ordinal=0, moiety_uuid=None,
                                     route="unresolved"),
             spl_evidence.SubjectRow(set_id="SET-1", version="1",
                                     subject_ordinal=1, moiety_uuid=None,
                                     route="absent_from_dailymed")],
            ingest_run_id=run_id, source=SOURCE)


# --------------------------------------------------------------------------
# 3. The wording register, and the prose column that is not there
# --------------------------------------------------------------------------

def test_spl_wording_has_NO_PROSE_COLUMN(conn):
    """Its absence is the licensing determination, so it is pinned.

    A `text` column added here would make the quote budget unenforceable in a
    single edit, silently: the budget bounds `spl_wording_quote`, and a full copy
    of the section sitting one table over would simply not be covered by it.
    """
    columns = {row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'spl_wording'")}
    assert columns == {"ingest_run", "source", "text_key", "char_length",
                       "label_count"}


def test_a_text_key_that_is_not_a_sha256_digest_is_refused(conn, run_id):
    """A producer that changed hash is refused at the table rather than quietly
    filling it with keys nothing joins to."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _wording(conn, run_id, text_key="not-a-digest")


def test_an_uppercase_digest_is_refused_because_the_fold_would_be_a_SECOND_rule(
        conn, run_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        _wording(conn, run_id, text_key="A" * 64)


def test_a_zero_length_wording_is_refused(conn, run_id):
    """It has a zero budget and can carry no evidence: malformed, not difficult."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _wording(conn, run_id, char_length=0)


def test_the_parser_and_the_check_agree_about_what_a_text_key_looks_like(
        conn, run_id):
    """The shape CHECK and the producer are two homes for one rule, so they are
    compared rather than assumed to match."""
    _wording(conn, run_id, text_key=spl.section_key("some section text"))


# --------------------------------------------------------------------------
# 4. The label's citation
# --------------------------------------------------------------------------

def test_a_blank_set_id_is_refused_at_the_FIRST_one(conn, run_id):
    """db/050 section 1's lesson transplanted.

    A blank key is a valid row as far as a count is concerned and sorts before
    every real key, so the first one is already a defect -- it must abort rather
    than wait for a second to collide with it.
    """
    _wording(conn, run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _label(conn, run_id, set_id="")


def test_a_blank_version_is_refused(conn, run_id):
    _wording(conn, run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _label(conn, run_id, version="")


def test_a_revised_label_is_a_SECOND_row_not_a_replacement(conn, run_id):
    """A revised label is a new document making its own statement; collapsing
    versions would silently prefer whichever was ingested last."""
    _wording(conn, run_id)
    _label(conn, run_id, version="1")
    _label(conn, run_id, version="2")
    (count,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_label WHERE set_id = 'SET-1'").fetchone()
    assert count == 2


def test_a_label_may_not_cite_a_wording_that_was_never_written(conn, run_id):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _label(conn, run_id, text_key=KEY_B)


# --------------------------------------------------------------------------
# 5. THE QUOTE BUDGET -- and it is SHOWN IT CAN FAIL
# --------------------------------------------------------------------------

def _quote(text_key, ordinal, start, end):
    return spl_evidence.QuoteRow(text_key=text_key, ordinal=ordinal,
                                 char_start=start, char_end=end,
                                 quote_text="x" * (end - start))


def test_windows_within_the_budget_are_accepted(conn, run_id):
    """The control. Without it, every refusal below could be a broken trigger."""
    _wording(conn, run_id, char_length=1_000)          # budget 250
    spl_evidence.write_quotes(
        conn, [_quote(KEY_A, 0, 0, 120), _quote(KEY_A, 1, 500, 620)],
        ingest_run_id=run_id, source=SOURCE)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_windows_OVER_the_budget_are_REFUSED(conn, run_id):
    """⇒ THE GUARD THIS SLICE MOST NEEDED SHOWN.

    Measured, an unbounded per-occurrence window stores 82.7% of a section, so a
    rule that were merely intended would make 'a quoted window' and 'the prose'
    the same act. db/050's finding was that every guard in a slice passed
    vacuously; this one is watched failing.
    """
    _wording(conn, run_id, char_length=1_000)          # budget 250
    spl_evidence.write_quotes(
        conn,
        [_quote(KEY_A, 0, 0, 130), _quote(KEY_A, 1, 300, 430),
         _quote(KEY_A, 2, 600, 730)],                  # 390 characters
        ingest_run_id=run_id, source=SOURCE)
    with pytest.raises(psycopg.errors.RaiseException, match="over the budget"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_budget_is_checked_AT_COMMIT_not_per_row(conn, run_id):
    """Deferred, because the writer inserts windows one at a time.

    An immediate check would refuse a legal final state on the way to it -- and
    the way it would refuse it is by rejecting a window that overlaps one already
    written, before the merge that removes the overlap.
    """
    _wording(conn, run_id, char_length=1_000)
    # 390 characters -- already over budget, and no error yet.
    spl_evidence.write_quotes(
        conn,
        [_quote(KEY_A, 0, 0, 130), _quote(KEY_A, 1, 300, 430),
         _quote(KEY_A, 2, 600, 730)],
        ingest_run_id=run_id, source=SOURCE)
    (count,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_wording_quote").fetchone()
    assert count == 3


def test_OVERLAPPING_windows_are_refused_so_the_sum_IS_the_characters_stored(
        conn, run_id):
    """The budget is argued in DISTINCT characters, so the sum has to be them.

    Overlapping windows would make the enforced quantity larger than the stored
    one -- conservative, but it would mean the constraint no longer measures what
    the determination is written in.
    """
    _wording(conn, run_id, char_length=10_000)         # budget 2,500
    spl_evidence.write_quotes(
        conn, [_quote(KEY_A, 0, 0, 200), _quote(KEY_A, 1, 100, 300)],
        ingest_run_id=run_id, source=SOURCE)
    with pytest.raises(psycopg.errors.RaiseException, match="overlapping"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_a_window_reaching_past_the_END_of_the_wording_is_refused(conn, run_id):
    """A quote nobody can cut back out of the source is not a citation."""
    _wording(conn, run_id, char_length=1_000)
    spl_evidence.write_quotes(
        conn, [_quote(KEY_A, 0, 900, 1_100)],
        ingest_run_id=run_id, source=SOURCE)
    with pytest.raises(psycopg.errors.RaiseException, match="end past"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_stored_text_must_be_as_long_as_its_offsets_claim(conn, run_id):
    """The one mistake these offsets are most exposed to: cutting the RAW text
    while offsetting the NORMALISED one, which differ by a variable amount."""
    _wording(conn, run_id, char_length=1_000)
    with pytest.raises(psycopg.errors.CheckViolation):
        spl_evidence.write_quotes(
            conn,
            [spl_evidence.QuoteRow(text_key=KEY_A, ordinal=0, char_start=0,
                                   char_end=100, quote_text="short")],
            ingest_run_id=run_id, source=SOURCE)


def test_the_budget_in_the_catalog_is_the_SAME_expression_as_the_python_one(conn):
    """Two homes for one number is two numbers that can disagree.

    The trigger computes `ceil(0.25 * char_length)` and `spl_quote.quote_budget`
    computes `ceil(QUOTE_SHARE * text_length)`; this drives the database's copy
    over a range of lengths and compares.
    """
    for length in (1, 3, 17, 100, 999, 1_000, 3_809, 100_001):
        (allowed,) = conn.execute(
            "SELECT ceil(0.25 * %s)::integer", (length,)).fetchone()
        assert allowed == spl_quote.quote_budget(length), length


# --------------------------------------------------------------------------
# 6. Occurrences
# --------------------------------------------------------------------------

def test_an_ambiguous_span_carries_ONE_ROW_PER_ENTRY_at_one_offset(
        conn, run_id, moiety):
    """Ambiguity is unresolved, never 'pick the first'.

    S- and R-warfarin take different CYP pathways, so the direction is not
    cosmetic.
    """
    second = _moiety(conn, run_id, "carvone, (-)-")
    _wording(conn, run_id)
    spl_evidence.write_occurrences(
        conn,
        [spl_evidence.OccurrenceRow(text_key=KEY_A, char_start=10, char_end=18,
                                    moiety_uuid=moiety, match_ambiguous=True),
         spl_evidence.OccurrenceRow(text_key=KEY_A, char_start=10, char_end=18,
                                    moiety_uuid=second, match_ambiguous=True)],
        ingest_run_id=run_id, source=SOURCE)
    (count,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_entity_occurrence").fetchone()
    assert count == 2


def test_one_moiety_may_not_be_recorded_twice_at_one_offset(
        conn, run_id, moiety):
    _wording(conn, run_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        spl_evidence.write_occurrences(
            conn,
            [spl_evidence.OccurrenceRow(text_key=KEY_A, char_start=10,
                                        char_end=18, moiety_uuid=moiety,
                                        match_ambiguous=False),
             spl_evidence.OccurrenceRow(text_key=KEY_A, char_start=10,
                                        char_end=18, moiety_uuid=moiety,
                                        match_ambiguous=False)],
            ingest_run_id=run_id, source=SOURCE)


def test_an_empty_span_is_refused(conn, run_id, moiety):
    _wording(conn, run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        spl_evidence.write_occurrences(
            conn,
            [spl_evidence.OccurrenceRow(text_key=KEY_A, char_start=10,
                                        char_end=10, moiety_uuid=moiety,
                                        match_ambiguous=False)],
            ingest_run_id=run_id, source=SOURCE)
