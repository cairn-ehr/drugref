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


def _run(conn, source="DRUGREF"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'gap-release', 'deadbeef') RETURNING ingest_run_id",
        (source,)).fetchone()[0]


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
    a subtree nobody has reviewed. Legal -- and exactly the risky case."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "G010")
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=0, threshold_total=2, severity="major")
    assert effect in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_requiring_a_major_is_not_reported(conn):
    """threshold_major >= 1 filters the noise, which is why it is the recommendation."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "G011")
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=1, threshold_total=2, severity="major")
    assert effect not in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


def test_enough_graded_contributors_clears_the_threshold_gap(conn):
    """(0, 2) is the CORRECT encoding for a fully curated effect, so the view must
    stop reporting one once the curation is actually there."""
    run_id = _run(conn)
    effect = _class(conn, run_id, "G012")
    accumulation.curate_effect(conn, effect, run_id, accumulates=True,
                               threshold_major=0, threshold_total=2, severity="major")
    for code in ("G012A", "G012B"):
        accumulation.grade_contribution(conn, effect, _class(conn, run_id, code),
                                        "major", run_id)
    assert effect not in _keys(conn, "gap_uncurated_threshold", "effect_class_uuid")


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
