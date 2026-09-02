# src/drugref/cli_status.py
"""`drugref status`'s BLOCKS, split out of cli.py -- the loaded releases and their
runtimes (issue 159), the class grain (db/035) and the unrankable-severity fault
(db/038, issue 116).

THREE BLOCKS NOW, and the file docstring has twice been written too narrowly for what
the module holds: it said "the CLASS-GRAIN BLOCK" while holding one, then "LATER BLOCKS"
and "TWO BLOCKS" while the first block came here too. The module name was always the
general one; a `cli_unrankable.py` or a `cli_releases.py` would exist only because a
sentence was drawn too tight.

WHY A MODULE RATHER THAN ANOTHER 50 LINES OF cli.py. cli.py was 483 lines before this
round and the class grain's block plus its guard took it past CLAUDE.md rule 4's ~500
cap. The two ways out are shaving comments and moving code, and shaving comments is the
wrong one twice over: rule 3 makes the inline documentation mandatory, so trimming it to
fit rule 4 sets the two rules against each other, and the comments that would go are the
ones recording WHY the guard below exists -- which is exactly the knowledge this round
lost once already. `cli_chain`, `cli_curate`, `cli_policy`, `cli_signing` and
`cli_signing_release` are the same split for the same reason; this is the sixth.

NO SQL AGAINST A CURATED TABLE LIVES HERE, and the qualifier is the whole rule -- an
earlier version of this paragraph said "NO SQL LIVES HERE, deliberately", which stopped
being true the moment the loaded-release block arrived with its `SELECT` against
`drugref.loaded_release`. The class-grain read goes through
`curation.class_grain_counts` because two of its three views derive from
`curated_class_interaction`,
and cli.py's module docstring makes the rule: a handler must not embed SQL against
curated, append-only tables, since the sweep that finds readers works through
`pg_rewrite` and cannot see a query living in a Python string.
`test_the_cli_embeds_no_sql_against_a_curated_table` covers this module for the same
reason it covers the five above -- and it PASSES on the loaded-release SELECT because
`loaded_release` and `ingest_run_incomplete` are operational views nothing curates, the
same exception cli.py's docstring already draws for `_handle_status`.
"""
from drugref import curated_read, curation, migration_guard, provenance


