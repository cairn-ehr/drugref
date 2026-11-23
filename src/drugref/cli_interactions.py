# src/drugref/cli_interactions.py
"""`drugref interactions` -- the curated grade surface, asked a question (issue 114).

WHY THIS MODULE EXISTS. `curated_read.py` argues that "a view with no consumer is half a
feature", the rule this project wrote down after shipping `expansion_policy_unresolved`
and `curated_target_unresolved` with none. db/037 gave `curated_ddi_pair_effective` a
caller -- `effective_grades_for` -- and PR #113's review then found the caller had none
itself: `grep -rn effective_grades_for src tests` returned its own definition and one
test module. The standard the file sets for the view was one it did not meet, and the
issue's own judgement was that giving it a CLI "is the one that would actually test the
design". It did; see the directionality note below.

A MODULE OF ITS OWN, following `cli_policy`, `cli_signing`, `cli_curate` and the rest:
each owns its handlers plus a `register(commands)` that `cli.build_parser` calls, so the
global --dsn/--log-level flags and the single connect-and-dispatch path in `cli.main`
keep serving every command. cli.py stood at 461 lines, and CLAUDE.md rule 4 caps files
at ~500. (Said "three more" while `curated_read.py` called itself "the seventh" over the
same population -- two ordinals, one number, already disagreeing. Neither is numbered
now: `ls src/drugref/cli_*.py` cannot go stale.)

NO SQL LIVES HERE, for the rule cli.py's module docstring states: a handler must not
embed SQL against curated, append-only tables, because the sweep that finds readers
works through `pg_rewrite` and cannot see a query living in a Python string. The read is
`curated_read.effective_grades_for`, which reads the view that applies the precedence --
so the rule deciding between two grains grading one pair stays in exactly one place.

⇒ DIRECTIONALITY IS THE INTERESTING PART, AND BUILDING THIS IS WHAT MADE IT CONCRETE.
db/006's convention is that a rule stated as (X, Y) does not answer (Y, X), and
`effective_grades_for` deliberately refuses to union the two: "folding the mirror in
here would hide from the caller that two lookups happened". That is defensible in a
library
and dangerous at a prompt, because the two questions a user asks are not the same shape:

  * "what does drug X interact with" is ONE lookup, and can only ever return the rules
    drugref states with X as SUBJECT. The answer is genuinely partial, so the command
    SAYS it is partial rather than letting an empty list read as reassurance.
  * "do X and Y interact" is TWO lookups, which is exactly what the docstring
    prescribes -- and the command does both, visibly, rather than asking the library to
    hide them.

NEITHER FORM EVER SAYS "SAFE". drugref publishes facts, never verdicts (the rule
`accumulation.py` states for the same reason): an absent rule is an absent rule, not
evidence of absence, and the overlay is small and deliberately so.
"""
import sys
import uuid

from drugref import (class_grain_read, class_ruling_reach, curated_read, curation,
                     migration_guard, registry_read, release_state)


def _print_grades(grades, heading: str) -> None:
    """One labelled block of graded partners, most concerning first.

    THE HEADING NAMES THE DIRECTION SEARCHED, on every block including the empty ones.
    That is the whole mitigation for the partial-answer problem above: a bare list
    leaves the reader to assume it covers both directions, and the assumption is wrong.
    """
    if not grades:
        print(f"{heading}: no curated grade")
        return
    print(f"{heading}: {len(grades)}")
    for g in grades:
        # `rule_grain` IS PRINTED, not merely available: a class-grain grade is a
        # statement about the drug's whole class, and reading it as a statement about
        # this drug is precisely the over-generalisation issue 55 objects to elsewhere.
        #
        # `effective_rank` IS THE NUMBER (db/038, issue 116): it is never NULL, so the
        # line renders on a database whose severity vocabulary has a hole instead of
        # printing "None" beside a severity word drugref cannot rank.
        #
        # AND `severity_rank` IS STILL READ, which an earlier draft of this file did not
        # do -- the PR #119 review found it had NO reader anywhere in `src/`. The whole
        # argument for publishing both columns is that the nullable one is "the only
        # evidence the schema is broken"; printing only the safe number preserves that
        # evidence in the database and discards it at the one place a human reads it.
        # 0 MEANS "LEAST" IN EVERY OTHER NUMBERING A READER HAS MET, so unmarked it
        # inverts the very warning it encodes -- and this row has DISCARDED a real grade
        # for the same pair, which on this path may be the contraindication.
        rank = f"rank {g.effective_rank}"
        if g.severity_rank is None:
            rank = (f"rank {g.effective_rank} ** UNRANKABLE: drugref cannot rank "
                    f"{g.severity!r}, and this row OUTRANKED and DISCARDED any real "
                    f"grade for this pair. Run `drugref status` **")
        print(f"  {g.partner_moiety}  {g.severity} ({rank}) "
              f"[{g.relationship}] {g.rule_grain} · {g.evidence_grade} · "
              f"{g.signature_status}")
        for label, text in (("mechanism", g.mechanism), ("management", g.management)):
            if text:
                print(f"      {label}: {text}")


