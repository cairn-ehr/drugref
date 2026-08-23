# tests/test_drugcentral_schema.py
"""db/049's shape: the source vocabulary, the severity map, the assertion table.

WHY A SCHEMA TEST AT ALL, when later tasks exercise the same objects: a new
source spelling is not a one-line change. It must land in the database CHECK,
in ids._SOURCE_CANONICAL and in provenance.WRITERS *in the same migration*, and
the failure mode when it does not is silent -- a per-source rebuild deletes
nothing and reports success. These tests are the guard against that silence.
"""
import psycopg
import pytest

from drugref import ids, provenance
from drugref.ingest import drugcentral_resolve


def test_drugcentral_is_a_canonical_source_spelling():
    """Listed EXPLICITLY, though the upper-case fall-through would also produce it.

    ids.py's own docstring warns by name against leaning on that fall-through:
    'openFDA-SPL' and 'MeDIC' fold to spellings a mixed-case CHECK would never
    match. 'DRUGCENTRAL' survives by luck, exactly as 'GSRS', 'DRUGREF' and
    'FDA-CYP' do, and the entry records that the luck was CHECKED.
    """
    assert ids.canonical_source("DRUGCENTRAL") == "DRUGCENTRAL"
    assert ids.canonical_source("drugcentral") == "DRUGCENTRAL"
    assert ids.canonical_source("  DrugCentral  ") == "DRUGCENTRAL"


def test_drugcentral_run_is_a_declared_writer():
    """provenance.WRITERS and db/049's CHECK are a PAIR (db/020's source-trio lesson)."""
    assert "drugcentral_run" in provenance.WRITERS


@pytest.mark.usefixtures("conn")
def test_ingest_run_admits_the_drugcentral_source_and_writer(conn):
    conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run')")


@pytest.mark.usefixtures("conn")
def test_ingest_run_still_refuses_a_misspelled_drugcentral_source(conn):
    """'DRUG-CENTRAL' is the typo db/012 finding 3 describes: it would insert
    cleanly under an unconstrained column and then match nothing, ever."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.ingest_run "
            "(source, upstream_release, source_checksum, writer) "
            "VALUES ('DRUG-CENTRAL', '11012023', 'deadbeef', 'drugcentral_run')")


@pytest.mark.usefixtures("conn")
def test_class_contraindication_source_is_NOT_widened(conn):
    """DrugCentral writes no class rule, so its source must stay OUT of that CHECK.

    HANDOVER said this CHECK needed widening for this source. It does not, and a
    widened CHECK would admit a row no writer in this project produces -- which is
    how a vocabulary grows a value nothing means.
    """
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'class_contraindication_source'").fetchone()
    assert "DRUGCENTRAL" not in definition


@pytest.mark.usefixtures("conn")
def test_the_two_va_bands_are_seeded_and_mapped(conn):
    """VA/NDF-RT's own semantics: Critical = avoid, Significant = monitor/adjust.

    `major` is deliberately unused by this source. A two-band authority has two
    bands, and spreading them across three grades would invent a distinction VA
    does not draw.
    """
    rows = conn.execute(
        "SELECT source_label, severity FROM drugref.ddi_source_severity "
        "WHERE source = 'DRUGCENTRAL' ORDER BY source_label").fetchall()
    assert rows == [("Critical", "contraindicated"), ("Significant", "moderate")]


@pytest.mark.usefixtures("conn")
def test_a_mapped_severity_must_be_a_real_grade(conn):
    """The FK into severity_kind is what stops a mapping naming a grade that has
    no rank -- and severity_rank is what decides which of two grades a consumer
    sees, so a rankless one would make that non-deterministic."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.ddi_source_severity "
            "(source, source_label, severity) "
            "VALUES ('DRUGCENTRAL', 'Catastrophic', 'apocalyptic')")


@pytest.mark.usefixtures("conn")
def test_the_mapping_is_keyed_per_source(conn):
    """Two authorities may both use the word 'Significant' and mean different
    things, so the label alone is not the key."""
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'ddi_source_severity_pkey'").fetchone()
    assert definition == "PRIMARY KEY (source, source_label)"


# ---- section 3: drugcentral_ddi_assertion, the content table ----------------------


