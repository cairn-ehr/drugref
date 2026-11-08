# tests/test_spl_advice.py
"""The advice reader: the label's own advice level around a mention (pure).

Texts are synthesised (rule 6) -- never real label prose.
"""

from drugref.ingest.spl_advice import (ADVICE_RULES, LEVELS, NMI_TEMPLATES, read_advice,
                                       sentence_span)


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
    rules = ADVICE_RULES + NMI_TEMPLATES
    ids = [r.rule_id for r in rules]
    assert len(ids) == len(set(ids))
    assert all(r.level in LEVELS[:5] and r.rule_id.startswith(r.level + ".") for r in rules)
    assert all(r.level == "no_meaningful_interaction" for r in NMI_TEMPLATES)


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
    ok = _read("No clinically significant interaction was observed with antacids.", "antacids")
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


def test_r7_coordinated_second_clause_blocks_nmi():
    for t in ("Do not exceed 10 mg with strong inhibitors, and no dose adjustment is needed "
              "with weak inhibitors.",
              "Limit the dose to 10 mg with strong inhibitors, and no dose reduction is "
              "needed with weak inhibitors.",
              "Use caution with strong inhibitors and no dose adjustment is needed with weak "
              "ones."):
        assert _read(t, "strong inhibitors").level != "no_meaningful_interaction", t
    assert _read("Take the tablet with food and no dose adjustment is needed with antacids.",
                 "antacids").level == "not_read"


def test_plain_single_clause_nmi_still_reads():
    r = _read("No clinically significant interaction was observed with antacids.", "antacids")
    assert r.level == "no_meaningful_interaction"


def test_not_only_contraindicated_is_still_contraindicated():
    r = _read("Ketoconazole is not only contraindicated with simvastatin.", "simvastatin")
    assert r.level == "contraindicated"


def test_r8_leaky_probes_are_never_nmi():
    s = "strong CYP3A inhibitors"
    probes = [
        ("No dose adjustment is required, with the exception of strong CYP3A inhibitors.", s),
        ("No dose adjustment is required other than with strong CYP3A inhibitors.", s),
        ("No dose adjustment is required apart from strong CYP3A inhibitors.", s),
        ("Do not take with strong inhibitors, no dose adjustment is needed with weak "
         "inhibitors.", "strong inhibitors"),
        ("Strong inhibitors: use 5 mg once daily, weak inhibitors: no dose adjustment "
         "needed.", "Strong inhibitors"),
        ("Strong inhibitors: use 5 mg once daily\nWeak inhibitors: no dose adjustment "
         "needed.", "Strong inhibitors"),
        ("No dose adjustment is needed with weak inhibitors (use 5 mg with strong "
         "inhibitors).", "strong inhibitors"),
        ("With strong inhibitors use 5 mg and with weak inhibitors no dose adjustment is "
         "needed.", "strong inhibitors"),
        ("No dose adjustment is needed whilst strong inhibitors require 5 mg.",
         "strong inhibitors"),
        ("No dose adjustment is needed, yet strong inhibitors require 5 mg.",
         "strong inhibitors"),
    ]
    for t, name in probes:
        assert _read(t, name).level != "no_meaningful_interaction", t


def test_r8_mention_before_the_phrase_is_refused():
    r = _read("Antacids showed no clinically significant interaction.", "Antacids")
    assert r.level == "not_read"


# --- R9: absence of data, separators, contrast, conditions ----------------

def test_r9a_absence_of_data_is_never_nmi():
    s = "strong inhibitors"
    for t in ("No specific dose adjustment recommendations can be made for strong inhibitors.",
              "No dose adjustment studies have been conducted with strong inhibitors.",
              "No dose adjustment data are available for strong inhibitors.",
              "No dose adjustment guidance is available for strong inhibitors.",
              "No clinically significant interaction studies were done with strong inhibitors.",
              "No clinically significant interaction data exist for strong inhibitors."):
        assert _read(t, s).level == "not_read", t  # not NMI, and not adjust_dose either


