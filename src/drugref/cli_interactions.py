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

A MODULE OF ITS OWN, following `cli_policy`, `cli_signing`, `cli_curate` and three more:
each owns its handlers plus a `register(commands)` that `cli.build_parser` calls, so the
global --dsn/--log-level flags and the single connect-and-dispatch path in `cli.main`
keep serving every command. cli.py stood at 461 lines, and CLAUDE.md rule 4 caps files
at ~500.

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
import uuid

from drugref import curated_read


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
        # `effective_rank` RATHER THAN `severity_rank` (db/038, issue 116): it is never
        # NULL, so the line renders identically on a database whose severity vocabulary
        # has a hole -- and on such a row `severity_rank` would print "None" beside a
        # severity word drugref cannot rank, which reads as a formatting bug rather
        # than as the schema fault `drugref status` reports it to be.
        print(f"  {g.partner_moiety}  {g.severity} (rank {g.effective_rank}) "
              f"[{g.relationship}] {g.rule_grain} · {g.evidence_grade} · "
              f"{g.signature_status}")
        for label, text in (("mechanism", g.mechanism), ("management", g.management)):
            if text:
                print(f"      {label}: {text}")


def _handle_interactions(conn, args) -> int:
    """Print drugref's curated grades for one moiety, or between two.

    RETURNS 0 EVEN WHEN NOTHING IS FOUND, and that is not laziness about exit codes.
    Most moieties carry no curated grade -- the overlay is small on purpose -- so an
    empty answer is the ORDINARY reading, not a failure; and this view's population is
    GRADES, not drugs, so it cannot distinguish an ungraded moiety from one nobody has
    heard of. A caller needing that distinction asks `substance_moiety`.
    """
    if args.against is None:
        # THE ONE-DRUG FORM: a single lookup, and the trailing sentence is what stops
        # its partial answer being read as a complete one.
        _print_grades(curated_read.effective_grades_for(conn, args.moiety),
                      f"grades with {args.moiety} as subject")
        print("note: rules are DIRECTIONAL (db/006) -- this lists only rules drugref "
              "states with this moiety as the SUBJECT. Ask about a specific pair to "
              "search both directions.")
        return 0

    # THE PAIR FORM: two lookups, done here rather than in the library, because
    # `effective_grades_for`'s contract is that the caller must know two happened.
    # Each block is filtered to the OTHER drug, so the answer is about this pair and
    # not a dump of everything either drug interacts with.
    found = 0
    for subject, partner in ((args.moiety, args.against), (args.against, args.moiety)):
        grades = [g for g in curated_read.effective_grades_for(conn, subject)
                  if g.partner_moiety == partner]
        found += len(grades)
        _print_grades(grades, f"{subject} -> {partner}")
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
