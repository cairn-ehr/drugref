"""Read side of allergy groups: a thin Python wrapper over drugref.allergy_check.

All the logic lives in SQL (db/068), because cairn-ehr calls that function directly
from the same Postgres; a second copy here could drift from it. This module only
turns its rows into frozen dataclasses.

THREE OUTCOMES, NEVER TWO. `match`, `no-match`, or `unplaced` (with a reason). A
wrong `no-match` is a missed allergy warning, so `unplaced` means "not checked" and
must never be shown as safe. Known gap: no penicillin-cephalosporin rule yet, so
cephalexin against a penicillin allergen answers `no-match` (spec section 2).

WHY THE FOLD IS NOT moiety_active_in_composite. Both sides are folded to EVERY
component their substance has in substance_composition (db/068's
moiety_allergy_fold), whatever is_active_component says. db/028's
moiety_active_in_composite keeps only ACTIVE components, and GSRS's "active" means
pharmacologically active, which is not what triggers an allergy: clemizole
penicillin marks penicillin G is_active_component FALSE, so an active-only fold
would answer a penicillin allergen with a silent no-match (owner's decision,
2026-11-09). The accepted trade: a salt's partner matches too -- penicillin G
procaine warns a procaine-allergic patient -- and each match names the component
that matched so the warning can say "via its procaine component".
"""
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class AllergyMatch:
    """One path from the allergen to the prescribed drug."""
    kind: str                            # 'same-drug' | 'same-group'
    group_class_uuid: uuid.UUID | None   # None for same-drug
    group_name: str | None
    group_mesh_ui: str | None            # the group's MeSH root, e.g. 'D010406'
    review_status: str | None            # 'structural-seed' | 'reviewed'
    allergen_component: uuid.UUID | None  # None when the allergen IS a group
    prescribed_component: uuid.UUID


@dataclass(frozen=True)
class AllergyAnswer:
    """drugref.allergy_check's answer: one outcome, and every path when it matches."""
    outcome: str                         # 'match' | 'no-match' | 'unplaced'
    reason: str | None                   # set only for 'unplaced'
    matches: tuple[AllergyMatch, ...]    # empty unless outcome == 'match'


def check(conn: psycopg.Connection, prescribed: uuid.UUID,
          allergen: uuid.UUID) -> AllergyAnswer:
    """Ask drugref.allergy_check; every row it returns shares one outcome/reason."""
    # By column NAME (dict_row), so a column added to the function can never
    # silently shift allergen_component into prescribed_component.
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("SELECT * FROM drugref.allergy_check(%s, %s)",
                           (prescribed, allergen)).fetchall()
    matches = tuple(
        AllergyMatch(kind=r["match_kind"], group_class_uuid=r["group_class_uuid"],
                     group_name=r["group_name"], group_mesh_ui=r["group_mesh_ui"],
                     review_status=r["review_status"],
                     allergen_component=r["allergen_component"],
                     prescribed_component=r["prescribed_component"])
        for r in rows if r["outcome"] == "match")
    return AllergyAnswer(outcome=rows[0]["outcome"], reason=rows[0]["reason"],
                         matches=matches)