def test_r9b_other_separators_block_nmi():
    s = "Strong inhibitors"
    base = "Weak inhibitors{0}No dose adjustment{0}Strong inhibitors{0}Use 5 mg"
    for sep in ("\r", "\t", "\v", "\f", "\u00a0", " \u2014 ", "\u2014", " \u2013 ", " - ",
                " / ", "/"):  # \u00a0 no-break space, \u2014 em dash, \u2013 en dash
        assert _read(base.format(sep), s).level != "no_meaningful_interaction", repr(sep)
    t = "No dose adjustment is needed with weak [use 5 mg with strong inhibitors]."
    assert _read(t, "strong inhibitors").level != "no_meaningful_interaction"


def test_r9c_contrast_words_block_nmi():
    for t in ("No dose adjustment is needed with weak not strong inhibitors.",
              "No dose adjustment is needed with weak rather than strong inhibitors.",
              "No dose adjustment is needed with weak inhibitors unlike strong inhibitors.",
              "No dose adjustment is needed with weak versus strong inhibitors.",
              "No dose adjustment is needed with weak vs strong inhibitors."):
        assert _read(t, "strong inhibitors").level == "not_read", t


def test_r9c_the_nmi_rule_own_not_does_not_block():
    # "dose adjustment is not required" is an NMI phrase that itself holds "not":
    # its matched span is excluded from the "not" check.
    r = _read("Dose adjustment is not required with antacids.", "antacids")
    assert r.level == "no_meaningful_interaction"


def test_r9d_conditional_all_clear_is_never_nmi():
    for t in ("No dose adjustment is needed with strong inhibitors if the patient takes 5 mg.",
              "No dose adjustment is needed with strong inhibitors after the first week of "
              "5 mg dosing.",
              "No dose adjustment is needed with strong inhibitors when taken at night.",
              "No dose adjustment is needed with strong inhibitors provided 5 mg is used.",
              "No dose adjustment is needed with strong inhibitors until day 7.",
              "No dose adjustment is needed with strong inhibitors before surgery.",
              "No dose adjustment is needed with strong inhibitors during induction."):
        assert _read(t, "strong inhibitors").level == "not_read", t


def test_r9_plain_positives_still_read_and_a_trailing_newline_is_not_a_separator():
    for t in ("No dose adjustment is needed with antacids.",
              "No clinically significant interaction was observed with antacids.",
              "No dose adjustment is needed with antacids.\r\nOther text follows."):
        assert _read(t, "antacids").level == "no_meaningful_interaction", repr(t)


# --- R10: NMI only via a closed full-match template -------------------------

def test_r10_unconstrained_verb_is_never_nmi():
    s = "strong inhibitors"
    for t in ("No dose adjustment is possible with strong inhibitors.",
              "No dose adjustment can compensate for strong inhibitors.",
              "No dose reduction is sufficient with strong inhibitors.",
              "No dose reduction will prevent toxicity with strong inhibitors.",
              "No dose adjustment has been established with strong inhibitors."):
        assert _read(t, s).level != "no_meaningful_interaction", t


def test_r10_a_mask_phrase_alone_is_never_nmi():
    # The NMI phrases only MASK the strength rules now; outside a template they read nothing.
    for t in ("No dose adjustment with antacids.",
              "No clinically significant interaction with antacids.",
              "No dose adjustment is needed with antacids in most patients.",
              "No dose adjustment is needed with  antacids."):  # double space
        assert _read(t, "antacids").level == "not_read", t


def test_r10_templates_read_nmi_and_name_the_template():
    for t, rule in (
            ("No dose adjustment is needed with antacids.", "no_dose_adjustment_needed"),
            ("No clinically significant interaction was observed with antacids.",
             "interaction_observed"),
            ("Dose adjustment is not required with antacids.", "dose_adjustment_not_needed"),
            ("No pharmacokinetic interactions are expected with antacids", "interaction_expected"),
            ("No dosage reductions are recommended for patients taking antacids.",
             "no_dose_adjustment_needed"),
            ):
        r = _read(t, "antacids")
        assert (r.level, r.rule_id) == ("no_meaningful_interaction",
                                        "no_meaningful_interaction." + rule), t


