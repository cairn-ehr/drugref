"""The ONLY module that writes the classification tables.

It mirrors claims.py's role for the identity tables -- concentrating writes in one
reviewable place -- but the discipline it enforces is DIFFERENT, and the
difference is the point:

* claims.py guards an APPEND-ONLY spine. Substance identity is immortal, and the
  database floor rejects UPDATE/DELETE outright.
* This module manages a REBUILDABLE PROJECTION. MED-RT is an upstream authority we
  re-ingest wholesale, and its edges are meant to be dropped and rebuilt -- so
  clear_source_edges() deliberately DELETEs. What survives a rebuild unchanged is
  class IDENTITY: class_uuid is a pure function of (source, code), so every class
  comes back with exactly the UUID it had before.
"""
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import psycopg

from drugref import db, ids


@dataclass(frozen=True)
class ClassConcept:
    """One classification class to upsert, in a SOURCE-NEUTRAL shape.

    This is the record every ingest hands to upsert_class, whatever authority it
    came from (MED-RT, MeSH, ...). It lives here, beside the writer that consumes
    it, rather than inside any one source's parser, precisely because more than one
    source now feeds it:

    * `nui`  -- the authority's stable identity key (MED-RT's NUI, a MeSH descriptor
               UI, ...). class_uuid is derived from it, so it must never change for
               a given class.
    * `code` -- the code AS PUBLISHED, which the source's own edges reference. Equal
               to `nui` for authorities (like MeSH) that key on their published UI;
               kept separate because MED-RT allows the two to differ (see the
               code-vs-NUI note in medrt.parse()).
    * `name` / `concept_type` -- the cached display name and the axis this class
               sits on (a MED-RT CTY such as 'MoA', or MeSH's 'PA').
    """
    nui: str
    code: str
    name: str
    concept_type: str


def upsert_class(conn: psycopg.Connection, concept: ClassConcept,
                 ingest_run_id: int, source: str) -> tuple[uuid.UUID, bool]:
    """Register a class (or refresh its cached name).

    `source` names the authority that defined the class ("MED-RT", "MeSH", ...).
    It is part of the identity key, not a label: the registry holds classes from
    several authorities, and without it two of them publishing the same code would
    silently collapse into one row.

    Returns (class_uuid, is_new), where is_new is True only the first time drugref
    ever saw this class. The caller needs that distinction because classes
    ACCUMULATE while edges are rebuilt, so "classes in this release" and "classes
    added by this run" are genuinely different numbers and a summary that reported
    only one of them would be ambiguous.

    The UUID is derived, never looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the name, type and code caches -- upstream does rename
    classes -- while first_seen_ingest is deliberately left out of the SET list,
    because it records when drugref FIRST saw the class, not when it was last
    confirmed. That is also what makes it the newness test: the row is new to us
    exactly when the value that came back is this run's id.
    """
    class_uuid = ids.mint_class_uuid(source, concept.nui)
    # Store the SAME canonicalisation the UUID was minted from, so the stored
    # source and the identity key can never drift apart -- two spellings of one
    # authority would otherwise share a class_uuid yet be stored as two strings,
    # and a per-source rebuild query would then miss half its own rows.
    stored_source = ids.canonical_source(source)
    first_seen = conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (class_uuid) DO UPDATE SET "
        "  class_name = EXCLUDED.class_name, concept_type = EXCLUDED.concept_type, "
        "  published_code = EXCLUDED.published_code "
        "RETURNING first_seen_ingest",
        (class_uuid, stored_source, concept.nui, concept.code, concept.name,
         concept.concept_type, ingest_run_id)).fetchone()[0]
    return class_uuid, first_seen == ingest_run_id


# The edge tables one classification feed owns. Both are rebuilt wholesale; the
# substance_class rows they reference are NOT, because a class_uuid is immortal.
CLASS_EDGE_TABLES = ("class_membership", "class_parent")


def clear_source_edges(conn: psycopg.Connection, source: str) -> None:
    """Drop every DAG and membership edge contributed by `source`.

    Called at the start of a re-ingest so a new upstream release fully REPLACES the
    previous one. This is why the edge tables must stay deletable: a class that
    lost a parent upstream has to lose it here too, and an insert-only merge can
    never express a removal. Scoped by source so an unrelated feed's edges survive.

    Class rows themselves are NOT deleted -- their UUIDs are immortal and are
    re-derived identically on the way back in.
    """
    db.clear_source_tables(conn, CLASS_EDGE_TABLES, source)


