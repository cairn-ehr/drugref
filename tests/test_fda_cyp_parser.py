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


def test_the_potency_cross_check_is_total_not_skipped_when_one_side_is_missing():
    """`if cell_potency is not None and column_potency is not None:` reads like a
    guard but is really a SKIP: whenever either side is missing, the comparison
    never runs and the cell is silently accepted. Neither shape below occurs on
    the pinned page today -- that is exactly why an optional-group check is the
    dangerous one: it passes everything it has never been asked to reject, and a
    re-fetch that adds either shape would sail through with the run reporting
    success. Spec section 8's whole point is to turn ANY shape drift here into a
    stopped ingest, not just the drift the pinned page happens to demonstrate.
    """
    # The column (CYP Mod INH) declares a potency band; the cell states none.
    with pytest.raises(fda_cyp.FdaCypParseError, match="disagree"):
        fda_cyp.parse_cell("3A inhibitor", 2, "CYP Mod INH")
    # The column (TRNSP INH) is a transporter column -- FDA publishes no potency
    # vocabulary for transporters at all -- yet the cell states one anyway.
    with pytest.raises(fda_cyp.FdaCypParseError, match="disagree"):
        fda_cyp.parse_cell("P-gp strong inhibitor", 9, "TRNSP INH")


def test_a_pathway_from_the_wrong_system_aborts_even_though_it_is_a_real_pathway():
    """PATHWAYS used to be one flat set, so a genuine transporter name slipped
    through under a CYP column (and vice versa) as long as it was SOME known
    pathway -- 'OATP1B1' is real, but it is not a CYP enzyme, and accepting it
    under 'CYP Mod INH' would mint a class as nonsensical as 'cyp:oatp1b1:...'.
    The vocabulary a token must belong to is the COLUMN's declared system, not
    the union of every system this table has.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="system"):
        fda_cyp.parse_cell("OATP1B1 moderate inhibitor", 2, "CYP Mod INH")
    with pytest.raises(fda_cyp.FdaCypParseError, match="system"):
        fda_cyp.parse_cell("3A inhibitor", 9, "TRNSP INH")


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


def test_the_release_is_read_from_the_pages_own_dateModified():
    """The spike said the HTML carries no release identifier. It carries one.

    Fetch time records when drugref LOOKED; dateModified records when FDA CHANGED
    the content, and only the second distinguishes a re-fetch of unchanged
    material from a genuine revision.
    """
    page = '<script>{"dateModified": "Fri, 05/29/2026 - 14:00"}</script>'
    assert fda_cyp.parse_release(page) == "2026-05-29T14:00"


def test_the_meta_tag_is_an_accepted_second_spelling():
    page = '<meta property="article:modified_time" content="Fri, 05/29/2026 - 14:00" />'
    assert fda_cyp.parse_release(page) == "2026-05-29T14:00"


def test_a_page_without_a_modified_date_FAILS_and_names_the_field():
    """It does NOT silently substitute fetch time. That would put a value with a
    different meaning in the same column, and this project has already lost
    rounds to one field carrying two meanings.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="dateModified"):
        fda_cyp.parse_release("<html><body>no date here</body></html>")


@pytest.mark.skipif(
    not pathlib.Path("downloads/FDA/fda_cyp_2026-05-29.html").exists(),
    reason="live page not downloaded")
def test_the_real_page_reports_the_expected_release():
    page = pathlib.Path("downloads/FDA/fda_cyp_2026-05-29.html").read_text(encoding="utf-8")
    assert fda_cyp.parse_release(page) == "2026-05-29T14:00"


def test_the_bupropion_footnote_is_read_verbatim_from_the_page(fixture_html):
    """Marker 2 is the footnote the whole withholding design in section 3 rests
    on: bupropion's row asserts '2B6 sensitive substrate' while this text says
    the opposite. Reading it wrong -- or not reading it at all -- would make
    the withholding decision look justified by a test while carrying nothing
    to back it up in the database.
    """
    footnotes = fda_cyp.parse_footnotes(fixture_html)
    assert footnotes["2"] == (
        "Bupropion itself is not a sensitive substrate. It is metabolized by "
        "multiple enzymes including CYP2B6 that is only responsible for the "
        "formation of hydroxybupropion, an active metabolite. Thus, the "
        "considerations of drug interactions with CYP2B6 modulators should "
        "take into account plasma concentration changes of both buproprion "
        "and hydroxybupropion.")


def test_a_marker_with_no_page_side_definition_is_simply_absent(fixture_html):
    """cenobamate's cell carries a bare letter marker, 'b' (section 2.3, 'a
    second namespace'), and FDA's own Footnotes list never defines a letter at
    all -- verified against the real page, not assumed. parse_footnotes must
    not raise over this: an undefined marker is a page oddity to record
    evidence about, not a reason to abort reading every OTHER, well-defined
    footnote on the page.
    """
    footnotes = fda_cyp.parse_footnotes(fixture_html)
    assert "b" not in footnotes
    assert len(footnotes) == 21, "the 21 numbered footnotes must still all be read"


def test_a_missing_footnotes_section_raises():
    """Unlike a single undefined MARKER, the whole SECTION going missing is a
    structural change to the page (FDA renaming or removing "Footnotes"), not
    a data variation -- so this raises, matching extract_rows on a missing
    table and parse_release on a missing dateModified stamp.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="Footnotes"):
        fda_cyp.parse_footnotes("<table><tr><td>x</td></tr></table>")


@pytest.mark.skipif(
    not pathlib.Path("downloads/FDA/fda_cyp_2026-05-29.html").exists(),
    reason="live page not downloaded")
def test_the_real_page_and_the_fixture_agree_on_every_footnote(fixture_html):
    """The fixture's appended Footnotes block is a verbatim excerpt of the real
    page (never hand-written, per the fixture rule at the top of this file),
    so parsing both must produce byte-identical text for all 21 markers.
    """
    real = pathlib.Path("downloads/FDA/fda_cyp_2026-05-29.html").read_text(encoding="utf-8")
    assert fda_cyp.parse_footnotes(real) == fda_cyp.parse_footnotes(fixture_html)
