# tests/test_overlay_contract.py
"""The overlay tier's correction rule lives in ONE place (#59).

INSERT the new assertion, then point whatever was live at it. That pair of statements
is the only sequence the append-only overlay admits: correcting in place is refused by
the floor (db/020), and pointing first is impossible because the target does not exist
yet. Three modules hand-wrote it -- accumulation (Plan C), questions (since db/007) and
interactions (since db/027) -- and this project has spent four rounds fixing one rule
kept in two places (#31, #40, #43, db/018's two CTEs).

Restated as a grep rather than by importing anything, for the same reason
test_source_clear_contract restates each writer's table tuple and test_provenance
greps for the run record: driving the expectation off the code under test would pass
whatever that code said.
"""
import pathlib
import re

SRC = pathlib.Path("src/drugref")


def _sources():
    return sorted(SRC.rglob("*.py"))


def test_only_overlay_points_a_row_at_its_successor():
    """One reader, one clear, one checksum, one run record -- and now ONE SUPERSESSION.

    A module that wrote this UPDATE itself would be re-deriving an ordering whose
    failure mode is a deferred constraint violation at COMMIT, arbitrarily far from the
    call that caused it. That is precisely the class of bug a shared primitive removes.
    """
    writers = [p for p in _sources()
               if "SET superseded_by" in p.read_text()]
    assert [p.name for p in writers] == ["overlay.py"]


# ---- where the policy table is named ----------------------------------------

# WHAT EACH FILE IS ALLOWED TO SAY, restated independently rather than counted from
# the code, so BOTH directions fail: a new reader added by accident, and an existing
# one deleted.
#
# test_only_the_current_view_reads_the_policy_table_directly pins the SQL side from
# pg_depend, but pg_rewrite sees only views and matviews -- it CANNOT see SQL embedded
# in Python or in a PL/pgSQL body. This is the other half.
#
# MATCHED BY REGEX, NOT `str.count`, and the negative lookahead is load-bearing:
# `drugref.class_expansion_policy_current` CONTAINS `drugref.class_expansion_policy`
# as a prefix, so a substring count reads the approved VIEW read as a base-table read.
# That would defeat the test -- swapping a view read for a base-table read would leave
# the total unchanged and pass, which is precisely the change this pin exists to catch.
# `(?!\w)` excludes `_current` and any later view built on the same stem.
POLICY_TABLE_NAMINGS = {
    # SQL. The INSERT and withdraw's `SELECT class_name` (db/027, #35), plus
    # decision_history's read of the history the _current view exists to filter out
    # (#61). The supersession UPDATE is NOT here: it goes through overlay.supersede,
    # which composes drugref.{table} from a bare argument. live_decisions's read of
    # class_expansion_policy_current is NOT here either -- it is the approved VIEW
    # path, which is exactly what the regex's negative lookahead excludes.
    "interactions.py": 3,
    # PROSE, NOT SQL -- the operator warning telling them the table is append-only and
    # naming the two functions that can revise it. A grep that called this a reader
    # would be counting the sentence that explains the rule.
    "medrt_run.py": 1,
}

_BASE_TABLE = re.compile(r"drugref\.class_expansion_policy(?!\w)")


def test_only_interactions_reads_the_policy_table_from_python():
    """The base table has ONE Python owner (#61).

    `drugref policy` could easily have written its own query -- it is a read, and the
    handler has a connection. It does not: cli.py calls interactions.py, because a
    handler with its own SELECT would be a reader no test in this repo could notice.
    """
    named = {p.name: len(_BASE_TABLE.findall(p.read_text()))
             for p in _sources()
             if _BASE_TABLE.search(p.read_text())}
    assert named == POLICY_TABLE_NAMINGS


def test_the_pin_does_not_count_a_read_of_the_current_view():
    """The view read IS the approved path -- live_decisions goes through
    class_expansion_policy_current precisely so a `withdrawn` row, which is live
    without binding, stays out. Counting it as a base-table read would make the pin
    blind to the one substitution it exists to catch."""
    assert _BASE_TABLE.findall("FROM drugref.class_expansion_policy_current ") == []
    assert _BASE_TABLE.findall("FROM drugref.class_expansion_policy ") == [
        "drugref.class_expansion_policy"]
