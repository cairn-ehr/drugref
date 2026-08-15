"""Reads of the IDENTITY SPINE -- does drugref know this moiety at all? (issue 120).

WHY A MODULE OF ITS OWN, and the two candidates it is deliberately not part of. This
project splits reads from writes by tier, and this read is in neither existing tier:

  * `curated_read.py` opens by scoping itself to "the curated overlay", and that scope
    is load-bearing rather than decorative -- its whole argument is that a view whose
    population is GRADES cannot answer a question about DRUGS. `substance_moiety` is
    slice 1's append-only identity spine, a tier below the overlay, and a module that
    spanned both would erase exactly the boundary issue 120 is about.
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

# `= ANY(%s)` RATHER THAN AN `IN (...)` BUILT BY STRING JOIN, which is the shape that
# invites a uuid into the SQL text. psycopg adapts a Python list to a Postgres array, so
# the whole variadic call is ONE round trip with ONE parameter, and the query text is
# constant no matter how many identifiers are asked about.
_KNOWN = """
SELECT moiety_uuid
FROM   drugref.substance_moiety
WHERE  moiety_uuid = ANY(%s)
"""


def known_moieties(conn: psycopg.Connection,
                   *moiety_uuids: uuid.UUID) -> set[uuid.UUID]:
    """Which of `moiety_uuids` name a registered moiety. Absent ones are simply missing.

    RETURNS THE KNOWN ONES, not the unknown ones, and the direction matters at the call
    site: a caller subtracts to get what it must warn about, so the warning is derived
    from a positive fact drugref actually holds rather than from this function's opinion
    about what is missing.

    A SET, not a list: callers ask "is this one in it", order carries no meaning, and
    duplicates in the argument list -- `interactions X --with X` reaches here before the
    self-pair check -- collapse instead of double-counting.

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
