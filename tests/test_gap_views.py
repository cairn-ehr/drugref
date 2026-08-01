# tests/test_gap_views.py
"""The derived gap views (Plan A, db/008).

Design rule: A GAP IS A QUERY, NEVER A REPORT. Generated documents are stale on
write and nobody trusts them; as views these are always current, shrink visibly as
curation lands, and make "how much do we not know" a number watchable per release.

Two of the three are pure views over tables that already exist. The third is not,
and that is worth stating plainly: the unmatched RxCUIs were only ever COUNTED
(`unmatched_rxcuis=len(unmatched)`) and the identities discarded, so making them
queryable needed a persisted table and a change to the ingest path -- not a view.
"""
import re
import uuid

import pytest
import psycopg

from drugref import classes, conditions, ids, indications, questions
from drugref.ingest.mesh_concepts import DESCRIPTOR, MeshRecord


@pytest.fixture(autouse=True)
def _isolate(conn):
    """The gap views read the WHOLE registry, so any moiety or class another module
    committed shows up as a gap here and makes these counts non-deterministic. The
    orchestrator tests (test_medrt_run, test_ingest_run) commit internally, so the
    conn fixture's rollback cannot isolate against them -- truncate first, exactly as
    those modules do for the same reason."""
    conn.execute("TRUNCATE drugref.class_contraindication, drugref.class_membership, "
                 "drugref.class_parent, drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


# The writer implied by each source this module's tests actually open a run
# under (db/025). A test that widened a CHECK to admit a new source would need a
# new entry here too, which is a KeyError rather than a silent NotNullViolation.
_WRITER_BY_SOURCE = {"MED-RT": "medrt_run", "MeSH": "mesh_run"}


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'test', 'deadbeef', %s) RETURNING ingest_run_id",
        (source, _WRITER_BY_SOURCE[source])).fetchone()[0]


def _class(conn, run_id, code, cty="PE", name=None):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "published_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, %s, %s, %s)",
        (cu, code, code, name or f"Class {code} [{cty}]", cty, run_id))
    return cu


def _moiety(conn, run_id, name="testium"):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _member(conn, run_id, moiety, klass, relationship="has_PE"):
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) "
                 "VALUES (%s, %s, %s, %s)", (moiety, klass, relationship, run_id))


def _parent(conn, run_id, child, parent):
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (child, parent, run_id))


def _ci(conn, run_id, moiety, klass, relationship="CI_PE"):
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, 'MED-RT', %s)", (moiety, klass, relationship, run_id))


def _register_condition(conn, ingest_run_id, ui, name, trees=(), record_kind=DESCRIPTOR,
                        scr_class=None):
    """Register one condition through the REAL writer (conditions.upsert_condition),
    not a hand-rolled INSERT -- so these tests exercise the same path an ingest does.

    `scr_class` GOES THROUGH THE WRITER TOO, and it did not always: while
    upsert_condition still ignored the field this helper set it with a follow-up
    UPDATE, which made the view's rare-disease arm pass over a value no ingest could
    ever have produced. The whole 11-row SCRClass = '3' carve-out rides on that one
    column, and its failure mode is a gap view going quiet rather than an error, so
    the scaffolding is gone and the real path is what these tests measure.
    """
    record = MeshRecord(concept_ui=f"M{ui}", record_ui=ui, record_kind=record_kind,
                        name=name, tree_numbers=trees, unii=frozenset(),
                        cas=frozenset(), is_preferred_concept=True,
                        scr_class=scr_class)
    condition_uuid, _ = conditions.upsert_condition(conn, record, ingest_run_id, "MeSH")
    return condition_uuid


def _unmatched(conn, run_id, rxcui, name, reason="classification"):
    """One ingest_unmatched_ingredient row. `reason` is REQUIRED by the table (#39);
    it defaults here only so the tests that predate the discriminator keep reading as
    statements about the view rather than about the column."""
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name, reason) VALUES (%s, %s, %s, %s)",
                 (run_id, rxcui, name, reason))


# ---- gap_unpopulated_contraindication ---------------------------------------


def test_a_contraindication_naming_an_empty_class_is_a_gap(conn):
    """MED-RT asserts the concern and never files a drug under it -- 41 rules across
    13 classes in the 2026.07.06 release. These can never produce a pair under ANY
    expansion policy, which is what makes them the highest-value worklist available:
    upstream authority already vouching that the answer matters."""
    run_id = _run(conn)
    empty = _class(conn, run_id, "N0000000001", name="Renal Arterial Vasoconstriction [PE]")
    _ci(conn, run_id, _moiety(conn, run_id), empty)

    rows = conn.execute("SELECT class_uuid, class_name, ci_rule_count "
                        "FROM drugref.gap_unpopulated_contraindication").fetchall()
    assert rows == [(empty, "Renal Arterial Vasoconstriction [PE]", 1)]


def test_a_populated_class_is_not_a_gap(conn):
    run_id = _run(conn)
    populated = _class(conn, run_id, "N0000000002")
    _member(conn, run_id, _moiety(conn, run_id), populated)
    _ci(conn, run_id, _moiety(conn, run_id, "other"), populated)
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 0


def test_a_member_on_a_DESCENDANT_class_closes_the_gap(conn):
    """'No drug filed under E' means nowhere in E's SUBTREE, not merely directly on E.
    A parent with an empty direct membership but a populated child is not a gap: the
    concern is answerable, just one level down. Getting this wrong would report every
    abstract class in the hierarchy as an open question."""
    run_id = _run(conn)
    parent = _class(conn, run_id, "N0000000003")
    child = _class(conn, run_id, "N0000000004")
    _parent(conn, run_id, child, parent)
    _member(conn, run_id, _moiety(conn, run_id), child)
    _ci(conn, run_id, _moiety(conn, run_id, "other"), parent)

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 0


def test_a_class_whose_ONLY_member_is_the_rules_own_subject_is_a_gap(conn):
    """MEASURED ON THE REAL RELEASE, and unreported until this round: acetohydroxamic
    acid carries a CI_MoA against `Urease Inhibitors [MoA]`, and the only urease
    inhibitor drugref's registry holds is acetohydroxamic acid itself.

    ddi_candidate_pair excludes the subject from its own partners
    (`m.moiety_uuid <> ci.subject_moiety_uuid`) -- a drug is not co-administered with
    itself -- so the rule yields nothing. This view's population test has to ask the
    same question the read path asks: is there a drug below that could BE a partner,
    not merely a drug below. Otherwise it calls the class populated and stays silent
    about a rule that can never fire.
    """
    run_id = _run(conn)
    klass = _class(conn, run_id, "N0000000031", "MoA", name="Urease Inhibitors [MoA]")
    subject = _moiety(conn, run_id, "acetohydroxamic acid")
    _member(conn, run_id, subject, klass, "has_MoA")
    _ci(conn, run_id, subject, klass, "CI_MoA")

    assert conn.execute(
        "SELECT class_name, ci_rule_count FROM "
        "drugref.gap_unpopulated_contraindication").fetchall() == \
        [("Urease Inhibitors [MoA]", 1)]


