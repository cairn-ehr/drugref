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
