# tests/test_ddi_pairs.py
"""ddi_candidate_pair expands class-level CI rules into concrete drug pairs over
the class_membership drugref already builds -- so the pair explosion is never
stored.

Three properties carry clinical weight and are pinned here. The axis mapping
(CI_MoA joins has_MoA members, CI_PE joins has_PE members) must not cross-wire, or
the meaning inverts; a drug is never paired with itself; and since Plan B the view
DESCENDS THE CLASS DAG, because for a contraindication fewer rows is the unsafe
direction -- a rule naming a parent class must reach a drug filed only under a
child of it.

Descendant expansion is bounded by class_expansion_policy (db/010), and the
bounding rule is subtle enough that test_a_descendant_of_a_denied_root_still_
expands exists specifically to pin it: the deny-list filters THE RULE'S OBJECT
CLASS, it is not a barrier encountered during the walk.
"""
import uuid

from drugref import classes, ids, interactions

# Real NUIs from the db/010 seed, so these tests exercise the shipped policy rather
# than a policy invented for the test.
HEMATOLOGIC = "N0000009065"      # denied: an abstract "<system> Activity Alteration"
VASOCONSTRICTION = "N0000009908"  # explicitly allowed


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, code, cty="MoA", name="Test Class"):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, 'MED-RT', %s, %s, %s, %s, %s)",
        (cu, code, code, name, cty, run_id))
    return cu


def _moiety(conn, run_id, name):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _parent(conn, run_id, child, parent):
    """`child` is a kind of `parent` -- the DAG edge slice 2a builds."""
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (child, parent, run_id))


def _partners(conn, subject):
    return [r[0] for r in conn.execute(
        "SELECT partner_moiety FROM drugref.ddi_candidate_pair WHERE subject_moiety = %s",
        (subject,)).fetchall()]


def _rows(conn, subject):
    """(partner, member_class, is_direct) for one subject, ordered for comparison."""
    return conn.execute(
        "SELECT partner_moiety, member_class, is_direct FROM drugref.ddi_candidate_pair "
        "WHERE subject_moiety = %s ORDER BY partner_moiety", (subject,)).fetchall()


# ---- the axis contract (unchanged by Plan B) --------------------------------


def test_ci_moa_expands_to_the_classs_has_moa_members(conn):
    run_id = _run(conn)
    subject, other = _moiety(conn, run_id, "subjectium"), _moiety(conn, run_id, "otherium")
    c = _class(conn, run_id, "N0000000601", "MoA")
    interactions.add_contraindication(conn, subject, c, "CI_MoA", "MED-RT", run_id)
    classes.add_membership(conn, other, c, "has_MoA", run_id)
    assert conn.execute(
        "SELECT subject_moiety, partner_moiety, relationship, via_class, member_class, "
        "is_direct FROM drugref.ddi_candidate_pair WHERE subject_moiety = %s", (subject,)
    ).fetchall() == [(subject, other, "CI_MoA", c, c, True)]


def test_ci_pe_joins_has_pe_members_and_never_has_moa_members(conn):
    """The axis mapping is not cross-wired: a CI_PE reaches has_PE members only. A
    member linked to the same class on the wrong axis must not be paired."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    pe_member, moa_member = _moiety(conn, run_id, "pe"), _moiety(conn, run_id, "moa")
    c = _class(conn, run_id, "N0000000602", "PE")
    interactions.add_contraindication(conn, subject, c, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, pe_member, c, "has_PE", run_id)
    classes.add_membership(conn, moa_member, c, "has_MoA", run_id)  # wrong axis
    assert _partners(conn, subject) == [pe_member]


def test_the_axis_is_not_cross_wired_through_a_descendant_either(conn):
    """Expansion widens WHICH CLASSES are searched, never which axis. A has_MoA
    member of a child class must stay invisible to a CI_PE rule on its parent --
    otherwise expansion would quietly become the cross-wiring db/006 forbids."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    pe_member, moa_member = _moiety(conn, run_id, "pe"), _moiety(conn, run_id, "moa")
    parent = _class(conn, run_id, "N0000000610", "PE")
    child = _class(conn, run_id, "N0000000611", "PE")
    _parent(conn, run_id, child, parent)
    interactions.add_contraindication(conn, subject, parent, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, pe_member, child, "has_PE", run_id)
    classes.add_membership(conn, moa_member, child, "has_MoA", run_id)
    assert _partners(conn, subject) == [pe_member]


def test_a_drug_is_never_contraindicated_with_itself(conn):
    """The subject is itself a member of the class it is contraindicated against
    (common: a drug of MoA C contraindicated with co-administered MoA-C drugs). It
    must not be paired with itself."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    c = _class(conn, run_id, "N0000000603", "MoA")
    interactions.add_contraindication(conn, subject, c, "CI_MoA", "MED-RT", run_id)
    classes.add_membership(conn, subject, c, "has_MoA", run_id)
    assert _partners(conn, subject) == []


def test_a_drug_is_not_paired_with_itself_via_a_descendant_either(conn):
    """The self-pair filter must survive expansion. A subject filed under a CHILD of
    the class its own rule names is still itself."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    parent = _class(conn, run_id, "N0000000604", "PE")
    child = _class(conn, run_id, "N0000000605", "PE")
    _parent(conn, run_id, child, parent)
    interactions.add_contraindication(conn, subject, parent, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, subject, child, "has_PE", run_id)
    assert _partners(conn, subject) == []


