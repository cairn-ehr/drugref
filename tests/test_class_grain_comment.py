# tests/test_class_grain_comment.py
"""The CATALOG COMMENTS a re-issue can silently revert, pinned whole.

WHY THIS FILE EXISTS. `COMMENT ON` OVERWRITES; it does not merge. Three migrations state
a comment over `gap_uncurated_class_interaction_rule` -- db/035 § 6, db/036 § 1 and
db/038 § 3 -- so the text a re-issue replaces is whichever ran LAST, and an author who
rebuilds from an OLDER file silently reverts every correction in between.

That is not hypothetical: db/038 § 3 came in to fix db/035's `nine` and rebuilt the
comment from db/035's text, reverting db/036 § 1's correction of the gap_key spelling
(`AXIS:` -> `CI_AXIS:`) and deleting the parenthetical that recorded it. Caught in the
review of PR #119, and NOTHING IN THE SUITE COULD SEE IT -- `test_curated_interaction_
comment.py` covers `curated_interaction` only, and this view had no pin at all.

WHY THE gap_key HALF IS THE SERIOUS ONE, worse than the figure that prompted the round:
`question_uuid = uuid5(gap_kind, gap_key)` and the key is FROZEN and externally citable.
A consumer reconstructing it from `\\d+` on a running node computes a DIFFERENT uuid and
gets NO ERROR -- just a uuid matching nothing. db/036 calls that the hardest kind of
wrong answer to notice, and it is right.

WHY THE ROUND'S OWN VERIFICATION MISSED IT, which is what this file is really built
against. db/038 verified its change by grepping the catalog for `%nine ingested%` and
`%seven ingested%` -- scoped to the word being changed, so it was structurally blind to
what else moved in the same overwrite. The lesson generalises past this one comment: a
re-issue must be checked for what it DROPPED, not only for what it set.

ASSERTED AGAINST THE CATALOG, NEVER THE MIGRATION TEXT, on
`test_curated_interaction_comment.py`'s precedent: the file a grep could check is not
the file that shipped once a later db/NNN replaces it.
"""


def _gap_view_comment_defects(comment):
    """PURE predicate: every way the class-grain gap view's comment can be wrong.

    Returns the defects found, empty when the comment is current. Pure and separate from
    the reader below so the guard test can drive it with the text that actually shipped,
    proving the check fires -- without a second copy of the rule to disagree with this.
    """
    if not comment:
        return ["no COMMENT ON VIEW at all"]
    defects = []
    # THE FROZEN KEY. `CI_AXIS:` is what `questions.py` emits ("'/CI_AXIS:' ||
    # relationship", twice) and what four tests pin literally. Checked BOTH ways: the
    # right spelling present, and the bare `/AXIS:` absent -- because the revert produced
    # a comment that was wrong without being empty, which a presence check alone misses.
    if "CI_AXIS:" not in comment:
        defects.append("does not spell the frozen gap_key `CI_AXIS:`")
    if "/AXIS:" in comment:
        defects.append("carries db/035's reverted `/AXIS:` spelling")
    # THE FIGURE db/038 § 3 CAME IN TO FIX. Seven class x class ONC entries were withheld
    # by issue 94; `nine` was issue 96's prose, quoted faithfully and never reconciled.
    if "seven ingested" not in comment:
        defects.append("does not state the seven ingested class rules")
    if "nine ingested" in comment:
        defects.append("carries issue 96's stale `nine ingested`")
    return defects


def _precedence_comment_defects(comment):
    """PURE predicate: `curated_ddi_pair`'s comment must name the THRESHOLD column.

    Separate from the gap view's predicate because they fail for unrelated reasons and a
    combined list would make either failure read as the other.

    THE STRING `severity_rank NULLS FIRST` IS NOT ITSELF A DEFECT, and that distinction
    is the point: db/038's text quotes db/037's old rule to record what changed, exactly
    as § 3 quotes `nine`. What must not survive is that spelling stated AS THE
    PRESCRIPTION -- so the check reads the `THE PRECEDENCE IS` clause, not the whole
    comment.
    """
    if not comment:
        return ["no COMMENT ON VIEW at all"]
    defects = []
    if "THE PRECEDENCE IS `ORDER BY effective_rank" not in comment:
        defects.append("does not prescribe ORDER BY effective_rank")
    if "THE PRECEDENCE IS `ORDER BY severity_rank" in comment:
        defects.append("still prescribes db/037's severity_rank NULLS FIRST")
    return defects


