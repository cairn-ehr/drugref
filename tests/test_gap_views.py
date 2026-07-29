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
import uuid

import pytest
import psycopg

from drugref import ids, questions


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


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


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
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')", (run_id,))
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
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')", (run_id,))
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unmatched_ingredient").fetchone()[0] == 0


def test_one_run_cannot_store_an_rxcui_twice(conn):
    """The (ingest_run, rxcui) primary key. Named for what it actually asserts:
    replacement ACROSS runs is a different property and is tested against the real
    ingest in test_medrt_run.py, which is the only place it can be exercised."""
    run_id = _run(conn)
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')", (run_id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                     "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')",
                     (run_id,))


def test_an_rxcui_two_sources_both_report_is_one_gap(conn):
    """clear_source_unmatched_ingredients clears ONE source, so the moment a second
    source reports unmatched ingredients the same RxCUI is stored twice. The view has
    to collapse them: gap_key is an input to question_uuid, so two rows here mint one
    question and register_from_gaps would over-report its own live count. The row
    kept is the most recent run's."""
    older, newer = _run(conn), _run(conn, source="MeSH")
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', NULL)", (older,))
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')", (newer,))

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
    """The collision, made concrete: one code, two namespaces, two objects."""
    _insert_unresolved(conn, ingest_run_id, object_source="MeSH",
                       object_code="D013449", name="Sulfonamides", count=36)
    _insert_unresolved(conn, ingest_run_id, object_source="CHEBI",
                       object_code="D013449", name="Something Else", count=4)

    rows = conn.execute(
        "SELECT object_source, object_name, ci_rule_count "
        "FROM drugref.gap_unresolved_ci_object ORDER BY object_source").fetchall()
    assert rows == [("CHEBI", "Something Else", 4), ("MeSH", "Sulfonamides", 36)]


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


def test_gap_kind_admits_the_fifth_kind(conn):
    """register_from_gaps INSERTs at the very LAST step of an ingest, so a kind the
    CHECK does not admit aborts the whole transaction after everything was rebuilt."""
    definition = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'open_question_gap_kind'").fetchone()[0]
    assert "unresolved_ci_object" in definition
