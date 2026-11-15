"""Every view that RANKS a severity reads only tables the unrankable sweep covers (#123).

THE BOUNDARY. `curated_unrankable_severity` (db/038) sweeps two of the tables carrying a
`severity_kind` foreign key. That is honest only while no other view ranks a severity
read from the uncovered ones (`curated_condition`, `additive_effect`,
`interaction_group_assertion`): a ranking view over one of those would turn a broken
vocabulary into a NULL rank, or a dropped row, that nothing reports.

#123 chose to KEEP the boundary rather than widen the view (#36: a review gate asks only
what an answer could change), on condition that a test enforce it instead of prose. This
is that test. Nothing below is hand-listed except the one exemption: the FK tables come
from `pg_constraint`, the ranking views and what they read from `pg_depend`. A new
ranking view over an uncovered table fails here, and the fix is to widen the sweep in
the same migration.

DIRECT DEPENDENCIES ONLY: a view ranking through `curated_ddi_pair` inherits that view's
reads, which already sit inside the sweep. DB-gated.
"""

# THE ONE EXEMPTION, and why it is not a gap: `ddi_source_severity` is the CANDIDATE
# tier's map from an upstream band to drugref's word (db/049). It holds no ruling, so
# there is no `reviewed_by` for the sweep to report, and its FK makes an unrankable word
# unreachable, which is the sweep's own argument for the curated tables.
_EXEMPT = {"ddi_source_severity"}

_FK_TABLES = """
SELECT conrelid::regclass::text
FROM   pg_constraint
WHERE  confrelid = 'drugref.severity_kind'::regclass AND contype = 'f'
"""

# (view, table) for every view reading `table` directly, through its rewrite rule.
_VIEW_READS = """
SELECT DISTINCT v.oid::regclass::text, d.refobjid::regclass::text
FROM   pg_depend d
JOIN   pg_rewrite r ON r.oid = d.objid
JOIN   pg_class v   ON v.oid = r.ev_class
WHERE  d.classid = 'pg_rewrite'::regclass AND d.refobjid <> v.oid
"""

# Views reading `severity_kind.severity_rank` by column, i.e. the ones that RANK.
_RANKERS = """
SELECT DISTINCT v.oid::regclass::text
FROM   pg_depend d
JOIN   pg_rewrite r ON r.oid = d.objid
JOIN   pg_class v   ON v.oid = r.ev_class
JOIN   pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
WHERE  d.classid = 'pg_rewrite'::regclass
AND    d.refobjid = 'drugref.severity_kind'::regclass
AND    a.attname = 'severity_rank'
"""

_SWEEP = "drugref.curated_unrankable_severity"


def _unswept_ranking_reads(conn) -> set[tuple[str, str]]:
    """(ranking view, FK table) pairs the sweep does not cover. EXPECTED EMPTY."""
    def strip(name):
        return name.removeprefix("drugref.")

    fk_tables = {strip(t) for (t,) in conn.execute(_FK_TABLES)} - _EXEMPT
    reads: dict[str, set[str]] = {}
    for view, table in conn.execute(_VIEW_READS):
        reads.setdefault(view, set()).add(strip(table))
    swept = reads.get(_SWEEP, set())
    return {(view, table)
            for (view,) in conn.execute(_RANKERS) if view != _SWEEP
            for table in reads.get(view, set()) & fk_tables - swept}


def test_no_ranking_view_reads_a_table_the_sweep_skips(conn):
    """The boundary as it stands: `curated_ddi_pair` ranks, and reads only the two
    swept tables; `drugcentral_ddi_pair` ranks the exempt candidate map."""
    assert _unswept_ranking_reads(conn) == set()


def test_a_new_ranking_view_over_an_unswept_table_is_caught(conn):
    """THE CHECK IS REAL: plant the consumer #123 warns about and watch it fail.
    Rolled back with the test's transaction."""
    conn.execute(
        "CREATE VIEW drugref.test_ranks_additive_effects AS "
        "SELECT a.additive_effect_id, s.severity_rank "
        "FROM drugref.additive_effect a "
        "JOIN drugref.severity_kind s ON s.severity = a.severity")

    assert _unswept_ranking_reads(conn) == {
        ("drugref.test_ranks_additive_effects", "additive_effect")}