def _grades_for(conn, subject: uuid.UUID):
    """`effective_grades_for`, with the migration guard every status reader already had.

    ISSUE 122'S "RELATED, SAME THEME", and the asymmetry is the point: all four status
    readers were guarded and the CLINICIAN-FACING one was not. On a db/035-db/037
    database the view exists but has no `effective_rank`, so `UndefinedColumn` escaped
    as a raw traceback -- loud rather than silent, so not a safety defect, but it is
    exactly what `register()` below says the `uuid.UUID` typing exists to prevent: "a
    psycopg error that names a query the user never wrote".

    BOTH EXCEPTIONS, for the standing rule db/035 wrote down the hard way: a migration
    widening a view a guarded block reads must widen the guard in the same commit. That
    rule now lives in `migration_guard.WRONG_SHAPE` rather than in five hand-written
    tuples, two of which had already failed to keep it.
    """
    with migration_guard.guarded(
            conn, relations=(curated_read.EFFECTIVE_VIEW,), migration="038",
            consequence=("drugref cannot answer what a drug interacts with, and an "
                         "unrankable severity would reach a client as a NULL rank")):
        return curated_read.effective_grades_for(conn, subject)


# AN ANSWER KNOWN TO BE INCOMPLETE gets its own exit code, for #82's reason: a banner
# alone is human-only, and a script reading 0 would take the list as complete. Not 2,
# which means "nothing was asked".
EXIT_INCOMPLETE = 3


def _report_orphans(conn, subjects: set[uuid.UUID]) -> bool:
    """Banner any curated ruling that may concern `subjects` and that the list above
    cannot show (#121).

    THE DETECTOR ALREADY EXISTED; ITS READER WAS THE WRONG PERSON. A re-ingest that
    drops a candidate leaves the ruling in the curated table and out of
    `curated_ddi_pair`, so the list reads `no curated grade` for a pair drugref has
    judged -- possibly contraindicated. `drugref status` told the operator; this tells
    the person being under-warned. Returns whether it printed, so the caller can
    withhold the pair form's "ABSENT RULE" sentence (which an orphan makes false) and
    exit EXIT_INCOMPLETE.

    SUBJECT-LEVEL, NOT PAIR-LEVEL, in the pair form too: a moiety orphan names a CLASS,
    and whether the partner is in it would take a membership read. A class-grain orphan
    is shown when an asked drug is among its subject OR partner members, or when
    either side reaches nobody (see `class_grain_read.class_rules_reaching`). Both lean
    towards over-warning, this tool's safe direction. A drug that has since left a
    class is still caught, via the reach recorded when the rule was made (or, for a
    ruling older than db/080, at migration time) (#258).

    TWO GUARDED READS, NOT ONE: each guarded block holds exactly one call
    (`migration_guard.guarded`), and the member read is a different migration
    (db/079, db/080). It runs only when a class-grain orphan exists, which is
    expected never. WITHOUT db/079 OR db/080 IT FALLS BACK RATHER THAN ABORTS: the
    list has already printed, so raising would exit 2 with no banner at all (moiety
    orphans included). Keeping every class orphan is the pre-#255 behaviour -- noisy,
    never silent -- and stderr says why.

    "RULING", NOT "GRADE": the view also carries `applies = false` rulings, which the
    list never shows. "Any grade among them is missing" is true of both.
    """
    with migration_guard.guarded(
            conn, relations=(curation.UNRESOLVED_VIEW,), migration="035",
            consequence="drugref cannot tell whether a curated grade is missing here"):
        unresolved = curation.unresolved_targets(conn)
    reaching = set()
    class_orphans = [o for o in unresolved
                     if o.target_table == "curated_class_interaction"]
    needed = (class_grain_read.RULE_MEMBER_VIEW, class_ruling_reach.REACH_TABLE)
    # Probed once, and only when a class orphan makes the answer matter.
    absent = [f"{rel} ({db})" for rel, db in zip(needed, ("db/079", "db/080"))
              if class_orphans and conn.execute(
                  "SELECT to_regclass(%s)", (rel,)).fetchone()[0] is None]
    if absent:
        verb = "are" if len(absent) > 1 else "is"
        print(f"note: {' and '.join(absent)} {verb} missing, so "
              "every class-rule orphan is shown, not only those concerning the drugs "
              "asked. Run `drugref status`.", file=sys.stderr)
        reaching = {(o.subject_class, o.object_uuid, o.relationship)
                    for o in class_orphans}
    elif class_orphans:
        # ONE three-digit prefix (the ledger rejects anything else); 080 recorded
        # implies 079 is too, since migrations apply in order.
        with migration_guard.guarded(
                conn, relations=needed, migration="080",
                consequence="drugref cannot tell which drugs a missing class-rule "
                            "grade concerns"):
            reaching = class_grain_read.class_rules_reaching(conn, subjects)
    orphans = curation.orphaned_interaction_grades(unresolved, subjects, reaching)
    if not orphans:
        return False
    asked = "these drugs" if len(subjects) > 1 else "this drug"
    print(f"** INCOMPLETE: drugref holds {len(orphans)} curated ruling(s) that may "
          f"concern {asked} whose candidate pair is no longer projected "
          "(curated_target_unresolved), so any grade among them is MISSING from the "
          "list above. Run `drugref status` **")
    for o in orphans:
        subject = (f"members of class {o.subject_class}"
                   if o.target_table == "curated_class_interaction"
                   else str(o.subject_moiety))
        print(f"   {subject} -> class {o.object_uuid} [{o.relationship}] "
              f"reviewed by {o.reviewed_by} against {o.reviewed_against}")
    return True