def test_r11_not_recommended_is_never_an_all_clear():
    # T4 and the mask exclude "recommended": these read avoid, not NMI / not_read.
    for t, name in (
            ("Dose adjustment is not recommended with antacids.", "antacids"),
            ("Dose adjustment is not recommended for patients receiving antacids.", "antacids"),
            ("Coadministration with strong inhibitors requiring dose reduction is not "
             "recommended.", "strong inhibitors"),
            ("Use with strong inhibitors even with dose adjustment is not recommended.",
             "strong inhibitors")):
        r = _read(t, name)
        assert (r.level, r.rule_id) == ("avoid", "avoid.not_recommended"), t



def test_scope_contains_the_mention_on_EVERY_path_even_when_a_rule_fires():
    """'Avoid Potassium. Salts are fine.' read at the span of 'Potassium. Salts': the
    sentence text[s:e] ends inside the span and 'Avoid' fires a rule, which used to
    return a scope db/066's CHECK refuses. The class pass now drops such spans (R17)
    but the reader is defensive on its own."""
    text = "Avoid Potassium. Salts are fine."
    start, end = 6, 22
    r = read_advice(text, start, end)
    assert r.scope_start <= start and r.scope_end >= end


def test_span_crosses_sentence_boundary():
    from drugref.ingest.spl_advice import span_crosses_sentence_boundary as crosses
    text = "Avoid Potassium. Salts are fine."
    assert crosses(text, 6, 22)
    assert not crosses(text, 6, 15)                      # the boundary is its end
    assert not crosses("Use e.g. Statins now.", 4, 15)   # an abbreviation is not one
    assert crosses("Use Potassium; Salts now.", 4, 20)   # ';' counts like '.'


def test_nmi_is_refused_for_a_mention_running_past_the_sentence_end():
    """The scope is widened only on the RETURNED reading; the read itself stays on the
    original sentence so _read_scope's refusal of NMI for a mention that runs past the
    sentence end still fires (zero-tolerance level, two defensive layers)."""
    text = ("No clinically significant interaction was observed with Potassium. "
            "Salts were given daily.")
    start = text.index("Potassium")
    end = text.index("Salts") + len("Salts")
    r = read_advice(text, start, end)
    assert r.level == "not_read"
    assert r.scope_start <= start and r.scope_end >= end


# ---- Task 9B tuning, iteration 1 (each test is a hand-sample failure, synthesised) ----
# The hand sample on SPL run 12 read avoid 60%, adjust_dose 82%, monitor 70% exact
# agreement (gate 90%), and found contraindications the reader could not see. Every
# test below reproduces one failure shape with invented wording (rule 6).

def test_contraindicated_with_a_table_footnote_letter():
    """A table cell 'Contraindicated' with a footnote letter glued on ('...dicateda')
    defeated the whole-word match: a contraindication read not_read."""
    r = _read("Supplements containing widgetines Contraindicateda HR", "widgetines")
    assert r.level == "contraindicated"


def test_a_cross_reference_to_the_contraindications_section_reads_contraindicated():
    """'X [See Dosage and Administration (2.5), Contraindications (4.1)]' is the label
    filing X under its Contraindications section; reading it lower under-warns."""
    r = _read("7.1 Widget Inhibitors (WIs) [See Dosage and Administration (2.5), "
              "Contraindications (4.1) and Warnings and Precautions (5.2)].", "Widget Inhibitors")
    assert (r.level, r.rule_id) == ("contraindicated", "contraindicated.see_contraindications")
    r = _read("Drug A should not be used with widget inducers (See CONTRAINDICATIONS).",
              "widget inducers")
    assert r.level == "contraindicated"


def test_a_highlights_cross_reference_ends_a_sentence():
    """Highlights write '... is not recommended. (7.1) Next item ...': without a split
    at the '(7.1)' the next item inherited the previous item's advice."""
    text = ("Co-administration with gizmocid is not recommended. (7.1) Concomitant use "
            "with oral widgetants may prolong clotting time. (7.2) Gizmo may reduce efficacy.")
    assert _read(text, "widgetants").level == "not_read"
    text = "Avoid gizmocid ( 5.6 , 7.2 ) Widget blockers may cause bradycardia. ( 7.3 )"
    assert _read(text, "Widget blockers").level == "not_read"


def test_a_bullet_starts_a_sentence():
    text = ("• Class I widgetics: Contraindicated. ( 4 , 7.1 ) • Widget channel blockers "
            "(WCB): Initiate WCB with low dose and increase after ECG verification.")
    assert _read(text, "Widget channel blockers").level == "adjust_dose"


