"""Reads of the IDENTITY SPINE -- does drugref know this moiety at all? (issue 120).

WHY A MODULE OF ITS OWN, and the two candidates it is deliberately not part of. This
project splits reads from writes by tier, and this read is in neither existing tier:

  * `curated_read.py` scopes itself to the curated overlay, and that scope is
    load-bearing rather than decorative: `effective_grades_for`'s own docstring is where
    this project wrote down that a view whose population is GRADES cannot answer a
    question about DRUGS. `substance_moiety` is slice 1's append-only identity spine, a
    tier below the overlay, and a module that spanned both would erase exactly the
    boundary issue 120 is about.
  * `classes.py` declares itself "the ONLY module that writes the classification
    tables". A registry-existence check is neither a write nor classification.

So the honest answer was a third home, following the split `curated_read.py` itself made
when `curation.py` owned the writes. It is small on purpose; the spine's other reads can
land here as they are needed.

⇒ AND THE SPINE'S BULK READ LANDED HERE IN 2026-09 (issue 172), which is the
sentence above being taken at its word. `Registry` / `load_registry` -- the
name -> moiety_uuid and UNII -> moiety_uuid lookups the SPL ingest resolves every
subject and every occurrence through -- lived in `spl_evidence.py`, a module whose
first sentence is "the SOLE writer of drugref's SPL rows". A read path inside the
sole writer is the boundary this file exists to keep, and the line count made the
argument unavoidable: rule 4 caps a module at ~500 lines and `spl_evidence.py`
reached 518 when the issue-174 ANALYZE guard landed. It reads `substance_moiety`
and `identity_claim` and nothing else, so it was always a spine read wearing an
SPL coat. `tests/test_spl_evidence_cap.py` is the gate that stops the next block
landing there instead.

WHAT IT IS FOR. `drugref interactions <uuid>` printed "no curated grade" both for a drug
drugref knows and has not graded -- the ordinary case, since the overlay is small by
design -- and for a uuid naming nothing whatsoever. Two very different states, one
rendering, exit 0 either way, and the pair form additionally asserting that drugref
"holds no curated grade for this pair", about a pair that may not exist. The harm
direction is UNDER-WARNING: an absent answer reading as "checked, nothing found" is the
one thing `cli_interactions.py`'s own docstring says the command exists to avoid.
"""
import uuid
from dataclasses import dataclass

import psycopg

# THE TABLE NAME, EXPORTED, so `cli_interactions`' migration guard probes the relation
# these reads actually name rather than a hand-copied second spelling (issue 122). One
# home per name: a rename that missed the guard would leave it reporting a healthy
# database's spine permanently absent.
MOIETY_TABLE = "drugref.substance_moiety"

# `= ANY(%s)` RATHER THAN AN `IN (...)` BUILT BY STRING JOIN, which is the shape that
# invites a uuid into the SQL text. psycopg adapts a Python list to a Postgres array, so
# the whole variadic call is ONE round trip with ONE parameter, and the query text is
# constant no matter how many identifiers are asked about.
_KNOWN = f"""
SELECT moiety_uuid
FROM   {MOIETY_TABLE}
WHERE  moiety_uuid = ANY(%s)
"""

# `EXISTS` RATHER THAN `count(*)`: the question is "any at all", and on a spine holding
# millions of rows a count would read every one of them to answer it.
_ANY_MOIETY = f"SELECT EXISTS (SELECT 1 FROM {MOIETY_TABLE})"


def known_moieties(conn: psycopg.Connection,
                   *moiety_uuids: uuid.UUID) -> set[uuid.UUID]:
    """Which of `moiety_uuids` name a registered moiety. Absent ones are simply missing.

    RETURNS THE KNOWN ONES, not the unknown ones, and the direction matters at the call
    site: a caller subtracts to get what it must warn about, so the warning is derived
    from a positive fact drugref actually holds rather than from this function's opinion
    about what is missing.

    A SET, not a list: callers ask "is this one in it", order carries no meaning, and a
    duplicate in the argument list collapses rather than double-counting. The CLI's only
    call site dedupes first for its own reasons (it reports the identifiers back in the
    order they were given), so that collapse is a property of this function rather than
    something the caller currently relies on.

    THE EMPTY CALL SHORT-CIRCUITS, and that is a correctness guard rather than an
    optimisation. `moiety_uuid = ANY('{}')` is false for every row, so the SQL would in
    fact answer correctly here -- but the failure it protects against is the one that
    would matter: any future rewrite of this predicate that mishandles the empty array
    would make an existence check silently AFFIRMATIVE, which is the under-warning
    direction issue 120 exists to close.
    """
    if not moiety_uuids:
        return set()
    return {row[0] for row in
            conn.execute(_KNOWN, (list(moiety_uuids),)).fetchall()}


