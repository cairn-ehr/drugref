# tests/test_spl_advice_scope.py
"""The advice reader's scope order with WITHHELD readings (R21), and the Task 9B fix-round
minor rules. Texts are synthesised (rule 6) -- never real label prose.
"""

from drugref.ingest.spl_advice import read_advice


def _shipped(text, name):
    """What ships: the default WITHHELD set applies."""
    i = text.index(name)
    return read_advice(text, i, i + len(name))


def _read(text, name):
    """The phrase rules alone: nothing withheld."""
    i = text.index(name)
    return read_advice(text, i, i + len(name), withheld=frozenset())


# ---- R21: a withheld sentence reading falls through like not_read ----------------------

def test_a_withheld_sentence_avoid_falls_through_to_a_contraindicated_block():
    """The MAOI shape: the heading's own sentence says "avoid" (withheld at sentence
    scope), and the block under the heading files the class under Contraindications.
    Before R21 the withheld sentence stopped the reader and the span shipped not_read
    -- an under-warning of a contraindication."""
    text = ("7.1 Widget Oxidase Inhibitors Avoid use of drug A with widget oxidase "
            "inhibitors. The risk of a widget syndrome rises [see Contraindications (4)]. "
            "7.2 Other Drugs No text here.")
    r = _shipped(text, "Widget Oxidase Inhibitors")
    assert (r.level, r.scope) == ("contraindicated", "heading_block")
    assert r.scope_start == 0 and r.scope_end == text.index("7.2")


def test_a_withheld_mixed_sentence_never_ships_the_weaker_level():
    """'Avoid ..., or reduce the dose' reads avoid (strongest wins). Withheld at
    sentence scope it must ship not_read there -- never the weaker adjust_dose."""
    text = "Avoid widget agonists, or reduce the dose of drug A."
    r = _shipped(text, "widget agonists")
    assert (r.level, r.rule_id, r.mixed, r.scope) == ("not_read", None, False, "sentence")
    assert _read(text, "widget agonists").level == "avoid"


def test_a_withheld_mixed_sentence_then_falls_through_to_its_heading_block():
    text = ("7.2 Widget Agonists Avoid widget agonists, or reduce the dose of drug A. "
            "Watch the patient. 7.3 Other Drugs No text here.")
    r = _shipped(text, "Widget Agonists")
    assert (r.level, r.mixed, r.scope) == ("avoid", True, "heading_block")


def test_a_withheld_sentence_falls_through_to_its_table_row():
    """A mention inside a PLR row's Impact cell whose sentence reads 'monitor'
    (withheld) reaches the row's Intervention cell, as not_read would."""
    text = ("Widget Inhibitors Clinical Impact: Monitor for raised widget levels with "
            "widget inhibitors. Intervention: Drug A is contraindicated with these drugs.")
    i = text.index("widget inhibitors")
    r = read_advice(text, i, i + len("widget inhibitors"))
    assert (r.level, r.scope) == ("contraindicated", "heading_block")


def test_a_withheld_sentence_with_nothing_further_still_ships_not_read_over_it():
    text = "Monitor INR in patients on widgetants."
    r = _shipped(text, "widgetants")
    assert (r.level, r.scope, r.scope_start, r.scope_end) == ("not_read", "sentence", 0,
                                                              len(text))


# ---- Fix-round minor (a): negated separate administration ------------------------------

def test_separate_administration_not_necessary_is_not_dose_advice():
    for text in ("Separate administration of drug A and widgetacids is not necessary.",
                 "Separate administration is not necessary with widgetacids.",
                 "Separate dosing is not required with widgetacids."):
        assert _read(text, "widgetacids").level == "not_read", text


def test_plain_separate_administration_still_reads_adjust_dose():
    assert _read("Separate administration of drug A and widgetacids by 2 hours.",
                 "widgetacids").level == "adjust_dose"


def test_separate_administration_mask_never_hides_a_strength_word():
    """R24: a contraindication inside the gap must still be read (comma or not)."""
    for text in ("Separate dosing with widgetacids, which are contraindicated in renal "
                 "failure, is not needed.",
                 "Separate dosing with widgetacids contraindicated in renal failure is "
                 "not needed."):
        assert _read(text, "widgetacids").level == "contraindicated", text
