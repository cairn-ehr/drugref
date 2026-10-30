# src/drugref/class_grain_read.py
"""The class grain's detector counts: what `drugref status`'s class-grain block prints.

WHY A MODULE OF ITS OWN (issues 177 and 89). This read lived at the foot of
`curation.py`, the curated overlay's WRITER, and took that file to 555 lines -- over
CLAUDE.md rule 4's ~500-line cap. It was a second job beside the writes and their one
operator-facing read (`curation.unresolved_targets`): it reads three derived views and
has a single consumer, `cli_status.print_class_grain_block`. Moved verbatim, the same
treatment `curated_read.py` got when `curation.py` kept the writes.

THE LAYERING RULE IS UNCHANGED BY THE MOVE: this module owns the read, `cli_status.py`
owns the operator's voice, and no SQL against a curated table lives in a cli_* handler.
"""
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class ClassGrainCounts:
    """The numbers `drugref status`'s fifth block prints (db/035; 111 added the
    denominator, 115 named it).

    ONE RECORD RATHER THAN FOUR RETURN VALUES, so a caller cannot silently transpose
    two ints -- the same reason `curation.UnresolvedTarget` binds by name. Three of
    the four are counts of things that should be ZERO on a healthy database, and they
    are three DIFFERENT failures, so the block says them in three different voices.

    `rules_total` IS THE ONE THAT IS NOT A FAULT COUNT, and it is here because the
    others are useless without it (issue 111). They report only on rules that EXIST, so
    an ONCHIGH re-ingest whose parser yields nothing -- upstream format change,
    truncated download, resolver regression -- empties the tier and silences all three
    at once. The block then renders byte-identically to a healthy, fully-curated
    registry, while `loaded_release` still shows ONCHIGH loaded and the command still
    exits 0. A count with a denominator distinguishes "healthy" from "the detector can
    see nothing"; a bare zero cannot.

    IT DENOMINATES `ungraded` AND `dead`, AND NOTHING ELSE (issue 115, which is why it
    is no longer called `total`). Those two are filters over `class_pair_rule_reach`,
    the same RULE tier `rules_total` counts, so `ungraded <= rules_total` and
    `dead <= rules_total` hold by construction. **`disagreements` counts PAIRS, not
    rules** -- rows in `curated_grain_disagreement`, whose grain is the rule pair over
    `curated_ddi_pair`'s two-grain expansion -- so it is not bounded by `rules_total`
    and never was. One class rule can expand to ~2,263 pairs (db/035), so
    `ClassGrainCounts(rules_total=7, ..., disagreements=2263)` is the EXPECTED shape
    once class-grain content ships, and the obvious `{disagreements} of {rules_total}`
    line a maintainer would write next to the one above it would be wrong by two orders
    of magnitude. The old name made that division read natural; this one makes it read
    wrong, which is the entire point of the rename. (Spelled `rules_total=9` and
    `{total}` until the review of PR #119: the illustration used the very figure db/038
    § 3 was correcting in the same commit, and quoted the removed field name in the line
    arguing the new one reads wrong.)

    `ungraded` AND `dead` ARE DISJOINT, which the field names do not say and a reader
    should not have to reconstruct: `gap_uncurated_class_interaction_rule` omits a rule
    reaching no pair via `HAVING max(max_pair_count) > 0` (#36 -- a review gate must
    only ask what an answer could change), so a dead rule is never also counted
    ungraded.
    """
    rules_total: int
    ungraded: int
    dead: int
    disagreements: int


