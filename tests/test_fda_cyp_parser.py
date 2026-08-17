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


def test_a_single_trailing_footnote_is_split_off():
    assert fda_cyp.split_footnotes("adefovir 1") == ("adefovir", "1")


def test_a_COMMA_SEPARATED_footnote_list_is_split_off():
    """THE LOAD-BEARING CASE. FDA prints 'ritonavir 14, 15, 16' -- three markers,
    comma-separated.

    A stripper that handles 'adefovir 1' but not this leaves the substance named
    with its markers attached, which resolves to nothing -- so one of the most
    important CYP3A inhibitors in medicine drops out of the ingest SILENTLY and
    the run still reports success.

    THE DESIGN ROUND FIRST WROTE THIS STRING DOWN AS 'ritonavir 14, 15,' -- a
    string that appears nowhere on FDA's page. It was its own probe stripper's
    output: the regex ate the trailing ' 16' and left the comma, and the result
    was recorded as a measurement of the source. A partially-working parser hands
    you a plausible string, and a plausible string gets quoted.
    """
    assert fda_cyp.split_footnotes("ritonavir 14, 15, 16") == ("ritonavir", "14, 15, 16")
    # And the trailing-comma form the probe produced must ALSO split cleanly, so a
    # re-fetch that really does end in a comma is not a new bug.
    assert fda_cyp.split_footnotes("ritonavir 14, 15,") == ("ritonavir", "14, 15")


def test_a_LETTER_marker_is_a_second_namespace():
    """Footnotes are numbered AND lettered: cenobamate's cell ends 'inducer b'."""
    assert fda_cyp.split_footnotes("CYP3A moderate inducer b") == ("CYP3A moderate inducer", "b")


def test_an_unfootnoted_name_is_returned_unchanged_with_no_markers():
    assert fda_cyp.split_footnotes("abiraterone") == ("abiraterone", None)


def test_a_number_that_is_part_of_the_name_is_not_eaten():
    """The stripper must not treat a pathway digit as a footnote.

    'peginterferon alpha-2a' and 'MATE2-K' both end in alphanumerics that are
    NAME, not marker. The rule is a marker is a whitespace-separated bare integer
    or single lower-case letter -- '2a' and '2-K' are neither.
    """
    assert fda_cyp.split_footnotes("peginterferon alpha-2a") == ("peginterferon alpha-2a", None)
    assert fda_cyp.split_footnotes("MATE2-K substrate") == ("MATE2-K substrate", None)


def test_the_fixture_yields_ritonavir_not_ritonavir_14_15(fixture_html):
    """End to end over the real bytes: the substance is named 'ritonavir'."""
    rows = fda_cyp.extract_rows(fixture_html)
    names = {fda_cyp.split_footnotes(row[0])[0] for row in rows}
    assert "ritonavir" in names
    assert not any(name.startswith("ritonavir 14") for name in names)