def test_not_generally_recommended_is_avoid():
    r = _read("Concomitant use of drug A and widgetacids is not generally recommended.",
              "widgetacids")
    assert (r.level, r.rule_id) == ("avoid", "avoid.not_recommended")


def test_dose_change_phrasings_read_adjust_dose():
    """Each of these was read monitor or not_read in the hand sample."""
    for text, name in (
            ("Monitor glucose, and the dose of the widgetylurea should be adjusted as "
             "necessary.", "widgetylurea"),
            ("Monitor blood glucose and adjust widgetin dosage regimen as needed.", "widgetin"),
            ("Use of widgetics with drug A may require lower doses than usually prescribed.",
             "widgetics"),
            ("When widgetates and drug A are coadministered, use the lowest possible doses "
             "for each drug.", "widgetates"),
            ("A dose decrease may be needed for these widgetants.", "widgetants"),
            ("Hypotension can be minimized by decreasing the dose of widgetics first.",
             "widgetics"),
            ("Adjustments in the dose of oral widgetants may be necessary.", "widgetants"),
            ("Patients on widgetant therapy may require downward adjustment of their "
             "widgetant dosage.", "widgetant therapy"),
            ("In patients taking widgetavir, the dose of drug A should not exceed 40 mg.",
             "widgetavir"),
            ("Initiate widgetics at a lower than usual dose and monitor carefully.", "widgetics"),
            ("An increased dose of widgetines may be required.", "widgetines"),
            ("Widgetines should each be titrated carefully.", "Widgetines"),
            ("Dosage of widgetin or other drugs may need adjustment.", "widgetin")):
        assert _read(text, name).level == "adjust_dose", text


def test_low_dose_as_a_product_descriptor_is_not_dose_advice():
    """'low-dose aspirin' names a product strength, not advice to lower a dose."""
    assert _read("Patients taking widgetants with low-dose aspirin bleed more.",
                 "widgetants").level == "not_read"


def test_caution_reads_monitor():
    for text, name in (
            ("Widgetines should be administered with caution to these patients.", "Widgetines"),
            ("Oral widgetants: Warfarin Use With Caution.", "widgetants"),
            ("Caution is warranted when widgetines are used.", "widgetines"),
            ("Use widgetines cautiously.", "widgetines")):
        assert _read(text, name).level == "monitor", text


def test_precautions_section_name_is_not_caution():
    assert _read("Widgetines (see PRECAUTIONS).", "Widgetines").level == "not_read"


def test_a_table_row_reads_its_intervention_cell():
    """PLR tables: '<row> Clinical Impact: ... . Intervention: ... contraindicated ...'.
    The mention sits in the row heading; the advice in the Intervention cell."""
    text = ("Table 3: Interactions with Drug A Widget Oxidase Inhibitors (WOIs) Clinical "
            "Impact: Concomitant use increases the risk of syndrome X. Intervention: Drug A "
            "is contraindicated in patients taking WOIs. Gizmocin Clinical Impact: Gizmocin "
            "raises levels. Intervention: Monitor levels.")
    r = _read(text, "Widget Oxidase Inhibitors")
    assert (r.level, r.scope) == ("contraindicated", "heading_block")
    assert r.scope_end <= text.index("Gizmocin Clinical")
    # The next row is read from its own Intervention, not the previous row's.
    assert _read(text, "Gizmocin").level == "monitor"


def test_a_table_row_examples_cell_reads_the_row_intervention():
    text = ("Widget Depressants Clinical Impact: Additive effects. Intervention: Limit "
            "dosages to the minimum required. Examples: Widgetines and other sedatives. "
            "Gizmos Clinical Impact: none known.")
    r = _read(text, "Widgetines")
    assert (r.level, r.scope) == ("adjust_dose", "heading_block")
    assert r.scope_start <= text.index("Widgetines") and r.scope_end >= text.index(
        "Widgetines") + len("Widgetines")


def test_a_row_without_an_intervention_cell_stays_not_read():
    text = ("Widget Inhibitors Clinical Impact: Levels rise. Gizmocin Clinical Impact: "
            "Levels fall. Intervention: Avoid gizmocin.")
    assert _read(text, "Widget Inhibitors").level == "not_read"
