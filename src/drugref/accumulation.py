"""The accumulation model (Plan C): its evaluation rules, and its only writer.

Two halves, deliberately separated:

  * PURE FUNCTIONS -- `fires` and `group_fires` -- encode the two rules a consumer
    applies to what drugref publishes. drugref does NOT evaluate on a consumer's
    behalf (spec 8 keeps the global tier stateless and free of patient data), but the
    rules are small, exact, and stated in a COMMENT ON, so handing them out as code is
    what stops every consumer re-implementing them slightly differently.
  * WRITERS -- the single-writer role classes.py, interactions.py and questions.py
    already play for their tables. Every one of them is INSERT-then-point, in that
    order, because superseded_by must reference a row that already exists.

WHY THE WRITERS LOOK REPETITIVE. Each curation function inserts the new assertion and
then calls overlay.supersede to point the previous live one at it. That pair is the
only sequence the overlay admits, and overlay.py's docstring is where the reason lives
-- stated once, because three modules used to restate it.
"""
import uuid

import psycopg

from drugref import ids, overlay


# ---- the evaluation rules (pure) --------------------------------------------


def fires(majors: int, contributors: int,
          threshold_major: int | None, threshold_total: int | None) -> bool:
    """Does an additive effect reach the threshold at which it is worth saying?

    `majors` and `contributors` are counted over `additive_effect_contributor` for one
    effect, intersected with the patient's regimen; the two thresholds come from the
    live `additive_effect` row. Every major IS a contributor, so `majors` is a subset
    count of `contributors`, never a separate population.

    The three realistic encodings this expresses:

        (0, 2)  any two contributors            -- a fully curated effect
        (1, 2)  a major plus anything else      -- the recommended default
        (1, 1)  a major alone is worth saying

    Raises rather than guessing on impossible inputs. A threshold answered from
    miscounted inputs is worse than an error: it is a clinical judgement resting on a
    number nobody checked, and `majors > contributors` means the caller counted two
    different populations.

    THE THRESHOLDS MAY ARRIVE AS None. They are nullable in the schema, because a
    curator who rules that an effect does NOT accumulate states none of them. Calling
    this with such a row asks a question the row does not answer, so it gets the same
    deliberate ValueError as the other impossible inputs -- not a bare TypeError from
    the `>=` below, which reads as a bug in here rather than a mistake out there.
    """
    if majors < 0 or contributors < 0:
        raise ValueError(
            f"counts may not be negative "
            f"(majors={majors}, contributors={contributors})")
    if majors > contributors:
        raise ValueError(
            f"majors ({majors}) exceeds contributors ({contributors}): every major is "
            "itself a contributor, so these must be counted over ONE population")
    if threshold_major is None or threshold_total is None:
        raise ValueError(
            f"both thresholds are required (threshold_major={threshold_major}, "
            f"threshold_total={threshold_total}). A row with NULL thresholds is a "
            "ruling that the effect does NOT accumulate -- read `accumulates` and do "
            "not evaluate it")
    return majors >= threshold_major and contributors >= threshold_total


def group_fires(required_roles: set[str], covered_roles: set[str]) -> bool:
    """Does a regimen cover every distinct role an interaction group names?

    `required_roles` is `SELECT DISTINCT role` over the group's LIVE members;
    `covered_roles` is the subset of those the patient's drugs actually satisfy.

    ROLES ARE A SET, which is the whole reason groups exist beside accumulation: two
    NSAIDs cover the `NSAID` role once, so counting drugs would fire the triple whammy
    on three NSAIDs -- a far weaker claim than one NSAID plus a RAAS blocker plus a
    diuretic.

    AN EMPTY REQUIRED SET NEVER FIRES, and that is not pedantry. A group whose members
    have all been retired has no roles, and `set() <= anything` is true -- so the
    natural subset test would fire such a group on EVERY regimen, including an empty
    one. Spec 5.3 says retiring the last member of a role removes the role; it does
    not say the group then applies to everybody.
    """
    if not required_roles:
        return False
    return required_roles <= covered_roles


# ---- the writers ------------------------------------------------------------


def curate_effect(conn: psycopg.Connection, effect_class_uuid: uuid.UUID,
                  ingest_run_id: int, *, accumulates: bool,
                  threshold_major: int | None = None,
                  threshold_total: int | None = None,
                  severity: str | None = None,
                  clinical_note: str | None = None) -> int:
    """Record (or correct) whether an effect class accumulates, and on what threshold.

    `accumulates=False` is a real ruling, not an absence: it records that a curator
    looked and decided this effect does not add up, which is what lets the class leave
    gap_uncurated_additive_effect instead of being asked about every release forever.
    A false ruling carries no thresholds and no severity -- db/020's CHECK enforces it.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.additive_effect (effect_class_uuid, accumulates, "
        "threshold_major, threshold_total, severity, clinical_note, source, "
        "ingest_run) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'DRUGREF', %s) RETURNING additive_effect_id",
        (effect_class_uuid, accumulates, threshold_major, threshold_total, severity,
         clinical_note, ingest_run_id)).fetchone()[0]
    overlay.supersede(conn, "additive_effect", "additive_effect_id", new_id,
                      ("effect_class_uuid",), (effect_class_uuid,))
    return new_id


def grade_contribution(conn: psycopg.Connection, effect_class_uuid: uuid.UUID,
                       contributor_class_uuid: uuid.UUID, magnitude: str,
                       ingest_run_id: int) -> int:
    """Grade a contributor CLASS for an effect. Promotion regrades; it never recruits.

    An explicit `minor` is not a no-op: it records that a curator looked at this class
    and it really is minor, which is a different fact from "nobody has looked" even
    though both grade to minor. That distinction is the whole basis of
    gap_ungraded_contribution's queue being finite.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.effect_contribution (effect_class_uuid, "
        "contributor_class_uuid, magnitude, source, ingest_run) "
        "VALUES (%s, %s, %s, 'DRUGREF', %s) RETURNING effect_contribution_id",
        (effect_class_uuid, contributor_class_uuid, magnitude,
         ingest_run_id)).fetchone()[0]
    overlay.supersede(conn, "effect_contribution", "effect_contribution_id", new_id,
                      ("effect_class_uuid", "contributor_class_uuid"),
                      (effect_class_uuid, contributor_class_uuid))
    return new_id


