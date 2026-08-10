# tests/test_signing_payload_coverage.py
"""The alarms that let the frozen field lists AND the frozen natural-key column lists
stay frozen (spec 4.5, 5.5). DB-gated.

TWO CONSTANTS, ONE PATTERN. `signing.FIELD_LISTS` decides which columns enter a payload;
`signing.NATURAL_KEY_COLUMNS` decides which columns render the `natural_key` a manifest
entry is PAIRED by. Both are frozen against the standing derive-from-the-catalog rule,
for the same reason -- they enter signed bytes, so deriving them means a migration
silently rewrites history -- and both therefore need the same alarm: a test that
compares the frozen list against what the live database says TODAY and fails loudly on a
divergence, forcing a deliberate `/v2` rather than a silent one.
"""
import pytest

from drugref import signing
from tests.test_live_key_index_guard import _single_live_tables

CURATED = [
    ("curated_interaction/v1", "curated_interaction", "curated_interaction_id"),
    ("curated_condition/v1", "curated_condition", "curated_condition_id"),
]


@pytest.mark.parametrize("context,table,pk", CURATED)
def test_the_frozen_field_list_accounts_for_every_column(conn, context, table, pk):
    """A new column on a curated table must FAIL here rather than drift into the void.

    THIS IS THE INVERSE OF THE STANDING RULE ON PURPOSE. Everywhere else in this suite,
    a covered set is derived from the catalog so a new object is covered the day it
    lands. A signed payload cannot work that way: derive it from information_schema and
    an ALTER TABLE ADD COLUMN silently changes every payload and invalidates every
    signature ever made. So the list is frozen and THIS test is the alarm -- a new
    column fails, forcing a deliberate choice (bump the context to /v2, or exclude the
    column here with a stated reason) instead of a silent one.

    The two excluded columns are excluded for different reasons, and neither is
    incidental. The surrogate primary key is a POINTER, local to one database, so
    signing it would break a signature carried into another. `superseded_by` is the ONE
    column db/020's floor permits to change, so signing it would invalidate every
    signature the moment its row was corrected -- which is to say, every time the
    overlay did the thing it exists to do.
    """
    live = {row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = %s", (table,)).fetchall()}
    signed = set(signing.FIELD_LISTS[context]) - set(signing.ATTESTATION_FIELDS)
    deliberately_unsigned = {pk, "superseded_by"}
    assert live == signed | deliberately_unsigned, (
        f"drugref.{table}'s columns and {context}'s frozen field list disagree. "
        f"Only in the table: {sorted(live - signed - deliberately_unsigned)}. "
        f"Only in the field list: {sorted(signed - live)}. "
        "A new column is a DELIBERATE decision: sign it under a new /v2 context, or "
        "add it to deliberately_unsigned with the reason. Do not add it to the v1 "
        "list -- that invalidates every signature already recorded.")


@pytest.mark.parametrize("context,table,pk", CURATED)
def test_the_frozen_natural_key_columns_match_todays_trigger(conn, context, table, pk):
    """A CHANGE TO A CURATED TABLE'S NATURAL KEY MUST FAIL HERE rather than silently
    re-key everything drugref has ever published. The alarm for
    `signing.NATURAL_KEY_COLUMNS`, and the exact counterpart of the field-list alarm
    above -- same inversion, same reason, one question over.

    WHAT THIS REPLACED, and why the replacement needed an alarm at all. `releases.py`
    used to read these columns straight out of `pg_trigger.tgargs` on every call --
    derived, catalog-driven, the house rule. But `release_manifest_entry.natural_key`
    stores a RENDERED STRING from publish time and is a SIGNED member of every manifest
    entry, so at VERIFY time that derivation reconstructs the PRESENT schema and compares
    it against a PAST recording. Widen this trigger by one column -- the additive
    migration db/029 explicitly contemplates for `curated_condition` -- and every live
    key re-renders, pairs with nothing, and an untouched database reports 100% churn.
    `test_a_widened_natural_key_trigger_does_not_re_key_a_published_release` in
    tests/test_releases.py is that scenario, driven end to end.

    THE TRIGGER IS STILL THE SOURCE OF TRUTH ABOUT WHAT THE OVERLAY MEANS BY "ONE ROW"
    (db/020's floor: at most one live row per this key). Freezing the Python copy does
    not dispute that -- it only refuses to let a change to it rewrite the past silently.
    So this test asserts the two AGREE today, which is what turns a future migration into
    a red test and a deliberate `/v2` decision instead of a silent re-keying.
    """
    trigger_columns = dict(_single_live_tables(conn))[table].split(", ")
    assert list(signing.NATURAL_KEY_COLUMNS[context]) == trigger_columns, (
        f"drugref.{table}'s single-live trigger and {context}'s frozen natural-key "
        f"columns disagree. Trigger: {trigger_columns}. Frozen: "
        f"{list(signing.NATURAL_KEY_COLUMNS[context])}. A natural key that changes is a "
        "DELIBERATE decision: mint a /v2 context, add its frozen key list here, and "
        "leave /v1's alone. Editing the /v1 tuple re-keys every manifest entry ever "
        "published, and a published entry's natural_key is inside signed bytes.")


@pytest.mark.parametrize("context,table,pk", CURATED)
def test_every_natural_key_column_is_also_a_signed_field(conn, context, table, pk):
    """`releases.enumerate_live` reads a row's natural-key values straight out of the
    content fields it already fetched, so a key column that is NOT in the same context's
    frozen field list is a `KeyError` at publish time -- on the one code path that must
    never fail halfway. This asserts the containment directly rather than waiting for
    that crash, and it is a real property rather than a tautology: a row's identity being
    part of what gets signed is a design commitment (spec 4.5), not an accident of which
    columns the two tuples happen to name.
    """
    assert set(signing.NATURAL_KEY_COLUMNS[context]) <= set(signing.FIELD_LISTS[context])


def test_every_curated_context_has_a_frozen_natural_key():
    """The coverage check on the coverage check: a third curated target kind whose
    context is missing from NATURAL_KEY_COLUMNS would make `enumerate_live` raise
    `KeyError` the first time a release enumerated it, and the two parametrized alarms
    above would never notice, because they iterate CURATED -- a list in this file.
    `releases._CURATED_KINDS` is the real scope, so it is what gets checked."""
    from drugref import releases
    assert set(releases._CURATED_KINDS) == {"curated_interaction", "curated_condition"}
    for context in ("curated_interaction/v1", "curated_condition/v1"):
        assert context in signing.NATURAL_KEY_COLUMNS
    # A manifest is never itself enumerated by a manifest -- see NATURAL_KEY_COLUMNS'
    # own closing note. Asserted, not merely stated, so the day somebody adds it out of
    # tidiness the reason has to be re-read.
    assert "release_manifest/v1" not in signing.NATURAL_KEY_COLUMNS


def test_every_curated_catalog_kind_is_covered_by_a_release(conn):
    """REVIEW I4: THE ALARM USED TO POINT THE WRONG WAY.

    `test_every_curated_context_has_a_frozen_natural_key` above asserts
    `_CURATED_KINDS` equals a literal pair written in this file, so it fires when
    somebody edits the PYTHON CONSTANT and never when `signature_target_kind` gains a
    curated kind. A future slice adding a third curated table would leave
    `_CURATED_KINDS` untouched and the whole suite green -- while `enumerate_live`
    silently stopped enumerating an entire class of live curated assertions.

    THAT IS THE VACUOUS PASS IN THE ONE LAYER WHOSE JOB IS COMPLETENESS. The omitted
    rows would be absent from the manifest AND absent from the live side of the
    comparison, so they would never be reported as `added` either: `verify_release`
    would call an incomplete release intact.

    Derived from the catalog, so the catalog is what raises the alarm. `release_manifest`
    is excluded because a release cannot enumerate itself -- stated as a subtraction
    rather than a second literal list, so adding a fourth kind cannot satisfy it by
    accident."""
    from drugref import releases

    catalog_kinds = {row[0] for row in conn.execute(
        "SELECT target_kind FROM drugref.signature_target_kind").fetchall()}
    assert catalog_kinds - {"release_manifest"} == set(releases._CURATED_KINDS)


def test_every_frozen_context_names_its_own_target_kind(conn):
    """`signing.context_is_usable_for` decides whether a stored `payload_context` can be
    rebuilt against a target by comparing `context_target_kind(context)` -- the part
    before the `/v` -- against the target kind. That turns a NAMING CONVENTION into a
    security check, so the convention needs an alarm of its own.

    Both directions: every frozen field list's prefix must be a real catalog kind, and
    every catalog row's own context must name the kind it belongs to. Without this, a
    kind registered as `('curated_severity', ..., 'severity/v1')` would make every
    signature over it report BAD_SIGNATURE forever, with nothing pointing at why."""
    catalog = dict(conn.execute(
        "SELECT target_kind, payload_context "
        "FROM drugref.signature_target_kind").fetchall())

    for context in signing.FIELD_LISTS:
        assert signing.context_target_kind(context) in catalog, (
            f"{context} names a target kind the catalog does not have")

    for target_kind, context in catalog.items():
        assert signing.context_target_kind(context) == target_kind, (
            f"{target_kind} signs under {context}, whose prefix names a different kind "
            "-- signing.context_is_usable_for would reject every signature it makes")
