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
from drugref.ingest import spl, spl_checks, spl_quote, spl_subject

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

    ⇒ INSERTED AS RAW SQL, deliberately going AROUND `spl_evidence.SubjectRow`.
    That type now refuses both states in `__post_init__`, so a test driving the
    writer could no longer reach the database and would silently become a test
    of the dataclass. The CHECK's whole job is to bind a FUTURE writer -- one
    that does not exist yet and will not use this type -- so the only faithful
    way to show it holds is to attack the table directly.
    """
    _wording(conn, run_id)
    _label(conn, run_id)
    for route, value in (("openfda_unii", None), ("unresolved", moiety)):
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO drugref.spl_label_subject "
                    "(ingest_run, source, set_id, version, subject_ordinal, "
                    " moiety_uuid, route) "
                    "VALUES (%s, %s, 'SET-1', '1', 0, %s, %s)",
                    (run_id, SOURCE, value, route))


def test_the_subject_ROW_TYPE_refuses_both_states_before_the_database_does(
        moiety):
    """The same rule one layer up, and the reason it is worth restating.

    `_subject_rows` reads a validated `spl_subject.Subject` and writes a
    `SubjectRow`; the invariant was checked and then thrown away in the twelve
    lines between them. The CHECK above fires at COMMIT, at the end of a
    68,550-label ingest, naming no set_id -- this fires on the row that is wrong.
    """
    for route, value in (("openfda_unii", None), ("unresolved", moiety)):
        with pytest.raises(ValueError, match="spl_label_subject_complete"):
            spl_evidence.SubjectRow(set_id="SET-1", version="1",
                                    subject_ordinal=0, moiety_uuid=value,
                                    route=route)

    with pytest.raises(ValueError, match="not one of"):
        spl_evidence.SubjectRow(set_id="SET-1", version="1", subject_ordinal=0,
                                moiety_uuid=None, route="invented_route")


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
    while offsetting the NORMALISED one, which differ by a variable amount.

    Raw SQL, going around `QuoteRow`, for the reason the subject completeness
    test states: the row type now refuses this itself, so a test driving the
    writer would stop being a test of the CHECK -- and the CHECK exists to bind
    a writer that does not use this type.
    """
    _wording(conn, run_id, char_length=1_000)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.spl_wording_quote "
            "(ingest_run, source, text_key, ordinal, char_start, char_end, "
            " quote_text) VALUES (%s, %s, %s, 0, 0, 100, 'short')",
            (run_id, SOURCE, KEY_A))


def test_the_quote_ROW_TYPE_refuses_a_cut_that_does_not_match_its_offsets():
    """The same rule one layer up, where the text is still in scope.

    The CHECK fires partway through a 2,000-wording `COPY`, tens of minutes into
    a run, naming no text_key. This fires on the row that is wrong.
    """
    with pytest.raises(ValueError, match="spl_wording_quote_length"):
        spl_evidence.QuoteRow(text_key=KEY_A, ordinal=0, char_start=0,
                              char_end=100, quote_text="short")
    with pytest.raises(ValueError, match="is not a span"):
        spl_evidence.QuoteRow(text_key=KEY_A, ordinal=0, char_start=100,
                              char_end=10, quote_text="")


def test_from_window_REFUSES_a_window_running_past_its_text():
    """⇒ PYTHON SLICING CLAMPS SILENTLY, which is what makes this worth a check
    of its own rather than leaving it to `__post_init__`.

    `text[0:100]` on a 40-character string returns 40 characters with no error,
    so the offsets and the cut would then AGREE WITH EACH OTHER about the wrong
    characters -- and `__post_init__`, which only compares those two, would pass
    it. Only the text's own length can catch this, and only here, where it is
    still in scope.
    """
    text = "x" * 40
    with pytest.raises(ValueError, match="runs past the"):
        spl_evidence.QuoteRow.from_window(
            text_key=KEY_A, ordinal=0,
            window=spl_quote.Window(char_start=0, char_end=100), text=text)


def test_from_window_cuts_the_text_the_offsets_NAME():
    """The control, and the reason `from_window` exists: the cut and the offsets
    cannot disagree when one is derived from the other."""
    text = "abcdefghij" * 10
    row = spl_evidence.QuoteRow.from_window(
        text_key=KEY_A, ordinal=3,
        window=spl_quote.Window(char_start=10, char_end=25), text=text)
    assert row.quote_text == text[10:25]
    assert (row.char_start, row.char_end, row.ordinal) == (10, 25, 3)


