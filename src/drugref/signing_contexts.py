# src/drugref/signing_contexts.py
"""WHICH FIELDS each signed context covers: the frozen vocabulary of slice 5c.4. PURE.

WHY THIS IS ITS OWN MODULE (issue 177). `signing.py` says HOW bytes are built and what a
signature over them means; this module says WHAT goes into those bytes for each context
-- the frozen field lists (spec 4.5), the frozen natural-key columns (spec 5.5), and the
three predicates that ask whether a stored context can be rebuilt against a target kind.
It is a second vocabulary, and it was moved out of `signing.py` verbatim. Nothing here
touches a connection or the cryptography; every value is a string or a tuple of strings.

`signing.canonical_payload` takes the field pairs IN THE ORDER these lists give them;
the callers that build payloads (`signatures`, `releases`, `release_verification`)
read the lists from here.
"""


# ---- the frozen field lists (spec 4.5) -------------------------------------
#
# FROZEN CONSTANTS, AND THIS DELIBERATELY INVERTS A STANDING RULE. The gates round's
# rule reads "derive the covered set from the catalog, never from a list you maintain",
# and here the opposite is required: deriving the payload from information_schema means
# a later ALTER TABLE ADD COLUMN silently changes every payload and INVALIDATES EVERY
# SIGNATURE EVER MADE.
#
# The alarm the rule exists for is rebuilt rather than abandoned --
# tests/test_signing_payload_coverage.py compares these lists against the live catalog
# and FAILS on a new column, forcing an explicit choice: bump to /v2, or exclude the
# column with a stated reason. Frozen bytes, catalog-driven alarm.
#
# THAT ALARM COVERS THE TWO CURATED LISTS ONLY, and saying so matters because this
# block heads THREE. `RELEASE_MANIFEST_V1`'s members are DERIVED scalars and two group
# cardinalities, not columns of `release_manifest`, so there is no catalog to compare
# it against -- the coverage test parametrizes over the curated contexts alone. What
# guards that list instead is the committed vector in tests/fixtures/signing_vectors.
# json, which pins its bytes.
#
# Adding a field to a list below without bumping the context is a BREAKING change to
# every signature already recorded. There is no way to make that safe; there is only a
# test that makes it deliberate.
ATTESTATION_FIELDS = ("signer_key_fingerprint", "signed_at")

CURATED_INTERACTION_V1 = (
    "subject_moiety_uuid", "object_class_uuid", "relationship", "applies",
    "severity", "mechanism", "management", "evidence_grade", "question_uuid",
    "source", "reviewed_by", "reviewed_against", "reviewed_at",
    *ATTESTATION_FIELDS)

# NOTE THE ASYMMETRY, which mirrors db/029's own and is not an oversight: this table is
# keyed on the (drug, condition) PAIR and carries `ruling` where its sibling carries
# `relationship` + `applies`, because one pair genuinely holds both an indication and a
# contraindication in 168 cases. See spec 3 of the 5c.1 design.
CURATED_CONDITION_V1 = (
    "subject_moiety_uuid", "object_condition_uuid", "ruling",
    "severity", "mechanism", "management", "evidence_grade", "question_uuid",
    "source", "reviewed_by", "reviewed_against", "reviewed_at",
    *ATTESTATION_FIELDS)

# The manifest's scalars. The two group cardinalities are stated as scalars as well as
# being derivable from the groups themselves (spec 5.5): a group truncated at its END is
# otherwise detectable only by recomputing the whole digest, and a scalar count makes
# that specific failure nameable.
#
# THEY ARE NOT INDEPENDENTLY CHECKABLE AT VERIFY TIME, and a reader should not
# expect them to be: `manifest_payload` derives both from `len(entries)`/
# `len(upstream)` when PUBLISHING and again when VERIFYING, so they can never
# disagree with the groups they count. What actually catches a truncated entry
# list is `release_manifest.row_count`, which `verify_release` compares against
# the stored entries (`row_count_ok`). These two scalars put the group sizes into
# the SIGNED bytes, which is a different job.
RELEASE_MANIFEST_V1 = (
    "release_tag", "published_by", "published_at", "entry_count", "upstream_count",
    *ATTESTATION_FIELDS)