def test_one_OTHER_member_is_enough_to_close_that_gap(conn):
    """The other half: the subject exclusion must not report a class that does hold a
    partner. A second urease inhibitor makes the rule yield a pair, so the question is
    answered and must leave the worklist."""
    run_id = _run(conn)
    klass = _class(conn, run_id, "N0000000032", "MoA")
    subject = _moiety(conn, run_id, "acetohydroxamic acid")
    _member(conn, run_id, subject, klass, "has_MoA")
    _member(conn, run_id, _moiety(conn, run_id, "other inhibitor"), klass, "has_MoA")
    _ci(conn, run_id, subject, klass, "CI_MoA")

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 0


def test_rules_naming_one_empty_class_are_counted_together(conn):
    """The register is per CLASS, with the rule count as the priority signal --
    Genitourinary Arterial Vasoconstriction carries 7 rules, Renal 6."""
    run_id = _run(conn)
    empty = _class(conn, run_id, "N0000000005")
    for i in range(3):
        _ci(conn, run_id, _moiety(conn, run_id, f"m{i}"), empty)
    assert conn.execute("SELECT ci_rule_count FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 3


def test_a_member_on_the_WRONG_AXIS_does_not_close_the_gap(conn):
    """POPULATED IS PER AXIS. ddi_candidate_pair expands a CI_PE rule over has_PE
    members and nothing else (db/006's ci_axis), so a class whose only members sit on
    has_MoA yields no pair -- and a relationship-blind "does this class have any
    member at all" test would call it populated and HIDE the gap. That is the
    two-lists-in-two-places failure db/006 exists to prevent, and this view must
    consult ci_axis rather than re-deriving the mapping."""
    run_id = _run(conn)
    klass = _class(conn, run_id, "N0000000010")
    _member(conn, run_id, _moiety(conn, run_id), klass, "has_MoA")
    _ci(conn, run_id, _moiety(conn, run_id, "subj"), klass, "CI_PE")

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 1


def test_the_matching_axis_is_what_closes_the_gap(conn):
    """The other half of the pair above: same shape, correct axis, no gap. Asserted
    so the fix cannot be "report everything" -- that would pass the test above while
    burying the real gaps, which is the failure mode the subtree descent avoids."""
    run_id = _run(conn)
    klass = _class(conn, run_id, "N0000000011")
    _member(conn, run_id, _moiety(conn, run_id), klass, "has_MoA")
    _ci(conn, run_id, _moiety(conn, run_id, "subj"), klass, "CI_MoA")

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 0


def test_only_the_dead_rules_on_a_partly_populated_class_are_counted(conn):
    """A class can carry rules on BOTH axes. With has_PE members present and has_MoA
    absent, the CI_PE rules can yield pairs and the CI_MoA one cannot -- so the count
    is the dead rules only. Counting all of them would overstate the worklist; not
    listing the class at all would lose a real gap."""
    run_id = _run(conn)
    klass = _class(conn, run_id, "N0000000012")
    _member(conn, run_id, _moiety(conn, run_id), klass, "has_PE")
    _ci(conn, run_id, _moiety(conn, run_id, "a"), klass, "CI_PE")
    _ci(conn, run_id, _moiety(conn, run_id, "b"), klass, "CI_MoA")

    assert conn.execute("SELECT ci_rule_count FROM "
                        "drugref.gap_unpopulated_contraindication").fetchall() == [(1,)]


# ---- gap_unclassified_moiety ------------------------------------------------


def test_a_moiety_with_no_PE_membership_is_a_gap(conn):
    """Structurally unable to participate in an effect-accumulation model: nothing
    can ever accumulate for a drug no effect class contains."""
    run_id = _run(conn)
    m = _moiety(conn, run_id, "orphanium")
    rows = conn.execute("SELECT moiety_uuid, display_name FROM "
                        "drugref.gap_unclassified_moiety").fetchall()
    assert rows == [(m, "orphanium")]


def test_a_moiety_with_a_PE_membership_is_not_a_gap(conn):
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    _member(conn, run_id, m, _class(conn, run_id, "N0000000006"), "has_PE")
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unclassified_moiety").fetchone()[0] == 0


def test_a_moiety_with_only_a_MoA_membership_is_still_a_gap(conn):
    """PE is the convergence axis the accumulation model needs; a drug classified on
    mechanism alone still cannot participate in an effect that adds up."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    _member(conn, run_id, m, _class(conn, run_id, "N0000000007", "MoA"), "has_MoA")
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unclassified_moiety").fetchone()[0] == 1


# ---- gap_unmatched_ingredient (needs the persisted table) --------------------


def test_an_unmatched_rxcui_is_queryable(conn):
    """The identities, not merely the count. MED-RT classifies far more ingredients
    than pass drugref's moiety gate, and each one is a drug the registry cannot say
    anything about -- which is a question, not a silent statistic."""
    run_id = _run(conn)
    _unmatched(conn, run_id, "5640", "ibuprofen")
    rows = conn.execute("SELECT rxcui, name FROM "
                        "drugref.gap_unmatched_ingredient").fetchall()
    assert rows == [("5640", "ibuprofen")]


def test_an_rxcui_the_registry_later_carries_is_no_longer_a_gap(conn):
    """The view is the join, not the stored row: once a moiety claims the RxCUI the
    gap closes without anyone rewriting the ingest table."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    conn.execute("INSERT INTO drugref.identity_claim "
                 "(moiety_uuid, scheme, value, ingest_run) "
                 "VALUES (%s, 'RXNORM_IN', '5640', %s)", (m, run_id))
    _unmatched(conn, run_id, "5640", "ibuprofen")
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unmatched_ingredient").fetchone()[0] == 0


def test_one_run_cannot_store_an_rxcui_twice_for_one_reason(conn):
    """The (ingest_run, reason, rxcui) primary key. Named for what it actually
    asserts: replacement ACROSS runs is a different property and is tested against the
    real ingest in test_medrt_run.py, which is the only place it can be exercised."""
    run_id = _run(conn)
    _unmatched(conn, run_id, "5640", "ibuprofen")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _unmatched(conn, run_id, "5640", "ibuprofen")


def test_the_reason_must_be_DECLARED(conn):
    """NOT NULL with NO DEFAULT (#39), the discipline db/014 gave
    condition_ci_axis.expands_descendants.

    The reason scopes the per-writer clear, so a default would let a new writer insert
    rows into another writer's bucket -- which is #39 itself, silently restored. A
    writer that does not say why it is reporting an RxCUI aborts its ingest instead.
    """
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                     "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')",
                     (run_id,))


