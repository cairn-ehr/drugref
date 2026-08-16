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

WHAT IT IS FOR. `drugref interactions <uuid>` printed "no curated grade" both for a drug
drugref knows and has not graded -- the ordinary case, since the overlay is small by
design -- and for a uuid naming nothing whatsoever. Two very different states, one
rendering, exit 0 either way, and the pair form additionally asserting that drugref
"holds no curated grade for this pair", about a pair that may not exist. The harm
direction is UNDER-WARNING: an absent answer reading as "checked, nothing found" is the
one thing `cli_interactions.py`'s own docstring says the command exists to avoid.
"""
import uuid

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