def register_group(conn: psycopg.Connection, source_code: str,
                   ingest_run_id: int) -> uuid.UUID:
    """Mint (or re-derive) an interaction group's immortal identity.

    Idempotent: group_uuid is a pure function of the code, so calling this twice
    returns the same UUID and writes one row. The identity carries no assertion, so
    there is nothing here a later call could need to correct.
    """
    group_uuid = ids.mint_group_uuid("DRUGREF", source_code)
    conn.execute(
        "INSERT INTO drugref.interaction_group (group_uuid, source, source_code, "
        "first_seen_ingest) VALUES (%s, 'DRUGREF', %s, %s) "
        "ON CONFLICT (group_uuid) DO NOTHING",
        (group_uuid, source_code, ingest_run_id))
    return group_uuid


def assert_group(conn: psycopg.Connection, group_uuid: uuid.UUID, name: str,
                 severity: str, ingest_run_id: int,
                 clinical_note: str | None = None, *, applies: bool = True) -> int:
    """Record (or correct) what drugref claims ABOUT a group -- including "no longer".

    Separate from the identity so that retiring or restating a group never touches the
    UUID its members and any external citation point at.

    RETIRING A GROUP IS `applies=False` (db/023), the same move `satisfies_role=False`
    makes for a single member. Supersession must point at a later row carrying the same
    natural key, so an explicit false is the only way an append-only table can say
    "drugref no longer asserts this". Retiring every member one at a time also stops
    the group firing -- an empty required-role set never fires, see `group_fires` --
    but it takes one INSERT per member and leaves a live assertion still claiming a
    severity for a group that cannot fire.

    WHY THIS DEFAULTS TO True WHEN THE COLUMN REFUSES A DEFAULT, since `accumulates`
    and `satisfies_role` are both required of their callers. Those two are curation
    OUTCOMES -- a curator weighs the evidence and rules either way, so letting either
    end of that be assumed is letting the answer be guessed. `applies` is not an
    outcome but an ACT: asserting and retiring are different things to do, and a caller
    reaching `assert_group` with a name and a severity has already chosen the first.
    The column still refuses a DEFAULT, so nothing reaching the table by any other
    route gets to leave it unanswered.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.interaction_group_assertion (group_uuid, name, severity, "
        "clinical_note, applies, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s, 'DRUGREF', %s) "
        "RETURNING interaction_group_assertion_id",
        (group_uuid, name, severity, clinical_note, applies,
         ingest_run_id)).fetchone()[0]
    overlay.supersede(conn, "interaction_group_assertion",
                      "interaction_group_assertion_id", new_id,
                      ("group_uuid",), (group_uuid,))
    return new_id


def set_group_member(conn: psycopg.Connection, group_uuid: uuid.UUID, role: str,
                     class_uuid: uuid.UUID, satisfies_role: bool,
                     ingest_run_id: int) -> int:
    """State whether a class satisfies a role in a group -- including "no longer".

    RETIRING A MEMBER IS `satisfies_role=False`, not a DELETE and not an absence. The
    overlay's supersession must point at a later row carrying the same natural key, so
    an explicit false is the only way an append-only table can say "this class stopped
    satisfying this role" -- and it is what makes spec 5.3's "superseding the last
    member of a role removes the role" actually true.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.interaction_group_member (group_uuid, role, class_uuid, "
        "satisfies_role, source, ingest_run) VALUES (%s, %s, %s, %s, 'DRUGREF', %s) "
        "RETURNING interaction_group_member_id",
        (group_uuid, role, class_uuid, satisfies_role, ingest_run_id)).fetchone()[0]
    overlay.supersede(conn, "interaction_group_member",
                      "interaction_group_member_id", new_id,
                      ("group_uuid", "role", "class_uuid"),
                      (group_uuid, role, class_uuid))
    return new_id


# ---- reading, for a consumer that wants the rule applied here ---------------


def effect_counts(conn: psycopg.Connection, effect_class_uuid: uuid.UUID,
                  regimen: list[uuid.UUID]) -> tuple[int, int]:
    """(majors, contributors) for one effect over one regimen.

    A convenience over `additive_effect_contributor`, which is unique on
    (effect, moiety) -- so this counts drugs, never rows. A consumer computing the
    same numbers from a non-deduplicated join would get them wrong, which is why
    spec 8 states that uniqueness as part of the contract rather than leaving it to
    be inferred.
    """
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE magnitude = 'major'), count(*) "
        "FROM drugref.additive_effect_contributor "
        "WHERE effect_class_uuid = %s AND moiety_uuid = ANY(%s)",
        (effect_class_uuid, list(regimen))).fetchone()
    return row[0], row[1]