def test_an_unknown_reason_is_refused(conn):
    """The clear branches on this literal, exactly as class_expansion_policy.decision
    does: a row spelled 'contraindications' would be cleared by nobody and accumulate
    forever."""
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        _unmatched(conn, run_id, "5640", "ibuprofen", reason="typo")


def test_the_python_reason_constants_are_exactly_what_the_CHECK_admits(conn):
    """Two statements of one vocabulary -- classes.REASONS and the table's CHECK --
    and the clears are scoped on it, so a value in one and not the other is a bucket
    nobody clears (rows accumulate forever) or a writer that cannot insert at all.

    Pinned rather than trusted, because #47 will add a FOURTH value -- slice 5b.2 took
    the third ('indication', db/019 section 7) -- and the two places are in different
    languages, five files apart.
    """
    definition = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'ingest_unmatched_ingredient_reason'").fetchone()[0]
    admitted = set(re.findall(r"'([a-z_]+)'::text", definition))
    assert admitted == set(classes.REASONS)


def test_one_rxcui_reported_for_BOTH_reasons_is_still_one_gap(conn):
    """The grain the discriminator creates, and the grain the curator reads.

    An RxCUI can be both classified-but-uncarried and contraindicated-but-uncarried;
    those are two writers' rows, which is the whole point of #39. It is still ONE
    question -- gap_key is an input to question_uuid, so two view rows would mint one
    question and register_from_gaps would over-report its own live count.
    """
    run_id = _run(conn)
    _unmatched(conn, run_id, "5640", "ibuprofen", reason="classification")
    _unmatched(conn, run_id, "5640", "ibuprofen", reason="contraindication")

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unmatched_ingredient").fetchone()[0] == 1


def test_which_of_one_runs_two_reason_rows_wins_is_DECIDED(conn):
    """db/008's tie-break was TOTAL under the old (ingest_run, rxcui) key: one run
    held at most one row per RxCUI, so `ORDER BY rxcui, ingest_run DESC` named a
    unique row. The discriminator widened the grain underneath it, and DISTINCT ON
    with a tie keeps whichever row the plan emits first -- so `name` could flip with
    no data change.

    db/018 adds `reason` to the ORDER BY. Asserted on the NAME, not merely on the
    count above: a count of one is satisfied by either row, which is exactly why the
    non-determinism could sit unnoticed under a passing test.
    """
    run_id = _run(conn)
    _unmatched(conn, run_id, "5640", "ibuprofen", reason="classification")
    _unmatched(conn, run_id, "5640", None, reason="contraindication")

    assert conn.execute("SELECT rxcui, name FROM "
                        "drugref.gap_unmatched_ingredient").fetchall() == \
        [("5640", "ibuprofen")]


def test_an_rxcui_two_sources_both_report_is_one_gap(conn):
    """clear_source_unmatched_ingredients clears ONE source, so the moment a second
    source reports unmatched ingredients the same RxCUI is stored twice. The view has
    to collapse them: gap_key is an input to question_uuid, so two rows here mint one
    question and register_from_gaps would over-report its own live count. The row
    kept is the most recent run's."""
    older, newer = _run(conn), _run(conn, source="MeSH")
    _unmatched(conn, older, "5640", None)
    _unmatched(conn, newer, "5640", "ibuprofen")

    assert conn.execute("SELECT rxcui, name FROM "
                        "drugref.gap_unmatched_ingredient").fetchall() == [("5640", "ibuprofen")]


# ---- gap_unreviewed_expansion_root -------------------------------------------
#
# The review gate for Plan B's deny-list. A curated list of abstract roots rots the
# first time upstream adds one, so §7's own rule supplies the mechanism: a gap is a
# query, never a report. A new large CI object class surfaces here as an open
# question instead of silently fanning out.
#
# The >20-descendant-CLASSES threshold is a DISCOVERY HEURISTIC for the worklist,
# never the criterion for denying expansion -- that judgement is qualitative and
# lives in class_expansion_policy. Size is only how the original fourteen were
# found, and the boundary test below pins it so a retune is a deliberate act.

DISCOVERY_THRESHOLD = 20


def _wide_root(conn, run_id, code, descendants, with_ci=True):
    """A class with `descendants` classes beneath it, optionally carrying a CI rule."""
    root = _class(conn, run_id, code, name=f"Root {code} [PE]")
    base = int(code[1:])
    for i in range(descendants):
        child = _class(conn, run_id, f"N{base + i + 1:010d}")
        _parent(conn, run_id, child, root)
    if with_ci:
        _ci(conn, run_id, _moiety(conn, run_id, "subj"), root)
    return root


def _policy(conn, code, decision):
    conn.execute(
        "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
        "class_name, rationale, reviewed_by, reviewed_against) "
        "VALUES ('MED-RT', %s, %s, 'X', 'because', 'test', '2026.07.06')",
        (code, decision))


def _unreviewed(conn):
    return conn.execute(
        "SELECT class_uuid, descendant_class_count, ci_rule_count "
        "FROM drugref.gap_unreviewed_expansion_root").fetchall()


def test_a_large_unreviewed_contraindicated_class_is_a_gap(conn):
    """The rot this view exists to catch: the next MED-RT release adds an abstract
    organ-system root, a rule names it, and nothing on the deny-list stops the
    fan-out. It should arrive as a question for a pharmacist, not as 1,200 pairs."""
    run_id = _run(conn)
    root = _wide_root(conn, run_id, "N0000100000", 25)
    assert _unreviewed(conn) == [(root, 25, 1)]


def test_denying_a_root_takes_it_off_the_worklist(conn):
    run_id = _run(conn)
    _wide_root(conn, run_id, "N0000200000", 25)
    _policy(conn, "N0000200000", "deny")
    assert _unreviewed(conn) == []


def test_allowing_a_root_takes_it_off_the_worklist_too(conn):
    """`allow` and `deny` differ for the pair set and agree here: both mean REVIEWED,
    and this view asks only whether a human has looked. Three of the fourteen seeded
    roots are `allow` precisely so they stop being asked about."""
    run_id = _run(conn)
    _wide_root(conn, run_id, "N0000300000", 25)
    _policy(conn, "N0000300000", "allow")
    assert _unreviewed(conn) == []


def test_a_class_at_the_threshold_is_not_yet_asked_about(conn):
    """The boundary, pinned in both directions so retuning the heuristic is a
    deliberate act rather than a drift. `Decreased Coagulation Activity` has 6
    descendant classes and 109 drugs beneath it: it must expand silently, never
    appear here."""
    run_id = _run(conn)
    _wide_root(conn, run_id, "N0000400000", DISCOVERY_THRESHOLD)
    assert _unreviewed(conn) == []

    over = _wide_root(conn, run_id, "N0000500000", DISCOVERY_THRESHOLD + 1)
    assert _unreviewed(conn) == [(over, DISCOVERY_THRESHOLD + 1, 1)]