# DISTINCT ON THE THREE NATURAL-KEY COLUMNS, not count(*): `class_pair_rule_reach`
# inherits the candidate tier's primary key, which includes `source`, so one clinical
# rule asserted by two authorities is two rows there and count(*) would report it twice.
# `gap_uncurated_class_interaction_rule` already groups for this reason, so the other
# two numbers have to as well or the block's own lines disagree about what a rule is --
# db/018's "one quantity stated twice is a quantity that will disagree", on the operator
# surface instead of in the schema.
#
# ONE FORMAT STRING, TWO CALLERS. The denominator (issue 111) and the dead count are
# the same query over the same population differing only by a WHERE, and writing them
# out twice is how they would drift apart. `{where}` is interpolated from the two
# literals below and never from anything reaching this module from outside.
#
# `shared_effective_member_count` IS NAMED TO WIDEN THE GUARD, not because the count
# needs it (PR #113 review). cli.py's rule -- "a migration widening a view a guarded
# block reads must widen the guard in the same commit" -- did not reach db/037, which
# corrects this view's ARITHMETIC while every name read here still resolves under
# db/035. So the guard stayed quiet on a db/035-or-036 database and the block printed
# counts from the old, overstated `max_pair_count`, under-reporting `dead` exactly
# where db/037 section 1 exists to help. Naming a db/037 column makes the existing
# UndefinedColumn arm cover that case.
#
# IT CANNOT CHANGE THE COUNT: every input to this view's arithmetic is a function of
# (subject_class_uuid, object_class_uuid, relationship) alone -- `cpc.source` and
# `cpc.ingest_run` feed none of it -- so the two rows one rule asserted by two
# authorities produces carry identical values here. Tested rather than argued, both
# halves, in tests/test_class_grain_detectors.py.
# THE VIEW NAMES, EXPORTED, so the migration guard in `cli_status.py` probes the
# relations these reads actually name rather than a hand-copied second spelling of them
# (issue 122). One home per name: a rename that missed the guard would leave it
# reporting a healthy database's view permanently absent.
#
# NAMED SINGLY AND THEN COLLECTED, rather than indexed out of the tuple. An earlier
# draft read `CLASS_GRAIN_VIEWS[0]/[1]/[2]`, which gave one tuple two incompatible jobs:
# an UNORDERED set for the guard to probe, and an ORDERED record binding three views to
# three reads. Nothing pinned index to meaning, and `[1]` and `[2]` are both consumed as
# `SELECT count(*) FROM <view>` -- so swapping them raises nothing and silently reports
# the disagreement count as ungraded, in the status block whose entire purpose is those
# numbers. Alphabetising the literal would have been enough to do it. Derived this way
# the tuple's order stops mattering to anything and each read names what it reads.
RULE_REACH_VIEW = "drugref.class_pair_rule_reach"
UNGRADED_RULE_VIEW = "drugref.gap_uncurated_class_interaction_rule"
GRAIN_DISAGREEMENT_VIEW = "drugref.curated_grain_disagreement"

CLASS_GRAIN_VIEWS = (RULE_REACH_VIEW, UNGRADED_RULE_VIEW, GRAIN_DISAGREEMENT_VIEW)


def _rule_count_sql(where: str) -> str:
    """The DISTINCT-rule count, with an optional WHERE. `where` is never external input.

    A FUNCTION RATHER THAN A TEMPLATE CONSTANT, because the constant had to be built as
    an f-string adjacent to a plain string -- `f"... FROM {view}" "{where}) z"` -- so
    that `{where}` survived for a later `.format()`. That is correct and unreadable, and
    a tidying pass unifying the two prefixes would have broken it. Here the guarantee is
    the signature: module-private, two literal call sites below, nothing from outside.
    """
    return ("SELECT count(*) FROM (SELECT DISTINCT subject_class_uuid, "
            "object_class_uuid, relationship, shared_effective_member_count "
            f"FROM {RULE_REACH_VIEW}{where}) z")


def class_grain_counts(conn: psycopg.Connection) -> ClassGrainCounts:
    """Read the class grain's detectors and the denominator they need (db/035, 111).

    IN THIS MODULE RATHER THAN IN `cli.py`, for the rule cli.py's own module docstring
    states and `curation.unresolved_targets` already obeys: a handler must not embed SQL
    against curated, append-only tables, because the sweep that finds readers works
    through `pg_rewrite` and cannot see a query living in a Python string. Two of these
    views are derived from `curated_class_interaction`, so the read belongs here.
    It also keeps cli.py under CLAUDE.md rule 4's size cap, which it had just breached.

    THE DENOMINATOR IS TAKEN FROM `class_pair_rule_reach`, NOT from
    `class_pair_contraindication`, even though the tier is what it counts. The view
    reaches the tier through INNER joins to `ci_axis` and `substance_class` only, both
    of which the tier's own foreign keys guarantee, and every count it adds is a LEFT
    join -- so it drops nothing and is 1:1 with the tier by construction. Reading the
    tier directly would be a second, differently-written statement of one population,
    and it is the NUMERATORS' population that matters here: a denominator that could
    disagree with the numbers beside it is worse than no denominator at all.

    RAISES psycopg's UndefinedTable UNCAUGHT on a database predating db/035. Converting
    that into an operator sentence is the CALLER's job, exactly as it is for
    `curation.unresolved_targets` -- this module owns the read, `cli_status.py` owns
    the voice.
    """
    rules_total = conn.execute(_rule_count_sql("")).fetchone()[0]
    ungraded = conn.execute(
        f"SELECT count(*) FROM {UNGRADED_RULE_VIEW}").fetchone()[0]
    dead = conn.execute(
        _rule_count_sql(" WHERE max_pair_count = 0")).fetchone()[0]
    disagreements = conn.execute(
        f"SELECT count(*) FROM {GRAIN_DISAGREEMENT_VIEW}").fetchone()[0]
    return ClassGrainCounts(rules_total=rules_total, ungraded=ungraded, dead=dead,
                            disagreements=disagreements)