def registry_is_empty(conn: psycopg.Connection) -> bool:
    """Whether the spine holds NO moieties at all -- not the same as "not this one".

    WHY A CALLER NEEDS THIS TO SAY THE FIRST THING HONESTLY. `known_moieties` returning
    nothing has two causes that look identical at the call site: the identifier is not
    one drugref holds, or drugref holds nothing yet because the ingest has not run. The
    banner for the first blames the user's typing, and on a migrated-but-never-ingested
    database it blames them for every uuid there is -- a guard asserting the cause it
    imagined, which is the defect issue 122 is about, reproduced in the message #120
    added. This is the one read that tells the two apart.
    """
    return not conn.execute(_ANY_MOIETY).fetchone()[0]


@dataclass(frozen=True, kw_only=True)
class Registry:
    """The two lookups the SPL ingest resolves through, AND WHAT THEY DISCARDED.

    Built from the identity spine alone -- `substance_moiety.display_name` and
    live `identity_claim` UNIIs -- which is why it lives with the spine's other
    reads rather than with the writer that happens to be its only caller today
    (issue 172). `drugcentral_run` keeps its own differently-shaped registry
    loader; the two answer different questions and are deliberately not merged.

    **NAMED, not a bare 2-tuple, because the two halves are the same type.**
    `names, known_uniis = load_registry(conn)` type-checks just as well the wrong
    way round, and a transposition would build the matcher out of UNII codes and
    resolve every subject against display names -- caught only by `check_floors`
    at the very end of a full ingest -- 2 min 09 s since issue 160, and 12 min 51 s
    when that was measured -- if at all.

    **A DATACLASS AND NOT A `NamedTuple`, WHICH IS NOT A STYLE CHOICE.** This was
    a `NamedTuple` for one round, and a `NamedTuple` is still unpackable: the two
    committed tools that read the registry kept `names, uniis =
    load_registry(conn)` and began raising `ValueError: too many values to
    unpack` on their first line of real work -- silently, because neither tool
    has a test. A type that cannot be destructured at all fails at EVERY call
    site the moment the shape changes, instead of only at the ones whose arity
    happens to stop matching.

    **The collision counts exist because `load_registry` used to say they were
    "the caller's to report" and no caller reported them.** `identity_claim` is
    unique on (moiety_uuid, scheme, value) and deliberately NOT across moieties,
    so two moieties may legitimately claim one UNII; first-wins then attaches
    every subject derived from it to whichever moiety sorted first. Deterministic,
    and reproducibly wrong is still wrong -- so the number of discarded entries
    is carried out where the summary can print it.
    """

    by_name: dict[str, str]
    by_unii: dict[str, str]
    #: display names claimed by more than one moiety, and UNIIs likewise. Each
    #: counts DISCARDED entries, not colliding keys: three moieties on one UNII
    #: is two discards.
    name_collisions: int
    unii_collisions: int


def load_registry(conn: psycopg.Connection) -> Registry:
    """`Registry(display_name -> moiety_uuid, UNII -> moiety_uuid, collisions)`.

    ONE STATEMENT, NOT TWO, for the reason `drugcentral_run.load_registry`
    records: a single statement always sees a single snapshot at any isolation
    level, so this reads consistently without the transaction having to be
    REPEATABLE READ -- and raising isolation for the whole run made a concurrent
    write to any question row abort the entire ingest with SerializationFailure.

    EVERY READ IS ORDERED, and that is not cosmetic. `identity_claim` is unique on
    (moiety_uuid, scheme, value) and deliberately NOT across moieties, so two
    moieties may legitimately claim one UNII. An unordered read would let the same
    release resolve differently on two runs.

    LIVE CLAIMS ONLY (`superseded_by IS NULL`): a corrected-away identifier must
    not resurrect a resolution.

    **First-wins on a collision, and the collision is COUNTED here.** Both
    mappings are built in one pass in sorted order, so which entry wins is a
    property of the data rather than of the plan -- and the count of what lost
    rides out on `Registry` so the orchestrator can report it. It used to say the
    collision was "the caller's to report"; the sole caller reported only the
    post-de-duplication sizes, so the number was unobservable anywhere.
    """
    rows = conn.execute(
        "  SELECT 'display_name' AS lookup, display_name AS key, "
        "         moiety_uuid::text AS moiety_uuid "
        "    FROM drugref.substance_moiety "
        "   UNION ALL "
        "  SELECT 'UNII', value, moiety_uuid::text "
        "    FROM drugref.identity_claim "
        "   WHERE scheme = 'UNII' AND superseded_by IS NULL "
        "   ORDER BY lookup, key, moiety_uuid").fetchall()

    by_name: dict[str, str] = {}
    by_unii: dict[str, str] = {}
    name_collisions = unii_collisions = 0
    for lookup, key, moiety_uuid in rows:
        if lookup == "display_name":
            if key in by_name:
                name_collisions += 1
            else:
                by_name[key] = moiety_uuid
        elif key in by_unii:
            unii_collisions += 1
        else:
            by_unii[key] = moiety_uuid
    return Registry(by_name=by_name, by_unii=by_unii,
                    name_collisions=name_collisions,
                    unii_collisions=unii_collisions)
