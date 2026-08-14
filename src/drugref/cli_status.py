# src/drugref/cli_status.py
"""`drugref status`'s CLASS-GRAIN BLOCK, split out of cli.py (db/035).

WHY A MODULE RATHER THAN ANOTHER 50 LINES OF cli.py. cli.py was 483 lines before this
round and the class grain's block plus its guard took it past CLAUDE.md rule 4's ~500
cap. The two ways out are shaving comments and moving code, and shaving comments is the
wrong one twice over: rule 3 makes the inline documentation mandatory, so trimming it to
fit rule 4 sets the two rules against each other, and the comments that would go are the
ones recording WHY the guard below exists -- which is exactly the knowledge this round
lost once already. `cli_chain`, `cli_curate`, `cli_policy`, `cli_signing` and
`cli_signing_release` are the same split for the same reason; this is the sixth.

NO SQL LIVES HERE, deliberately, and that is not incidental to the file's existence.
The read is `curation.class_grain_counts`, because two of the three views this block
reports on derive from `curated_class_interaction`, and cli.py's module docstring makes
the rule: a handler must not embed SQL against curated, append-only tables, since the
sweep that finds readers works through `pg_rewrite` and cannot see a query living in a
Python string. This module is a VOICE, not a reader --
`test_the_cli_embeds_no_sql_against_a_curated_table` covers it for the same reason it
covers the five modules above.
"""
import psycopg

from drugref import curation


def print_class_grain_block(conn) -> None:
    """THE FIFTH BLOCK (db/035): what the class x class grain is doing, if anything.

    WHY THE GRAIN NEEDS A BLOCK OF ITS OWN AND THE MOIETY GRAIN DOES NOT. The moiety
    grain's failures all reach a human already -- its ungraded rules are a gap kind, so
    they land in `question_worklist`; its orphans are the block above. The class grain
    had neither until this migration, and one of its two failures still cannot be a gap
    kind: a rule that expands to ZERO drug pairs is not a question a curator can answer
    (grading it changes nothing -- #36's measured lesson), yet it is exactly the state
    the PR #95 review named as the whole point of this round -- "ingested, graded,
    committed and reported successful while reaching zero patients". A curator cannot
    be told; an operator must be.

    SPLIT OUT OF `_handle_status` rather than inlined, because it is the one block a
    test can drive on its own. The four blocks above it need a whole status run to
    reach, which is why three of them shipped untested and two of those shipped
    unreached (issues 74, 76, review I7).

    THE SAME GUARD AS THE TWO BLOCKS ABOVE, because the argument for leaving it out did
    not survive review: "any database this code can reach has migrated to at least
    db/029, so a missing db/035 view would be a genuinely mis-shaped schema". True
    premise, and the conclusion does not follow -- reaching db/029 does not imply
    reaching db/035. A database at db/029-db/034 clears both guards above and finds none
    of these three views, which is not a mis-shaped schema but every deployment between
    pulling this code and running `drugref migrate`.

    ONE GUARD FOR ALL THREE COUNTS, unlike the single-call guards in cli.py, because the
    three views ship in ONE migration: a database has all of them or none of them, so
    three guards would be three copies of one sentence with three chances to disagree.
    """
    try:
        counts = curation.class_grain_counts(conn)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as exc:
        raise RuntimeError(
            "the class-grain detector views are missing: this database predates "
            "db/035, so ungraded, unreachable and cross-grain-disagreeing class rules "
            "cannot be reported. Run `drugref migrate` and re-run status.") from exc

    # THREE LINES, THREE VOICES. `ungraded` is ordinary work queued for a curator and
    # says so flatly; the other two are faults nobody asked for, so they carry the `**`
    # banner the orphan block established -- and carry it ONLY when non-zero, because a
    # banner printed on a healthy database is a banner an operator learns to skip.
    print(f"\nungraded class rules: {counts.ungraded}")
    if counts.dead:
        print(f"class rules reaching no pair: {counts.dead}"
              "  ** ingested and graded, reaching zero patients -- check the axis "
              "against both classes' membership (issue #92) **")
    else:
        print("class rules reaching no pair: 0")
    if counts.disagreements:
        print(f"cross-grain disagreements: {counts.disagreements}"
              "  ** one drug pair graded differently by both grains; consumers take "
              "the MORE SEVERE, so reconcile or the broader rule stands **")
    else:
        print("cross-grain disagreements: 0")
