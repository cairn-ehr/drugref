# tests/test_accumulation_read_path.py
"""The output contract of the accumulation model (Plan C, db/021, spec 8).

drugref publishes FACTS AND THRESHOLDS, never verdicts: `additive_effect_contributor`
is the flattened effect -> moiety -> grade table, and the CONSUMER intersects it with
a patient's regimen and applies `additive_effect`'s thresholds. That keeps the global
tier stateless and free of patient data.

Two properties here are contract rather than implementation, and a consumer may rely
on both:

  * PROMOTION REGRADES, IT NEVER RECRUITS. effect_contribution does not list
    contributors -- membership does. A curator who promotes a class sharing no member
    with the effect changes nothing, and must not thereby add anyone to the effect.
  * ONE ROW PER (effect, moiety), at max(magnitude). Spec 8 is explicit that this is
    part of the contract and not a detail: the whole evaluation is COUNT THE
    CONTRIBUTORS, so a moiety emitted twice is the difference between firing and not
    firing at threshold_total = 2 -- one drug counted as two.
"""
import pytest

from drugref import ids


def _run(conn, source="DRUGREF"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test-release', 'deadbeef') RETURNING ingest_run_id",
        (source,)).fetchone()[0]


def _class(conn, run_id, code, concept_type="PE", source="MED-RT"):
    class_uuid = ids.mint_class_uuid(source, code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "class_name, concept_type, first_seen_ingest) VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (class_uuid, source, code, f"class {code}", concept_type, run_id))
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


def _effect(conn, run_id, class_uuid, major=1, total=2):
    return conn.execute(
        "INSERT INTO drugref.additive_effect (effect_class_uuid, accumulates, "
        "threshold_major, threshold_total, severity, clinical_note, source, ingest_run) "
        "VALUES (%s, true, %s, %s, 'major', 'adds up', 'DRUGREF', %s) "
        "RETURNING additive_effect_id", (class_uuid, major, total, run_id)).fetchone()[0]


def _rule_out(conn, run_id, class_uuid):
    """A curator ruling that this class does NOT accumulate -- a real answer, and the
    thing that lets a reviewed class leave gap_uncurated_additive_effect."""
    return conn.execute(
        "INSERT INTO drugref.additive_effect (effect_class_uuid, accumulates, source, "
        "ingest_run) VALUES (%s, false, 'DRUGREF', %s) RETURNING additive_effect_id",
        (class_uuid, run_id)).fetchone()[0]


def _promote(conn, run_id, effect_class, contributor_class, magnitude="major"):
    return conn.execute(
        "INSERT INTO drugref.effect_contribution (effect_class_uuid, "
        "contributor_class_uuid, magnitude, source, ingest_run) "
        "VALUES (%s, %s, %s, 'DRUGREF', %s) RETURNING effect_contribution_id",
        (effect_class, contributor_class, magnitude, run_id)).fetchone()[0]


def _contributors(conn, effect_class):
    return dict(conn.execute(
        "SELECT moiety_uuid, magnitude FROM drugref.additive_effect_contributor "
        "WHERE effect_class_uuid = %s", (effect_class,)).fetchall())


@pytest.fixture
def bleeding(conn):
    """A curated effect with a descendant class, one promoted class, and an outsider.

        EFFECT (PE)                       PROMOTED (EPC)        OUTSIDE (EPC)
          |- warfarin  (direct member) <----- warfarin           |- ranitidine
          |- SUBCLASS (PE)
               |- apixaban (descendant member)

    `ranitidine` belongs to a class nobody promoted and to no effect member class, so
    it must never appear. `warfarin` is reachable through both the effect and the
    promotion, which is what makes it the regrade case.
    """
    run_id = _run(conn)
    effect = _class(conn, run_id, "EFFECT")
    subclass = _class(conn, run_id, "SUBCLASS")
    promoted = _class(conn, run_id, "PROMOTED", concept_type="EPC")
    outside = _class(conn, run_id, "OUTSIDE", concept_type="EPC")
    _edge(conn, run_id, effect, subclass)

    warfarin = _moiety(conn, run_id, "WARF01")
    apixaban = _moiety(conn, run_id, "APIX01")
    ranitidine = _moiety(conn, run_id, "RANI01")
    _member(conn, run_id, warfarin, effect)
    _member(conn, run_id, apixaban, subclass)
    _member(conn, run_id, warfarin, promoted, "has_EPC")
    _member(conn, run_id, ranitidine, outside, "has_EPC")

    _effect(conn, run_id, effect)
    return {
        "run_id": run_id, "effect": effect, "subclass": subclass,
        "promoted": promoted, "outside": outside,
        "warfarin": warfarin, "apixaban": apixaban, "ranitidine": ranitidine,
    }


# ---- the contributor set ----------------------------------------------------


def test_uncurated_members_default_to_minor(conn, bleeding):
    """Tension A: default-minor rather than default-excluded, because excluding
    uncurated members throws away the ingested-membership leverage that motivates
    the whole model."""
    assert _contributors(conn, bleeding["effect"])[bleeding["warfarin"]] == "minor"


def test_descendant_members_are_contributors(conn, bleeding):
    """Spec 5.2: the contributor set includes DAG descendants. Without this, an effect
    curated on a parent class would silently miss every drug MED-RT files one level
    down -- the same recall gap Plan B measured at 65% for contraindications."""
    assert bleeding["apixaban"] in _contributors(conn, bleeding["effect"])


def test_promotion_regrades_an_existing_contributor(conn, bleeding):
    _promote(conn, bleeding["run_id"], bleeding["effect"], bleeding["promoted"])
    assert _contributors(conn, bleeding["effect"])[bleeding["warfarin"]] == "major"


def test_promotion_never_recruits_a_non_member(conn, bleeding):
    """THE RULE AN IMPLEMENTER GETS WRONG. Promoting a class does not add its members
    to the effect -- it regrades the ones already there. Reading it the other way
    turns every promotion into a silent widening of who the effect applies to."""
    _promote(conn, bleeding["run_id"], bleeding["effect"], bleeding["outside"])
    assert bleeding["ranitidine"] not in _contributors(conn, bleeding["effect"])


def test_a_promotion_with_an_empty_intersection_changes_nothing(conn, bleeding):
    before = _contributors(conn, bleeding["effect"])
    _promote(conn, bleeding["run_id"], bleeding["effect"], bleeding["outside"])
    assert _contributors(conn, bleeding["effect"]) == before


def test_one_moiety_reached_by_two_promotions_appears_once_at_major(conn, bleeding):
    """SPEC 8's CONFLICT RULE, asserted directly because a consumer's COUNT depends on
    it. A duplicated row is the difference between firing and not firing at
    threshold_total = 2 -- one drug counted as two."""
    run_id = bleeding["run_id"]
    second = _class(conn, run_id, "PROMOTED2", concept_type="EPC")
    _member(conn, run_id, bleeding["warfarin"], second, "has_EPC")
    _promote(conn, run_id, bleeding["effect"], bleeding["promoted"], "major")
    _promote(conn, run_id, bleeding["effect"], second, "minor")

    rows = conn.execute(
        "SELECT magnitude FROM drugref.additive_effect_contributor "
        "WHERE effect_class_uuid = %s AND moiety_uuid = %s",
        (bleeding["effect"], bleeding["warfarin"])).fetchall()
    assert rows == [("major",)], "major must win, and the pair must appear exactly once"


def test_the_view_is_unique_on_effect_and_moiety(conn, bleeding):
    """The guarantee spec 8 says a consumer may rely on, asserted over the whole view
    rather than one row, so a future join that fans out fails here."""
    _promote(conn, bleeding["run_id"], bleeding["effect"], bleeding["promoted"])
    total, distinct = conn.execute(
        "SELECT count(*), count(DISTINCT (effect_class_uuid, moiety_uuid)) "
        "FROM drugref.additive_effect_contributor").fetchone()
    assert total == distinct


def test_a_superseded_promotion_stops_regrading(conn, bleeding):
    run_id = bleeding["run_id"]
    first = _promote(conn, run_id, bleeding["effect"], bleeding["promoted"], "major")
    second = _promote(conn, run_id, bleeding["effect"], bleeding["promoted"], "minor")
    conn.execute("UPDATE drugref.effect_contribution SET superseded_by = %s "
                 "WHERE effect_contribution_id = %s", (second, first))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert _contributors(conn, bleeding["effect"])[bleeding["warfarin"]] == "minor"


def test_a_superseded_effect_is_not_the_one_read(conn, bleeding):
    """Spec 10: a superseded additive_effect must stop firing. The view exposes the
    LIVE assertion's id, so this is answerable directly rather than inferred from a row
    count -- and a consumer joining back for thresholds gets the corrected ones."""
    run_id = bleeding["run_id"]
    old = conn.execute(
        "SELECT additive_effect_id FROM drugref.additive_effect "
        "WHERE effect_class_uuid = %s", (bleeding["effect"],)).fetchone()[0]
    new = _effect(conn, run_id, bleeding["effect"], major=2, total=3)
    conn.execute("UPDATE drugref.additive_effect SET superseded_by = %s "
                 "WHERE additive_effect_id = %s", (new, old))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    seen = {r[0] for r in conn.execute(
        "SELECT DISTINCT additive_effect_id FROM drugref.additive_effect_contributor "
        "WHERE effect_class_uuid = %s", (bleeding["effect"],)).fetchall()}
    assert seen == {new}, "the superseded assertion must contribute nothing"


def test_a_ruling_that_an_effect_does_not_accumulate_empties_the_view(conn, bleeding):
    """The retirement path. Supersession must point at a LATER row with the SAME
    natural key, so an effect can never be retired by superseding it with nothing --
    `accumulates = false` is how a curator says no, and the read path must honour it."""
    run_id = bleeding["run_id"]
    old = conn.execute(
        "SELECT additive_effect_id FROM drugref.additive_effect "
        "WHERE effect_class_uuid = %s", (bleeding["effect"],)).fetchone()[0]
    ruled_out = _rule_out(conn, run_id, bleeding["effect"])
    conn.execute("UPDATE drugref.additive_effect SET superseded_by = %s "
                 "WHERE additive_effect_id = %s", (ruled_out, old))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert _contributors(conn, bleeding["effect"]) == {}


def test_the_view_carries_provenance(conn, bleeding):
    """Spec 8: every view carries upstream_release / asserted_at so STALENESS is
    answerable from the read path itself -- the lesson db/006 applied to
    ddi_candidate_pair."""
    row = conn.execute(
        "SELECT upstream_release, asserted_at FROM drugref.additive_effect_contributor "
        "WHERE effect_class_uuid = %s LIMIT 1", (bleeding["effect"],)).fetchone()
    assert row[0] == "test-release" and row[1] is not None


# ---- interaction groups -----------------------------------------------------


@pytest.fixture
def whammy(conn):
    """The triple whammy: one group, three roles, one class each, plus a descendant."""
    run_id = _run(conn)
    group_uuid = ids.mint_group_uuid("DRUGREF", "TRIPLE_WHAMMY")
    conn.execute(
        "INSERT INTO drugref.interaction_group (group_uuid, source, source_code, "
        "first_seen_ingest) VALUES (%s, 'DRUGREF', 'TRIPLE_WHAMMY', %s)",
        (group_uuid, run_id))

    roles = {}
    for role, code in (("NSAID", "NSAIDCLS"), ("RAAS blocker", "RAASCLS"),
                       ("diuretic", "DIURCLS")):
        cls = _class(conn, run_id, code, concept_type="EPC")
        moiety = _moiety(conn, run_id, f"U{code}")
        _member(conn, run_id, moiety, cls, "has_EPC")
        conn.execute(
            "INSERT INTO drugref.interaction_group_member (group_uuid, role, "
            "class_uuid, satisfies_role, source, ingest_run) "
            "VALUES (%s, %s, %s, true, 'DRUGREF', %s)",
            (group_uuid, role, cls, run_id))
        roles[role] = {"class": cls, "moiety": moiety}
    return {"run_id": run_id, "group": group_uuid, "roles": roles}


def test_group_members_expand_to_moieties(conn, whammy):
    rows = conn.execute(
        "SELECT role, moiety_uuid FROM drugref.interaction_group_member_moiety "
        "WHERE group_uuid = %s", (whammy["group"],)).fetchall()
    assert len(rows) == 3
    assert {r for r, _ in rows} == {"NSAID", "RAAS blocker", "diuretic"}


def test_group_roles_expand_over_descendants(conn, whammy):
    """The spec is SILENT on this, so db/021 states it: a role inherits down the DAG,
    for the same reason a grade does, plus db/010's safety direction -- a group that
    fires is an advisory, and missing a member is the harm direction."""
    run_id = whammy["run_id"]
    child = _class(conn, run_id, "COXIB", concept_type="EPC")
    _edge(conn, run_id, whammy["roles"]["NSAID"]["class"], child)
    celecoxib = _moiety(conn, run_id, "CELE01")
    _member(conn, run_id, celecoxib, child, "has_EPC")

    covered = conn.execute(
        "SELECT moiety_uuid FROM drugref.interaction_group_member_moiety "
        "WHERE group_uuid = %s AND role = 'NSAID'", (whammy["group"],)).fetchall()
    assert celecoxib in [m for (m,) in covered]


def _retire_member(conn, run_id, group_uuid, role, class_uuid):
    """Retire a role member the only way an append-only overlay can: assert `false`,
    then point the `true` row at it."""
    older = conn.execute(
        "SELECT interaction_group_member_id FROM drugref.interaction_group_member "
        "WHERE group_uuid = %s AND role = %s AND class_uuid = %s AND superseded_by IS NULL",
        (group_uuid, role, class_uuid)).fetchone()[0]
    newer = conn.execute(
        "INSERT INTO drugref.interaction_group_member (group_uuid, role, class_uuid, "
        "satisfies_role, source, ingest_run) VALUES (%s, %s, %s, false, 'DRUGREF', %s) "
        "RETURNING interaction_group_member_id",
        (group_uuid, role, class_uuid, run_id)).fetchone()[0]
    conn.execute("UPDATE drugref.interaction_group_member SET superseded_by = %s "
                 "WHERE interaction_group_member_id = %s", (newer, older))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_a_role_survives_while_one_live_member_remains(conn, whammy):
    """Spec 5.3: required roles are SELECT DISTINCT role over LIVE members. Retiring
    ONE of two classes that satisfy a role must leave the role standing."""
    run_id = whammy["run_id"]
    group = whammy["group"]
    spare = _class(conn, run_id, "THIAZIDE", concept_type="EPC")
    spare_drug = _moiety(conn, run_id, "THIA01")
    _member(conn, run_id, spare_drug, spare, "has_EPC")
    conn.execute(
        "INSERT INTO drugref.interaction_group_member (group_uuid, role, class_uuid, "
        "satisfies_role, source, ingest_run) "
        "VALUES (%s, 'diuretic', %s, true, 'DRUGREF', %s)", (group, spare, run_id))

    _retire_member(conn, run_id, group, "diuretic",
                   whammy["roles"]["diuretic"]["class"])

    covered = conn.execute(
        "SELECT moiety_uuid FROM drugref.interaction_group_member_moiety "
        "WHERE group_uuid = %s AND role = 'diuretic'", (group,)).fetchall()
    assert [m for (m,) in covered] == [spare_drug]


def test_retiring_the_last_member_of_a_role_removes_the_role(conn, whammy):
    """The sentence spec 5.3 requires and the schema could not express until
    satisfies_role existed: with no live TRUE member the role is simply ABSENT, so a
    consumer computing 'every distinct role covered' is never handed a role nothing
    can satisfy -- a group that could never fire again."""
    _retire_member(conn, whammy["run_id"], whammy["group"], "diuretic",
                   whammy["roles"]["diuretic"]["class"])
    live_roles = {r for (r,) in conn.execute(
        "SELECT DISTINCT role FROM drugref.interaction_group_member_moiety "
        "WHERE group_uuid = %s", (whammy["group"],)).fetchall()}
    assert live_roles == {"NSAID", "RAAS blocker"}