def _report_departures(conn, directions) -> bool:
    """Banner any live class ruling that graded an asked pair when the curator ruled
    and no longer reaches it, its candidate intact (#256). Returns whether it printed.

    THE GAP #121'S BANNER LEFT: a drug leaving a class, or a policy deny cutting the
    subclass it was reached through, drops the grade from the list with no orphan to
    show, so `no curated grade` read as reassurance for a pair drugref has judged.
    `directions` is (subject, partner-or-None) per lookup the caller printed, so the
    banner speaks about exactly the lists above it.

    WITHOUT db/080 THERE IS NO REACH TO COMPARE: a note on stderr, and the exit code
    is left alone -- "cannot tell" is not "known incomplete".
    """
    needed = (class_grain_read.RULE_MEMBER_VIEW, class_ruling_reach.REACH_TABLE)
    absent = [f"{rel} ({db})" for rel, db in zip(needed, ("db/079", "db/080"))
              if conn.execute("SELECT to_regclass(%s)", (rel,)).fetchone()[0] is None]
    if absent:
        print(f"note: {' and '.join(absent)} missing, so drugref cannot tell whether "
              "a class ruling's grade has dropped out since it was reviewed. Run "
              "`drugref status`.", file=sys.stderr)
        return False
    with migration_guard.guarded(
            conn, relations=needed, migration="080",
            consequence="drugref cannot tell whether a reviewed class grade "
                        "dropped out"):
        departed = [d for subject, partner in directions
                    for d in class_ruling_reach.departed_reach(conn, subject, partner)]
    if not departed:
        return False
    print(f"** CHANGED SINCE REVIEW: {len(departed)} curated class ruling(s) graded "
          "the pair(s) below when the curator ruled and NO LONGER REACH THEM (a drug "
          "left a class, or an expansion-policy deny now cuts the class it was reached "
          "through), so their grade is MISSING from the list above **")
    for d in departed:
        when = (" (reach recorded at db/080's migration, not at ruling time)"
                if d.captured == "backfill_db080" else "")
        pairs = ", ".join(f"{s} -> {p}" for s, p in sorted(d.pairs, key=str))
        print(f"   ruling {d.curated_class_interaction_id}: class {d.subject_class} -> "
              f"class {d.object_class} [{d.relationship}] {d.severity}, reviewed by "
              f"{d.reviewed_by} against {d.reviewed_against}{when}")
        print(f"      no longer reached: {pairs}. "
              f"See `drugref audit class-ruling {d.curated_class_interaction_id}`")
    return True