def test_a_large_class_no_contraindication_names_is_not_a_gap(conn):
    """The question is about expansion POLICY, and a class no rule names expands
    nothing. Asking about every large class in a 3,634-class DAG would bury the
    handful that matter."""
    run_id = _run(conn)
    _wide_root(conn, run_id, "N0000600000", 30, with_ci=False)
    assert _unreviewed(conn) == []


def test_the_rule_count_is_the_priority_signal(conn):
    """How many contraindications ride on the decision is what tells a reviewer which
    root to look at first -- Hematologic Activity Alteration carries 15."""
    run_id = _run(conn)
    root = _wide_root(conn, run_id, "N0000700000", 25)
    _ci(conn, run_id, _moiety(conn, run_id, "second"), root)
    assert _unreviewed(conn) == [(root, 25, 2)]


def test_a_root_only_non_expanding_predicates_name_is_not_asked_about(conn):
    """db/012. The question this view asks is "SHOULD this expand?", so a class named
    only by predicates that do not expand at all has nothing riding on the answer --
    and a decision recorded against it could not change one row of
    ddi_candidate_pair.

    db/008's gap_unpopulated_contraindication joins ci_axis for precisely this reason
    ("populated is per axis, not per class"); db/010 shipped this view axis-blind, so
    once slice 5b lands a MeSH-keyed predicate with expands_descendants false, a moot
    question would reach a pharmacist's worklist AND be minted an immortal
    question_uuid that no available decision retires."""
    run_id = _run(conn)
    root = _wide_root(conn, run_id, "N0000800000", 25)
    assert _unreviewed(conn) == [(root, 25, 1)]

    conn.execute("UPDATE drugref.ci_axis SET expands_descendants = false "
                 "WHERE relationship = 'CI_PE'")
    assert _unreviewed(conn) == []


def test_the_rule_count_counts_only_the_rules_that_actually_expand(conn):
    """The count is the priority signal, so it has to mean "rules whose reach the
    decision changes". A class named by one expanding and one non-expanding predicate
    stays on the worklist -- but weighted by the expanding rule alone."""
    run_id = _run(conn)
    root = _wide_root(conn, run_id, "N0000810000", 25)          # one CI_PE rule
    _ci(conn, run_id, _moiety(conn, run_id, "moa"), root, "CI_MoA")
    assert _unreviewed(conn) == [(root, 25, 2)]

    conn.execute("UPDATE drugref.ci_axis SET expands_descendants = false "
                 "WHERE relationship = 'CI_MoA'")
    assert _unreviewed(conn) == [(root, 25, 1)]


# ---- gap_dead_by_expansion_policy (#31) --------------------------------------
#
# The residue Plan B left. A contraindication whose object class is DENIED expands to
# DIRECT members only; if the class has none on the rule's axis, the rule yields no
# pair at all -- and until this view, nothing said so:
#
#   * gap_unpopulated_contraindication tests population over the whole SUBTREE, so it
#     calls the class populated and stays silent (a drug IS filed somewhere below);
#   * gap_unreviewed_expansion_root is silent too -- the class HAS been reviewed, and
#     the deny is the point.
#
# NOT A REGRESSION, A RESIDUE: these rules returned nothing before Plan B as well,
# when the view expanded over direct membership only. Plan B closed the hole
# everywhere except under a denied root.
#
# MEASURED against the real 2026.07.06 release at db/017: ONE class, `Endocrine
# Activity Alteration [PE]` -- 1 rule, 0 direct has_PE members, 300 distinct drugs in its
# subtree, 0 pairs. Issue #31 recorded TWO (`Cardiovascular Activity Alteration [PE]`
# was the other); it is no longer dead, because the #34 moiety-gate fix gave it 7
# direct members. The issue text predates that fix -- re-measure before quoting it.
#
# RE-MEASURED after the review put the subject exclusion on BOTH counts (#50): still
# ONE class and one rule -- neither shape the exclusion changes occurs in this release
# -- but 299 drugs held back, not 300. The subject, clomiphene, is itself filed under
# the class. The two tests below named for the subject pin the shapes that would move
# it on some other release.


def _dead_by_policy(conn):
    return conn.execute(
        "SELECT class_uuid, class_name, ci_rule_count, subtree_partner_count "
        "FROM drugref.gap_dead_by_expansion_policy").fetchall()


def _denied_root_with_members_only_below(conn, run_id, code="N0000900000"):
    """The Endocrine Activity Alteration shape: denied, no direct member, drugs below.

    A SYNTHETIC NUI, not the real N0000009036: db/010 seeds fourteen real policy rows
    and class_expansion_policy is curator data that no ingest -- and no autouse
    TRUNCATE here -- clears, so a fixture reusing a seeded code collides with it. The
    class NAME is the real one, because that is what the assertions read.
    """
    root = _class(conn, run_id, code, name="Endocrine Activity Alteration [PE]")
    child = _class(conn, run_id, "N0000900001")
    _parent(conn, run_id, child, root)
    _member(conn, run_id, _moiety(conn, run_id, "below"), child)
    _ci(conn, run_id, _moiety(conn, run_id, "subj"), root)
    _policy(conn, code, "deny")
    return root


def test_a_denied_root_with_no_direct_member_is_a_gap(conn):
    """THE CASE #31 EXISTS FOR. Upstream vouches the concern matters, the deny-list
    vouches that expansion is not the answer, and the rule reaches nobody. That is a
    genuine, actionable open question -- arguably a better curation target than most,
    which is why drugref publishes it rather than leaving it silent."""
    run_id = _run(conn)
    root = _denied_root_with_members_only_below(conn, run_id)
    assert _dead_by_policy(conn) == \
        [(root, "Endocrine Activity Alteration [PE]", 1, 1)]


