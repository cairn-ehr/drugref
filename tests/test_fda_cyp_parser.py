# tests/test_fda_cyp_parser.py
"""The FDA-CYP parser: pure, no DB, no network.

THE FIXTURE IS EXTRACTED VERBATIM from the live page and carries every trap the
design was derived from, because a fixture of clean rows would pass a parser
that mints four garbage classes. Do not hand-edit it; rebuild it with the
snippet in the plan's Task 2 if the page is re-fetched.
"""
import pathlib

import pytest

from drugref.ingest import fda_cyp

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "fda_cyp_table.html"


@pytest.fixture(scope="module")
def fixture_html():
    return FIXTURE.read_text(encoding="utf-8")


def test_every_row_has_exactly_eleven_cells(fixture_html):
    """The real page measures Counter({11: 245}) -- EXACT, not typical.

    So a ragged row is a structural change to the source, not a parse variation,
    and it must stop the ingest rather than be absorbed. This is one of the two
    integrity gates that make a regex parse of HTML defensible (spec section 8).
    """
    rows = fda_cyp.extract_rows(fixture_html)
    assert rows, "fixture yielded no data rows"
    for row in rows:
        assert len(row) == fda_cyp.EXPECTED_COLUMNS, f"ragged row: {row[0]!r}"


def test_a_ragged_row_raises_rather_than_being_absorbed(fixture_html):
    broken = fixture_html.replace("</tr>\n</table>", "<td>extra</td></tr>\n</table>")
    with pytest.raises(fda_cyp.FdaCypParseError, match="cells"):
        fda_cyp.extract_rows(broken)


def test_the_header_row_is_not_returned_as_data(fixture_html):
    rows = fda_cyp.extract_rows(fixture_html)
    assert not any(row[0] == "Drug or Other Substance" for row in rows)


def test_the_ten_role_columns_are_pinned_to_their_meanings():
    """Each column IS a (system, role, potency) tuple -- the table is a MATRIX,
    not a list of facts, and this mapping is the whole reason a cell can be read.
    """
    assert fda_cyp.ROLE_COLUMNS[1] == ("CYP", "inhibitor", "strong")
    assert fda_cyp.ROLE_COLUMNS[8] == ("CYP", "substrate", "moderate sensitive")
    assert fda_cyp.ROLE_COLUMNS[9] == ("transporter", "inhibitor", None)
    assert fda_cyp.ROLE_COLUMNS[10] == ("transporter", "substrate", None)
    assert len(fda_cyp.ROLE_COLUMNS) == 10