# db/035 (issue #98), the class x class grain. SAME COLUMNS AS CURATED_INTERACTION_V1
# with the subject one grain up -- `subject_class_uuid` where that one has
# `subject_moiety_uuid` -- because db/032 mirrored curated_interaction column for
# column, deliberately, and the only structural difference is what the subject IS.
#
# NOT AN ALIAS OF CURATED_INTERACTION_V1, though the tuples differ by one string. Each
# context's list is frozen INDEPENDENTLY: sharing the object would mean a future /v2 on
# one table silently re-keying the other's payloads, and these are two tables whose
# schemas are free to diverge.
CURATED_CLASS_INTERACTION_V1 = (
    "subject_class_uuid", "object_class_uuid", "relationship", "applies",
    "severity", "mechanism", "management", "evidence_grade", "question_uuid",
    "source", "reviewed_by", "reviewed_against", "reviewed_at",
    *ATTESTATION_FIELDS)

FIELD_LISTS = {
    "curated_interaction/v1": CURATED_INTERACTION_V1,
    "curated_condition/v1": CURATED_CONDITION_V1,
    "curated_class_interaction/v1": CURATED_CLASS_INTERACTION_V1,
    "release_manifest/v1": RELEASE_MANIFEST_V1,
}


# ---- the frozen natural-key columns (spec 5.5) -----------------------------
#
# FROZEN FOR EXACTLY THE REASON THE FIELD LISTS ABOVE ARE, and it is easy to miss,
# because a natural key reads like bookkeeping rather than content. It IS content:
# `release_manifest_entry.natural_key` stores a RENDERED STRING -- the key columns' own
# values, slash-joined, as they stood at publish time -- and that string is a SIGNED
# member of every manifest entry (spec 5.5's `--entries--` group). It is also the
# PAIRING KEY verification uses to find the live row an entry describes.
#
# DERIVING THE COLUMN LIST FROM `pg_trigger.tgargs` WAS MEASURED WRONG, which is why
# this constant exists. That derivation reads the PRESENT schema and compares it against
# a PAST recording: simulate the additive migration db/029 contemplates ("If a real case
# ever needs per-relationship grades it is an additive migration on a table that ships
# empty") -- widen `curated_interaction`'s single-live trigger by one column -- and
# every live row re-keys, not one new key pairs with a published entry, and a verifier
# reports 100% churn on a database nobody touched: C1-of-Task-8's failure mode reached
# through schema evolution instead of through id offsets.
#
# Spec 4.5 already states the general rule this follows: the standing
# derive-from-the-catalog discipline is deliberately INVERTED for anything that enters
# signed bytes, and `natural_key` does.
#
# THE ALARM IS REBUILT THE SAME WAY, so freezing does not mean going blind:
# tests/test_signing_payload_coverage.py compares these tuples against what the
# single-live triggers actually enforce TODAY and fails on any change, forcing a
# deliberate `/v2` decision rather than a silent re-keying of everything published.
#
# `release_manifest/v1` IS DELIBERATELY ABSENT: a release is never itself enumerated
# by a release (`releases._CURATED_KINDS` says as much one layer up), so it has no
# natural key to render and a lookup for it is a bug worth a loud KeyError.
CURATED_INTERACTION_V1_KEY = (
    "subject_moiety_uuid", "object_class_uuid", "relationship")

CURATED_CONDITION_V1_KEY = ("subject_moiety_uuid", "object_condition_uuid")

# db/035. THE SAME THREE-COLUMN SHAPE as CURATED_INTERACTION_V1_KEY, one grain up, and
# it must render all three: two of the three would fold every AXIS of one class pair
# onto a single manifest entry, and `natural_key` is what verification PAIRS on.
CURATED_CLASS_INTERACTION_V1_KEY = (
    "subject_class_uuid", "object_class_uuid", "relationship")

NATURAL_KEY_COLUMNS = {
    "curated_interaction/v1": CURATED_INTERACTION_V1_KEY,
    "curated_condition/v1": CURATED_CONDITION_V1_KEY,
    "curated_class_interaction/v1": CURATED_CLASS_INTERACTION_V1_KEY,
}


def context_target_kind(context: str) -> str:
    """The target kind a payload context belongs to: everything before the `/v`. PURE.

    A CONVENTION MADE CHECKABLE. Every context is spelled `<target_kind>/v<n>` -- the
    shape both `signing._CONTEXT` and db/030's three CHECKs already enforce, and true of
    every row `signature_target_kind` ships. Until now nothing depended on the first
    half MEANING anything, so nothing verified it; `context_is_usable_for` below does,
    and tests/test_signing_payload_coverage.py pins every frozen list's prefix against
    the live catalog so the convention cannot drift into a lie.
    """
    return context.split("/", 1)[0]