# ---- descendant expansion (Plan B, #15) -------------------------------------


def test_a_rule_on_a_parent_reaches_a_member_of_the_child(conn):
    """The defect #15 reports, in miniature. Measured on the real release: a rule on
    `Decreased Coagulation Activity [PE]` reached 4 drugs and missed 105, among them
    warfarin, apixaban, rivaroxaban, aspirin, every heparin and every thrombolytic."""
    run_id = _run(conn)
    subject, warfarin = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "warfarin")
    parent = _class(conn, run_id, "N0000175978", "PE", "Decreased Coagulation Activity [PE]")
    child = _class(conn, run_id, "N0000175979", "PE",
                   "Decreased Coagulation Factor Activity [PE]")
    _parent(conn, run_id, child, parent)
    interactions.add_contraindication(conn, subject, parent, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, warfarin, child, "has_PE", run_id)

    assert _rows(conn, subject) == [(warfarin, child, False)]


def test_expansion_is_transitive_not_one_level(conn):
    """MED-RT files membership at the most SPECIFIC node while writing rules against
    a parent, and the specific node may be several levels down."""
    run_id = _run(conn)
    subject, deep = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "deep")
    root = _class(conn, run_id, "N0000000620", "PE")
    mid = _class(conn, run_id, "N0000000621", "PE")
    leaf = _class(conn, run_id, "N0000000622", "PE")
    _parent(conn, run_id, mid, root)
    _parent(conn, run_id, leaf, mid)
    interactions.add_contraindication(conn, subject, root, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, deep, leaf, "has_PE", run_id)
    assert _rows(conn, subject) == [(deep, leaf, False)]


def test_a_class_with_no_descendants_is_unchanged_by_expansion(conn):
    """§3.2's control case, and the regression test the whole design leans on:
    `Serotonin Uptake Inhibitors [MoA]` has 77 direct members and 0 hidden ones. MoA
    and PE genuinely behave differently, and expansion must be a no-op where there is
    nothing below -- every row still `is_direct`."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    a, b = _moiety(conn, run_id, "a"), _moiety(conn, run_id, "b")
    c = _class(conn, run_id, "N0000000630", "MoA", "Serotonin Uptake Inhibitors [MoA]")
    interactions.add_contraindication(conn, subject, c, "CI_MoA", "MED-RT", run_id)
    classes.add_membership(conn, a, c, "has_MoA", run_id)
    classes.add_membership(conn, b, c, "has_MoA", run_id)

    rows = _rows(conn, subject)
    assert sorted(r[0] for r in rows) == sorted([a, b])
    assert all(is_direct for _, _, is_direct in rows)


def test_a_partner_reachable_by_two_branches_appears_once(conn):
    """The DAG is multi-parent (440 classes in the real release), so one drug can be
    reached down two paths from the same rule. A consumer counting candidate partners
    must not see it twice."""
    run_id = _run(conn)
    subject, both = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "both")
    root = _class(conn, run_id, "N0000000640", "PE")
    left, right = _class(conn, run_id, "N0000000641", "PE"), _class(conn, run_id, "N0000000642", "PE")
    _parent(conn, run_id, left, root)
    _parent(conn, run_id, right, root)
    interactions.add_contraindication(conn, subject, root, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, both, left, "has_PE", run_id)
    classes.add_membership(conn, both, right, "has_PE", run_id)

    assert _partners(conn, subject) == [both]


def test_direct_membership_wins_the_tie_for_member_class(conn):
    """A partner filed BOTH on the rule's own class and on a descendant is a direct
    hit, and reporting it as an expanded one would understate confidence. The choice
    is also what makes `WHERE is_direct` reproduce the pre-Plan-B row set exactly."""
    run_id = _run(conn)
    subject, p = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "p")
    root = _class(conn, run_id, "N0000000650", "PE")
    child = _class(conn, run_id, "N0000000651", "PE")
    _parent(conn, run_id, child, root)
    interactions.add_contraindication(conn, subject, root, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, p, root, "has_PE", run_id)
    classes.add_membership(conn, p, child, "has_PE", run_id)

    assert _rows(conn, subject) == [(p, root, True)]


def test_a_cycle_in_the_class_dag_does_not_hang_the_view(conn):
    """Only self-parenting is refused by db/002, so A-is-a-B-is-a-A is representable
    and one bad release could introduce it. The recursion therefore walks DISTINCT
    (root, class) pairs rather than paths -- a query that never returns is worse than
    a wrong answer, because nothing reports it."""
    run_id = _run(conn)
    subject, p = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "p")
    a, b = _class(conn, run_id, "N0000000660", "PE"), _class(conn, run_id, "N0000000661", "PE")
    _parent(conn, run_id, b, a)
    _parent(conn, run_id, a, b)          # the cycle
    interactions.add_contraindication(conn, subject, a, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, p, b, "has_PE", run_id)

    conn.execute("SET LOCAL statement_timeout = '10s'")
    assert _partners(conn, subject) == [p]


# ---- the deny-list ----------------------------------------------------------


def test_a_rule_on_a_denied_root_reaches_direct_members_only(conn):
    """`Hematologic Activity Alteration [PE]` names a system, not an effect. Its 114
    descendant classes hold 1,233 drugs against 6 direct ones, and "not with anything
    that alters hematologic activity" is not advice a prescriber can act on."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    near, far = _moiety(conn, run_id, "near"), _moiety(conn, run_id, "far")
    root = _class(conn, run_id, HEMATOLOGIC, "PE", "Hematologic Activity Alteration [PE]")
    child = _class(conn, run_id, "N0000000670", "PE")
    _parent(conn, run_id, child, root)
    interactions.add_contraindication(conn, subject, root, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, near, root, "has_PE", run_id)
    classes.add_membership(conn, far, child, "has_PE", run_id)

    assert _rows(conn, subject) == [(near, root, True)]