def test_the_rule_yields_no_pair_at_all(conn):
    """The premise the whole view rests on, asserted rather than assumed: if
    ddi_candidate_pair returned something here, this would be noise on a worklist."""
    run_id = _run(conn)
    _denied_root_with_members_only_below(conn, run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()[0] == 0


def test_a_denied_root_WITH_a_direct_member_is_not_a_gap(conn):
    """A deny does not make a rule dead -- it makes it direct-only. With a direct
    member the rule still pairs, which is exactly `Cardiovascular Activity Alteration`
    after the #34 gate fix gave it 7."""
    run_id = _run(conn)
    root = _denied_root_with_members_only_below(conn, run_id)
    _member(conn, run_id, _moiety(conn, run_id, "direct"), root)
    assert _dead_by_policy(conn) == []


def test_an_ALLOWED_root_is_never_dead_by_policy(conn):
    """The view is named for the CAUSE, so it must not report a class whose rules die
    of something else. An allowed root expands, so its members below are reachable."""
    run_id = _run(conn)
    root = _class(conn, run_id, "N0000900010")
    child = _class(conn, run_id, "N0000900011")
    _parent(conn, run_id, child, root)
    _member(conn, run_id, _moiety(conn, run_id, "below"), child)
    _ci(conn, run_id, _moiety(conn, run_id, "subj"), root)
    _policy(conn, "N0000900010", "allow")
    assert _dead_by_policy(conn) == []


def test_an_UNREVIEWED_root_is_not_dead_either(conn):
    """Absent is not denied. COALESCE(decision,'allow') in ddi_candidate_pair means an
    unreviewed class EXPANDS -- unreviewed is the recall-safe default, and
    gap_unreviewed_expansion_root is what asks about it."""
    run_id = _run(conn)
    root = _class(conn, run_id, "N0000900020")
    child = _class(conn, run_id, "N0000900021")
    _parent(conn, run_id, child, root)
    _member(conn, run_id, _moiety(conn, run_id, "below"), child)
    _ci(conn, run_id, _moiety(conn, run_id, "subj"), root)
    assert _dead_by_policy(conn) == []


def test_an_EMPTY_denied_subtree_belongs_to_the_OTHER_view(conn):
    """THE PARTITION THAT KEEPS ONE DEAD RULE FROM MINTING TWO QUESTIONS.

    If nothing is filed anywhere below, gap_unpopulated_contraindication already
    reports the class and the remedy is to populate it. Reporting it here as well
    would ask a second question -- "reconsider the deny?" -- whose answer changes
    nothing, because allowing expansion over an empty subtree reaches no one.

    Plan A tolerates a class raising two questions only when they are INDEPENDENTLY
    ANSWERABLE (unpopulated + unreviewed_expansion_root). These are not.
    """
    run_id = _run(conn)
    root = _class(conn, run_id, "N0000900030")
    _ci(conn, run_id, _moiety(conn, run_id, "subj"), root)
    _policy(conn, "N0000900030", "deny")

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 1
    assert _dead_by_policy(conn) == []


def test_a_CLASS_may_appear_in_both_views_when_its_rules_die_differently(conn):
    """The limit of the partition, stated so nobody "fixes" it. NO RULE raises both
    questions -- the two views are `= 0` and `> 0` on one column. A CLASS can, when
    two of its rules die of different causes, and that is Plan A's
    independently-answerable case rather than a double-count.

    Here one drug sits below a denied class. The rule whose subject IS that drug has
    no possible partner at all and wants "file a drug under this class"; the rule
    whose subject is someone else has a partner the deny is holding back and wants
    "revisit the deny". Both answers are real, and neither retires the other.
    """
    run_id = _run(conn)
    root = _class(conn, run_id, "N0000900070", name="Denied Root [PE]")
    child = _class(conn, run_id, "N0000900071")
    _parent(conn, run_id, child, root)
    only_drug_below = _moiety(conn, run_id, "the only drug below")
    _member(conn, run_id, only_drug_below, child)
    _ci(conn, run_id, only_drug_below, root)                    # no partner anywhere
    _ci(conn, run_id, _moiety(conn, run_id, "other subj"), root)  # one, denied away
    _policy(conn, "N0000900070", "deny")

    assert conn.execute(
        "SELECT class_name, ci_rule_count FROM "
        "drugref.gap_unpopulated_contraindication").fetchall() == \
        [("Denied Root [PE]", 1)]
    assert _dead_by_policy(conn) == [(root, "Denied Root [PE]", 1, 1)]


def test_the_direct_membership_test_is_PER_AXIS(conn):
    """Same rule as gap_unpopulated_contraindication's: ddi_candidate_pair pairs a
    CI_PE rule over has_PE members and nothing else, so a direct member on has_MoA
    does not save the rule -- and an axis-blind test would call it alive and hide the
    gap."""
    run_id = _run(conn)
    root = _denied_root_with_members_only_below(conn, run_id)
    _member(conn, run_id, _moiety(conn, run_id, "wrong axis"), root, "has_MoA")
    assert [r[0] for r in _dead_by_policy(conn)] == [root]


def test_a_non_expanding_predicate_is_not_asked_about(conn):
    """db/012's rule, applied in a fourth place. The question here is "should this deny
    be reconsidered?" -- and for a predicate that does not expand at all, ALLOWING
    expansion would change nothing, so no available decision retires the question. A
    dead rule of that shape is real but has a different remedy, and reporting it under
    this name would attribute it to a policy that did not cause it."""
    run_id = _run(conn)
    _denied_root_with_members_only_below(conn, run_id)
    conn.execute("UPDATE drugref.ci_axis SET expands_descendants = false "
                 "WHERE relationship = 'CI_PE'")
    assert _dead_by_policy(conn) == []


def test_dead_rules_on_one_class_are_counted_together(conn):
    """Per CLASS, because the decision is per class -- the same grain, and the same
    priority signal, as gap_unpopulated_contraindication."""
    run_id = _run(conn)
    root = _denied_root_with_members_only_below(conn, run_id)
    _ci(conn, run_id, _moiety(conn, run_id, "second"), root)
    assert _dead_by_policy(conn) == \
        [(root, "Endocrine Activity Alteration [PE]", 2, 1)]


def test_the_subtree_partner_count_is_what_the_deny_costs(conn):
    """The number a curator weighs: how many drugs the deny is holding back. 299 for
    Endocrine Activity Alteration on the real release -- large enough that `allow` is
    probably the wrong answer, which is exactly the judgement this view hands over
    rather than making."""
    run_id = _run(conn)
    root = _denied_root_with_members_only_below(conn, run_id)
    child = conn.execute(
        "SELECT child_class_uuid FROM drugref.class_parent").fetchone()[0]
    _member(conn, run_id, _moiety(conn, run_id, "another"), child)
    assert _dead_by_policy(conn) == \
        [(root, "Endocrine Activity Alteration [PE]", 1, 2)]


def test_the_partner_count_does_not_count_the_rules_own_subject(conn):
    """PARTNERS, not members. The count drives the question text, and a curator reads
    it as "this many drugs would become reachable if I revisited the deny" -- so a
    subject counted among them overstates the case for `allow` by one, on exactly the
    rules where the margin is thin enough to matter.

    Here the subject itself is filed below the denied root alongside one other drug:
    two members, one partner.
    """
    run_id = _run(conn)
    root = _class(conn, run_id, "N0000900040", name="Denied Root [PE]")
    child = _class(conn, run_id, "N0000900041")
    _parent(conn, run_id, child, root)
    subject = _moiety(conn, run_id, "subj")
    _member(conn, run_id, subject, child)                        # not a partner
    _member(conn, run_id, _moiety(conn, run_id, "other"), child)  # the only partner
    _ci(conn, run_id, subject, root)
    _policy(conn, "N0000900040", "deny")

    assert _dead_by_policy(conn) == [(root, "Denied Root [PE]", 1, 1)]


def test_a_denied_root_whose_only_DIRECT_member_is_the_SUBJECT_is_dead(conn):
    """THE SILENT SHAPE THE REVIEW OF THIS ROUND FOUND, and the reason the subject
    exclusion had to live in ONE place rather than in each view that needs it.

    A deny leaves DIRECT membership alone, so a direct member normally keeps a rule
    alive. Not when that member is the rule's own subject: ddi_candidate_pair excludes
    it, the descendants are out of reach, and the rule pairs with nobody. The first
    draft of this view tested `NOT EXISTS (a direct member)` subject-blind, so it saw
    a direct member and stayed silent -- while gap_unpopulated_contraindication, whose
    subtree test HAD learned the exclusion, saw partners below and stayed silent too.
    A dead rule reported by nothing, which is the exact failure #31 was filed about.
    """
    run_id = _run(conn)
    root = _class(conn, run_id, "N0000900050", name="Denied Root [PE]")
    child = _class(conn, run_id, "N0000900051")
    _parent(conn, run_id, child, root)
    subject = _moiety(conn, run_id, "subj")
    _member(conn, run_id, subject, root)                          # DIRECT, the subject
    _member(conn, run_id, _moiety(conn, run_id, "below"), child)  # denied out of reach
    _ci(conn, run_id, subject, root)
    _policy(conn, "N0000900050", "deny")

    assert conn.execute(
        "SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()[0] == 0
    assert _dead_by_policy(conn) == [(root, "Denied Root [PE]", 1, 1)]


def test_a_denied_root_whose_only_PARTNER_would_be_the_subject_is_the_OTHER_VIEWS(conn):
    """The partition again, in the shape that first broke it.

    A subtree holding nothing but the rule's own subject has no partner in it, so
    allowing expansion would reach nobody and the only answerable question is
    gap_unpopulated_contraindication's "file a drug under this class". While this
    view's reach test was subject-blind it counted that subject as a drug held back
    and asked a second, unanswerable question about the same dead rule.

    Both views now read one column: `= 0` there, `> 0` here.
    """
    run_id = _run(conn)
    root = _class(conn, run_id, "N0000900060", name="Denied Root [PE]")
    child = _class(conn, run_id, "N0000900061")
    _parent(conn, run_id, child, root)
    subject = _moiety(conn, run_id, "subj")
    _member(conn, run_id, subject, child)   # the ONLY member anywhere below
    _ci(conn, run_id, subject, root)
    _policy(conn, "N0000900060", "deny")

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 1
    assert _dead_by_policy(conn) == []


def test_a_dead_by_policy_class_becomes_a_question(conn, ingest_run_id):
    """The sixth gap kind. The question names the class and says what the decision
    governs, so it is answerable without opening a database."""
    root = _denied_root_with_members_only_below(conn, ingest_run_id)
    counts = questions.register_from_gaps(conn, ingest_run_id)
    assert counts["dead_by_expansion_policy"] == 1

    gap_key, text = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'dead_by_expansion_policy'").fetchone()
    assert gap_key == f"CLASS:{root}"
    assert "Endocrine Activity Alteration [PE]" in text


# ---- gap_unresolved_ci_object -------------------------------------------------
#
# The review gate for the class arm slice 5b deliberately withholds. CI_ChemClass's
# class arm (405 assertions over 103 MeSH chemical classes, MEASURED against the real
# 2026.07.06 release) is real upstream safety content drugref does not ingest --
# expanding it over MeSH's STRUCTURAL chemical tree would make a rule on Sulfonamides
# reach bendroflumethiazide and bosentan, the discredited sulfa cross-reactivity
# inference. Withholding it is the right call; withholding it silently is not, so each
# withheld object becomes a citable question.
#
# ON 103, NOT 108. 108 counts MeSH ConceptUIs; this worklist is keyed on the MeSH
# RECORD ui, because ONE RECORD IS ONE CURATOR DECISION, and five records are each
# named by two withheld concepts. db/014 and db/016 were corrected to 103 before merge
# -- the checksum ledger binds a DATABASE, not the repo, so a migration no database
# outside a disposable local one has ever seen is still editable; immutability starts
# at merge. The slice-5b SPEC still says 108 and cannot be corrected at all (specs
# under docs/superpowers/specs/ are immutable by project rule), so the standing
# correction for it is the docs-site decision record "A structural chemical tree is
# not a clinical class".


def test_unresolved_ci_object_becomes_a_question(conn, ingest_run_id):
    """The 405 withheld CI_ChemClass assertions are PUBLISHED as questions, not
    dropped -- Plan B's precedent, where a pharmacist ruled on each expansion root
    before drugref expanded over it."""
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, object_kind, "
        "assertion_count) VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D013449',"
        "'Sulfonamides','CHEMICAL_CLASS',36)",
        (ingest_run_id,))
    counts = questions.register_from_gaps(conn, ingest_run_id)
    assert counts["unresolved_ci_object"] == 1

    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ci_object'").fetchone()
    assert row[0] == "MESH:D013449"
    assert "Sulfonamides" in row[1]
    assert "36" in row[1]
    assert "structural tree" in row[1]


def test_an_unregistered_substance_gets_the_other_question(conn, ingest_run_id):
    """THE CATEGORY ERROR THIS GUARDS. Both object kinds sit on one worklist, but a
    substance drugref simply does not carry must NOT be asked the class question.

    Before object_kind existed, every unresolved object got the class text, so a run
    against a registry missing Pimozide -- a leaf drug descriptor with nothing beneath
    it at all -- asked a curator whether contraindications naming it should "be
    expanded to the drugs beneath it in MeSH's structural tree". The remedy for this
    kind is to register the moiety; saying so is the whole point of the split.
    """
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, object_kind, "
        "assertion_count) VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D010868',"
        "'Pimozide','UNREGISTERED_SUBSTANCE',1)",
        (ingest_run_id,))
    questions.register_from_gaps(conn, ingest_run_id)
    text = conn.execute(
        "SELECT question_text FROM drugref.open_question "
        "WHERE gap_key = 'MESH:D010868'").fetchone()[0]
    assert "Pimozide" in text
    assert "registers no moiety" in text
    # The class question, and only it, offers tree expansion as the remedy.
    assert "be expanded to the drugs beneath it" not in text


