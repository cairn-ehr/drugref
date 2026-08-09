# tests/test_signing_payload_coverage.py
"""The alarm that lets the frozen field lists stay frozen (spec 4.5). DB-gated."""
import pytest

from drugref import signing

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