def add_parent_edge(conn: psycopg.Connection, child_uuid: uuid.UUID,
                    parent_uuid: uuid.UUID, ingest_run_id: int) -> bool:
    """Add one subclass edge. Returns True if a new row was inserted.

    ON CONFLICT DO NOTHING keeps a file that repeats an edge harmless.
    """
    cur = conn.execute(
        "INSERT INTO drugref.class_parent (child_class_uuid, parent_class_uuid, ingest_run) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (child_uuid, parent_uuid, ingest_run_id))
    return cur.rowcount == 1


def add_membership(conn: psycopg.Connection, moiety_uuid: uuid.UUID,
                   class_uuid: uuid.UUID, relationship: str,
                   ingest_run_id: int) -> bool:
    """Link a moiety to a class on one axis. Returns True if newly inserted."""
    cur = conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (moiety_uuid, class_uuid, relationship, ingest_run_id))
    return cur.rowcount == 1


def moieties_by_scheme(conn: psycopg.Connection, scheme: str) -> dict[str, list[uuid.UUID]]:
    """Build a {claim value -> moieties} index for one identity-claim scheme.

    This is the generic membership-join primitive. A classification feed keyed on
    some external identifier (MED-RT on RxCUI, MeSH PA on UNII/CAS) resolves its
    members to moieties by asking "which moiety carries this claim?", and slice 1
    already recorded those claims -- so no feed needs new bridge data.

    Two rules that every caller depends on, applied here once:

    * Superseded claims are excluded (superseded_by IS NULL), so a corrected-away
      identifier cannot resurrect a stale membership (chebi.py's InChIKey rule).
    * EVERY claimant is kept, not the first. identity_claim is unique on
      (moiety_uuid, scheme, value) but NOT across moieties, so two moieties may
      legitimately carry the same value; picking one arbitrarily would drop a real
      membership and -- being an unordered single-row read -- could answer
      differently run to run. The ORDER BY makes the retained order deterministic.

    Read WHOLE rather than queried per member: a feed states many memberships per
    substance, so a per-member lookup re-asks an already-answered question. The
    index is bounded by the moiety registry (one bucket per claim value it holds),
    so it grows with the registry, not with the feed; if that ever outgrows memory
    it is the same conversation as the whole-file parse (production-ingest
    follow-up), not solved differently here.
    """
    index: dict[str, list[uuid.UUID]] = {}
    for value, moiety_uuid in conn.execute(
            "SELECT value, moiety_uuid FROM drugref.identity_claim "
            "WHERE scheme = %s AND superseded_by IS NULL "
            "ORDER BY value, moiety_uuid", (scheme,)).fetchall():
        index.setdefault(value, []).append(moiety_uuid)
    return index


def moieties_by_display_name(conn: psycopg.Connection) -> dict[str, list[uuid.UUID]]:
    """Build a {display_name -> moieties} index, for name-keyed bridges (#26).

    The companion to moieties_by_scheme, for the case where the join key is
    drugref's own LABEL rather than an external identifier. The local (PBS) tier
    is the consumer: PBS carries no UNII, CAS or InChIKey, so a name match is the
    only licence-clean join available to it (slice 8a spec §1).

    WHY NOT JUST INDEX THE `INN` CLAIMS, which is what slice 8a originally did:
    since the #26 gate redesign the registry admits ~6,850 moieties on a USAN or
    an RxCUI rather than an INN -- amoxicillin, morphine, codeine, doxycycline,
    tacrolimus. Those carry a display_name but NO `INN` claim, because drugref
    will not assert an INN it has no source for. An INN-keyed index therefore
    could not see the very moieties the gate redesign was written to admit, and
    against the real July-2026 PBS release that cost 1,256 of 3,140 unmatched
    components and 1,235 products.

    The switch is LOSSLESS, not a trade: display_name and the INN claim value are
    both gate.inn_display_name(cand, crosswalk) for an INN holder, and against
    the real 26Feb2026 release all 12,588 INN claims equal their moiety's
    display_name -- zero mismatches. So this index is a strict superset of the
    one it replaces.

    Every claimant is kept, for the same reason moieties_by_scheme keeps them:
    display_name is not unique, and picking one arbitrarily would drop a real
    bridge and could answer differently run to run. Order is deterministic.
    """
    index: dict[str, list[uuid.UUID]] = {}
    for name, moiety_uuid in conn.execute(
            "SELECT display_name, moiety_uuid FROM drugref.substance_moiety "
            "ORDER BY display_name, moiety_uuid").fetchall():
        index.setdefault(name, []).append(moiety_uuid)
    return index


def moieties_by_rxcui(conn: psycopg.Connection) -> dict[str, list[uuid.UUID]]:
    """The RxCUI -> moieties index MED-RT membership joins on.

    A thin alias for moieties_by_scheme(conn, 'RXNORM_IN'): MED-RT states class
    membership against RxNorm ingredient concepts whose code IS the RxCUI, and
    slice 1 attached an RXNORM_IN claim to every moiety carrying one.
    """
    return moieties_by_scheme(conn, "RXNORM_IN")


UNMATCHED_INGREDIENT_TABLES = ("ingest_unmatched_ingredient",)

