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
import uuid
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class GradedPair:
    """One partner drug, with the grade drugref's precedence actually selects.

    `severity_rank` is carried alongside `severity` deliberately: it is the ORDINAL
    (1 = contraindicated) and `severity` is the published word, and a client that wants
    to threshold ("warn at major or worse") needs the number -- `ORDER BY severity`
    sorts `minor` above `moderate`, which is the whole reason db/035 created the
    vocabulary table. NULLABLE, and a NULL means the severity is not in
    `severity_kind` at all: unreachable while the foreign key stands, and sorted FIRST
    rather than last if it ever is, because under-warning is the harm direction.

    `rule_grain` says which grain won, so a client can tell a grade written against
    this drug ('moiety_rule') from one written against its whole class ('class_rule').
    """
    partner_moiety: uuid.UUID
    relationship: str
    severity: str
    severity_rank: int | None
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
_COLUMNS = ("partner_moiety", "relationship", "severity", "severity_rank",
            "evidence_grade", "mechanism", "management", "rule_grain",
            "signature_status")

_EFFECTIVE_FOR_SUBJECT = f"""
SELECT {', '.join(_COLUMNS)}
FROM   drugref.curated_ddi_pair_effective
WHERE  subject_moiety = %s
ORDER  BY severity_rank NULLS FIRST, partner_moiety, relationship
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
    precise about because both mention `severity_rank`. The precedence chooses BETWEEN
    two rows describing ONE pair, and lives in the view. This orders DIFFERENT pairs
    against each other so the caller sees the most concerning partner first -- a
    presentation choice about a list, not a rule about a conflict. `NULLS FIRST` for
    the same harm-direction reason either way; `partner_moiety, relationship` after it
    so the list is TOTALLY ordered and a test cannot flake on ties.

    EMPTY IS AN ORDINARY ANSWER: most moieties carry no curated grade at all -- the
    overlay is small and deliberately so -- and that is not distinguishable here from a
    moiety nobody has heard of. A caller needing that distinction asks
    `substance_moiety`; this view's population is grades, not drugs.
    """
    return [GradedPair(**dict(zip(_COLUMNS, row, strict=True))) for row in
            conn.execute(_EFFECTIVE_FOR_SUBJECT, (subject_moiety,)).fetchall()]