def _known_moieties(conn, asked: list[uuid.UUID]) -> set[uuid.UUID]:
    """`known_moieties`, guarded like every other read this command makes.

    THE ONE RELATION `interactions` NOW TOUCHES FIRST HAD NO GUARD, which #120's fix
    introduced by putting an existence check in front of the graded read. A `--dsn`
    pointed at a database with no `drugref.substance_moiety` produced precisely what
    `_grades_for` exists to prevent -- "a psycopg error that names a query the user
    never wrote" -- from the newest line in the file. db/001 is the migration asked
    about because the identity spine is slice 1's, so its absence means an empty or
    non-drugref database rather than a version skew.
    """
    with migration_guard.guarded(
            conn, relations=(registry_read.MOIETY_TABLE,), migration="001",
            consequence=("drugref cannot tell a drug it has never heard of from one it "
                         "holds and has not graded")):
        return registry_read.known_moieties(conn, *asked)


def _report_unknown(unknown: list[uuid.UUID], *, registry_is_empty: bool) -> None:
    """Say that nothing was looked up -- WITHOUT borrowing the vocabulary of an absence.

    THE WORDING IS THE WHOLE FIX, so it is worth being precise about what it must not
    say. "no curated grade" is a statement about the OVERLAY; every word here is a
    statement about the REGISTRY, and mixing the two is the defect. The banner therefore
    names the identifier, says explicitly that this is not a finding about a drug, and
    points at the table that settles it.

    ⇒ AND IT MUST NOT REPEAT #122'S DEFECT IN ITS OWN VOICE. The three causes it offers
    -- a class_uuid, a uuid from another node, a transposed digit -- all point at the
    USER'S TYPING, and on a migrated-but-never-ingested database EVERY uuid lands here
    while none of the three applies. `registry_is_empty` is the one fact that separates
    "you typed something drugref does not hold" from "drugref holds nothing at all", and
    it costs one comparison the caller has already made.
    """
    print(f"** drugref holds no moiety with "
          f"{'these uuids' if len(unknown) > 1 else 'this uuid'}: "
          f"{', '.join(str(u) for u in unknown)}")
    if registry_is_empty:
        print("   THE REGISTRY IS EMPTY -- drugref holds no moieties at all, so this "
              "says nothing about the identifier you gave. Run the ingest (see "
              "`drugref chain --help`) before reading interactions. **")
        return
    print("   THIS IS NOT A STATEMENT ABOUT A DRUG -- nothing was looked up. Check the "
          "identifier against substance_moiety; a class_uuid, a uuid from another node "
          "or one transposed digit all parse as valid here. **")