# Why an RxCUI is on the unmatched worklist -- and, because the clear is scoped on it,
# WHICH writer owns the row (#39, db/018). The table's CHECK admits exactly these
# three VALUES, written today by TWO writers (medrt_run owns one bucket, mesh_rel_run
# owns two), and the invariant a fourth VALUE must preserve is ONE WRITER PER
# (source, reason): add a value here rather than sharing one, or the clears collide
# again exactly as medrt_run's and the MeSH-keyed run's did. #47 is the next candidate --
# medrt_run's own CI subjects, which it counts today and does not persist.
#
# VALUES AND WRITERS ARE COUNTED SEPARATELY ON PURPOSE. They were equal until this
# slice and the sentence above conflated them; INDICATION is the change that proves
# they differ, so a reader must not infer "three values" from "three writers" again.
#
# INDICATION is what one orchestrator owning TWO buckets looks like, and it does not
# weaken the invariant: mesh_rel_run writes both, so each bucket still has exactly one
# writer. Two writers sharing one bucket is what #39 was.
CLASSIFICATION = "classification"    # medrt_run: an ingredient the release CLASSIFIES
CONTRAINDICATION = "contraindication"  # mesh_rel_run: the SUBJECT of a contraindication
INDICATION = "indication"            # mesh_rel_run: the SUBJECT of an indication
# medrt_run's OWN CI subjects (#47, db/026) -- the subject of a CI_MoA/CI_PE rule that
# no moiety carries. Its own bucket, never `contraindication`: that one is
# mesh_rel_run's, and sharing it is #39 with nothing to notice it. Named to sort AFTER
# `classification`, which the issue's own suggested `class_contraindication` did not --
# see db/026 for why that matters.
CONTRAINDICATION_CLASS = "contraindication_class"
REASONS = (CLASSIFICATION, CONTRAINDICATION, INDICATION, CONTRAINDICATION_CLASS)

# The COLUMN the clear narrows on, named here rather than spelled at the call site:
# clear_source_tables interpolates it as an identifier (a column name cannot be a bind
# parameter), and its contract is that every such identifier comes from a module
# constant. A literal passed inline satisfies that by accident, not by construction.
REASON_COLUMN = "reason"


def clear_source_unmatched_ingredients(conn: psycopg.Connection, source: str,
                                       reason: str) -> None:
    """Drop the previous release's unmatched-ingredient list for `(source, reason)`.

    Same rebuildable-projection discipline as clear_source_edges, and needed for the
    same reason: an ingredient that starts matching (because the moiety gate widened,
    or the registry grew) must LEAVE the list. Without this the worklist would grow
    by its own length on every ingest and never shrink, which is precisely the
    "generated document, stale on write" failure the gap views exist to avoid.

    SCOPED BY REASON AS WELL AS SOURCE, and `reason` is required rather than
    defaulted: two orchestrators write this table under source 'MED-RT' from different
    upstream assertions, so a source-only clear made the worklist depend on which ran
    last (#39). A caller that does not say which bucket it rebuilds must fail here,
    not silently take another writer's rows with it.
    """
    db.clear_source_tables(conn, UNMATCHED_INGREDIENT_TABLES, source,
                           match={REASON_COLUMN: reason})


def add_unmatched_ingredients(conn: psycopg.Connection, rxcuis: Iterable[str],
                              ingest_run_id: int, reason: str,
                              names: Mapping[str, str] | None = None) -> int:
    """Record that each RxCUI was named upstream but is carried by no moiety.

    Not an error, and not a silent drop: MED-RT classifies far more ingredients than
    pass drugref's moiety gate, and each one is a drug the registry can say nothing
    about. Persisting the identity (rather than only counting it, as the ingest did
    before Plan A) is what lets gap_unmatched_ingredient be a query.

    `reason` says WHY this writer is reporting the RxCUI -- CLASSIFICATION,
    CONTRAINDICATION or INDICATION above -- and is what its own clear is scoped on.
    Required, and positional before the optional `names`, so a writer cannot inherit a
    bucket it does not own; the column has no DEFAULT either, so a forgotten reason
    fails in the database as well as here.

    Batched rather than one call per RxCUI -- this is thousands of rows on a real
    release, and its siblings (add_membership, add_parent_edge) are per-row only
    because their callers need the insert-vs-conflict answer to count with. Nobody
    needs it here: the summary's count comes from the deduped set the caller already
    holds. Returns rows written, for a caller that wants to assert on it.

    `names` is optional and MED-RT's membership assertions carry none, so the ingest
    leaves them NULL today; it is here for whichever source does supply one, because
    a worklist a human cannot read is a worklist nobody works.
    """
    names = names or {}
    rows = [(ingest_run_id, rxcui, names.get(rxcui), reason) for rxcui in rxcuis]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO drugref.ingest_unmatched_ingredient "
            "(ingest_run, rxcui, name, reason) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", rows)
    return len(rows)