def test_a_descendant_of_a_denied_root_still_expands(conn):
    """THE test this design exists to protect, and the wrong reading is implementable.
    The deny-list filters the RULE'S OBJECT CLASS; it is not a barrier met during the
    walk. `Decreased Coagulation Activity` is a DESCENDANT of the denied `Hematologic
    Activity Alteration`, so a traversal barrier would leave the coagulation rules
    unexpanded -- deleting the single most important case Plan B was built for."""
    run_id = _run(conn)
    subject, warfarin = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "warfarin")
    denied = _class(conn, run_id, HEMATOLOGIC, "PE", "Hematologic Activity Alteration [PE]")
    coagulation = _class(conn, run_id, "N0000175978", "PE",
                         "Decreased Coagulation Activity [PE]")
    factor = _class(conn, run_id, "N0000175979", "PE",
                    "Decreased Coagulation Factor Activity [PE]")
    _parent(conn, run_id, coagulation, denied)     # coagulation sits UNDER the denied root
    _parent(conn, run_id, factor, coagulation)
    # The rule names the coagulation class, which carries no policy row of its own.
    interactions.add_contraindication(conn, subject, coagulation, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, warfarin, factor, "has_PE", run_id)

    assert _rows(conn, subject) == [(warfarin, factor, False)]


def test_an_explicit_allow_expands_exactly_as_an_unreviewed_class_does(conn):
    """`allow` and "no row" differ only for the review gate. If `allow` changed the
    pair set, the act of reviewing a class would silently alter clinical output."""
    run_id = _run(conn)
    subject, p = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "p")
    allowed = _class(conn, run_id, VASOCONSTRICTION, "PE", "Vasoconstriction [PE]")
    child = _class(conn, run_id, "N0000000680", "PE", "Arterial Vasoconstriction [PE]")
    _parent(conn, run_id, child, allowed)
    interactions.add_contraindication(conn, subject, allowed, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, p, child, "has_PE", run_id)

    assert _rows(conn, subject) == [(p, child, False)]


def test_turning_expansion_off_for_a_predicate_reduces_it_to_direct_membership(conn):
    """The ci_axis switch. Slice 5b's MeSH-keyed predicates sit over a differently
    shaped tree, so the decision is per predicate and declared beside the axis
    mapping rather than assumed."""
    run_id = _run(conn)
    subject, far = _moiety(conn, run_id, "s"), _moiety(conn, run_id, "far")
    root = _class(conn, run_id, "N0000000690", "PE")
    child = _class(conn, run_id, "N0000000691", "PE")
    _parent(conn, run_id, child, root)
    interactions.add_contraindication(conn, subject, root, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, far, child, "has_PE", run_id)
    assert _partners(conn, subject) == [far]

    conn.execute("UPDATE drugref.ci_axis SET expands_descendants = false "
                 "WHERE relationship = 'CI_PE'")
    assert _partners(conn, subject) == []


def test_is_direct_reproduces_the_pre_expansion_row_set(conn):
    """The opt-out a precision-sensitive consumer relies on. Filtering on is_direct
    must give exactly what the view returned before Plan B -- no more, no fewer."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    near, far = _moiety(conn, run_id, "near"), _moiety(conn, run_id, "far")
    root = _class(conn, run_id, "N0000000700", "PE")
    child = _class(conn, run_id, "N0000000701", "PE")
    _parent(conn, run_id, child, root)
    interactions.add_contraindication(conn, subject, root, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, near, root, "has_PE", run_id)
    classes.add_membership(conn, far, child, "has_PE", run_id)

    assert sorted(_partners(conn, subject)) == sorted([near, far])
    assert [r[0] for r in conn.execute(
        "SELECT partner_moiety FROM drugref.ddi_candidate_pair "
        "WHERE subject_moiety = %s AND is_direct", (subject,)).fetchall()] == [near]