def test_an_unhandled_object_kind_aborts_rather_than_mislabels(conn, ingest_run_id):
    """The CASE in questions.py has no ELSE, deliberately.

    A third object_kind added without its own question text yields NULL, and
    open_question.question_text is NOT NULL -- so the ingest dies loudly at the
    register step instead of handing a curator a confidently wrong sentence. That is
    the force-a-declaration discipline db/014 gave condition_ci_axis, applied to the
    consumer side. The CHECK is widened inside this test's transaction only; the
    `conn` fixture rolls it back.
    """
    conn.execute("ALTER TABLE drugref.ingest_unresolved_ci_object "
                 "DROP CONSTRAINT ingest_unresolved_ci_object_kind")
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, object_kind, "
        "assertion_count) VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D999999',"
        "'Something New','A_THIRD_KIND',7)",
        (ingest_run_id,))
    with pytest.raises(psycopg.errors.NotNullViolation):
        questions.register_from_gaps(conn, ingest_run_id)


def test_unresolved_ci_object_question_uuid_is_stable(conn, ingest_run_id):
    """Re-running an ingest must not re-mint the question: external tools cite it."""
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, object_kind, "
        "assertion_count) VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D013449',"
        "'Sulfonamides','CHEMICAL_CLASS',36)",
        (ingest_run_id,))
    questions.register_from_gaps(conn, ingest_run_id)
    first = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind='unresolved_ci_object'").fetchone()[0]
    questions.register_from_gaps(conn, ingest_run_id)
    second = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind='unresolved_ci_object'").fetchone()[0]
    assert first == second
    assert first == ids.mint_question_uuid("unresolved_ci_object", "MESH:D013449")


