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

    ONE GUARD FOR ALL FOUR COUNTS, unlike the single-call guards in cli.py, because the
    three views ship in ONE migration: a database has all of them or none of them, so
    three guards would be three copies of one sentence with three chances to disagree.
    (Three views, four counts since issue 111 added the denominator -- this line said
    "three counts" for a round after that stopped being true.)

    IT GUARDS db/037 AS WELL AS db/035, which took a review to notice. db/037 corrects
    `class_pair_rule_reach`'s arithmetic without changing any name this block reads, so
    the guard did not fire on a db/035-or-036 database and the block printed numbers
    computed from the OLD, OVERSTATED `max_pair_count` -- `dead` under-reporting exactly
    the rule db/037 exists to surface. `curation._RULE_COUNT` now names a db/037 column
    so the UndefinedColumn arm below reaches that case too; see its comment for why the
    added column cannot move the count.
    """
    try:
        counts = curation.class_grain_counts(conn)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as exc:
        raise RuntimeError(
            "the class-grain detector views are missing or predate db/037: this "
            "database is behind on migrations, so ungraded, unreachable and "
            "cross-grain-disagreeing class rules cannot be reported -- and a rule "
            "reaching no pair would be under-reported even where they exist. Run "
            "`drugref migrate` and re-run status.") from exc

    # THE DENOMINATOR LEADS (issue 111), and the two numerators hang off it in
    # parentheses rather than standing on their own lines.
    #
    # WHAT IT FIXES. Both numerators report only on rules that EXIST. An ONCHIGH
    # re-ingest whose parser yields nothing empties the tier -- per-source rebuilds
    # are delete-and-rebuild -- and silences both at once, while `loaded_release`
    # still shows ONCHIGH loaded and this command still exits 0. The old three-zero
    # block was then BYTE-IDENTICAL to a healthy, fully-curated registry.
    # `class rules: 0` and `class rules: 9` are not, and that is the entire fix.
    #
    # STATED ONCE, NOT THREE TIMES. Repeating "of 9" on each numerator would put one
    # quantity in three places, the shape this project has paid for repeatedly. It
    # also reads worse: an operator diffing two status runs wants the population
    # first and the faults second, in that order.
    #
    # STILL COUNTS, NOT `none` -- deliberately departing from the symmetry the four
    # blocks above use, and the reason is worth keeping: those print lists that
    # happen to be empty, whereas these are numbers an operator DIFFS between runs,
    # and `none` cannot be diffed. A zero denominator is now itself the informative
    # reading, which is what `none` was reaching for and could not express.
    #
    # NO BANNER ON A ZERO DENOMINATOR, though it is tempting. Nothing writes the class
    # grain yet, so every healthy database reads 0 today, and a banner that fires on
    # every node until the first class-grain source lands is a banner an operator learns
    # to skip -- which would cost more than it buys on the day it means something.
    print(f"\nclass rules: {counts.total} "
          f"(ungraded {counts.ungraded}, reaching no pair {counts.dead})")
    # The faults keep the `**` banner the orphan block established, ONLY when non-zero,
    # and on their own lines because a banner belongs beside the number it explains.
    if counts.dead:
        print(f"  ** {counts.dead} ingested and graded, reaching zero patients -- "
              "check the axis against both classes' membership (issue #92) **")
    # NO DENOMINATOR ON THIS LINE, and the omission is considered, not forgotten. Its
    # population is `curated_ddi_pair`'s two-grain expansion, not the rule tier above,
    # so `counts.total` would be the WRONG denominator -- and the right one is a second
    # unfiltered read of the very view issue 112 has open to be measured before
    # class-grain content ships. A denominator here waits on that measurement.
    if counts.disagreements:
        print(f"cross-grain disagreements: {counts.disagreements}"
              "  ** one drug pair graded differently by both grains; consumers take "
              "the MORE SEVERE, so reconcile or the broader rule stands **")
    else:
        print("cross-grain disagreements: 0")