def print_loaded_release_block(conn) -> None:
    """THE FIRST BLOCK: which upstream release each writer last landed, and how long it
    took. Moved here from cli.py when issue 159's runtime column took that file to
    499 of rule 4's ~500 lines -- the same trade this module's docstring argues for
    above, and the reason it exists.

    ⇒ THE RUNTIME IS NOT A SUBTRACTION DONE HERE. `provenance.format_run_duration`
    owns it, and REFUSES to print one for a row that does not say its two stamps were
    measured -- rows written before db/053 hold two transaction timestamps whose
    difference is a plausible-looking number and not a duration at all (issue 159).
    Doing the arithmetic in this file would be a second home for that rule, and the
    second home is always the one that forgets.

    ⇒ THE ROW DECIDES, NOT THE CLOCK (issue 176, db/054). This block used to read the
    ledger once per command for the moment db/053 was applied and hand it to the
    formatter, which compared it against each `started_at`. That asks WHEN a row was
    written when the question is WHICH CODE wrote it: an older client against a
    migrated database cleared the watershed and published a two-second run as `0.0s`,
    and a genuinely new row backdated over its orchestrator's pre-open parse was
    refused although both its stamps were correct. `duration_measured` is on the row,
    so there is no lookup left to do and no second question to get wrong.

    ⇒ AND THE REFUSAL STILL SAYS WHY, ONCE. Printing "unmeasured" on every line while
    leaving an operator to guess what would change it is a wrong answer by omission --
    the same fault, one level up, as publishing a number nobody can vouch for. The line
    is printed only when a row actually shows the refusal, so a fully re-ingested
    database does not carry an explanation of a state it is not in.
    """
    # THE GUARD, WHICH IS NEW WITH db/054 AND IS AN `UndefinedColumn` ONE. Every
    # deployment between pulling this code and running `drugref migrate` has
    # `loaded_release` in its db/025 shape -- the view is present and one column
    # short -- which is precisely the state `migration_guard.WRONG_SHAPE` catches, and
    # exactly how db/035 and db/038 widened a view under a reader before.
    with migration_guard.guarded(
            conn, relations=("drugref.loaded_release",), migration="054",
            # PHRASED TO SURVIVE WHAT THE GUARD APPENDS. `guard_message` closes three
            # of its four branches with "until it is fixed" or "meanwhile", so a
            # consequence ending in a relative clause reads as "... the first thing
            # `drugref status` is asked for until it is fixed". Verified by running it.
            consequence=("the loaded releases and their runtimes -- the first thing "
                         "`drugref status` is asked for -- cannot be reported")):
        loaded = conn.execute(
            "SELECT source, writer, upstream_release, finished_at, started_at, "
            "duration_measured FROM drugref.loaded_release").fetchall()
    print("loaded releases:" if loaded else "loaded releases: none")
    for source, writer, release, finished_at, started_at, measured in loaded:
        print("  {:<8} {:<14} {:<12} {} {}".format(
            source, writer, release, finished_at,
            provenance.format_run_duration(started_at=started_at,
                                           finished_at=finished_at,
                                           duration_measured=measured)))
    if any(not row[-1] for row in loaded):
        print(f"  ({provenance.UNMEASURED}: the row was not written by this version of "
              "drugref -- a run from before db/053, an older client, or a direct "
              "INSERT -- so its two stamps are not a duration and no number is "
              "published for it. Re-ingest that source to record one.)")


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
    the rule db/037 exists to surface. `curation._rule_count_sql` now names a db/037
    column so the UndefinedColumn arm below reaches that case too; see its comment for
    why the added column cannot move the count.
    """
    # ISSUE 122. ALL THREE VIEWS ARE NAMED, not just the one that raised: they ship in
    # one migration (db/035), so an operator seeing two present and one absent is
    # looking at a manual repair rather than an upgrade -- a distinction the single-name
    # message could not draw. db/037 is the migration ASKED ABOUT because
    # `_rule_count_sql` reads `shared_effective_member_count`, a db/037 column, so a
    # db/035-db/036 database has every view present and still fails; the "relations
    # exist but the migration is not applied" branch is written for exactly that state.
    with migration_guard.guarded(
            conn, relations=curation.CLASS_GRAIN_VIEWS, migration="037",
            consequence=("ungraded, unreachable and cross-grain-disagreeing class "
                         "rules cannot be reported, and a rule reaching no pair would "
                         "be under-reported even where they exist")):
        counts = curation.class_grain_counts(conn)

    # THE DENOMINATOR LEADS (issue 111), and the two numerators hang off it in
    # parentheses rather than standing on their own lines.
    #
    # WHAT IT FIXES. Both numerators report only on rules that EXIST. An ONCHIGH
    # re-ingest whose parser yields nothing empties the tier -- per-source rebuilds
    # are delete-and-rebuild -- and silences both at once, while `loaded_release`
    # still shows ONCHIGH loaded and this command still exits 0. The old three-zero
    # block was then BYTE-IDENTICAL to a healthy, fully-curated registry.
    # `class rules: 0` and `class rules: 7` are not, and that is the entire fix.
    #
    # STATED ONCE, NOT THREE TIMES. Repeating "of 7" on each numerator would put one
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
    print(f"\nclass rules: {counts.rules_total} "
          f"(ungraded {counts.ungraded}, reaching no pair {counts.dead})")
    # The faults keep the `**` banner the orphan block established, ONLY when non-zero,
    # and on their own lines because a banner belongs beside the number it explains.
    if counts.dead:
        print(f"  ** {counts.dead} ingested and graded, reaching zero patients -- "
              "check the axis against both classes' membership (issue #92) **")
    # NO DENOMINATOR ON THIS LINE, and the omission is considered, not forgotten. Its
    # population is `curated_ddi_pair`'s two-grain expansion, not the rule tier above,
    # so `counts.rules_total` would be the WRONG denominator -- and the right one is a
    # second unfiltered read of the very view issue 112 has open to be measured before
    # class-grain content ships. A denominator here waits on that measurement.
    #
    # THE FIELD IS NOW NAMED FOR THAT (issue 115). This paragraph was the ONLY place the
    # boundary was written down, in a different module from the type it constrains, and
    # `curation.py`'s own docstring says that is not where such knowledge lives. The
    # record's docstring states it too, so `{disagreements} of {rules_total}` now reads
    # wrong at a glance instead of reading like the symmetry it is not.
    if counts.disagreements:
        print(f"cross-grain disagreements: {counts.disagreements}"
              "  ** one drug pair graded differently by both grains; consumers take "
              "the MORE SEVERE, so reconcile or the broader rule stands **")
    else:
        print("cross-grain disagreements: 0")


def print_unrankable_severity_block(conn) -> None:
    """THE SIXTH BLOCK (db/038, issue 116): a severity drugref cannot rank.

    WHAT IT REPORTS, and it is a SCHEMA fault rather than a curator's error. A live
    curated ruling whose `severity` is absent from `severity_kind` is unreachable while
    the foreign key on both curated tables stands, so a non-empty reading here means a
    dropped constraint, a deleted vocabulary row, or a restore that lost the table.

    WHY IT IS WORTH A BLOCK NOW THAT `effective_rank` EXISTS. db/038 § 1 makes such a
    row harmless to a thresholding client, which is the urgent half -- and silent. Two
    things are still wrong underneath it: the row WINS
    `curated_ddi_pair_effective`'s `DISTINCT ON`, so a real `contraindicated` grade for
    that pair is DISCARDED in favour of a word drugref cannot rank; and nothing else in
    the schema would ever mention it. A mitigation that hides its own trigger is how
    issues 74 and 76 happened.

    THE LOUDER VOICE OF THE ORPHAN BLOCK, not the class grain's counts, and the choice
    follows what the numbers mean rather than where the code lives. The class-grain
    block prints bare counts because an operator DIFFS them between runs and a zero is
    an informative reading there. This is a list that should not exist at all, so it
    says `none` like the four `none`-voiced blocks above it (there are five blocks; the
    class grain's is the one that counts instead) and banners when it is not empty.

    SAME UndefinedTable GUARD, SAME NARROW SCOPE as its three siblings: a database
    predating db/038 has no view to read, and that must be ONE sentence rather than a
    psycopg traceback arriving after five blocks of real answers -- which reads as a
    partial success and names neither the cause nor the fix.
    """
    # ⇒ ISSUE 122'S SELF-REFERENTIAL CASE, AND THIS IS THE BLOCK IT IS ABOUT. "A restore
    # that lost the vocabulary table" is one of the three faults this very view was
    # written to REPORT: drop `severity_kind` and the view goes with it (CASCADE, or a
    # partial restore), so the old guard answered "this database predates db/038, run
    # `drugref migrate`" -- a no-op, since db/038 is recorded applied -- and status
    # repeated it forever. The detector misdiagnosed the exact fault it exists to
    # diagnose. The ledger check is what breaks that loop.
    #
    # THE SECOND SITE THAT CAUGHT `UndefinedTable` ALONE, now covered by `WRONG_SHAPE`
    # like the other four: `_UNRANKABLE_COLUMNS` is the same widening-prone shape as the
    # views that have already gained a column twice.
    with migration_guard.guarded(
            conn, relations=(curated_read.UNRANKABLE_VIEW,), migration="038",
            consequence=("a curated ruling whose severity drugref cannot rank goes "
                         "unreported, and such a ruling outranks and DISCARDS every "
                         "real grade for its pair")):
        unrankable = curated_read.unrankable_severities(conn)

    # NAMED FOR THE GRAIN IT SWEEPS, not for the vocabulary as a whole. db/035 put the
    # `severity_kind` foreign key on FIVE tables and this detector reads the two the DDI
    # read path ranks (`curated_interaction`, `curated_class_interaction`); the other
    # three -- `curated_condition`, `additive_effect`, `interaction_group_assertion` --
    # have no consumer that ranks a severity, so there is no read-path harm to report
    # today. But `unrankable severities: none` is a claim about the VOCABULARY, and this
    # is a sweep of part of it: labelling the line is what keeps a bounded check from
    # reading as an all-clear. Widening it is tracked on #123 rather than assumed.
    if unrankable:
        print(f"\nunrankable severities (DDI grain): {len(unrankable)}"
              "  ** a severity is missing from severity_kind, so these rulings outrank "
              "and DISCARD every real grade for their pairs -- check the foreign key "
              "on both curated tables and severity_kind's contents **")
        for u in unrankable:
            print(f"  {u.target_table:<25} #{u.target_id} severity {u.severity!r} "
                  f"reviewed by {u.reviewed_by} at {u.reviewed_at}")
    else:
        print("\nunrankable severities (DDI grain): none")
