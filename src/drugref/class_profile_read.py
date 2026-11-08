"""Read side of class profiles: a thin Python reader over the class-profile views.

The SQL views (db/059-db/065; db/062, db/064 and db/065 replace class_property,
db/063 class_property_member) are the consumer contract -- a prescribing front end reads
them directly. This module only turns their rows into frozen dataclasses with NAMES
instead of UUIDs so the `drugref profile` command can print them. All SQL lives here.

Every tuple is sorted (by name, then relationship) so output and tests are stable.
"""
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class PropertyLine:
    relationship: str
    object_kind: str          # 'condition' | 'moiety' | 'class'
    object_uuid: uuid.UUID
    object_name: str


@dataclass(frozen=True)
class ClassProperty:
    prop: PropertyLine
    is_asserted: bool
    asserted_via: tuple[str, ...]      # class names (the class itself and/or ancestors)
    asserted_by: tuple[str, ...]       # sources
    is_observed: bool
    carriers: int
    members: int
    not_confirmed: tuple[str, ...]     # drug names
    confirmed_exceptions: tuple[str, ...]


@dataclass(frozen=True)
class SubclassOnlyDrug:
    """A drug reaching the class only through a subclass: not a member, not counted.

    Spec §4: the class's ASSERTED properties pass down to it (observed ones do not), so
    `inherits` lists them, less its confirmed exceptions and any interaction with
    itself. Only what is asserted on THIS class or its ancestors: what the `via` class
    asserts belongs to the via class's own profile. A drug in two subclasses appears
    once per `via` class. The subtraction is class_subclass_only_property's (db/065).
    """
    name: str
    via: str
    inherits: tuple[PropertyLine, ...]
    confirmed_exceptions: tuple[PropertyLine, ...]


@dataclass(frozen=True)
class LabelStatement:
    """What one drug's drug labels say about this class: a MACHINE-READ statement.

    Deliberately NOT a ClassProperty (owner decision, design 6.3): a phrase-matching
    reader's level stays in its own section until a curator confirms it. A
    `does_not_hold` ruling does not hide the row; it is shown beside it (`ruling`),
    because hiding it would erase that the labels do say something.
    """
    drug: str
    strongest_level: str     # contraindicated | avoid | adjust_dose | monitor | ...
    label_count: int
    mixed: bool              # more than one strength level fired in a cited sentence
    ambiguous: bool          # the class name also matches another class
    subject_in_class: bool   # the drug is itself a member (a label on its own class)
    ruling: str | None       # live curated verdict for drug -> interacts_with -> class
    # label_count spans every level; level_labels is how many say strongest_level.
    level_labels: int
    cite_set_id: str         # ONE citing label at the strongest level (design 6.3)
    cite_version: str
    quote: str | None        # its existing quote window over the span; None if none


@dataclass(frozen=True)
class ClassProfile:
    class_uuid: uuid.UUID
    class_name: str
    source: str
    concept_type: str
    members: tuple[str, ...]
    properties: tuple[ClassProperty, ...]
    peculiarities: Mapping[str, tuple[PropertyLine, ...]]
    subclass_only: tuple[SubclassOnlyDrug, ...]
    # Defaulted so a profile built without label evidence stays valid.
    label_statements: tuple[LabelStatement, ...] = ()


@dataclass(frozen=True)
class DrugLens:
    """One drug through one class: the FOUR-WAY split in class_property_member's
    COMMENT. not_confirmed is rendered, never dropped: dropped, it reads as "does not
    apply"."""
    class_uuid: uuid.UUID
    class_name: str
    shared: tuple[tuple[PropertyLine, str], ...]  # standing: carries | inherits[ via X]
    peculiar: tuple[PropertyLine, ...]
    not_confirmed: tuple[PropertyLine, ...]
    confirmed_exceptions: tuple[PropertyLine, ...]


