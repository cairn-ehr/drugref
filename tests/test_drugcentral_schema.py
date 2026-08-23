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
    run = _open_run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        'display_name', 'unresolved')", (run,))


@pytest.mark.usefixtures("conn")
def test_a_moiety_on_an_unresolved_route_is_unrepresentable(conn):
    run = _open_run(conn)
    one = _a_moiety(conn, run, "a")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_1_uuid, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        %s, 'unresolved', 'unresolved')", (run, one))


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
