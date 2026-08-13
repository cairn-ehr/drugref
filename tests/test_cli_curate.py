# tests/test_cli_curate.py
"""`drugref curate onchigh` -- the OVERLAY tier of slice 5c.2 (task 7).

Task 6's `drugref ingest onchigh` rebuilds `class_contraindication` rows with
source ONCHIGH: a rebuildable, delete-and-rebuild PROJECTION. This module
tests the sibling command that writes drugref's own graded judgement --
severity, mechanism, management, evidence_grade -- into `curated_interaction`,
which is APPEND-ONLY (db/029): a trigger refuses UPDATE/DELETE of values
outright, and a DEFERRED constraint trigger refuses two live rows for one
natural key, catching a duplicate only at COMMIT. The two commands are
deliberately separate entry points so a routine re-run of the candidate chain
can never reach the append-only tier.

IDEMPOTENCE HERE IS BY COMPARISON, NOT BY LUCK. A blind re-run that inserted
unconditionally would write a permanent duplicate on every invocation and only
be told so at COMMIT, long after the write happened. `curate_onchigh` must
therefore read the live row before writing and compare the GRADED fields only
-- `applies`, `severity`, `mechanism`, `management`, `evidence_grade` --
deliberately excluding `reviewed_at` (which moves every run) and
`reviewed_by` (an operator argument, not a fact about the judgement).

DB-GATED, same trap as test_onchigh_run.py. `ingest_onchigh`
(via `provenance.open_run`) COMMITS, so `ingested` below -- and everything
`seeded` stages on the same connection before it runs -- escapes the `conn`
fixture's rollback-based isolation. The `_clean` autouse fixture is this
module's own explicit cleanup, copied from test_onchigh_run.py's fixture of
the same name and same reasoning.
"""
import pathlib
import uuid
from dataclasses import dataclass

import psycopg
import pytest

from drugref import cli_curate, ids
from drugref.ingest import onchigh_run

# The real, committed, well-formed fixture (Task 3/7) -- see
# test_onchigh_run.py's own comment on this constant. Only "warfarin-nsaid"
# resolves fully against `seeded` below; "tranylcypromine-cox" does not
# (neither its UNII nor its MED-RT class is registered here), so it
# contributes no curated row -- the ordinary, mixed-resolution case.
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "onc_fixture.toml"

# warfarin-nsaid resolves and expands to warfarin's two gated-in forms
# (`seeded`: warfarin itself, warfarin sodium) -- two curated_interaction rows
# per run, mirroring test_onchigh_run.py's own _expected_onchigh_rows.
_expected_forms = 2


@pytest.fixture(autouse=True)
def _clean(conn):
    """Truncate everything a committing `ingest_onchigh` call (or `seeded`,
    staged on the same connection and swept into that commit) could have left
    behind. Copied from test_onchigh_run.py's `_clean`: TRUNCATE, not DELETE,
    because substance_moiety/identity_claim sit on the append-only floor whose
    row-level triggers refuse DELETE outright -- TRUNCATE bypasses row-level
    triggers entirely, which a statement-level floor would not.

    CASCADE reaches curated_interaction too: its `subject_moiety_uuid` is a
    foreign key into substance_moiety with no ON DELETE clause of its own, so
    TRUNCATE ... CASCADE must (and does) pull it in along with
    class_contraindication, class_membership, ingest_unresolved_onc_endpoint
    and open_question.

    ROLLS BACK FIRST, unlike test_onchigh_run.py's identical-looking fixture
    -- a difference forced by `curate_onchigh` NOT committing (unlike
    `ingest_onchigh`, which always does). A test here that never calls
    `conn.commit()` itself leaves curated_interaction's DEFERRED single-live
    trigger event still pending when this teardown runs, and Postgres
    refuses to TRUNCATE a table with pending trigger events
    (`ObjectInUse`). Rolling back first clears that pending event whether or
    not the test committed -- a no-op when it did, and the reason this
    fixture can clean up after both kinds of test.
    """
    yield
    conn.rollback()
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()


@dataclass
class Seeded:
    """The UUIDs this module's tests need to refer back to, once `seeded`
    has written the rows they name. A leaner cousin of test_onchigh_run.py's
    own `Seeded`: this module never exercises salt-gate exclusion or
    worklist pairing, so it carries only what `curate_onchigh` resolves
    against."""
    warfarin: uuid.UUID
    warfarin_sodium: uuid.UUID
    nsaid_class: uuid.UUID