@dataclass(frozen=True)
class DrugProfile:
    moiety_uuid: uuid.UUID
    name: str
    class_tags: tuple[tuple[uuid.UUID, str], ...]
    lenses: tuple[DrugLens, ...]
    own_properties: tuple[PropertyLine, ...]  # filled only for a drug with no class tag


def _names(conn: psycopg.Connection, uuids) -> dict[uuid.UUID, str]:
    """uuid -> display name over conditions, moieties and classes."""
    rows = conn.execute(
        "SELECT condition_uuid, name FROM drugref.condition "
        "WHERE condition_uuid = ANY(%(u)s) "
        "UNION ALL SELECT moiety_uuid, display_name FROM drugref.substance_moiety "
        "WHERE moiety_uuid = ANY(%(u)s) "
        "UNION ALL SELECT class_uuid, class_name FROM drugref.substance_class "
        "WHERE class_uuid = ANY(%(u)s)", {"u": list(set(uuids))}).fetchall()
    return dict(rows)


def _line(names, rel, kind, obj) -> PropertyLine:
    return PropertyLine(rel, kind, obj, names.get(obj, str(obj)))


def _sorted_lines(lines) -> tuple[PropertyLine, ...]:
    return tuple(sorted(lines, key=lambda p: (p.object_name, p.relationship)))