def _view_comment(conn, view):
    """The live catalog comment, read the way a consumer's `\\d+` reads it."""
    return conn.execute(
        "SELECT obj_description(%s::regclass, 'pg_class')", (f"drugref.{view}",)
    ).fetchone()[0]


def test_a_missing_comment_is_itself_a_defect():
    """The `not comment` arm, unreachable on a healthy schema.

    A migration that DROPPED a comment rather than restating it leaves the catalog
    silent, and silence is the one answer neither check may read as current. DB-free:
    covering a pure predicate's last branch should not need a database.
    """
    for predicate in (_gap_view_comment_defects, _precedence_comment_defects):
        assert predicate(None) == ["no COMMENT ON VIEW at all"]
        assert predicate("") == ["no COMMENT ON VIEW at all"]


def test_the_gap_view_comment_states_the_frozen_key_and_the_real_figure(conn):
    """db/038 § 3's two obligations at once, which is deliberate.

    They are one `COMMENT ON` statement, so a future re-issue drops them together or
    keeps them together. Asserting them in one test makes that coupling visible instead
    of leaving a maintainer to notice two files.
    """
    assert _gap_view_comment_defects(_view_comment(
        conn, "gap_uncurated_class_interaction_rule")) == []


def test_the_gap_view_check_rejects_the_comment_db038_first_shipped(conn):
    """THE GUARD'S OWN GUARD, and the mutation is not invented -- it is the exact text
    db/038 § 3 first published, which the PR #119 review found in the live catalog.

    Postgres `COMMENT` is transactional and the `conn` fixture rolls back, so the real
    catalog can be mutated back to the shape that shipped and the same reader re-run.
    """
    conn.execute(
        "COMMENT ON VIEW drugref.gap_uncurated_class_interaction_rule IS "
        "'... so seven ingested rules could sit permanently uncurated. GROUPED WITHOUT "
        "`source` so one rule asserted by two authorities raises ONE question -- its "
        "gap_key is CLASS:{subject}/CLASS:{object}/AXIS:{relationship} and question_uuid "
        "is a pure function of it.'")
    assert _gap_view_comment_defects(_view_comment(
        conn, "gap_uncurated_class_interaction_rule")) == [
        "does not spell the frozen gap_key `CI_AXIS:`",
        "carries db/035's reverted `/AXIS:` spelling",
    ]


def test_the_gap_view_check_rejects_db035s_stale_figure(conn):
    """The other half of the same overwrite, driven separately.

    db/036's text is the one db/038 SHOULD have rebuilt from: correct key, stale figure.
    Pinning it proves the figure check is not riding on the key check.
    """
    conn.execute(
        "COMMENT ON VIEW drugref.gap_uncurated_class_interaction_rule IS "
        "'... so nine ingested rules could sit permanently uncurated -- its gap_key is "
        "CLASS:{subject}/CLASS:{object}/CI_AXIS:{relationship}.'")
    assert _gap_view_comment_defects(_view_comment(
        conn, "gap_uncurated_class_interaction_rule")) == [
        "does not state the seven ingested class rules",
        "carries issue 96's stale `nine ingested`",
    ]


def test_the_pair_view_comment_prescribes_the_threshold_column(conn):
    """`\\d+ drugref.curated_ddi_pair` prints this FIRST, so it is the most-read
    statement of the precedence rule -- and db/038's first draft left it naming
    `severity_rank`, the column § 1 exists to stop clients thresholding on.
    """
    assert _precedence_comment_defects(_view_comment(conn, "curated_ddi_pair")) == []


def test_the_precedence_check_rejects_db037s_prescription(conn):
    """THE GUARD'S OWN GUARD, driven with db/037's actual sentence.

    Also pins the distinction the predicate's docstring draws: this mutation carries the
    old spelling AS THE PRESCRIPTION and must fail, while the live comment quotes the
    same words as history and must pass -- which the test above asserts.
    """
    conn.execute(
        "COMMENT ON VIEW drugref.curated_ddi_pair IS "
        "'THE PRECEDENCE IS `ORDER BY severity_rank NULLS FIRST, (rule_grain = "
        "''moiety_rule'') DESC` -- MOST SEVERE FIRST.'")
    assert _precedence_comment_defects(_view_comment(conn, "curated_ddi_pair")) == [
        "does not prescribe ORDER BY effective_rank",
        "still prescribes db/037's severity_rank NULLS FIRST",
    ]