def test_the_budget_in_the_catalog_is_the_SAME_expression_as_the_python_one(conn):
    """Two homes for one number is two numbers that can disagree.

    ⇒ THIS READS THE DEPLOYED TRIGGER, not a literal retyped here. The first
    version of this test ran `SELECT ceil(0.25 * %s)` with the 0.25 typed in the
    test, which made the test a THIRD home rather than a cross-check: mutating
    the trigger to `ceil(0.35 * ...)` left it, and every other test in this file,
    green. A test that restates the number it is checking cannot detect the
    disagreement it is named for.
    """
    (prosrc,) = conn.execute(
        "SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        " WHERE n.nspname = 'drugref' AND p.proname = 'spl_wording_quote_budget'"
    ).fetchone()
    assert f"ceil({spl_quote.QUOTE_SHARE} * wording_length)" in prosrc, prosrc

    # AND the RESULTS, over lengths that separate ceil from floor. The substring
    # pin catches a changed constant; it would not catch `ceil` becoming `round`,
    # and it is sensitive to whitespace a future CREATE OR REPLACE might change.
    # Three lines to keep both kinds of check rather than trade one for the other.
    for length in (1, 3, 17, 100, 999, 1_000, 3_809, 100_001):
        (allowed,) = conn.execute(
            "SELECT ceil(%s * %s)::integer", (spl_quote.QUOTE_SHARE, length)
        ).fetchone()
        assert allowed == spl_quote.quote_budget(length), length


def test_a_wording_quoted_EXACTLY_to_the_budget_is_accepted(conn, run_id):
    """The boundary, from below. `spent > allowed`, not `>=`.

    Mutating the trigger's comparison to `>=` left every budget test green,
    because the suite bracketed 24% accepted against 39% refused and never
    touched the edge between them. The determination is "up to 25%", so the
    wording that spends its budget to the last character has to COMMIT.
    """
    _wording(conn, run_id, char_length=1_000)
    budget = spl_quote.quote_budget(1_000)             # 250
    spl_evidence.write_quotes(
        conn, [_quote(KEY_A, 0, 0, budget)],
        ingest_run_id=run_id, source=SOURCE)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_a_wording_quoted_ONE_CHARACTER_over_the_budget_is_REFUSED(conn, run_id):
    """The boundary, from above -- the other half of the edge."""
    _wording(conn, run_id, char_length=1_000)
    budget = spl_quote.quote_budget(1_000)
    spl_evidence.write_quotes(
        conn, [_quote(KEY_A, 0, 0, budget + 1)],
        ingest_run_id=run_id, source=SOURCE)
    with pytest.raises(psycopg.errors.RaiseException, match="over the budget"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_a_window_ending_EXACTLY_at_the_last_character_is_accepted(conn, run_id):
    """`char_end > char_length`, not `>=` -- and this case is the COMMON one.

    `spl_quote.fixed_window` clamps `char_end` to the text length, so EVERY quote
    over a moiety named within `QUOTE_RADIUS` characters of a section's end ends
    exactly at `char_length`. Mutating the trigger's comparison to `>=` left the
    suite green while refusing, in production, a window the shipped writer emits
    constantly.
    """
    _wording(conn, run_id, char_length=1_000)
    spl_evidence.write_quotes(
        conn, [_quote(KEY_A, 0, 900, 1_000)],
        ingest_run_id=run_id, source=SOURCE)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


# --------------------------------------------------------------------------
# 5a. THE REGISTRY READ -- first-wins, and what first-wins DISCARDED
# --------------------------------------------------------------------------

def test_the_registry_read_COUNTS_what_first_wins_discarded(conn, run_id):
    """⇒ "the collision is the caller's to report", and no caller reported it.

    `identity_claim` is unique on (moiety_uuid, scheme, value) and deliberately
    NOT across moieties, so two moieties may legitimately claim one UNII. The
    read is first-wins in sorted order, which makes it deterministic -- but every
    subject resolved through that UNII then names ONE moiety and the other never
    appears. Deterministic and reproducibly wrong is still wrong, so the number
    of discarded entries has to leave this function.
    """
    first = _moiety(conn, run_id, "shared-name")
    second = _moiety(conn, run_id, "shared-name")          # same display name
    for moiety_uuid in (first, second):
        conn.execute(
            "INSERT INTO drugref.identity_claim "
            "(moiety_uuid, scheme, value, ingest_run) "
            "VALUES (%s, 'UNII', 'SHAREDUNII', %s)", (moiety_uuid, run_id))

    registry = spl_evidence.load_registry(conn)

    assert registry.name_collisions == 1
    assert registry.unii_collisions == 1
    # First-wins, and "first" is the sorted moiety_uuid -- a property of the
    # data, not of the plan.
    assert registry.by_name["shared-name"] == min(first, second)
    assert registry.by_unii["SHAREDUNII"] == min(first, second)
    # Keyed lookups, not whole-table equality: these read `substance_moiety` and
    # `identity_claim`, which the `conn` fixture rolls back but a crashed earlier
    # run can leave rows in.


def test_a_registry_with_no_collisions_reports_none(conn, run_id):
    """The control: without it the counts above could be an always-incrementing
    counter."""
    _moiety(conn, run_id, "warfarin")
    registry = spl_evidence.load_registry(conn)
    assert registry.name_collisions == 0 and registry.unii_collisions == 0


def test_the_registry_halves_are_NAMED_because_they_are_the_same_type(conn,
                                                                     run_id):
    """`names, known_uniis = load_registry(conn)` type-checks the wrong way round
    too. Swapped, the matcher is built from UNII codes and every subject resolves
    against display names -- a failure nothing notices until the floors, twelve
    minutes later."""
    moiety_uuid = _moiety(conn, run_id, "warfarin")
    conn.execute(
        "INSERT INTO drugref.identity_claim "
        "(moiety_uuid, scheme, value, ingest_run) VALUES (%s, 'UNII', 'ABC', %s)",
        (moiety_uuid, run_id))
    registry = spl_evidence.load_registry(conn)
    assert registry.by_name["warfarin"] == moiety_uuid
    assert registry.by_unii["ABC"] == moiety_uuid


def test_the_pair_read_REFUSES_rows_belonging_to_another_run(conn, run_id,
                                                            moiety):
    """`pairs` and `novel` read the PUBLISHED view, which does not group by run.

    That is deliberate -- a scoped variant would re-derive the pair grain and
    become a second home for it -- but it is only correct because
    `clear_source_spl` emptied this source in the same transaction. The
    precondition is therefore checked rather than assumed, unlike the rest of
    this module, where `reconcile` scopes every count instead.
    """
    other_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'other', 'other', %s) RETURNING ingest_run_id",
        (SOURCE, spl.WRITER)).fetchone()[0]
    _wording(conn, other_run)
    _label(conn, other_run)

    with pytest.raises(ValueError, match="belong to a run other than"):
        spl_checks.read_pairs(conn, run_id)


