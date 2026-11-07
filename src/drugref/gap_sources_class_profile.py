"""Gap kind 19: a class property that is not confirmed for one member (db/061).

A VOCABULARY, NOT A WRITER (see gap_sources_registry.py). The gap_key format is
FROZEN: question_uuid is uuid5(gap_kind, gap_key), immortal and externally cited.
"""

# Correlated subqueries must name the outer row; the caller gives the view no alias,
# so qualify with the view name itself.
_V = "drugref.gap_unconfirmed_class_property"

CLASS_PROFILE_GAP_SOURCES = {
    "unconfirmed_class_property": {
        "view": "gap_unconfirmed_class_property",
        # One question per (member, property): a fact about the DRUG, not the class.
        "key_sql": (
            "'CLASSPROP:' || moiety_uuid || ':' || relationship || ':' || "
            "object_kind || ':' || object_uuid"),
        # Never suggests the drug is safe: "not confirmed" is absence of a record.
        "text_sql": (
            "'Does ' || (SELECT display_name FROM drugref.substance_moiety "
            "WHERE moiety_uuid = " + _V + ".moiety_uuid) || ' have the property ' || "
            "relationship || ' ' || COALESCE("
            "(SELECT name FROM drugref.condition "
            "WHERE condition_uuid = " + _V + ".object_uuid), "
            "(SELECT display_name FROM drugref.substance_moiety "
            "WHERE moiety_uuid = " + _V + ".object_uuid), "
            "(SELECT class_name FROM drugref.substance_class "
            "WHERE class_uuid = " + _V + ".object_uuid)) || "
            "'? It is not confirmed for this drug, though it is common in ' || "
            "(SELECT string_agg(class_name, ', ' "
            "ORDER BY class_name) FROM drugref.substance_class "
            "WHERE class_uuid = ANY(" + _V + ".raising_classes)) || "
            "'. Answer: holds, does not hold, or cannot be established. "
            # True of every answer: no alert-path view reads the rulings (#204).
            "Answering does not change prescribing alerts.'"),
    },
}