# ---- the object's NAMESPACE is part of its identity (issue #41, db/017) -------
#
# The worklist is keyed (ingest_run, source, relationship, object_source,
# object_code) precisely because a future authority can name an object outside
# MeSH. The view collapsed that onto object_code alone and questions.py hardcoded
# the 'MESH:' prefix a second time, so the assumption had to be broken in BOTH
# places or it was not broken at all -- and the Python half is invisible to any
# migration-only fix.
#
# What rides on it: an object code is not namespace-unique in general, so two
# different objects would fold into one row with sum() attributing one authority's
# rules to the other's object -- and, worse, would mint ONE question_uuid. Since
# question_state and question_evidence are append-only and keyed off that UUID, a
# curator decision recorded against one object would be permanently attached to
# the other's. That half no rebuild can repair.


def _insert_unresolved(conn, ingest_run_id, *, object_source, object_code, name,
                       kind="CHEMICAL_CLASS", count=1, relationship="CI_ChemClass"):
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, object_kind, "
        "assertion_count) VALUES (%s,'MED-RT',%s,%s,%s,%s,%s,%s)",
        (ingest_run_id, relationship, object_source, object_code, name, kind, count))


def test_two_authorities_naming_the_same_code_stay_two_objects(conn, ingest_run_id):
    """The collision, made concrete: one code, two namespaces, two objects.

    'MeSH' reads back as 'MESH' because the view publishes the namespace already
    canonicalised -- see test_the_views_grain_is_the_gap_keys_grain for why that is
    the grouping key and not a cosmetic choice. What THIS test pins is orthogonal:
    two DIFFERENT namespaces are never folded together, whatever their spelling.
    """
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=36)
    _insert_unresolved(conn, ingest_run_id, object_source="CHEBI",
                       object_code="D013449", name="Something Else", count=4)

    rows = conn.execute(
        "SELECT object_source, object_name, ci_rule_count "
        "FROM drugref.gap_unresolved_ci_object ORDER BY object_source").fetchall()
    assert rows == [("CHEBI", "Something Else", 4), ("MESH", "Sulfonamides", 36)]


def test_two_authorities_naming_the_same_code_get_two_questions(conn, ingest_run_id):
    """The half a migration alone cannot fix. The gap_key must carry the namespace,
    or the two objects share one immortal, externally-citable question_uuid."""
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=36)
    _insert_unresolved(conn, ingest_run_id, object_source="CHEBI",
                       object_code="D013449", name="Something Else", count=4)

    questions.register_from_gaps(conn, ingest_run_id)
    keys = [r[0] for r in conn.execute(
        "SELECT gap_key FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ci_object' ORDER BY gap_key").fetchall()]
    assert keys == ["CHEBI:D013449", "MESH:D013449"]


def test_the_existing_mesh_gap_keys_are_preserved_exactly(conn, ingest_run_id):
    """A DELIBERATE choice, pinned so it cannot be undone by accident.

    Taking the namespace from the data could have re-keyed every existing MeSH
    question ('MeSH:' != the frozen 'MESH:'), and a question_uuid is meant to be
    externally citable forever. Upper-casing the namespace keeps the frozen
    SCHEME:value convention every other gap kind uses (MOIETY:, CLASS:,
    RXNORM_IN:) AND leaves all 103 existing MeSH question UUIDs bit-for-bit
    unchanged, so nothing had to be migrated.
    """
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=36)
    questions.register_from_gaps(conn, ingest_run_id)
    uuid_now = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ci_object'").fetchone()[0]
    assert uuid_now == ids.mint_question_uuid("unresolved_ci_object", "MESH:D013449")


def test_one_object_under_two_predicates_is_still_one_question(conn, ingest_run_id):
    """THE GRAIN IS PER OBJECT, and it stays that way -- which is why relationship
    is NOT in the grouping key.

    "May a rule naming Sulfonamides expand over MeSH's structural tree?" is one
    decision about one object, whatever predicate asserted it; the rule count is
    how much rides on that one answer, so it sums across predicates. Splitting the
    grain by relationship without also putting relationship in the gap_key would
    be worse than lossy: two view rows would mint the SAME question_uuid and the
    executemany upsert would silently keep whichever text was written last.

    The predicates are reported rather than picked arbitrarily by max(), so a
    second predicate becomes visible instead of overwriting the first.
    """
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=36)
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=4,
                       relationship="CI_with")

    row = conn.execute(
        "SELECT relationship, ci_rule_count FROM drugref.gap_unresolved_ci_object"
    ).fetchall()
    assert row == [("CI_ChemClass, CI_with", 40)]
    assert questions.register_from_gaps(conn, ingest_run_id)["unresolved_ci_object"] == 1


def test_the_views_grain_is_the_gap_keys_grain(conn, ingest_run_id):
    """THE SAME COLLISION, ONE CASE NARROWER -- and the reason the view groups on
    upper(object_source) rather than on the stored spelling.

    gap_key is upper(object_source) || ':' || object_code, because the frozen
    SCHEME:value convention is upper-case. Group the view on the VERBATIM spelling
    and 'MeSH' and 'MESH' become two view rows folding to ONE gap_key -- two rows
    minting one question_uuid, with the executemany upsert silently keeping
    whichever text landed last. That is exactly what db/017 was written to remove;
    a view row that does not survive to its own gap_key is a collision however it
    arose.

    So one namespace is one row whatever spelling a writer used, and the counts SUM
    rather than one silently replacing the other. This is a merge, not a loss: two
    spellings name the same namespace. Genuinely different namespaces are never
    merged by upper() -- pinned by the CHEBI cases above.
    """
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=36)
    _insert_unresolved(conn, ingest_run_id, object_source="MESH",
                       object_code="D013449", name="Sulfonamides", count=4)

    rows = conn.execute(
        "SELECT object_source, ci_rule_count "
        "FROM drugref.gap_unresolved_ci_object").fetchall()
    assert rows == [("MESH", 40)]           # one row, canonical, counts summed
    assert questions.register_from_gaps(conn, ingest_run_id)["unresolved_ci_object"] == 1