@pytest.fixture
def seeded(conn, ingest_run_id) -> Seeded:
    """Everything `curate_onchigh` must resolve FIXTURE's "warfarin-nsaid"
    entry against: warfarin and its gated-in salt warfarin sodium, and the
    MED-RT EPC class the entry names. Same real, verified identifiers
    test_onchigh_run.py's own `seeded` uses (5c.2 spec §6), inserted directly
    rather than via a real ingest -- this module is about the curate
    command, not identity/classification ingest.
    """
    def _moiety(unii, name):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, name, ingest_run_id))
        conn.execute(
            "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
            "VALUES (%s, 'UNII', %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, unii, ingest_run_id))
        return moiety_uuid

    warfarin = _moiety("5Q7ZVV76EI", "warfarin")
    warfarin_sodium_unii = "4V2UBU7H8W"
    warfarin_sodium = _moiety(warfarin_sodium_unii, "warfarin sodium")

    nsaid_class = ids.mint_class_uuid("MED-RT", "N0000175722")
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', 'N0000175722', %s, 'EPC', %s) "
        "ON CONFLICT DO NOTHING",
        (nsaid_class, "Nonsteroidal Anti-inflammatory Drug [EPC]", ingest_run_id))

    # The one composition edge that makes salt-form expansion exercisable:
    # warfarin sodium is a GATED-IN active component of warfarin.
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES (%s, %s, 'SALT_SOLVATE', true, %s) ON CONFLICT DO NOTHING",
        (warfarin_sodium_unii, warfarin, ingest_run_id))

    return Seeded(warfarin=warfarin, warfarin_sodium=warfarin_sodium,
                  nsaid_class=nsaid_class)


@pytest.fixture
def class_seeded(conn, ingest_run_id, seeded) -> uuid.UUID:
    """One extra MED-RT class beyond `seeded`'s own nsaid_class, standing in
    for a CLASS SUBJECT (design spec section 14, Task 10): Monoamine Oxidase
    Inhibitors, contraindicated with `seeded`'s own nsaid_class as the object
    side. A separate fixture from `seeded` -- most of this module's tests
    exercise only the moiety-subject grain and never ask about a second
    class."""
    maoi_class = ids.mint_class_uuid("MED-RT", "N0000175724")
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', 'N0000175724', %s, 'MoA', %s) ON CONFLICT DO NOTHING",
        (maoi_class, "Monoamine Oxidase Inhibitors [MoA]", ingest_run_id))
    return maoi_class


@pytest.fixture
def ingested(conn, seeded):
    """The candidate tier already populated -- the realistic precondition for
    `curate onchigh`: an operator runs `ingest onchigh` (Task 6) before
    `curate onchigh` (this task). `curate_onchigh` does not actually read
    `class_contraindication` (it resolves straight from the file, exactly as
    `ingest_onchigh` itself does), so this fixture is not load-bearing for
    correctness -- it is here so the tests exercise the two commands in the
    order an operator actually runs them, and so a future change that DID
    make curate depend on the candidate tier would have something to run
    against rather than silently passing on an empty one.

    COMMITS (provenance.open_run's own early commit, then its final commit)
    -- see this module's `_clean` docstring for why that matters.
    """
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="ONCHigh-2015")


@pytest.fixture
def FIXTURE_REGRADED(tmp_path) -> pathlib.Path:
    """The same warfarin-nsaid rule as FIXTURE, regraded from "major" to
    "contraindicated" -- everything else (evidence_grade, the identifiers)
    held fixed, so exactly one graded field differs and a rerun must
    supersede rather than leave the row untouched.

    Generated into tmp_path rather than committed under tests/fixtures/:
    onc_fixture.toml is hand-reviewed, real-identifier content, and a
    regraded variant is test scaffolding, not curated content.
    """
    path = tmp_path / "onc_regraded.toml"
    path.write_text(
        '[[entry]]\n'
        'entry_id = "warfarin-nsaid"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_unii = "5Q7ZVV76EI"\n'
        'subject_name = "warfarin"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_EPC"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = true\n'
        'severity = "contraindicated"\n'
        'evidence_grade = "established"\n')
    return path


@pytest.fixture
def FIXTURE_BAD_SEVERITY(tmp_path) -> pathlib.Path:
    """A structurally well-formed file (onchigh.parse accepts it -- severity
    is not a vocabulary that module checks) carrying a severity that is not
    one of db/029's four. Proves the illegal value reaches the database
    exactly as db/006's lesson requires: no Python allow-list to catch it
    first and no CheckViolation to fake.
    """
    path = tmp_path / "onc_bad_severity.toml"
    path.write_text(
        '[[entry]]\n'
        'entry_id = "warfarin-nsaid"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_unii = "5Q7ZVV76EI"\n'
        'subject_name = "warfarin"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_EPC"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = true\n'
        'severity = "critical"\n'
        'evidence_grade = "established"\n')
    return path