def context_is_usable_for(stored_context: str, target_kind: str) -> bool:
    """Can a payload recorded under `stored_context` be rebuilt against `target_kind`'s
    own table? PURE -- both arguments are strings.

    THE QUESTION A VERIFIER MUST ASK BEFORE SUBSCRIPTING ANYTHING. Every
    `payload_context` column in db/030 carries only a regex CHECK and, deliberately, NO
    foreign key, so a signature may name any well-shaped context: one no frozen list has
    ever known (`bogus/v9`), or one belonging to a DIFFERENT target kind
    (`curated_condition/v1` on a `curated_interaction` row). Both are one ordinary
    INSERT away, and rebuilding under either crashed -- `KeyError` for the first,
    psycopg's `UndefinedColumn` for the second, neither of them a `RuntimeError`, so
    `cli.main` caught neither. `UndefinedColumn` additionally ABORTS THE TRANSACTION.
    Because `assertion_signature` is INSERT-ONLY the offending row can never be deleted,
    so one planted row denied verification of that target PERMANENTLY -- a far cheaper
    attack than forging a signature.

    IT DOES NOT CONSULT THE CATALOG, AND THAT IS THE POINT. An earlier cut of this fix
    compared the stored context against `signature_target_kind`'s CURRENT context and
    reported a signature unusable whenever the catalog had moved on -- which is the
    "re-describe the present" error this whole slice is built to avoid, and it broke
    `test_verification_reconstructs_the_past_context_not_the_present` exactly as it
    should have. A `curated_interaction/v1` signature stays rebuildable after the
    catalog moves to a `/v2` this build has never heard of: `/v2` can only ADD columns,
    so `/v1`'s field names still resolve against the table. The only thing that matters
    is whether the stored context's fields are columns of THIS kind's table, and the
    `<target_kind>/v<n>` naming answers that without a round trip.
    """
    return (stored_context in FIELD_LISTS
            and context_target_kind(stored_context) == target_kind)


def entry_context_is_reproducible(entry_context: str, current_context: str) -> bool:
    """Can a manifest entry recorded under `entry_context` be rebuilt against a row
    whose kind signs under `current_context` today? PURE -- both arguments are strings.

    WHY THIS IS A QUESTION AT ALL. `release_manifest_entry.payload_context` carries only
    a regex CHECK and, deliberately, NO foreign key -- an entry may name any well-shaped
    context string, including one no frozen list has ever known (`bogus/v9`) or one
    belonging to a DIFFERENT target kind (`curated_condition/v1` on a
    `curated_interaction` entry). Both are one INSERT away on a table nothing else
    constrains.

    A VERIFIER MUST NOT CRASH ON EITHER, and the first version of the C1 fix did: it
    subscripted `FIELD_LISTS`/`NATURAL_KEY_COLUMNS` directly, so an unknown context
    threw `KeyError` -- not a `RuntimeError`, so `cli.main` does not catch it and
    `drugref verify --release` printed a raw traceback. That is strictly worse than the
    behaviour it replaced (the old derive-from-`pg_trigger` code reported drop+add), and
    it contradicts `release_signature._worst_verdict`'s own stated principle: a crash
    at the verification core is worse than treating an unknown outcome with maximum
    suspicion. Callers use this to make an unusable entry FAIL TO PAIR -- reported as
    dropped -- instead.

    THE TEST IS CONTAINMENT, both halves of it. `entry_context`'s fields must all be
    columns the current context also names (that is what makes
    `signatures._row_content_fields`' SELECT resolvable against the kind's table -- a
    cross-kind context otherwise raises `UndefinedColumn`, the same crash one layer
    over), and its natural-key columns likewise, since `enumerate_live` reads them out
    of exactly that field set.

    A STATED, CONSERVATIVE LIMITATION: a future `/v2` that both ADDS and REMOVES a
    column would make a genuine `/v1` entry non-containable once the catalog moved on,
    and this reports it as dropped+added rather than comparing it. That is a misread
    in the direction that asks a human to look -- `_published_content_is_history`'s own
    limitation, chosen the same way -- and never a false clean bill of health. Version
    evolution that only ADDS columns (the realistic direction, and the one the alarm in
    tests/test_signing_payload_coverage.py forces a decision about) is unaffected.
    """
    fields = FIELD_LISTS.get(entry_context)
    key_columns = NATURAL_KEY_COLUMNS.get(entry_context)
    current = FIELD_LISTS.get(current_context)
    if fields is None or key_columns is None or current is None:
        return False
    return set(fields) <= set(current) and set(key_columns) <= set(current)
