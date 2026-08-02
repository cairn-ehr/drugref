# tests/test_accumulation_gap_views.py
"""The four curation-dependent gap views (Plan C, db/022, spec 7.1).

A GAP IS A QUERY, NEVER A REPORT. Generated documents are stale on write and nobody
trusts them; as views over ingested + curated data these are always current, shrink
visibly as curation lands, and make "how much do we not know" a number watchable per
release.

The four here are the ones that need curation to exist before they mean anything, and
each answers a different question a curator would otherwise have to remember to ask:

  gap_uncurated_additive_effect  which effects has nobody RULED on?
  gap_uncurated_threshold        which effects fire on DEFAULTS nobody reviewed?
  gap_ineffective_contribution   which promotions are silent NO-OPS?
  gap_ungraded_contribution      which contributors has nobody GRADED?

The sharpest test in this module is the reviewed-minor one: an explicitly-minor class
grades identically to an ungraded one, so ONLY its absence from the queue distinguishes
"a curator looked" from "nobody looked". Reading the queue the other way would re-earn
the same curator attention forever -- the nagging failure mode spec 7.2.1 diagnoses.
"""
import pytest

from drugref import accumulation, ids


# The writer implied by each source this module's tests actually open a run
# under (db/025). A KeyError on an unlisted source beats a silent NotNullViolation.
_WRITER_BY_SOURCE = {"DRUGREF": "curation", "MED-RT": "medrt_run"}


def _run(conn, source="DRUGREF"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'gap-release', 'deadbeef', %s) RETURNING ingest_run_id",
        (source, _WRITER_BY_SOURCE[source])).fetchone()[0]


def _class(conn, run_id, code, concept_type="PE"):
    class_uuid = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (class_uuid, code, f"class {code}", concept_type, run_id))
    return class_uuid


def _moiety(conn, run_id, unii):
    moiety_uuid = ids.mint_moiety_uuid(unii)
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, f"drug {unii}", run_id))
    return moiety_uuid