@pytest.fixture
def CLASS_FIXTURE(tmp_path) -> pathlib.Path:
    """One ONC entry whose subject is a CLASS (design spec section 14, Task
    10) -- isolated at tmp_path rather than folded into the committed
    onc_fixture.toml, which this module's entry-count assertions already pin
    to exactly two moiety-subject entries (task-10 brief). Resolves fully
    against `seeded` + `class_seeded`."""
    path = tmp_path / "onc_class_subject.toml"
    path.write_text(
        '[[entry]]\n'
        'entry_id = "maoi-nsaid"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_medrt_code = "N0000175724"\n'
        'subject_name = "Monoamine Oxidase Inhibitors [MoA]"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_MoA"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = true\n'
        'severity = "major"\n'
        'evidence_grade = "established"\n')
    return path


@pytest.fixture
def BOTH_GRAINS_FIXTURE(tmp_path) -> pathlib.Path:
    """One moiety-subject entry (warfarin-nsaid, identical to FIXTURE's own),
    one class-subject entry (maoi-nsaid, identical to CLASS_FIXTURE's own),
    and one deliberately unresolvable entry -- so the reconciliation equation
    Task 7 pinned (`rules_seen == entries_resolved + entries_unresolved`) is
    exercised across BOTH grains in a single run, not just proven separately
    for each (task-10 brief: 'extend it, do not weaken it')."""
    path = tmp_path / "onc_both_grains.toml"
    path.write_text(
        '[[entry]]\n'
        'entry_id = "warfarin-nsaid"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_unii = "5Q7ZVV76EI"\n'
        'subject_name = "warfarin"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_EPC"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = true\n'
        'severity = "major"\n'
        'evidence_grade = "established"\n'
        '\n'
        '[[entry]]\n'
        'entry_id = "maoi-nsaid"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_medrt_code = "N0000175724"\n'
        'subject_name = "Monoamine Oxidase Inhibitors [MoA]"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_MoA"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = true\n'
        'severity = "major"\n'
        'evidence_grade = "established"\n'
        '\n'
        '[[entry]]\n'
        'entry_id = "unresolved-class-subject"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_medrt_code = "N9999999999"\n'
        'subject_name = "Not A Real Class"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_MoA"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = false\n')
    return path


def test_a_first_run_writes_one_judgement_per_resolved_form(conn, seeded, ingested):
    summary = cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                                        reviewed_against="ONCHigh-2015")
    assert summary.judgements_written == _expected_forms
    assert summary.judgements_superseded == 0


def test_every_entry_is_accounted_for_in_exactly_one_bucket(conn, seeded, ingested):
    """FIXTURE carries two entries and `seeded` deliberately resolves only one of
    them: "warfarin-nsaid" resolves fully, "tranylcypromine-cox" resolves NEITHER
    endpoint (neither tranylcypromine's UNII nor its MED-RT class is registered
    here) -- the same mixed-resolution fixture test_onchigh_run.py documents on
    its own copy of FIXTURE. Fix round 1 found that fact was true of the fixture
    but untested here, and that `curate_onchigh` dropped the unresolved entry with
    no counter and no log line -- exactly issue 71's "a dropped row counted into
    nothing" defect. This test pins BOTH: the reconciliation equation every future
    outcome must keep true, and the concrete split this fixture produces today, so
    a change that quietly widened `seeded` to resolve both entries would also be
    caught (by the second pair of assertions) rather than passing on a vacuously
    satisfied equation.
    """
    summary = cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                                        reviewed_against="ONCHigh-2015")
    # THE EQUATION: every entry `onchigh.parse` returned lands in EXACTLY ONE of
    # these two buckets, always -- true regardless of how many entries or how they
    # resolve, so a third outcome added later without a matching counter breaks
    # this line rather than going quiet.
    assert summary.rules_seen == summary.entries_resolved + summary.entries_unresolved
    # THE CONCRETE SPLIT, so "tranylcypromine-cox doesn't resolve" stays a fact
    # this suite checks rather than one only a docstring asserts.
    assert summary.entries_resolved == 1
    assert summary.entries_unresolved == 1