def test_the_view_emits_the_namespace_already_canonicalised(conn, ingest_run_id):
    """object_source is published UPPER-CASED, not merely grouped that way.

    A consumer reading the namespace off this view must read the same string
    questions.py keys on; emitting the stored spelling beside a canonicalised
    grouping would hand a reader 'MeSH' for a row whose question is 'MESH:...'.
    """
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=36)
    assert conn.execute(
        "SELECT object_source FROM drugref.gap_unresolved_ci_object"
    ).fetchone()[0] == "MESH"


def test_gap_kind_admits_the_fifth_kind(conn):
    """register_from_gaps INSERTs at the very LAST step of an ingest, so a kind the
    CHECK does not admit aborts the whole transaction after everything was rebuilt."""
    definition = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'open_question_gap_kind'").fetchone()[0]
    assert "unresolved_ci_object" in definition


# ---- gap_condition_without_indication (Task 6, the seventh gap kind) ---------
#
# A COMPLEMENTARY FILTER on Task 5's condition_indication_reach (db/019 section 5),
# never a second walk of the condition DAG -- db/018's round is why: the same reach
# measure stated twice there, only one copy learned a correction, and a whole class
# of dead rules was reported by nothing.
#
# SCOPED ON PURPOSE. 939 registry conditions are unreached; only 97 are gaps. 842 are
# excluded, 670 of them surgical procedures -- "nothing is indicated for
# Abdominoplasty" is a category error, not a gap, and question_uuid is immortal and
# externally citable, so minting 842 of them for noise would bury the 97 real rows.
# (Every figure here is POST-GATE and therefore differs from the spec's 855/66/789/669,
# which counted before the moiety gate -- see db/019 section 6.)
# The tests below pin each edge of that scope: a real gap, a gap closed by an
# ancestor's indication, an excluded procedure, and the tree-less SCR carve-out that
# recovers 17 genuine rare diseases while excluding a chemical.


def test_a_disease_with_no_indication_anywhere_above_it_is_published(conn,
                                                                     ingest_run_id):
    """The gap this kind exists for: drugref holds nothing that treats this disease,
    and nothing that treats anything above it either."""
    orphan = _register_condition(conn, ingest_run_id, "D000000", "Rare Disease X",
                                 trees=("C10.999",))
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert orphan in rows


def test_a_disease_reached_by_an_ancestors_indication_is_not_a_gap(conn, a_moiety,
                                                                   ingest_run_id):
    parent = _register_condition(conn, ingest_run_id, "D004827", "Epilepsy",
                                 trees=("C10.228.140.490",))
    child = _register_condition(conn, ingest_run_id, "D004833", "Epilepsy, Temporal",
                                trees=("C10.228.140.490.360",))
    conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
    indications.add_condition_indication(conn, a_moiety, parent, "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert child not in rows and parent not in rows


def test_a_surgical_procedure_is_never_a_gap(conn, ingest_run_id):
    """670 of the 939 unreached conditions are E-tree procedures. 'Nothing is indicated
    for Abdominoplasty' is a category error, not a gap, and 842 such rows would bury the
    97 real ones under externally-citable question_uuids for noise."""
    procedure = _register_condition(conn, ingest_run_id, "D015917", "Abdominoplasty",
                                    trees=("E04.680",))
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert procedure not in rows


def test_a_rare_disease_SCR_is_a_gap_but_a_chemical_SCR_is_not(conn, ingest_run_id):
    """An SCR bears no tree numbers, so it has no DAG position and 'nothing above it'
    is vacuously true. SCRClass is the only thing that separates the 17 real rare
    diseases from records like aliskiren.

    BOTH VALUES ARE ROUND-TRIPPED THROUGH conditions.upsert_condition, so this is also
    what holds the ingest's scr_class write in place -- the column has exactly one
    writer and exactly one reader (this view's second arm), and neither an INSERT that
    dropped the value nor a SET list that stopped refreshing it would fail anywhere
    else. The refresh is asserted separately below because it is the branch a future
    edit is most likely to drop: it only fires on a re-ingest.
    """
    rare = _register_condition(conn, ingest_run_id, "C580439", "Short QT Syndrome",
                               trees=(), record_kind="SCR", scr_class="3")
    chemical = _register_condition(conn, ingest_run_id, "C446481", "aliskiren",
                                   trees=(), record_kind="SCR", scr_class="1")
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert rare in rows
    assert chemical not in rows

    # THE ON CONFLICT ARM. Upstream re-files records between releases, so a rebuild
    # must be able to move one OUT of the carve-out as well as into it -- and a
    # condition_uuid is immortal, so the second upsert is the same row, not a new one.
    reclassified = _register_condition(conn, ingest_run_id, "C580439",
                                       "Short QT Syndrome", trees=(),
                                       record_kind="SCR", scr_class="1")
    assert reclassified == rare
    assert conn.execute("SELECT scr_class FROM drugref.condition "
                        "WHERE condition_uuid = %s", (rare,)).fetchone()[0] == "1"
    assert rare not in [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]


def test_the_gap_reaches_the_question_register(conn, ingest_run_id):
    _register_condition(conn, ingest_run_id, "D000000", "Rare Disease X",
                        trees=("C10.999",))
    questions.register_from_gaps(conn, ingest_run_id)
    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'condition_without_indication'").fetchone()
    assert row[0].startswith("CONDITION:")
    assert "Rare Disease X" in row[1]


def test_the_condition_views_grain_is_the_gap_keys_grain(conn, ingest_run_id):
    """#41's test, restated for this kind -- and RENAMED rather than reusing that
    test's exact name, because two module-level functions sharing one name is not two
    tests: Python keeps only the second, and the first silently stops being collected
    at all. (Caught by ruff's F811, which is why this one runs under a distinct name.)

    question_uuid is a pure function of (gap_kind, gap_key), so two view rows folding
    to one key would hand two conditions ONE immortal question that append-only
    curator rows then attach to."""
    for ui in ("D000001", "D000002"):
        _register_condition(conn, ingest_run_id, ui, f"Disease {ui}",
                            trees=(f"C10.99{ui[-1]}",))
    keys = conn.execute(
        "SELECT count(*), count(DISTINCT 'CONDITION:' || condition_uuid) "
        "FROM drugref.gap_condition_without_indication").fetchone()
    assert keys[0] == keys[1]