def _member(conn, run_id, moiety_uuid, class_uuid, relationship="has_PE"):
    conn.execute(
        "INSERT INTO drugref.class_membership (moiety_uuid, class_uuid, relationship, "
        "ingest_run) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, class_uuid, relationship, run_id))


def _edge(conn, run_id, parent, child):
    conn.execute(
        "INSERT INTO drugref.class_parent (child_class_uuid, parent_class_uuid, "
        "ingest_run) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (child, parent, run_id))


def _ci_rule(conn, run_id, subject_moiety, object_class):
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_PE', 'MED-RT', %s) ON CONFLICT DO NOTHING",
        (subject_moiety, object_class, run_id))


def _keys(conn, view, column="class_uuid"):
    return {r[0] for r in conn.execute(
        f"SELECT {column} FROM drugref.{view}").fetchall()}


# ---- gap_uncurated_additive_effect ------------------------------------------


def test_a_pe_class_carrying_a_ci_rule_is_a_pending_decision(conn):
    """The '>= 1 CI rule' half of the filter: MED-RT already thought this effect worth
    contraindicating over, so whether it ACCUMULATES is a decision someone owes."""
    run_id = _run(conn, "MED-RT")
    effect = _class(conn, run_id, "G001")
    drug = _moiety(conn, run_id, "G001U")
    _ci_rule(conn, run_id, drug, effect)
    assert effect in _keys(conn, "gap_uncurated_additive_effect")


def test_a_small_pe_class_with_no_rule_is_not_asked_about(conn):
    """The filter is deliberately crude but it must be a filter: without it every one
    of the release's 1,873 PE classes becomes an externally-citable question."""
    run_id = _run(conn, "MED-RT")
    effect = _class(conn, run_id, "G002")
    _member(conn, run_id, _moiety(conn, run_id, "G002U"), effect)
    assert effect not in _keys(conn, "gap_uncurated_additive_effect")


def test_a_pe_class_with_ten_subtree_members_is_a_pending_decision(conn):
    """The other half of the filter -- and it counts the SUBTREE, because a class whose
    members all sit one level down is exactly as much of a pending decision as one
    holding them directly."""
    run_id = _run(conn, "MED-RT")
    effect = _class(conn, run_id, "G003")
    child = _class(conn, run_id, "G003C")
    _edge(conn, run_id, effect, child)
    for i in range(10):
        _member(conn, run_id, _moiety(conn, run_id, f"G003U{i}"), child)
    assert effect in _keys(conn, "gap_uncurated_additive_effect")


def test_a_curated_effect_leaves_the_queue(conn):
    run_id = _run(conn, "MED-RT")
    effect = _class(conn, run_id, "G004")
    _ci_rule(conn, run_id, _moiety(conn, run_id, "G004U"), effect)
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    assert effect not in _keys(conn, "gap_uncurated_additive_effect")


def test_a_ruling_that_it_does_not_accumulate_ALSO_leaves_the_queue(conn):
    """THE POINT OF THE `accumulates` COLUMN. "Reviewed, does not add up" is an answer,
    and a worklist that kept asking would re-earn the same curator attention every
    release forever."""
    run_id = _run(conn, "MED-RT")
    effect = _class(conn, run_id, "G005")
    _ci_rule(conn, run_id, _moiety(conn, run_id, "G005U"), effect)
    accumulation.curate_effect(conn, effect, run_id, accumulates=False)
    assert effect not in _keys(conn, "gap_uncurated_additive_effect")


def test_a_non_pe_class_is_never_asked_about(conn):
    """Accumulation is a claim about a physiologic EFFECT. An EPC or MoA class is not
    a thing that adds up, and asking would be the category error db/014's object_kind
    was added to stop one slice over."""
    run_id = _run(conn, "MED-RT")
    epc = _class(conn, run_id, "G006", concept_type="EPC")
    _ci_rule(conn, run_id, _moiety(conn, run_id, "G006U"), epc)
    assert epc not in _keys(conn, "gap_uncurated_additive_effect")


# ---- gap_uncurated_threshold ------------------------------------------------


def test_an_effect_firing_purely_on_defaults_is_reported(conn):
    """Tension A made visible: (0, 2) with nothing graded fires on any two members of
    a subtree nobody has reviewed. Legal -- and exactly the risky case.

    THE EFFECT NEEDS REAL MEMBERS for this to be the risky case at all; before db/023
    this test asserted it over an effect with none, which the old row-counting gate
    reported anyway."""
    run_id = _run(conn)
    effect, _holders = _effect_over_members(conn, run_id, "G010", 2)
    assert effect in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_an_effect_with_too_few_members_to_fire_is_not_reported(conn):
    """The behaviour db/023 changed, pinned so it stays deliberate. An effect with fewer
    contributors than threshold_total cannot fire on ANYBODY, reviewed or not, so
    "would fire on members nobody reviewed" is false of it. It reappears by itself the
    moment an ingest brings enough members in -- a gap is a query, not a report."""
    run_id = _run(conn)
    effect, _holders = _effect_over_members(conn, run_id, "G017", 1)
    assert effect not in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_requiring_a_major_is_not_reported(conn):
    """threshold_major >= 1 filters the noise, which is why it is the recommendation.

    Given four ungraded members -- more than enough to trip the gate if the effect were
    (0, 4) -- so what keeps it off the queue is the threshold and nothing else."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "G011")
    for i in range(4):
        _member(conn, run_id, _moiety(conn, run_id, f"G011U{i}"), effect)
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    assert effect not in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def _effect_over_members(conn, run_id, code, n_members, total=2):
    """A (0, `total`) effect whose members each sit in their own gradeable class.

    Each member gets its own class so that grading is a decision a curator can make
    one member at a time -- which is what lets these tests move the graded/ungraded
    boundary a single drug at a time.
    """
    effect = _class(conn, run_id, code)
    holders = []
    for i in range(n_members):
        holder = _class(conn, run_id, f"{code}H{i}")
        _edge(conn, run_id, effect, holder)
        _member(conn, run_id, _moiety(conn, run_id, f"{code}U{i}"), holder)
        holders.append(holder)
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=0, threshold_total=total,
                               severity="major")
    return effect, holders


def test_grading_every_member_clears_the_threshold_gap(conn):
    """(0, 2) is the CORRECT encoding for a fully curated effect, so the view must
    stop reporting one once the curation is actually there -- which means once too few
    UNREVIEWED members remain to trip the threshold on their own."""
    run_id = _run(conn)
    effect, holders = _effect_over_members(conn, run_id, "G012", 2)
    for holder in holders:
        accumulation.grade_contribution(conn, effect, holder, "major", run_id)
    assert effect not in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_grading_too_few_members_does_not_clear_the_threshold_gap(conn):
    """Two of four graded still leaves TWO members nobody looked at, and the effect
    fires on any two. The gap must survive until the unreviewed population can no
    longer trip the threshold by itself."""
    run_id = _run(conn)
    effect, holders = _effect_over_members(conn, run_id, "G013", 4)
    for holder in holders[:2]:
        accumulation.grade_contribution(conn, effect, holder, "major", run_id)
    assert effect in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_grading_explicitly_MINOR_still_counts_as_reviewed(conn):
    """An explicit `minor` is a curator LOOKING, so it clears the member the same way
    a `major` does -- the distinction the whole model rests on (spec 5.2). What it must
    NOT do is clear members it never reached, which is the next test."""
    run_id = _run(conn)
    effect, holders = _effect_over_members(conn, run_id, "G014", 2)
    for holder in holders:
        accumulation.grade_contribution(conn, effect, holder, "minor", run_id)
    assert effect not in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_grading_classes_that_reach_nobody_does_not_clear_the_threshold_gap(conn):
    """THE HOLE THIS VIEW HAD. Counting live effect_contribution ROWS let a curator
    clear the gap with promotions that regrade nobody: both UUIDs are valid classes, so
    two no-op rows satisfied `graded >= threshold_total` while every member the effect
    actually fires on stayed unreviewed. The gate is the UNGRADED MEMBER count, so a
    promotion that reaches nothing moves it by nothing."""
    run_id = _run(conn)
    effect, _holders = _effect_over_members(conn, run_id, "G015", 4)
    for i in range(2):
        stranger = _class(conn, run_id, f"G015X{i}", concept_type="EPC")
        _member(conn, run_id, _moiety(conn, run_id, f"G015XU{i}"), stranger, "has_EPC")
        accumulation.grade_contribution(conn, effect, stranger, "major", run_id)
    assert effect in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_the_threshold_gap_counts_only_promotions_that_bite(conn):
    """`graded_contributor_count` is what the question text quotes back to a curator,
    so a no-op promotion inflating it would report review that never happened."""
    run_id = _run(conn)
    effect, holders = _effect_over_members(conn, run_id, "G016", 4)
    accumulation.grade_contribution(conn, effect, holders[0], "major", run_id)
    stranger = _class(conn, run_id, "G016X", concept_type="EPC")
    _member(conn, run_id, _moiety(conn, run_id, "G016XU"), stranger, "has_EPC")
    accumulation.grade_contribution(conn, effect, stranger, "major", run_id)
    graded, ungraded = conn.execute(
        "SELECT graded_contributor_count, ungraded_member_count "
        "FROM drugref.gap_uncurated_threshold WHERE effect_class_uuid = %s",
        (effect,)).fetchone()
    assert (graded, ungraded) == (1, 3)


# ---- gap_ineffective_contribution -------------------------------------------


def test_a_promotion_sharing_no_member_with_the_effect_is_reported(conn):
    """A silent no-op the schema cannot catch: both UUIDs are valid classes, so
    nothing errors and nothing happens. Also the view most likely to fire right after
    a MED-RT reshuffle moves a class out from under an effect."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "G020")
    stranger = _class(conn, run_id, "G021", concept_type="EPC")
    _member(conn, run_id, _moiety(conn, run_id, "G020U"), effect)
    _member(conn, run_id, _moiety(conn, run_id, "G021U"), stranger, "has_EPC")
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    accumulation.grade_contribution(conn, effect, stranger, "major", run_id)
    assert stranger in _keys(conn, "gap_ineffective_contribution",
                             "contributor_class_uuid")


def test_one_contributor_class_can_bite_for_one_effect_and_be_a_no_op_for_another(conn):
    """WHAT THE db/024 REWRITE MUST NOT LOSE. This view's verdict is per (effect,
    contributor) PAIR, never per contributor: the same class is a sound promotion for
    an effect whose drugs it shares and a silent no-op for one it does not. Anything
    that decides "does this class bite?" once per class -- the obvious way to make the
    query cheap -- collapses those two answers into one and reports the wrong row.

    It is the same reason the gap_key is compound (`CLASS:a/CLASS:b`), asserted here
    against the view rather than against the key."""
    run_id = _run(conn)
    shared = _class(conn, run_id, "G025", concept_type="EPC")
    drug = _moiety(conn, run_id, "G025U")
    _member(conn, run_id, drug, shared, "has_EPC")

    reached = _class(conn, run_id, "G026")     # holds the same drug -> promotion bites
    _member(conn, run_id, drug, reached)
    missed = _class(conn, run_id, "G027")      # holds a different drug -> no-op
    _member(conn, run_id, _moiety(conn, run_id, "G027U"), missed)

    for effect in (reached, missed):
        accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                                   threshold_major=1, threshold_total=2, severity="major")
        accumulation.grade_contribution(conn, effect, shared, "major", run_id)

    reported = {(e, c) for e, c in conn.execute(
        "SELECT effect_class_uuid, contributor_class_uuid "
        "FROM drugref.gap_ineffective_contribution").fetchall()}
    assert (missed, shared) in reported, "the no-op pair must be reported"
    assert (reached, shared) not in reported, "the biting pair must NOT be reported"


