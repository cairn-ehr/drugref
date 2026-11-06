"""The `tools/` measurement types refuse a negative count (review of PR #197).

Each of these checks that its figures ADD UP, and a sum is satisfied by a -1 on
one side and a +1 on the other. Issue #195's audit covered `src/` only; these
are the `tools/` types that reconcile a sum -- and the project has published
wrong figures from tools before. Every case below still adds up, so only the
shared `counts.refuse_negative_counts` can refuse it.
"""
from __future__ import annotations

import pytest

from tools.drugcentral_ddi_measure import Measurement
from tools.spl_ddi_measure import BandTally, PairCount
from tools.spl_label_extract import Census


@pytest.mark.parametrize("build, field", [
    (lambda: Measurement(
        rows=2, raw_names=0, names=0, names_resolved=0, routes={},
        unresolved_names=(), unresolvable_rows=-1, self_pair_rows=1,
        pair_rows=2, pairs=1, held=0), "unresolvable_rows"),
    (lambda: PairCount(distinct=1, held=-1, novel=2, self_pairs_excluded=0),
     "held"),
    (lambda: BandTally(class_occurrences=1, banded=0,
                       by_band={"weak": -1, "strong": 1}), "by_band"),
    (lambda: Census(records=2, with_section=1, by_product_type={"A": -1, "B": 2},
                    with_unii=0, distinct_text_keys=0), "by_product_type"),
])
def test_a_negative_count_that_still_ADDS_UP_is_refused(build, field):
    with pytest.raises(ValueError, match=field):
        build()
