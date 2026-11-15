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

TRANSITIVE THROUGH VIEWS, COLUMN-PRECISE: a ranker's reads are followed through every
view it stands on, and an FK table counts only where its severity COLUMN is read. A
ranker over an intermediate view of `additive_effect` is caught; a view that joins a
curated table only by id is not a false alarm. DB-gated.
"""

# THE ONE EXEMPTION, and why it is not a gap: `ddi_source_severity` is the CANDIDATE
# tier's map from an upstream band to drugref's word (db/049). It holds no ruling, so
# there is no `reviewed_by` for the sweep to report, and its FK makes an unrankable word
# unreachable, which is the sweep's own argument for the curated tables.
_EXEMPT = {"ddi_source_severity"}

# (table, attnum) of every column holding a `severity_kind` foreign key.
_FK_COLUMNS = """
SELECT conrelid::regclass::text, unnest(conkey)
FROM   pg_constraint
WHERE  confrelid = 'drugref.severity_kind'::regclass AND contype = 'f'
"""

# (view, relation, attnum, relation is a view) for every column a view reads directly,
# through its rewrite rule. Column-level, so a view that only joins on a table's id is
# told apart from one that reads its severity.
_VIEW_READS = """
SELECT DISTINCT v.oid::regclass::text, d.refobjid::regclass::text, d.refobjsubid,
       c.relkind IN ('v', 'm')
FROM   pg_depend d
JOIN   pg_rewrite r ON r.oid = d.objid
JOIN   pg_class v   ON v.oid = r.ev_class
JOIN   pg_class c   ON c.oid = d.refobjid
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


def _strip(name: str) -> str:
    return name.removeprefix("drugref.")


def _rankers(conn) -> set[str]:
    return {view for (view,) in conn.execute(_RANKERS)}


def _unswept_ranking_reads(conn) -> set[tuple[str, str]]:
    """(ranking view, FK table) pairs the sweep does not cover. EXPECTED EMPTY.

    TRANSITIVE THROUGH VIEWS: a ranker over an intermediate view that reads
    `additive_effect.severity` ranks that severity as surely as one reading it
    directly. Each ranker's closure is walked, and an FK table counts only where a
    view in it reads the FK COLUMN itself -- so a signature or status view that merely
    joins a curated table by id does not trip this.
    """
    fk_columns = {(_strip(t), col) for t, col in conn.execute(_FK_COLUMNS)}
    reads: dict[str, set[tuple[str, int, bool]]] = {}
    for view, rel, col, is_view in conn.execute(_VIEW_READS):
        reads.setdefault(view, set()).add((rel, col, is_view))
    swept = {_strip(rel) for rel, _col, _v in reads.get(_SWEEP, set())}
    exempt_or_swept = _EXEMPT | swept

    found = set()
    for ranker in _rankers(conn) - {_SWEEP}:
        seen, todo = set(), [ranker]
        while todo:
            view = todo.pop()
            if view in seen:
                continue
            seen.add(view)
            for rel, col, is_view in reads.get(view, set()):
                if is_view:
                    todo.append(rel)
                elif (_strip(rel), col) in fk_columns and _strip(rel) not in exempt_or_swept:
                    found.add((ranker, _strip(rel)))
    return found


def test_no_ranking_view_reads_a_table_the_sweep_skips(conn):
    """The boundary as it stands: `curated_ddi_pair` ranks, and reads only the two
    swept tables; `drugcentral_ddi_pair` ranks the exempt candidate map. The rankers
    query must find them, or `== set()` would pass on a query that matches nothing."""
    assert {"drugref.curated_ddi_pair", "drugref.drugcentral_ddi_pair"} <= _rankers(conn)
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


def test_a_ranking_view_over_an_intermediate_view_is_caught(conn):
    """The two-level shape: the inner view reads the severity without ranking it, the
    outer view ranks without touching the table. Direct reads alone would pass this."""
    conn.execute(
        "CREATE VIEW drugref.test_additive_severities AS "
        "SELECT additive_effect_id, severity FROM drugref.additive_effect")
    conn.execute(
        "CREATE VIEW drugref.test_ranks_through_a_view AS "
        "SELECT v.additive_effect_id, s.severity_rank "
        "FROM drugref.test_additive_severities v "
        "JOIN drugref.severity_kind s ON s.severity = v.severity")

    assert _unswept_ranking_reads(conn) == {
        ("drugref.test_ranks_through_a_view", "additive_effect")}