def test_a_promotion_that_regrades_someone_is_not_reported(conn):
    run_id = _run(conn)
    effect = _class(conn, run_id, "G022")
    promoted = _class(conn, run_id, "G023", concept_type="EPC")
    drug = _moiety(conn, run_id, "G022U")
    _member(conn, run_id, drug, effect)
    _member(conn, run_id, drug, promoted, "has_EPC")
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    accumulation.grade_contribution(conn, effect, promoted, "major", run_id)
    assert promoted not in _keys(conn, "gap_ineffective_contribution",
                                 "contributor_class_uuid")


# ---- gap_ungraded_contribution ----------------------------------------------


@pytest.fixture
def graded(conn):
    """One curated effect over two member classes, one graded and one not."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "G030")
    reviewed = _class(conn, run_id, "G031")
    untouched = _class(conn, run_id, "G032")
    _edge(conn, run_id, effect, reviewed)
    _edge(conn, run_id, effect, untouched)
    _member(conn, run_id, _moiety(conn, run_id, "G031U"), reviewed)
    _member(conn, run_id, _moiety(conn, run_id, "G032U"), untouched)
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    return {"run_id": run_id, "effect": effect,
            "reviewed": reviewed, "untouched": untouched}


def test_an_ungraded_member_class_is_on_the_queue(conn, graded):
    assert graded["untouched"] in _keys(conn, "gap_ungraded_contribution",
                                        "contributor_class_uuid")


def test_a_class_reviewed_and_confirmed_minor_LEAVES_the_queue(conn, graded):
    """THE ASSERTION THAT PINS SPEC 5.2's distinction. An explicit `minor` grades
    identically to an ungraded class -- both are minor in
    additive_effect_contributor -- so this absence is the ONLY observable difference
    between "a curator looked" and "nobody looked"."""
    accumulation.grade_contribution(conn, graded["effect"], graded["reviewed"],
                                    "minor", graded["run_id"])
    on_queue = _keys(conn, "gap_ungraded_contribution", "contributor_class_uuid")
    assert graded["reviewed"] not in on_queue
    assert graded["untouched"] in on_queue, "the uncurated one must still be asked about"


def test_a_class_with_no_members_is_not_on_the_queue(conn, graded):
    """Grading a class nothing is filed under would be a promotion with an empty
    intersection -- the no-op gap_ineffective_contribution exists to report. The queue
    must not ask a curator to create one."""
    empty = _class(conn, graded["run_id"], "G033")
    _edge(conn, graded["run_id"], graded["effect"], empty)
    assert empty not in _keys(conn, "gap_ungraded_contribution",
                              "contributor_class_uuid")


def test_the_queue_is_empty_for_an_uncurated_effect(conn):
    """These three views are meaningless before curation begins, and returning rows
    for an effect nobody has ruled on would put a grading task ahead of the decision
    it depends on."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "G040")
    _member(conn, run_id, _moiety(conn, run_id, "G040U"), effect)
    assert effect not in _keys(conn, "gap_ungraded_contribution", "effect_class_uuid")


