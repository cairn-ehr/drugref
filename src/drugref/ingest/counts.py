# src/drugref/ingest/counts.py
"""Issue #187: one shared refusal of negative counts in a dataclass.

The SPL summary types (`spl.Corpus`, `spl_checks.SplSummary`,
`spl_release.ScanResult`) each check that their buckets ADD UP. A sum is
satisfied by a -1 on one side and a +1 on the other, so an "adds up" check is
only as strong as the guarantee that no term is negative. That guarantee lives
here, once, so the three types cannot drift apart on it.

Pure: no I/O, no DB.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping


def _is_count(value: object) -> bool:
    # `bool` is a subclass of `int` in Python, but a flag is not a count.
    return isinstance(value, int) and not isinstance(value, bool)


def refuse_negative_counts(instance: object) -> None:
    """Raise `ValueError` naming the first negative count on `instance`.

    Checks every `int` field of the dataclass `instance`, and every `int` value
    of every mapping field (e.g. per-route buckets, whose sum is reconciled the
    same way). Non-count fields -- strings, tuples, sets -- are ignored, so a
    new counter is covered by existing rather than by being listed here.
    """
    for field in dataclasses.fields(instance):
        value = getattr(instance, field.name)
        if _is_count(value) and value < 0:
            raise ValueError(
                f"{field.name} = {value}: a count cannot be negative, and a "
                "negative term lets a reconciliation add up when it should not")
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _is_count(item) and item < 0:
                    raise ValueError(
                        f"{field.name}[{key!r}] = {item}: a count cannot be "
                        "negative, and a negative bucket cancels a real one")
