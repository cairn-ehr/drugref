"""The consumer-facing read of drugref's GRADED drug pairs (`db/037`, issue 110).

WHY A MODULE OF ITS OWN. `curation.py` owns the curated overlay's writes and its
detector counts and stood at 485 lines, so adding this to it would have breached
CLAUDE.md rule 4's ~500-line cap on the spot -- the breach that is already open as issue
89 for two other files. `interactions.py` was the other candidate and is worse: its
first sentence is "The ONLY module that writes the interaction tables", and a curated
READ has no business there. `cli_status.py`, `cli_policy.py` and four siblings are the
same split for the same reason; this is the seventh.

WHY IT EXISTS AT ALL, which is the more interesting half. `db/035` shipped a
`severity_rank` column and stated drugref's two-grain precedence in a `COMMENT` --
`ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC` -- and then applied it
nowhere. Issue 110 measured the consequence: grepping src/drugref/ for either name
found only comment mentions, so **drugref never applied its own precedence and no test
could regress it**. `db/037` made the rule a view; this module is that
view's caller, because a view with no consumer is half a feature -- the standing rule
this project has now written down after shipping `expansion_policy_unresolved` and
`curated_target_unresolved` with none.

NO PRECEDENCE LOGIC LIVES HERE, and that is the point of reading the `_effective` view
rather than `curated_ddi_pair` plus an ORDER BY. The rule that decides BETWEEN two
grains grading one pair is stated once, in the view, where a consumer querying from any
language gets it too.
"""
import datetime
import uuid
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class GradedPair:
    """One partner drug, with the grade drugref's precedence actually selects.

    `severity_rank` is carried alongside `severity` deliberately: it is the ORDINAL
    (1 = contraindicated) and `severity` is the published word, and `ORDER BY severity`
    sorts `minor` above `moderate`, which is the whole reason db/035 created the
    vocabulary table. NULLABLE, and a NULL means the severity is not in `severity_kind`
    at all -- a SCHEMA FAULT, not a grade, and unreachable while the foreign key stands.

    THRESHOLD ON `effective_rank`, NEVER ON `severity_rank` (db/038, issue 116). A
    client that wants "warn at major or worse" needs a number that is always there:
    every form of that test drops a NULL -- SQL `WHERE severity_rank <= 2` is UNKNOWN,
    `g.severity_rank <= 2` raises TypeError, and `g.severity_rank and ... <= 2` is
    silently False. `effective_rank` is `COALESCE(severity_rank, 0)`, so it is never
    NULL and an unrankable severity ranks 0 -- ABOVE contraindicated = 1, because
    under-warning is the harm direction here.

    THE TWO ARE BOTH PUBLISHED BECAUSE THEY ANSWER DIFFERENT QUESTIONS, and collapsing
    them would be the worse bug. db/037 sorted the unrankable row first, which inside a
    `DISTINCT ON` makes it WIN and discards the rankable competitor outright -- so the
    NULL reached the client with no second row behind it. COALESCEing `severity_rank`
    itself would fix the threshold and destroy the only evidence the schema is broken.
    `drugref status` counts these rows so the fault reaches an operator too.

    `rule_grain` says which grain won, so a client can tell a grade written against
    this drug ('moiety_rule') from one written against its whole class ('class_rule').
    """
    partner_moiety: uuid.UUID
    relationship: str
    severity: str
    severity_rank: int | None
    effective_rank: int
    evidence_grade: str
    mechanism: str | None
    management: str | None
    rule_grain: str
    signature_status: str


# THE ONE COLUMN LIST, generating the SELECT and binding the record BY KEYWORD --
# keys._COLUMNS' shape and curation._UNRESOLVED_COLUMNS' reason, and this is the third
# module to need it. The first draft of this file spelled the nine names twice, in the
# field list above and in the SELECT below, and bound them with `GradedPair(*row)`.
# SEVEN OF THE NINE ARE text OR nullable text, so a transposition builds a WELL-TYPED
# WRONG record that no annotation and no arity check can see: PR #113's review swapped
# `mechanism` and `management` here and the ENTIRE SUITE STAYED GREEN, while drugref
# handed clinical management advice to a client under the label "mechanism".
# `relationship`/`evidence_grade` and `rule_grain`/`signature_status` are the same
# shape. Binding by name removes the failure mode instead of testing for it;
# strict=True catches a column this view gains or loses.
#
# TEN SINCE db/038 added `effective_rank` beside `severity_rank` (issue 116). Two
# adjacent int columns are the worst case this list exists to defend against -- a
# transposition between them is well-typed AND plausible -- and binding by keyword is
# what makes it unrepresentable rather than merely tested.
_COLUMNS = ("partner_moiety", "relationship", "severity", "severity_rank",
            "effective_rank", "evidence_grade", "mechanism", "management",
            "rule_grain", "signature_status")

# `effective_rank` RATHER THAN `severity_rank NULLS FIRST` (db/038, issue 116), and the
# order is IDENTICAL -- 0 precedes 1 exactly as NULLS FIRST placed the NULL. One
# spelling of one rule: db/037 wrote the ordering here and in the view separately, and
# issue 116 is what happened when the two drifted, the sort getting fixed while the
# payload a client thresholds on did not.
_EFFECTIVE_FOR_SUBJECT = f"""
SELECT {', '.join(_COLUMNS)}
FROM   drugref.curated_ddi_pair_effective
WHERE  subject_moiety = %s
ORDER  BY effective_rank, partner_moiety, relationship
"""