# --------------------------------------------------------------------------
# 5b. RECONCILE -- watched refusing, because it is the only cross-check
# --------------------------------------------------------------------------
#
# ⇒ THREE MUTATIONS SURVIVED THIS FUNCTION. `if stored != written:` -> `if
# False:`, `if past_end:` -> `if False:`, and `char_end > char_length` -> `>=`
# each left the whole suite green: `reconcile` could be deleted outright without
# failing a test. It is the ONLY check in the slice that compares what Python
# believes it wrote against what the database actually holds, and its own
# docstring calls the second half "THE ONE NO CONSTRAINT CAN EXPRESS". Both
# halves are now watched refusing, which is what db/050's finding asks of any
# guard whose failure mode is silent.

def _occurrence(text_key, moiety_uuid, char_start, char_end):
    return spl_evidence.OccurrenceRow(
        text_key=text_key, char_start=char_start, char_end=char_end,
        moiety_uuid=moiety_uuid, match_ambiguous=False)


def test_reconcile_ACCEPTS_counts_that_match_what_was_written(conn, run_id):
    """The control. Without it the two refusals below could be a broken query."""
    _wording(conn, run_id)
    _label(conn, run_id)
    spl_checks.reconcile(conn, run_id, wordings=1, labels=1,
                         subjects=0, occurrences=0)


def test_reconcile_REFUSES_a_count_the_database_does_not_hold(conn, run_id):
    """A COPY that silently dropped rows is the failure this exists to catch.

    The orchestrator counts in Python what it hands to `COPY`; nothing else ever
    reads those numbers back. If `COPY` stored fewer rows than it was given, the
    summary would report the Python figure and the projection would be short.
    """
    _wording(conn, run_id)
    _label(conn, run_id)
    with pytest.raises(ValueError, match="does not hold what the summary"):
        spl_checks.reconcile(conn, run_id, wordings=2, labels=1,
                             subjects=0, occurrences=0)


def test_reconcile_REFUSES_an_occurrence_reaching_PAST_its_wording(
        conn, run_id, moiety):
    """⇒ THE RAW-VERSUS-NORMALISED BUG, at the occurrence grain.

    The offsets live in `spl_entity_occurrence` and the length in `spl_wording`,
    so no row-local CHECK can see the contradiction and the quote trigger does
    not police this table. Matching the RAW section text while storing the
    NORMALISED length puts every offset past the end by a variable amount -- and
    this is the only thing in the slice that would notice.
    """
    _wording(conn, run_id, char_length=1_000)
    spl_evidence.write_occurrences(
        conn, [_occurrence(KEY_A, moiety, 990, 1_200)],
        ingest_run_id=run_id, source=SOURCE)
    with pytest.raises(ValueError, match="past their wording's length"):
        spl_checks.reconcile(conn, run_id, wordings=1, labels=0,
                             subjects=0, occurrences=1)


def test_an_occurrence_ending_EXACTLY_at_the_wording_length_reconciles(
        conn, run_id, moiety):
    """`char_end > char_length`, not `>=`. A half-open span ending at the last
    character is the ordinary case for a moiety named at the end of a section."""
    _wording(conn, run_id, char_length=1_000)
    spl_evidence.write_occurrences(
        conn, [_occurrence(KEY_A, moiety, 992, 1_000)],
        ingest_run_id=run_id, source=SOURCE)
    spl_checks.reconcile(conn, run_id, wordings=1, labels=0,
                         subjects=0, occurrences=1)


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
