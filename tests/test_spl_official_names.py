"""`spl_official_names`: UNII official names as extra moiety match keys (#213).

Pure: no database. The registry here is two plain mappings, exactly the shape
`registry_read.load_registry` hands `spl_run` (`by_name`, `by_unii`).
"""
from drugref.ingest import spl_official_names as on

P, E, R, X = "uuid-paracetamol", "uuid-epinephrine", "uuid-racepinephrine", "uuid-x"


def test_an_official_name_becomes_a_key_for_its_uniis_moiety():
    """The point of #213: labels write 'acetaminophen', the registry 'paracetamol'."""
    names, counts = on.adopt(
        {"paracetamol": P},
        [("ACETAMINOPHEN", "U1"), ("PARACETAMOL", "U1")],
        by_unii={"U1": P}, excluded=())
    assert names == {"paracetamol": P, "ACETAMINOPHEN": P}
    # 'PARACETAMOL' folds onto the display name it already has: not a second key,
    # or the matcher would see two entries on one span and flag it ambiguous.
    assert (counts.adopted, counts.already_named) == (["ACETAMINOPHEN"], 1)


def test_the_registry_display_name_of_ANOTHER_moiety_wins():
    """'epinephrine' is an official name of the racemate's UNII, but the registry
    already names the L-isomer 'epinephrine'. Adopting it would make every
    'epinephrine' span ambiguous between the two; the registry name stays sole."""
    names, counts = on.adopt(
        {"epinephrine": E, "racepinephrine": R},
        [("EPINEPHRINE", "UR")], by_unii={"UR": R}, excluded=())
    assert names == {"epinephrine": E, "racepinephrine": R}
    assert counts.shadowed == ["EPINEPHRINE"] and counts.adopted == []


def test_a_name_two_moieties_claim_is_dropped_never_first_wins():
    names, counts = on.adopt(
        {"x": X}, [("WIDGETOL", "U1"), ("Widgetol", "U2")],
        by_unii={"U1": P, "U2": R}, excluded=())
    assert names == {"x": X}
    assert counts.ambiguous == ["WIDGETOL"]


def test_an_excluded_name_is_not_adopted_and_is_listed():
    """Compared folded: the file says 'glucose', UNII_Names 'GLUCOSE'."""
    names, counts = on.adopt(
        {"dextrose": P}, [("GLUCOSE", "U1")], by_unii={"U1": P},
        excluded=("glucose",))
    assert names == {"dextrose": P}
    assert counts.excluded == ["GLUCOSE"]


def test_one_name_spelled_twice_for_one_moiety_is_one_key():
    names, counts = on.adopt(
        {"x": X}, [("RIFAMPIN", "U1"), ("Rifampin", "U1")],
        by_unii={"U1": R}, excluded=())
    assert names == {"x": X, "RIFAMPIN": R}
    assert counts.adopted == ["RIFAMPIN"]


def test_the_shipped_exclusions_are_the_four_measured_ones():
    """Each is justified by a measured distribution in the data file's comments;
    a new one needs a new measurement, so the set is pinned here."""
    assert set(on.shipped_exclusions()) == {"glucose", "retinal", "pca", "glycol"}


def test_the_summary_line_says_every_outcome():
    _, counts = on.adopt(
        {"epinephrine": E}, [("EPINEPHRINE", "UR"), ("ALBUTEROL", "U2")],
        by_unii={"UR": R, "U2": P}, excluded=())
    text = str(counts)
    assert "1 official names adopted" in text and "1 shadowed" in text


def test_no_names_file_says_so():
    """`ingest_spl(unii_names=None)` adopts nothing; the line must not read as if
    the release simply had no new names."""
    assert "WARNING" in str(on.skipped())