def effective_grades_for(conn: psycopg.Connection,
                         subject_moiety: uuid.UUID) -> list[GradedPair]:
    """Every partner drug carrying a live drugref grade against `subject_moiety`.

    Reads `curated_ddi_pair_effective`, so each (partner, relationship) appears ONCE
    with the grade db/035's precedence selects -- most severe first, the moiety grain
    breaking ties. Reading `curated_ddi_pair` instead would hand back both grades when
    the grains disagree and leave this function to choose, which is exactly the
    duplication db/037 removed.

    DIRECTIONAL, and the caller must know it. These rows follow db/006's convention:
    a rule stated as (X, Y) does not answer (Y, X), so a client asking "do these two
    interact" queries BOTH directions. This function deliberately does NOT union them
    itself -- `subject_moiety` is the indexed lookup key the hot path is built around,
    and folding the mirror in here would hide from the caller that two lookups happened.

    THE `ORDER BY` HERE IS NOT THE PRECEDENCE RULE, and the difference is worth being
    precise about because both mention a rank. The precedence chooses BETWEEN two rows
    describing ONE pair, and lives in the view. This orders DIFFERENT pairs against each
    other so the caller sees the most concerning partner first -- a presentation choice
    about a list, not a rule about a conflict. Both sort on `effective_rank` for the
    same harm-direction reason (db/038: an unrankable severity ranks 0 and heads the
    list, where NULLS LAST would have buried it below every real grade);
    `partner_moiety, relationship` after it so the list is TOTALLY ordered and a test
    cannot flake on ties.

    EMPTY IS AN ORDINARY ANSWER: most moieties carry no curated grade at all -- the
    overlay is small and deliberately so -- and that is not distinguishable here from a
    moiety nobody has heard of. A caller needing that distinction asks
    `substance_moiety`; this view's population is grades, not drugs.
    """
    return [GradedPair(**dict(zip(_COLUMNS, row, strict=True))) for row in
            conn.execute(_EFFECTIVE_FOR_SUBJECT, (subject_moiety,)).fetchall()]


@dataclass(frozen=True)
class UnrankableSeverity:
    """One LIVE curated ruling whose severity is absent from `severity_kind`.

    A SCHEMA FAULT, NOT A CURATOR'S MISTAKE, and the distinction decides the wording of
    the block that prints it: a foreign key on both curated tables makes this
    unreachable, so a non-empty result means a dropped constraint, a deleted
    `severity_kind` row, or a restore that lost the vocabulary table.

    `target_table` AND `target_id` rather than a bare count (db/038, issue 116), for the
    reason `UnresolvedTarget` above carries the same pair: the rulings live in two
    append-only tables and an operator who is only told "one exists" has to go and find
    it. `severity` is the offending word itself, which is usually the whole diagnosis.
    """
    target_table: str
    target_id: int
    severity: str
    reviewed_by: str
    reviewed_at: datetime.datetime


# ONE LIST, GENERATING THE SELECT AND BINDING BY KEYWORD -- `_COLUMNS`' reason above,
# and it applies here for a sharper version of it: `target_table`, `severity` and
# `reviewed_by` are three adjacent text columns, so any transposition among them builds
# a well-typed wrong record that no annotation can catch.
_UNRANKABLE_COLUMNS = ("target_table", "target_id", "severity", "reviewed_by",
                       "reviewed_at")

# ORDERED so a test cannot flake and two status runs can be diffed. `target_table` first
# groups the two grains; `target_id` inside it is the insertion order an operator
# working through them would naturally follow.
_UNRANKABLE = f"""
SELECT {', '.join(_UNRANKABLE_COLUMNS)}
FROM   drugref.curated_unrankable_severity
ORDER  BY target_table, target_id
"""


def unrankable_severities(conn: psycopg.Connection) -> list[UnrankableSeverity]:
    """Live curated rulings drugref cannot rank -- EMPTY on a healthy database.

    WHY THIS EXISTS WHEN `effective_rank` ALREADY MAKES THE READ SAFE. `effective_rank`
    stops a thresholding client losing the pair, which is the urgent half, and it also
    makes the fault SILENT: the client gets a usable number and nothing reports that the
    vocabulary table has a hole. Worse, such a row still WINS
    `curated_ddi_pair_effective`'s `DISTINCT ON` and DISCARDS the competing grade, so a
    real `contraindicated` ruling for that pair is suppressed in favour of a severity
    word drugref cannot rank. That is an operator's problem, and issues 74, 76 and
    review I7 are this project's three previous lessons in what a detector with no
    consumer is worth.

    COUNTS RULES, NOT EXPANDED PAIRS. One bad class rule expands to every
    (subject member x object member) pair -- db/035 records ~2,263 for a real one -- and
    an operator fixes the RULE. Reading `curated_ddi_pair` instead would also drag
    `ddi_candidate_pair`'s ~2.7 s scan (issue 75) into every `drugref status`.

    RAISES psycopg's UndefinedTable UNCAUGHT on a database predating db/038. Converting
    that into an operator sentence is the CALLER's job, exactly as it is for
    `curation.unresolved_targets` and `curation.class_grain_counts` -- this module owns
    the read, `cli_status.py` owns the voice.
    """
    return [UnrankableSeverity(**dict(zip(_UNRANKABLE_COLUMNS, row, strict=True)))
            for row in conn.execute(_UNRANKABLE).fetchall()]