def _open_run(conn):
    """A DRUGCENTRAL ingest_run to hang assertion rows off. Returns its id."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _a_moiety(conn, run, name):
    """A gated-in moiety to resolve an endpoint onto. Returns its uuid.

    Takes `run` and writes it into `first_seen_ingest`: that column is
    `NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)` with no default (see
    db/001), so a two-argument insert of only (moiety_uuid, display_name) would
    raise NotNullViolation on every call. The brief's version of this helper
    predates that column being read from the live schema rather than from a
    stale plan -- db/012 finding 3's own lesson, applied to the helper that
    tests it.
    """
    return conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (gen_random_uuid(), %s, %s) RETURNING moiety_uuid",
        (name, run)).fetchone()[0]


@pytest.mark.usefixtures("conn")
def test_an_assertion_row_round_trips(conn):
    run = _open_run(conn)
    one = _a_moiety(conn, run, "a")
    two = _a_moiety(conn, run, "b")
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
        " route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'C56.3352', 'gemfibrozil', 'pioglitazone', "
        "        'GEMFIBROZIL/PIOGLITAZONE HCL [VA Drug Interaction]', "
        "        'Significant', %s, %s, 'display_name', 'display_name')",
        (run, one, two))


@pytest.mark.usefixtures("conn")
def test_an_unmapped_severity_band_is_refused_at_insert(conn):
    """The load-bearing constraint. A future release inventing a third band must
    be REFUSED, not stored and silently mapped to nothing."""
    run = _open_run(conn)
    one, two = _a_moiety(conn, run, "a"), _a_moiety(conn, run, "b")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
            " route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', "
            "        'Potentially significant', %s, %s, "
            "        'display_name', 'display_name')",
            (run, one, two))


@pytest.mark.usefixtures("conn")
def test_a_resolved_route_without_a_moiety_is_unrepresentable(conn):
    """Asserts the constraint NAME, not just the exception type. Re-review (round
    2) found that `pytest.raises(CheckViolation)` alone does not attribute the
    violation to any particular constraint -- a mutation that broke
    endpoint_1_complete so it never fires would still pass this test if any OTHER
    constraint on the row happened to raise instead. Naming the constraint is
    what makes this test catch a mutation to endpoint_1_complete specifically,
    rather than to the table in general.
    """
    run = _open_run(conn)
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        'display_name', 'unresolved')", (run,))
    assert caught.value.diag.constraint_name == \
        "drugcentral_ddi_assertion_endpoint_1_complete"


@pytest.mark.usefixtures("conn")
def test_a_moiety_on_an_unresolved_route_is_unrepresentable(conn):
    """Asserts the constraint NAME for the same reason as its sibling above:
    without it, this test would still pass even if endpoint_1_complete were
    broken, so long as some other constraint on the row raised in its place.
    """
    run = _open_run(conn)
    one = _a_moiety(conn, run, "a")
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_1_uuid, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        %s, 'unresolved', 'unresolved')", (run, one))
    assert caught.value.diag.constraint_name == \
        "drugcentral_ddi_assertion_endpoint_1_complete"


@pytest.mark.usefixtures("conn")
def test_a_resolved_route_2_without_a_moiety_is_unrepresentable(conn):
    """Mirrors test_a_resolved_route_without_a_moiety_is_unrepresentable, but on
    ENDPOINT 2 -- added on code review because the parametrized pinning test
    only checks that endpoint_2_complete's route VOCABULARY matches Python; it
    extracts quoted literals from the constraint definition and would not
    notice the constraint being wired to the WRONG COLUMN, since the same
    literal set appears whichever column it is attached to (see db/029's
    index-column mutation, which survived 936 passing tests for the same
    reason -- PROJECT-NOTES.md).

    Endpoint 1 is left FULLY RESOLVED (moiety_1_uuid set, route_1 =
    'display_name') so the attribution is unambiguous, and specifically so this
    test is SENSITIVE to a copy-paste bug that wired
    drugcentral_ddi_assertion_endpoint_2_complete to moiety_1_uuid instead of
    moiety_2_uuid: with endpoint 1 resolved, that wrong right-hand side would
    read `moiety_1_uuid IS NOT NULL` = TRUE, matching route_2's `IN (...)` =
    TRUE, so the row would insert cleanly instead of raising -- and this test
    would fail (no CheckViolation) exactly when that mutation is present. A
    design where moiety_1_uuid stayed NULL would let the same wrong wiring
    still raise for the wrong reason and hide the bug.

    ALSO asserts the constraint NAME (re-review, round 2): route_1 and route_2
    carry the SAME value here ('display_name'/'display_name'), so a mutation
    that rewired drugcentral_ddi_assertion_route_2's vocabulary CHECK to read
    route_1 instead of route_2 would be invisible to `pytest.raises` alone --
    endpoint_2_complete still raises on this row regardless, for the unrelated
    reason this test targets, so the bare exception type passes either way.
    Naming endpoint_2_complete specifically closes that gap for THIS test; the
    route_2-reads-route_1 mutation itself is caught by
    test_route_2_outside_the_vocabulary_is_unrepresentable below, which is the
    one test built to isolate that constraint alone.
    """
    run = _open_run(conn)
    one = _a_moiety(conn, run, "a")
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_1_uuid, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        %s, 'display_name', 'display_name')", (run, one))
    assert caught.value.diag.constraint_name == \
        "drugcentral_ddi_assertion_endpoint_2_complete"


@pytest.mark.usefixtures("conn")
def test_a_moiety_on_an_unresolved_route_2_is_unrepresentable(conn):
    """Mirrors test_a_moiety_on_an_unresolved_route_is_unrepresentable, but on
    ENDPOINT 2 -- the execution-based counterpart the pinning test cannot
    provide, for the same reason as its sibling above.

    Endpoint 1 is left legally UNRESOLVED (route_1 = 'unresolved',
    moiety_1_uuid NULL) rather than resolved, deliberately: that gives
    moiety_1_uuid and moiety_2_uuid OPPOSITE nullity (NULL vs. NOT NULL) here,
    which is what makes the test sensitive to endpoint_2_complete being wired
    to moiety_1_uuid instead of its own column. Under that wrong wiring, the
    right-hand side would read `moiety_1_uuid IS NOT NULL` = FALSE, matching
    route_2's `IN (...)` = FALSE (route_2 is 'unresolved'), so the row would
    insert cleanly instead of raising -- and this test would fail (no
    CheckViolation) exactly when that mutation is present. Leaving endpoint 1
    resolved instead (moiety_1_uuid NOT NULL, matching moiety_2_uuid's
    nullity here) would make the wrong wiring raise for the wrong reason and
    hide the bug, the same trap the sibling test above avoids in the other
    direction.

    ALSO asserts the constraint NAME (re-review, round 2), for the same reason
    as its sibling above: route_1 and route_2 are both 'unresolved' here, so a
    route_2-reads-route_1 mutation would not change this row's outcome (the
    same value either way), and endpoint_2_complete raises regardless -- so
    without naming the constraint, this test cannot tell that mutation from a
    correctly wired schema. test_route_2_outside_the_vocabulary_is_
    unrepresentable below is the test built to isolate route_2's own CHECK.
    """
    run = _open_run(conn)
    two = _a_moiety(conn, run, "b")
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_2_uuid, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        %s, 'unresolved', 'unresolved')", (run, two))
    assert caught.value.diag.constraint_name == \
        "drugcentral_ddi_assertion_endpoint_2_complete"


@pytest.mark.usefixtures("conn")
def test_route_2_outside_the_vocabulary_is_unrepresentable(conn):
    """ISOLATES drugcentral_ddi_assertion_route_2 -- re-review, round 2. The two
    endpoint-2 mirrored tests above use the SAME value for route_1 and route_2,
    so a mutation that rewired route_2's vocabulary CHECK to read route_1
    instead produces the identical boolean on those rows and endpoint_2_complete
    raises anyway either way; neither the bare exception type nor those tests'
    constraint-name assertion (which names endpoint_2_complete, correctly, for
    what THEY test) can see a route_2-reads-route_1 swap.

    This row is built so route_2's vocabulary CHECK is the ONLY constraint that
    can fire: route_2 = 'not_a_route' is outside `drugcentral_resolve.ROUTES`
    entirely, so it is not in the resolved subset either -- the completeness
    CHECK reads (route_2 IN resolved-routes) = FALSE, (moiety_2_uuid IS NOT
    NULL) = FALSE (omitted here), FALSE = FALSE = TRUE, satisfied. Endpoint 1 is
    kept fully legal and resolved (moiety_1_uuid set, route_1 = 'display_name')
    so nothing on that side can raise either. Under the route_2-reads-route_1
    mutation, route_1's own value ('display_name') is IN the vocabulary, so the
    mutated constraint would be satisfied too and the row would insert cleanly
    -- exactly when this test must fail. Verified against a scratch table
    reproducing that mutation before trusting this test (task-4-report.md).
    """
    run = _open_run(conn)
    one = _a_moiety(conn, run, "a")
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_1_uuid, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        %s, 'display_name', 'not_a_route')", (run, one))
    assert caught.value.diag.constraint_name == "drugcentral_ddi_assertion_route_2"


@pytest.mark.usefixtures("conn")
def test_route_1_outside_the_vocabulary_is_unrepresentable(conn):
    """The mirror of test_route_2_outside_the_vocabulary_is_unrepresentable,
    ISOLATING drugcentral_ddi_assertion_route_1 -- added alongside it because
    the file had no dedicated isolation test for route_1's own CHECK either
    (the two endpoint-1 tests above both attribute to endpoint_1_complete).

    route_1 = 'not_a_route' is outside the vocabulary entirely, with
    moiety_1_uuid omitted, so endpoint_1_complete reads FALSE = FALSE = TRUE
    and stays satisfied; endpoint 2 is kept fully legal and resolved
    (moiety_2_uuid set, route_2 = 'display_name') so nothing on that side can
    raise. Under a route_1-reads-route_2 mutation, route_2's value
    ('display_name') is IN the vocabulary, so the mutated constraint would be
    satisfied too and the row would insert cleanly -- exactly when this test
    must fail.
    """
    run = _open_run(conn)
    two = _a_moiety(conn, run, "b")
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_2_uuid, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        %s, 'not_a_route', 'display_name')", (run, two))
    assert caught.value.diag.constraint_name == "drugcentral_ddi_assertion_route_1"


@pytest.mark.usefixtures("conn")
def test_an_unresolved_row_is_stored_rather_than_dropped(conn):
    """db/039's fda_cyp_assertion states the principle: the withheld rows are the
    point. An endpoint drugref cannot key is a WORKLIST ENTRY, not a drop."""
    run = _open_run(conn)
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'X', 'phytomenadione', 'warfarin', "
        "        'PHYTONADIONE/WARFARIN [VA Drug Interaction]', 'Critical', "
        "        'unresolved', 'unresolved')", (run,))


@pytest.mark.usefixtures("conn")
@pytest.mark.parametrize("constraint,expected", [
    ("drugcentral_ddi_assertion_route_1", "ROUTES"),
    ("drugcentral_ddi_assertion_route_2", "ROUTES"),
    ("drugcentral_ddi_assertion_endpoint_1_complete", "RESOLVED_ROUTES"),
    ("drugcentral_ddi_assertion_endpoint_2_complete", "RESOLVED_ROUTES"),
])
def test_the_route_checks_match_the_python_vocabulary(conn, constraint, expected):
    """THE PINNING TEST FOR AN ADMITTED SECOND HOME.

    drugcentral_resolve holds the closed route vocabulary as frozensets; these
    CHECKs restate it in SQL. That is a vocabulary in two places -- the defect
    db/006 was written to remove -- and it is admitted here deliberately, on the
    same terms ids._SOURCE_CANONICAL and ingest_run_source already live under:
    the two are a pair, and a test asserts both. A route added to Python and not
    to the CHECK would abort an ingest; a route REMOVED from Python while the
    CHECK still admits it would leave the database accepting a label nothing
    produces, which is the direction no error catches.

    PARSING NOTE: the brief sketched slicing the definition between the first
    'ARRAY[' and the following ']'. Measured against the live definitions, that
    slice happens to land correctly even for the two _complete CHECKs, whose
    `pg_get_constraintdef` comes back as an EQUALITY with the ARRAY-wrapped IN
    list on the LEFT and `(moiety_1_uuid IS NOT NULL)` on the right, e.g.:

        ((route_1 = ANY (ARRAY['display_name'::text, 'inchikey'::text,
         'cas'::text])) = (moiety_1_uuid IS NOT NULL))

    -- but it is correct only because nothing between those two brackets ever
    contains a bracket of its own; positional slicing gives no guarantee of that
    for a definition postgres is free to reformat, and a value that ever needed
    to carry a literal ']' would silently truncate the parse. Used instead:
    tests/test_schema_accumulation.py's test_the_source_trio_stays_in_lockstep
    already solved this exact shape of question (a CHECK's vocabulary vs. a
    Python list) by pulling every single-quoted literal out of the WHOLE
    definition with one regex, with no assumption about where the array sits.
    That is sound here because the only quoted strings in either CHECK's
    definition are the route labels themselves -- `IS NOT NULL`, `= ANY`,
    `::text` and the parentheses carry no quotes -- so the comparison being
    pinned (SQL vocabulary == Python frozenset) does not depend on the
    equality's shape or which side the array is on.
    """
    found = {row[0] for row in conn.execute(
        "SELECT unnest(literals) FROM ("
        "  SELECT regexp_matches(pg_get_constraintdef(oid), '''([^'']+)''', 'g') "
        "         AS literals "
        "  FROM pg_constraint WHERE conname = %s) s", (constraint,)).fetchall()}
    wanted = set(getattr(drugcentral_resolve, expected))
    assert found == wanted, (
        f"{constraint} admits {sorted(found)}, "
        f"drugcentral_resolve.{expected} is {sorted(wanted)}")
