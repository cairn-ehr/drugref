"""Gap kind 21: a machine-read label statement about a drug's effect on one
laboratory analyte that no curator has answered (db/092, #294).

A VOCABULARY, NOT A WRITER (see gap_sources_registry.py). The gap_key format is
FROZEN: question_uuid is uuid5(gap_kind, gap_key), immortal and externally cited.
"""

# Correlated subqueries must name the outer row; the caller gives the view no alias,
# so qualify with the view name itself.
_G = "drugref.gap_unconfirmed_label_lab_effect"

LAB_EFFECT_GAP_SOURCES = {
    "unconfirmed_label_lab_effect": {
        "view": "gap_unconfirmed_label_lab_effect",
        # One question per (drug, analyte): the review unit a ruling answers (design
        # spec 6.5), however many roles or directions the labels were read in.
        "key_sql": "'LABEFFECT:' || moiety_uuid || ':' || analyte_code",
        # Strongest label section first (1 = boxed warning).
        "rank_sql": "section_rank",
        # Says "machine-read, not confirmed" and never that the drug is safe.
        "text_sql": (
            "'What does ' || (SELECT display_name FROM drugref.substance_moiety "
            "WHERE moiety_uuid = " + _G + ".moiety_uuid) || ' do to ' || "
            "(SELECT display_name FROM drugref.lab_analyte "
            "WHERE analyte_code = " + _G + ".analyte_code) || '? ' || label_count || "
            "' label(s) mention an effect, strongest in the ' || "
            "replace((SELECT section FROM drugref.lab_effect_section "
            "WHERE section_rank = " + _G + ".section_rank), '_', ' ') || "
            "' section (machine-read, not confirmed). Rule each role that holds "
            "(causes, more dangerous, monitoring, interferes) with its direction, or "
            "rule that it does not hold or cannot be established.'"),
    },
}
