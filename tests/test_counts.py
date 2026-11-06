"""`counts.refuse_negative_counts`: the shared guard behind every "adds up" check."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from drugref.ingest.counts import refuse_negative_counts


@dataclass
class _Tally:
    read: object = 0
    buckets: object = None


@pytest.mark.parametrize("negative", [-1, -1.0, Decimal(-1)])
def test_a_negative_count_is_refused_whatever_NUMBER_type_carries_it(negative):
    """Review of PR #197: only an `int` was checked, so a count read through SQL
    `SUM()` -- which psycopg returns as a `Decimal` -- slipped past unchecked."""
    with pytest.raises(ValueError, match="read"):
        refuse_negative_counts(_Tally(read=negative))
    with pytest.raises(ValueError, match="buckets"):
        refuse_negative_counts(_Tally(buckets={"oral": negative}))


def test_non_numbers_and_non_negative_counts_pass():
    refuse_negative_counts(_Tally(read=0, buckets={"oral": Decimal(3)}))
    refuse_negative_counts(_Tally(read="text", buckets=("a",)))
    refuse_negative_counts(_Tally(read=False))
