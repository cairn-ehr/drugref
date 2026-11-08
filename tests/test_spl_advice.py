# tests/test_spl_advice.py
"""The advice reader: the label's own advice level around a mention (pure).

Texts are synthesised (rule 6) -- never real label prose.
"""

from drugref.ingest.spl_advice import ADVICE_RULES, LEVELS, read_advice, sentence_span


def _read(text, name):
    i = text.index(name)
    return read_advice(text, i, i + len(name))


def test_contraindicated():
    r = _read("Use with strong CYP1A2 inhibitors is contraindicated.", "strong CYP1A2 inhibitors")
    assert (r.level, r.mixed, r.rule_id, r.scope) == (
        "contraindicated", False, "contraindicated.contraindicated", "sentence")


def test_strongest_wins_and_mixed():
    r = _read("Avoid MAO inhibitors; use is contraindicated within 14 days.", "MAO inhibitors")
    assert (r.level, r.mixed) == ("contraindicated", True)


def test_not_contraindicated_is_not_contraindicated():
    assert _read("Use with antacids is not contraindicated.", "antacids").level == "not_read"


def test_no_dose_adjustment_is_nmi_not_adjust_dose():
    r = _read("No dose adjustment is needed with antacids.", "antacids")
    assert r.level == "no_meaningful_interaction"


def test_nmi_never_beats_a_strength_level():
    r = _read("No clinically significant interaction with antacids, but monitor INR.", "antacids")
    assert r.level == "monitor"


def test_sentence_scope_only():
    text = "Monitor patients on diuretics. Thiazides are fine."
    assert _read(text, "Thiazides").level == "not_read"


def test_eg_and_decimal_do_not_split():
    text = "Avoid strong inhibitors (e.g. ketoconazole) at doses above 0.5 mg."
    s, e = sentence_span(text, text.index("ketoconazole"), text.index("ketoconazole") + 12)
    assert (s, e) == (0, len(text))


def test_heading_block_scope():
    text = ("7.1 Warfarin text here. 7.2 Potassium-Sparing Diuretics Concomitant use "
            "increases potassium. Avoid concomitant use. 7.3 Other text.")
    r = _read(text, "Potassium-Sparing Diuretics")
    assert (r.level, r.scope) == ("avoid", "heading_block")
    assert text[r.scope_start:r.scope_end].startswith("7.2")


def test_unknown_phrase_is_not_read():
    r = _read("Diuretics were studied.", "Diuretics")
    assert (r.level, r.rule_id) == ("not_read", None)


def test_rule_ids_unique_and_levels_known():
    ids = [r.rule_id for r in ADVICE_RULES]
    assert len(ids) == len(set(ids))
    assert all(r.level in LEVELS[:5] and r.rule_id.startswith(r.level + ".") for r in ADVICE_RULES)


# --- choices the brief leaves open ---------------------------------------

def test_matching_is_case_insensitive_including_the_negation_guard():
    assert _read("CONTRAINDICATED with nitrates.", "nitrates").level == "contraindicated"
    assert _read("Antacids are Not Contraindicated.", "Antacids").level == "not_read"


def test_do_not_coadminister_is_avoid():
    r = _read("Do not coadminister with nitrates.", "nitrates")
    assert (r.level, r.rule_id) == ("avoid", "avoid.do_not_coadminister")


def test_nmi_is_not_read_at_heading_block_scope():
    # A block can mix statements, so a "no interaction" sentence elsewhere in it
    # must not become the reading for a mention in a different sentence.
    text = "7.1 Antacids Seen in studies. No clinically significant interaction with iron. 7.2 Other."
    assert _read(text, "Antacids").level == "not_read"


# --- review fixes (R6, negation, conditionals, boundaries, speed) -----------

def test_r6_strength_advice_beside_nmi_clause_is_not_an_all_clear():
    r = _read("Concomitant use with strong CYP3A inhibitors is not recommended; "
              "no dose adjustment is needed with weak CYP3A inhibitors.", "strong CYP3A inhibitors")
    assert r.level == "avoid"


def test_r6_except_clause_blocks_nmi():
    r = _read("No dose adjustment is required except with strong CYP3A inhibitors.",
              "strong CYP3A inhibitors")
    assert r.level == "not_read"


def test_r6_do_not_use_beside_nmi():
    r = _read("Do not use with nitrates; no clinically significant interaction with antacids.",
              "nitrates")
    assert r.level == "avoid"


def test_r6_unseeded_advice_beside_nmi_is_never_nmi():
    r = _read("Do not exceed 10 mg with strong inhibitors; no dose adjustment with weak "
              "inhibitors.", "strong inhibitors")
    assert r.level != "no_meaningful_interaction"


def test_r6_contrast_words_block_nmi_but_single_clause_reads():
    ok = _read("No clinically significant interaction with antacids.", "antacids")
    assert ok.level == "no_meaningful_interaction"
    for w in ("but", "However", "unless", "although", "while", "whereas"):
        r = _read(f"No clinically significant interaction with antacids {w} iron differs.", "antacids")
        assert r.level == "not_read", w


def test_negation_guard_survives_whitespace_and_an_intervening_word():
    for t in ("Not\ncontraindicated with antacids.", "Not  contraindicated with antacids.",
              "Antacids are not considered contraindicated."):
        assert _read(t, "ntacids").level == "not_read", t


def test_no_dose_reduction_is_nmi_not_adjust_dose():
    assert _read("No dose reduction is needed with antacids.", "antacids").level == \
        "no_meaningful_interaction"


def test_conditional_dose_advice_reads_adjust_dose():
    # Safe direction: advice is present, so it is read (never an all-clear).
    r = _read("If coadministration is necessary, reduce the dose of simvastatin.", "simvastatin")
    assert r.level == "adjust_dose"


def test_semicolon_before_capital_splits_and_marker_splits():
    t = "Avoid nitrates; Statins are fine. 7.2 Fibrates need care."
    assert _read(t, "Statins").level == "not_read"
    s, e = sentence_span(t, t.index("Fibrates"), t.index("Fibrates") + 8)
    assert t[s:e].startswith("7.2")


def test_long_text_is_read_fast():
    import time
    t = ("Diuretics were studied in 0.5 mg cohorts e.g. adults. " * 1000) + "Avoid nitrates."
    t0 = time.perf_counter()
    r = _read(t, "nitrates")
    assert r.level == "avoid" and time.perf_counter() - t0 < 1.0