@pytest.fixture
def FIXTURE_COLLIDING(tmp_path) -> pathlib.Path:
    """Two DIFFERENT entries that resolve to the SAME curated natural key.

    `onchigh.parse` refuses a duplicate `entry_id`, but the natural key a
    curated row is written under is (subject_moiety, object_class, axis) --
    which the parser cannot know, because resolving it needs a database. Two
    entries naming the same subject and object under different entry_ids
    therefore parse cleanly and collide only at write time.
    """
    path = tmp_path / "onc_colliding.toml"
    entry = (
        '[[entry]]\n'
        'entry_id = "{eid}"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_unii = "5Q7ZVV76EI"\n'
        'subject_name = "warfarin"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_EPC"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = true\n'
        'severity = "{sev}"\n'
        'evidence_grade = "established"\n')
    path.write_text(entry.format(eid="warfarin-nsaid", sev="major")
                    + "\n"
                    + entry.format(eid="warfarin-nsaid-duplicate",
                                   sev="contraindicated"))
    return path


def test_two_entries_resolving_to_one_rule_are_refused(conn, seeded,
                                                       FIXTURE_COLLIDING):
    """A file stating one rule twice, with two grades, must not write either.

    Left unguarded this is silent AND unbounded. Entry A writes; entry B reads
    A's row as `live`, finds the grade differs, and SUPERSEDES it -- inside a
    single run. The file's two clinical claims collapse to whichever happens to
    come last, and because the collision increments `judgements_superseded` --
    the same counter a legitimate regrade increments -- the summary reads as a
    routine re-run. Worse, it never converges: every subsequent invocation
    writes two more permanent rows into an append-only table, forever, while
    the deferred single-live trigger stays quiet because exactly one row IS
    live at commit.

    RAISE, not "last one wins" and not a warning: the file is hand-authored, so
    two entries claiming one rule is a defect in the file, and this is the same
    reasoning `EndpointMismatchError` already applies one stage earlier -- the
    reviewer approved something other than what would land.
    """
    with pytest.raises(cli_curate.CollidingRuleError) as exc:
        cli_curate.curate_onchigh(conn, path=FIXTURE_COLLIDING,
                                  reviewed_by="Dr X", reviewed_against="test")
    # Both entry_ids named, so the curator knows which two lines to reconcile.
    assert "warfarin-nsaid" in str(exc.value)
    assert "warfarin-nsaid-duplicate" in str(exc.value)


def test_a_second_run_against_an_unedited_file_writes_nothing(conn, seeded, ingested):
    """Idempotent by COMPARISON, not by luck. The table is append-only, so a
    re-run that blindly inserted would write a permanent duplicate on every
    invocation -- and the deferred single-live trigger would only catch it at
    COMMIT, after the damage is done."""
    cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    second = cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                                       reviewed_against="ONCHigh-2015")
    assert second.judgements_written == 0
    assert second.unchanged == _expected_forms


def test_a_second_run_by_a_DIFFERENT_curator_still_writes_nothing(
        conn, seeded, ingested):
    """The comparison must span the GRADED fields only -- proven, not assumed.

    Both existing idempotence tests pass `reviewed_by="Dr X"` on both runs, so
    a comparison that wrongly included `reviewed_by` (or `reviewed_against`,
    or `reviewed_at`) would still report `unchanged` and both would pass. This
    one varies exactly those fields and nothing else.

    The failure it guards is not cosmetic: `curated_interaction` is
    append-only, so a second curator running the same unedited file would
    supersede EVERY rule in it, permanently, and there is no undo.
    """
    cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    before = conn.execute(
        "SELECT count(*), max(reviewed_at) FROM drugref.curated_interaction").fetchone()

    second = cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr Y",
                                       reviewed_against="ONCHigh-2015-reprint")
    conn.commit()

    assert second.judgements_written == 0
    assert second.judgements_superseded == 0
    assert second.unchanged == _expected_forms
    # Observed, not merely self-reported: the table itself is untouched, and
    # reviewed_at has not moved -- the field whose drift the exclusion exists
    # to prevent.
    assert conn.execute(
        "SELECT count(*), max(reviewed_at) FROM drugref.curated_interaction"
    ).fetchone() == before


def test_an_edited_grade_supersedes_rather_than_mutates(
        conn, seeded, ingested, FIXTURE_REGRADED):
    """The previous grade survives as history -- which matters most for exactly
    the rows that fired an alert."""
    cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    summary = cli_curate.curate_onchigh(conn, path=FIXTURE_REGRADED, reviewed_by="Dr X",
                                        reviewed_against="ONCHigh-2015")
    conn.commit()
    assert summary.judgements_superseded == _expected_forms
    rows = conn.execute(
        "SELECT severity, superseded_by IS NULL FROM drugref.curated_interaction "
        "ORDER BY curated_interaction_id").fetchall()
    # ORDERED BY curated_interaction_id, so the FIRST rows written (this run's
    # predecessors) sort first regardless of how many salt forms expand --
    # robust to _expected_forms changing without retying this assertion to an
    # exact row count.
    assert rows[0] == ("major", False)      # history, pointed at its successor
    assert rows[-1] == ("contraindicated", True)