def _handle_interactions(conn, args) -> int:
    """Print drugref's curated grades for one moiety, or between two.

    RETURNS 0 EVEN WHEN NOTHING IS FOUND, and that is not laziness about exit codes.
    Most moieties carry no curated grade -- the overlay is small on purpose -- so an
    empty answer is the ORDINARY reading, not a failure.

    BUT ONLY FOR A DRUG DRUGREF ACTUALLY HOLDS (#120, closed here). The view's
    population is GRADES, not drugs, so it cannot tell an ungraded moiety from one
    nobody has heard of -- and an earlier draft of this file settled that with "a
    caller needing the distinction asks `substance_moiety`", true of the VIEW and no
    answer at all
    to the objection: a user who has mistyped a uuid does not know they need the
    distinction, which is exactly what made it silent. The distinction now comes from
    `registry_read.known_moieties`, a read of the identity spine rather than of the
    overlay, and the two states stop rendering alike.

    EXISTENCE IS ESTABLISHED FIRST, BEFORE THE SELF-PAIR BRANCH, and the order is load
    bearing rather than incidental. `interactions X --with X` where X names nothing
    satisfies both conditions, and answering "the two moieties are the same drug" would
    be a confident claim about a drug that does not exist -- the same shape as #122's
    guards asserting the cause they imagined instead of the one they confirmed.

    EXIT 2, LIKE THE SELF-PAIR, for the reason the self-pair gives: nothing was asked,
    so nothing was answered. A banner alone would leave the distinction human-only,
    which is what #82 objects to elsewhere; a script piping this command gets a
    machine-readable signal instead.
    """
    # THE RELEASE HEADER COMES FIRST, whatever follows (#258): a grade read out of
    # context is the failure the decision audit exists to prevent.
    print(release_state.release_header(conn))
    # dict.fromkeys RATHER THAN set(): the report lists the offending identifiers, and a
    # set would reorder them, so `X --with Y` could name Y first. It also collapses the
    # duplicate in the self-pair case, so the same uuid is not reported twice.
    asked = list(dict.fromkeys(
        u for u in (args.moiety, args.against) if u is not None))
    known = _known_moieties(conn, asked)
    unknown = [u for u in asked if u not in known]
    if unknown:
        _report_unknown(unknown,
                        registry_is_empty=registry_read.registry_is_empty(conn))
        return 2

    if args.against is None:
        # THE ONE-DRUG FORM: a single lookup, and the trailing sentence is what stops
        # its partial answer being read as a complete one.
        _print_grades(_grades_for(conn, args.moiety),
                      f"grades with {args.moiety} as subject")
        incomplete = _report_orphans(conn, {args.moiety})
        incomplete |= _report_departures(conn, [(args.moiety, None)])
        print("note: rules are DIRECTIONAL (db/006) -- this lists only rules drugref "
              "states with this moiety as the SUBJECT. Ask about a specific pair to "
              "search both directions.")
        return EXIT_INCOMPLETE if incomplete else 0

    if args.moiety == args.against:
        # A DRUG AGAINST ITSELF IS A USER ERROR, AND MUST NOT RENDER AS A DATA ANSWER.
        # Both grains exclude self-pairs by construction (db/004, and db/038's
        # `sm.subject_moiety <> pm.partner_moiety`), so the query would run twice, print
        # two identical empty blocks, and conclude "drugref holds no curated grade for
        # this pair" -- a factual-sounding statement about a pair that does not exist.
        # Exit 2, not 0: nothing was asked, so nothing was answered.
        print("the two moieties are the same drug. drugref grades pairs of DIFFERENT "
              "moieties, so this is a question about the argument list rather than "
              "about the data -- check the second uuid.")
        return 2

    # THE PAIR FORM: two lookups, done here rather than in the library, because
    # `effective_grades_for`'s contract is that the caller must know two happened.
    # Each block is filtered to the OTHER drug, so the answer is about this pair and
    # not a dump of everything either drug interacts with.
    found = 0
    for subject, partner in ((args.moiety, args.against), (args.against, args.moiety)):
        grades = [g for g in _grades_for(conn, subject)
                  if g.partner_moiety == partner]
        found += len(grades)
        _print_grades(grades, f"{subject} -> {partner}")
    # Both banners print before either decides the exit: one must not hide the other.
    orphaned = _report_orphans(conn, {args.moiety, args.against})
    if _report_departures(conn, [(args.moiety, args.against),
                                 (args.against, args.moiety)]) or orphaned:
        return EXIT_INCOMPLETE
    if not found:
        # NOT "no interaction", AND NOT "safe". drugref has not asserted anything about
        # this pair; saying so plainly is the only honest rendering, and a command that
        # editorialised here would be making a clinical claim the data does not support.
        print("drugref holds no curated grade for this pair in either direction. That "
              "is an ABSENT RULE, not a safety finding.")
    return 0


def register(commands) -> None:
    """Add the `interactions` command to an existing subparsers object.

    Registration rather than a parser of its own, for the reason cli_policy.register
    states: the global --dsn/--log-level flags and cli.main's single
    connect-and-dispatch path keep serving every command.
    """
    interactions = commands.add_parser(
        "interactions",
        help="drugref's curated grades for one moiety, or between two")
    # TYPED AS `uuid.UUID` so a malformed identifier fails at the boundary, with
    # argparse naming the offending value -- rather than three layers down as a psycopg
    # error that names a query the user never wrote.
    #
    # A UUID RATHER THAN A NAME, deliberately: `moiety_uuid` is the immortal identity
    # this whole registry is built on, and a name lookup is a different feature with
    # its own ambiguity rules (drugref carries `azithromycin dihydrate` and not
    # `azithromycin` -- see the DrugCentral evaluation's 15 base names).
    interactions.add_argument(
        "moiety", type=uuid.UUID,
        help="the moiety_uuid to look up (the subject of the rules, unless --with)")
    interactions.add_argument(
        "--with", dest="against", type=uuid.UUID,
        help="a second moiety_uuid: search BOTH directions for rules about the pair")
    interactions.set_defaults(handler=_handle_interactions)