def class_profile(conn: psycopg.Connection, class_uuid: uuid.UUID) -> ClassProfile:
    head = conn.execute(
        "SELECT class_name, source, concept_type FROM drugref.substance_class "
        "WHERE class_uuid = %s", (class_uuid,)).fetchone()
    if head is None:
        raise LookupError(f"no class {class_uuid}")
    if head[1:] == ("MED-RT", "PK"):
        # Every view excludes these (#155); an empty profile would read as "no members".
        raise LookupError(f"{class_uuid} is a MED-RT PK class: not profiled (#155)")
    props = conn.execute(
        "SELECT relationship, object_kind, object_uuid, is_asserted, asserted_via, "
        "asserted_by, is_observed, carriers, members "
        "FROM drugref.class_property WHERE class_uuid = %s", (class_uuid,)).fetchall()
    standing = conn.execute(
        "SELECT relationship, object_kind, object_uuid, moiety_uuid, standing "
        "FROM drugref.class_property_member WHERE class_uuid = %s",
        (class_uuid,)).fetchall()
    pec = conn.execute(
        "SELECT moiety_uuid, relationship, object_kind, object_uuid "
        "FROM drugref.class_member_peculiarity WHERE class_uuid = %s",
        (class_uuid,)).fetchall()
    sub = conn.execute(
        "SELECT moiety_uuid, via_class_uuid FROM drugref.class_subclass_only_member "
        "WHERE class_uuid = %s", (class_uuid,)).fetchall()
    members = [r[0] for r in conn.execute(
        "SELECT moiety_uuid FROM drugref.class_profile_member WHERE class_uuid = %s",
        (class_uuid,))]
    # What each subclass-only drug inherits (db/065, #209): asserted only, less its
    # confirmed exceptions and any interaction with itself -- the subtraction is SQL's.
    inherited = conn.execute(
        "SELECT moiety_uuid, via_class_uuid, relationship, object_kind, object_uuid, "
        "standing FROM drugref.class_subclass_only_property WHERE class_uuid = %s",
        (class_uuid,)).fetchall() if sub else []

    ids = {p[2] for p in props} | {s[3] for s in standing} | {p[0] for p in pec} \
        | {p[3] for p in pec} | {s[0] for s in sub} | {s[1] for s in sub} | set(members)
    ids |= {c for p in props for c in p[4]}
    # Machine-read label statements (db/066): one row per drug, LEFT JOINed to the live
    # (superseded_by IS NULL) ruling. Ordered strongest first; the two levels that are
    # not a strength have a NULL rank and sort last, then most labels, then drug name
    # (names only exist in Python, so the final order is applied below).
    stmts = conn.execute(
        "SELECT st.subject_moiety, st.strongest_level, st.label_count, st.any_mixed, "
        "st.any_ambiguous, st.subject_in_class, r.verdict, al.strength_rank, "
        "(st.level_counts ->> st.strongest_level)::int, c.set_id, c.version, "
        "c.quote_text "
        "FROM drugref.spl_class_statement st "
        "JOIN drugref.spl_advice_level al ON al.level = st.strongest_level "
        "LEFT JOIN drugref.curated_property_ruling r "
        "  ON r.subject_kind = 'moiety' AND r.subject_uuid = st.subject_moiety "
        " AND r.relationship = 'interacts_with' AND r.object_kind = 'class' "
        " AND r.object_uuid = st.class_uuid AND r.superseded_by IS NULL "
        "JOIN LATERAL ( "     # citation: newest strongest-level label
        "  SELECT e.set_id, e.version, e.quote_text "
        "  FROM drugref.spl_class_statement_evidence e "
        "  WHERE e.subject_moiety = st.subject_moiety AND e.class_uuid = st.class_uuid "
        "    AND e.advice_level = st.strongest_level "
        "  ORDER BY e.effective_time DESC NULLS LAST, e.set_id, e.version, "
        "           e.char_start, e.quote_text NULLS LAST LIMIT 1) c ON true "
        "WHERE st.class_uuid = %s "
        "ORDER BY al.strength_rank NULLS LAST, st.label_count DESC",   # names: below
        (class_uuid,)).fetchall()
    ids |= {s[0] for s in stmts}
    names = _names(conn, ids)

    by_prop: dict[tuple, dict[str, list[str]]] = {}
    for rel, kind, obj, mo, st in standing:
        by_prop.setdefault((rel, kind, obj), {}).setdefault(st, []).append(names[mo])

    out = []
    for rel, kind, obj, asserted, via, by, observed, carriers, n in props:
        st = by_prop.get((rel, kind, obj), {})
        out.append(ClassProperty(
            _line(names, rel, kind, obj), asserted,
            tuple(sorted(names[c] for c in via)), tuple(sorted(by)), observed,
            carriers, n, tuple(sorted(st.get("not_confirmed", ()))),
            tuple(sorted(st.get("confirmed_exception", ())))))
    out.sort(key=lambda c: (c.prop.object_name, c.prop.relationship))

    by_route: dict[tuple, dict[str, list[PropertyLine]]] = {}
    for mo, via, rel, kind, obj, st in inherited:
        by_route.setdefault((mo, via), {}).setdefault(st, []).append(
            _line(names, rel, kind, obj))
    subclass_only = []
    for mo, via in sub:            # every route, including one that inherits nothing
        st = by_route.get((mo, via), {})
        subclass_only.append(SubclassOnlyDrug(
            names[mo], names[via], _sorted_lines(st.get("inherits", ())),
            _sorted_lines(st.get("confirmed_exception", ()))))
    subclass_only.sort(key=lambda e: (e.name, e.via))

    label_statements = tuple(sorted(
        ((rank is None, rank or 0, -n, names[m],
          LabelStatement(names[m], lvl, n, mixed, amb, own, ruling, k, sid, ver, q))
         for m, lvl, n, mixed, amb, own, ruling, rank, k, sid, ver, q in stmts),
        key=lambda t: t[:4]))
    label_statements = tuple(t[4] for t in label_statements)

    pecs: dict[str, list[PropertyLine]] = {}
    for mo, rel, kind, obj in pec:
        pecs.setdefault(names[mo], []).append(_line(names, rel, kind, obj))
    return ClassProfile(
        class_uuid, head[0], head[1], head[2], tuple(sorted(names[m] for m in members)),
        tuple(out), {k: _sorted_lines(v) for k, v in sorted(pecs.items())},
        tuple(subclass_only), label_statements)