def test_an_illegal_severity_reaches_the_database_check(
        conn, seeded, ingested, FIXTURE_BAD_SEVERITY):
    """No Python list of legal severities. db/006's lesson: two vocabularies
    kept in step by a comment drift the moment one is widened."""
    with pytest.raises(psycopg.errors.CheckViolation):
        cli_curate.curate_onchigh(conn, path=FIXTURE_BAD_SEVERITY,
                                  reviewed_by="Dr X", reviewed_against="x")


# ---- Task 10: the class-subject grain (design spec section 14) --------------


def test_a_class_subject_entry_writes_one_class_judgement(
        conn, seeded, class_seeded, CLASS_FIXTURE):
    """The class-subject twin of test_a_first_run_writes_one_judgement_per_
    resolved_form: a class-subject entry writes EXACTLY ONE
    curated_class_interaction row (no salt-form expansion, design spec
    section 14.3), and counts as one resolved entry."""
    summary = cli_curate.curate_onchigh(conn, path=CLASS_FIXTURE, reviewed_by="Dr X",
                                        reviewed_against="ONCHigh-2015")
    assert summary.entries_resolved == 1
    assert summary.entries_unresolved == 0
    assert summary.judgements_written == 1
    assert summary.judgements_superseded == 0
    assert conn.execute(
        "SELECT severity FROM drugref.curated_class_interaction "
        "WHERE superseded_by IS NULL").fetchone() == ("major",)


def test_a_second_run_against_the_class_fixture_writes_nothing(
        conn, seeded, class_seeded, CLASS_FIXTURE):
    """The class-subject twin of test_a_second_run_against_an_unedited_file_
    writes_nothing: idempotent by comparison on this grain too."""
    cli_curate.curate_onchigh(conn, path=CLASS_FIXTURE, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    second = cli_curate.curate_onchigh(conn, path=CLASS_FIXTURE, reviewed_by="Dr X",
                                       reviewed_against="ONCHigh-2015")
    assert second.judgements_written == 0
    assert second.unchanged == 1


def test_a_class_judgement_supersedes_rather_than_mutates(
        conn, seeded, class_seeded, CLASS_FIXTURE, tmp_path):
    """The class-subject twin of test_an_edited_grade_supersedes_rather_than_
    mutates: a regraded class-subject entry supersedes on
    curated_class_interaction, not curated_interaction."""
    cli_curate.curate_onchigh(conn, path=CLASS_FIXTURE, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    regraded = tmp_path / "onc_class_regraded.toml"
    regraded.write_text(CLASS_FIXTURE.read_text().replace(
        'severity = "major"', 'severity = "contraindicated"'))
    summary = cli_curate.curate_onchigh(conn, path=regraded, reviewed_by="Dr X",
                                        reviewed_against="ONCHigh-2015")
    conn.commit()
    assert summary.judgements_superseded == 1
    rows = conn.execute(
        "SELECT severity, superseded_by IS NULL FROM drugref.curated_class_interaction "
        "ORDER BY curated_class_interaction_id").fetchall()
    assert rows[0] == ("major", False)
    assert rows[-1] == ("contraindicated", True)


def test_the_reconciliation_equation_holds_across_both_grains(
        conn, seeded, class_seeded, BOTH_GRAINS_FIXTURE):
    """Task 7's reconciliation assertion, EXTENDED to cover both grains in one
    run rather than weakened (task-10 brief): every entry lands in exactly
    one of entries_resolved/entries_unresolved regardless of which grain it
    belongs to, and every resolved judgement target -- salt forms on the
    moiety grain, the single rule on the class grain -- lands in exactly one
    of judgements_written/judgements_superseded/unchanged."""
    summary = cli_curate.curate_onchigh(
        conn, path=BOTH_GRAINS_FIXTURE, reviewed_by="Dr X",
        reviewed_against="ONCHigh-2015")
    assert summary.rules_seen == summary.entries_resolved + summary.entries_unresolved
    assert summary.rules_seen == 3
    assert summary.entries_resolved == 2
    assert summary.entries_unresolved == 1
    # warfarin-nsaid expands to 2 salt forms (warfarin, warfarin sodium);
    # maoi-nsaid contributes exactly 1 class rule -- 3 judgement targets in
    # total, all newly written on a first run.
    total_targets = (summary.judgements_written + summary.judgements_superseded
                     + summary.unchanged)
    assert total_targets == 3
    assert summary.judgements_written == 3