def test_the_views_grain_is_the_gap_keys_grain(conn, graded):
    """#41's standing rule, restated for every new gap kind: a view that groups more
    coarsely than the gap_key folds two rows onto one immortal question_uuid, and more
    finely mints two questions for one gap. Both views keyed on the (effect,
    contributor) PAIR must return one row per pair."""
    for view in ("gap_ungraded_contribution", "gap_ineffective_contribution"):
        total, distinct = conn.execute(
            f"SELECT count(*), count(DISTINCT (effect_class_uuid, "
            f"contributor_class_uuid)) FROM drugref.{view}").fetchone()
        assert total == distinct, f"{view} does not have one row per (effect, contributor)"


# ---- registration into the question registry --------------------------------


def test_the_four_kinds_register_as_open_questions(conn, graded):
    """End-to-end: view -> gap_key -> question_uuid -> open_question. The compound-key
    kinds are the ones worth proving, because their key_sql concatenates two columns
    and a typo there would mint questions nothing can reconcile later."""
    from drugref import questions

    accumulation.grade_contribution(conn, graded["effect"], graded["reviewed"],
                                    "minor", graded["run_id"])
    counts = questions.register_from_gaps(conn, graded["run_id"])

    assert counts["ungraded_contribution"] >= 1
    key, text = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'ungraded_contribution' LIMIT 1").fetchone()
    assert key.count("CLASS:") == 2 and "/" in key
    assert "MAJOR or a minor contributor" in text


def test_a_registered_question_uses_the_minted_uuid(conn, graded):
    from drugref import questions

    questions.register_from_gaps(conn, graded["run_id"])
    row = conn.execute(
        "SELECT question_uuid, gap_kind, gap_key FROM drugref.open_question "
        "WHERE gap_kind = 'ungraded_contribution' LIMIT 1").fetchone()
    assert row[0] == ids.mint_question_uuid(row[1], row[2])