def drug_profile(conn: psycopg.Connection, moiety_uuid: uuid.UUID,
                 lens_class_uuids: Sequence[uuid.UUID] = ()) -> DrugProfile:
    """The drug seen through each lens class; no lenses given means every class tag."""
    row = conn.execute("SELECT display_name FROM drugref.substance_moiety "
                       "WHERE moiety_uuid = %s", (moiety_uuid,)).fetchone()
    if row is None:
        raise LookupError(f"no moiety {moiety_uuid}")
    tags = conn.execute(
        "SELECT m.class_uuid, c.class_name FROM drugref.class_profile_member m "
        "JOIN drugref.substance_class c USING (class_uuid) WHERE m.moiety_uuid = %s "
        "ORDER BY c.class_name, m.class_uuid", (moiety_uuid,)).fetchall()
    tag_ids = {t[0] for t in tags}
    foreign = [u for u in lens_class_uuids if u not in tag_ids]
    if foreign:
        # A lens must be a profiled class the drug is a DIRECT member of; a parent class
        # it reaches only through a subclass, or a MED-RT PK class, is refused too.
        raise ValueError("not a profiled class this drug is a direct member of: "
                         f"{', '.join(map(str, foreign))}")
    chosen = [t for t in tags if not lens_class_uuids or t[0] in set(lens_class_uuids)]

    # ONE query per view for every lens at once, grouped below: one round trip per view,
    # and the part every class shares (member_property, class_subtree) is paid once.
    cus = [t[0] for t in chosen]
    standing_rows = conn.execute(
        "SELECT pm.class_uuid, pm.relationship, pm.object_kind, pm.object_uuid, "
        "pm.standing, cp.asserted_via FROM drugref.class_property_member pm "
        "JOIN drugref.class_property cp USING (class_uuid, relationship, "
        "object_kind, object_uuid) "
        "WHERE pm.class_uuid = ANY(%s) AND pm.moiety_uuid = %s",
        (cus, moiety_uuid)).fetchall() if cus else []
    pec_rows = conn.execute(
        "SELECT class_uuid, relationship, object_kind, object_uuid "
        "FROM drugref.class_member_peculiarity "
        "WHERE class_uuid = ANY(%s) AND moiety_uuid = %s",
        (cus, moiety_uuid)).fetchall() if cus else []
    # No profiled class tag: nothing to see it through, so show what it has itself.
    own_rows = [] if tags else conn.execute(
        "SELECT relationship, object_kind, object_uuid FROM drugref.member_property "
        "WHERE moiety_uuid = %s", (moiety_uuid,)).fetchall()
    names = _names(conn, {r[3] for r in standing_rows} | {r[3] for r in pec_rows}
                   | {a for r in standing_rows for a in r[5]}
                   | {r[2] for r in own_rows})

    groups = {cu: {"shared": [], "peculiar": [], "not_confirmed": [],
                   "confirmed_exception": []} for cu in cus}
    for cu, rel, kind, obj, st, via in standing_rows:
        line = _line(names, rel, kind, obj)
        if st == "carries":                # its own record: never "via" anything
            groups[cu]["shared"].append((line, st))
        elif st == "inherits":
            if cu not in via:              # asserted only on ancestors
                st = f"{st} via {', '.join(sorted(names[a] for a in via))}"
            groups[cu]["shared"].append((line, st))
        else:
            groups[cu][st].append(line)
    for cu, *prop in pec_rows:
        groups[cu]["peculiar"].append(_line(names, *prop))

    lenses = []
    for cu, cname in chosen:
        g = groups[cu]
        shared = sorted(g["shared"],
                        key=lambda s: (s[0].object_name, s[0].relationship))
        lenses.append(DrugLens(cu, cname, tuple(shared), _sorted_lines(g["peculiar"]),
                               _sorted_lines(g["not_confirmed"]),
                               _sorted_lines(g["confirmed_exception"])))
    return DrugProfile(moiety_uuid, row[0], tuple(tags), tuple(lenses),
                       _sorted_lines(_line(names, *r) for r in own_rows))
