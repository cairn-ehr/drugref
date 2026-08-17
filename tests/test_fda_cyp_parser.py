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


def test_a_simple_cell_yields_one_pathway():
    assert fda_cyp.parse_cell("2D6 moderate inhibitor", 2, "CYP Mod INH") == [("2D6", None)]


def test_a_semicolon_list_yields_several_pathways():
    assert fda_cyp.parse_cell("P-gp; BCRP inhibitor", 9, "TRNSP INH") == [
        ("P-gp", None), ("BCRP", None)]


def test_the_word_and_is_also_a_separator():
    assert fda_cyp.parse_cell("3A and 2C19 weak inhibitor", 3, "CYP WK INH") == [
        ("3A", None), ("2C19", None)]


def test_a_comma_is_also_a_separator():
    assert fda_cyp.parse_cell("1A2, 2B6 weak inducer", 6, "CYP WK IND") == [
        ("1A2", None), ("2B6", None)]


def test_a_cyp_prefix_is_normalised_away():
    """The page writes bare '3A' and prefixed 'CYP3A' for the same pathway."""
    assert fda_cyp.parse_cell("CYP3A moderate inducer", 5, "CYP Mod IND") == [("3A", None)]


def test_a_trailing_noun_is_not_a_pathway():
    """The real cell (grepped from downloads/FDA/fda_cyp_2026-05-29.html) is
    'BCRP and P-gp transporters inhibitor' -- the brief's own quote of it, taken
    from spec section 2.2 point 2, is a fragment that stops at 'transporters' and
    drops the trailing role word every real cell carries. Using the fragment
    verbatim as the parse_cell input makes _ROLE_PHRASE find no role phrase at
    all and raise, which is the same 'quoted the bug/fragment instead of the
    source' trap Task 2 hit with 'ritonavir 14, 15,' -- corrected the same way,
    against the live bytes rather than the spec's illustrative shorthand.
    """
    assert fda_cyp.parse_cell("BCRP and P-gp transporters inhibitor", 9, "TRNSP INH") == [
        ("BCRP", None), ("P-gp", None)]


def test_a_footnote_attached_to_ONE_pathway_is_kept_with_it():
    """ciprofloxacin: '1A2 20 ; 3A moderate inhibitor'. Marker 20 belongs to 1A2
    ALONE, mid-cell -- a trailing-only stripper turns '1A2 20' into a pathway and
    mints the garbage class 'cyp:1a2 20:inhibitor:moderate'.
    """
    assert fda_cyp.parse_cell("1A2 20 ; 3A moderate inhibitor", 2, "CYP Mod INH") == [
        ("1A2", "20"), ("3A", None)]


def test_a_footnote_on_SEVERAL_pathways_is_kept_with_each():
    """rifampin: 'OATP1B1 13 ; OATP1B3 13 inhibitor'."""
    assert fda_cyp.parse_cell("OATP1B1 13 ; OATP1B3 13 inhibitor", 9, "TRNSP INH") == [
        ("OATP1B1", "13"), ("OATP1B3", "13")]


def test_the_role_word_may_repeat_per_list_item():
    """teriflunomide: 'BCRP; OATP1B1 inhibitor; OAT3 inhibitor'. The trailing role
    phrase covers only the items that do not state their own -- reading it as part
    of the pathway mints 'transporter:oatp1b1 inhibitor'.
    """
    assert fda_cyp.parse_cell("BCRP; OATP1B1 inhibitor; OAT3 inhibitor", 9, "TRNSP INH") == [
        ("BCRP", None), ("OATP1B1", None), ("OAT3", None)]


def test_an_unknown_pathway_token_ABORTS():
    """The closed vocabulary, and the reason this parser exists in this shape.

    A lenient parse of the real page produces 69 classes instead of 65 while
    reporting zero errors. Four are garbage, minted with real immortal UUIDs.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="pathway"):
        fda_cyp.parse_cell("CYP9Z9 moderate inhibitor", 2, "CYP Mod INH")


def test_a_cell_whose_role_disagrees_with_its_COLUMN_aborts():
    """The page states role and potency TWICE -- in the column heading and in the
    cell -- so they can be cross-checked for free. A disagreement means the
    table's shape changed under an unchanged checksum, which must stop the ingest
    rather than be resolved by preferring one of them.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="disagree"):
        fda_cyp.parse_cell("2D6 strong inhibitor", 2, "CYP Mod INH")  # column says moderate


def test_moderately_sensitive_matches_the_columns_moderate_sensitive():
    """The legend's word is not always the cell's: 'moderately sensitive
    substrate' against the column's 'Mod SENS SUB'. Not a disagreement.
    """
    assert fda_cyp.parse_cell("2C8 and 3A moderately sensitive substrate", 8,
                              "CYP Mod SENS SUB") == [("2C8", None), ("3A", None)]


def test_OATP1B_is_its_own_pathway_and_is_never_expanded():
    """FDA writes the coarser 'OATP1B' where other rows say OATP1B1/OATP1B3.
    Expanding it would manufacture a specificity FDA declined to state.
    """
    assert fda_cyp.parse_cell("OATP1B transporter inhibitor", 9, "TRNSP INH") == [
        ("OATP1B", None)]
    assert "OATP1B" in fda_cyp.PATHWAYS


def test_parse_table_over_the_fixture_produces_no_garbage_pathway(fixture_html):
    """Every pathway in every tuple is in the closed vocabulary -- the property
    the four garbage classes violated.
    """
    for tup in fda_cyp.parse_table(fixture_html):
        assert tup.pathway in fda_cyp.PATHWAYS


def test_parse_table_carries_the_row_ordinal_because_names_repeat(fixture_html):
    """aprepitant occupies TWO rows, so the substance name is not a row key."""
    tuples = fda_cyp.parse_table(fixture_html)
    ordinals = {t.row_ordinal for t in tuples if t.substance == "aprepitant"}
    assert len(ordinals) == 2
