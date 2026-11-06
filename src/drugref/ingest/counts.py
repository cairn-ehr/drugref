# src/drugref/ingest/counts.py
"""Issue #187: one shared refusal of negative counts in a dataclass.

The SPL summary types (`spl.Corpus`, `spl_checks.SplSummary`,
`spl_release.ScanResult`) each check that their buckets ADD UP. A sum is
satisfied by a -1 on one side and a +1 on the other, so an "adds up" check is
only as strong as the guarantee that no term is negative. That guarantee lives
here, once, so the types cannot drift apart on it. Issue #195 audited every
other `__post_init__` under `src/` and added the two more that reconcile or
bound a sum: `drugcentral_run.DrugCentralSummary` and
`spl_suppress.NextWordProfile`. The PR #197 review added the `tools/` types
that reconcile sums (`tests/test_tools_negative_counts.py` lists them).

Pure: no I/O, no DB.
"""
from __future__ import annotations

import dataclasses
import numbers
from collections.abc import Mapping
from decimal import Decimal

#: Every number type a count can arrive as. `Decimal` is listed because it is
#: not a `numbers.Real`, yet it is what psycopg returns for SQL `SUM()`.
_NUMBER = (numbers.Real, Decimal)


def refuse_negative_counts(instance: object) -> None:
    """Raise `ValueError` naming the first negative count on `instance`.

    Checks every number field of the dataclass `instance` (`int`, `float`,
    `Decimal` ... -- not only `int`, so a count read through SQL `SUM()` is
    covered too), and every number value of every mapping field (e.g.
    per-route buckets, whose sum is reconciled the same way). Non-count
    fields -- strings, tuples, sets -- are ignored, so a new counter is
    covered by existing rather than by being listed here.
    (A `bool` is an `int` too, but it is never negative, so it never trips.)
    """
    for field in dataclasses.fields(instance):
        value = getattr(instance, field.name)
        if isinstance(value, _NUMBER) and value < 0:
            raise ValueError(
                f"{field.name} = {value}: a count cannot be negative, and a "
                "negative term lets a reconciliation add up when it should not")
        if isinstance(value, Mapping):
            for key, item in value.items():
                if isinstance(item, _NUMBER) and item < 0:
                    raise ValueError(
                        f"{field.name}[{key!r}] = {item}: a count cannot be "
                        "negative, and a negative bucket cancels a real one")
