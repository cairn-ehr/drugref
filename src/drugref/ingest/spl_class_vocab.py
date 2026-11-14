"""Pure helpers that turn a stored class name into the keys a label may use.

drugref stores a class as 'CYP1A2 strong inhibitor [FDA-CYP]' or 'Diuretics';
a label says 'strong CYP1A2 inhibitors' or 'diuretic'. This module derives the
spellings worth offering to the matcher, each tagged with HOW it was derived
(`FORM_*`) so a later reader can tell a stored-name hit from a rewritten one.
No DB, no I/O.
"""
from __future__ import annotations

import re

from drugref.ingest.spl_match import (  # noqa: F401  (re-exported)
    FORM_LABEL_ORDER, FORM_NUMBER, FORM_STORED, MATCH_FORMS, fold)

#: MED-RT and drugref tag a class with its axis ('... Inhibitors [MoA]'). That
#: tag never appears on a drug label, so it is stripped before matching.
_AXIS_TAG = re.compile(r"\s*\[[A-Za-z0-9-]+\]\s*$")

#: '<enzyme> <band> <role>' -- the only FDA-CYP shape labels reorder
#: (substrates and unbanded transporters have no 'strong X inhibitor' phrasing).
_CYP_BANDED = re.compile(r"^(\S+) (strong|moderate|weak) (inhibitor|inducer)$")


def strip_axis_tag(class_name: str) -> str:
    """'Calcium Channel Blocker [EPC]' -> 'Calcium Channel Blocker'."""
    return _AXIS_TAG.sub("", class_name).strip()


def number_variants(base: str) -> tuple[str, ...]:
    """The OTHER grammatical number only: 'Diuretics' -> ('Diuretic',),
    'Diuretic' -> ('Diuretics',). Deliberately mechanical (ends in 's' -> strip
    it, else add one); irregular plurals are not this function's job."""
    return (base[:-1] if base.endswith("s") else base + "s",)


def fda_cyp_label_orders(base: str) -> tuple[str, ...]:
    """Label word order for a banded FDA-CYP inhibitor/inducer, else `()`:
    'CYP1A2 strong inhibitor' -> ('strong CYP1A2 inhibitor', '... inhibitors')."""
    m = _CYP_BANDED.match(base)
    if not m:
        return ()
    enzyme, band, role = m.groups()
    return (f"{band} {enzyme} {role}", f"{band} {enzyme} {role}s")


#: Individually named classes never matched, as (source, concept_type, class_name).
#: Each entry carries its measured reason, so an addition is a visible, reviewed
#: edit and not a drift.
#: * 'Hormones' (MeSH PA), R19/P3: the twin of the excluded APC 'Hormone' (the same
#:   4,486 spans came back through it on run 11); it read 4/10 as a class mention,
#:   the false ones physiology ('antidiuretic hormone', 'plasma hormone levels') and
#:   every true one 'thyroid hormone', a narrower thing than the 138-member class.
#: * 'Electrolyte [EPC]', #214: an EMPTY class (its 110 run-17 statements reach no
#:   drug) whose 2,349 spans are physiology ('electrolyte depletion', 'fluid and
#:   electrolyte balance', 'monitor renal function and electrolytes').
#: * 'Ophthalmic Solutions' (MeSH PA), #214: a dosage form. Its 494 run-17 spans are
#:   product names ('timolol maleate ophthalmic solution'). Not generalised to MeSH's
#:   D26 tree: that branch also holds 'Contraceptives, Oral, Combined', a real class.
EXCLUDED_CLASSES: frozenset[tuple[str, str, str]] = frozenset({
    ("MeSH", "PA", "Hormones"),
    ("MED-RT", "EPC", "Electrolyte [EPC]"),
    ("MeSH", "PA", "Ophthalmic Solutions"),
})

#: The MED-RT PE (physiologic effect) classes still matched although the axis is not
#: (#214). 'Antacid [PE]': its spans all carry MeSH 'Antacids' too, but 3 of its 6
#: members (algeldrate, aluminum carbonate, aluminum hydroxide sulfate) are not
#: MeSH members, so excluding it would cost them their antacid advice.
KEPT_PE_CLASSES: frozenset[str] = frozenset({"Antacid [PE]"})


def is_matchable(source: str, concept_type: str, class_name: str | None = None) -> bool:
    """False for MED-RT's PK, APC and PE axes (bar `KEPT_PE_CLASSES`), allergy
    groups ('AG'), and for a class in `EXCLUDED_CLASSES`.

    FDA-CYP is also 'PK' but is matched, hence the source check. PK is excluded
    because its names are ordinary English (#155). APC is excluded for the same
    reason, measured on the 2026-08 release: all 44 APC classes are empty by
    construction (no drug is filed under them), their names are generic words
    ('Enzyme' 15,587 occurrences, 'Hormone' 4,486) and each duplicates a MeSH
    pharmacologic-action name that IS matched. PE is excluded because its names
    are effects ('Decreased Blood Pressure', 'Neuromuscular Blockade'): on run 17
    its 1,888 spans not shared with another axis were all effect phrases, 149 of
    them read contraindicated (#214). `class_name` is optional so an axis-level
    caller can still ask about the axis alone (PE then reads False).
    """
    # Allergy groups are drugref's own structural seeds, not a vocabulary that label
    # text is written in, so label text is never matched against them.
    if concept_type == "AG":
        return False
    if (source, concept_type, class_name) in EXCLUDED_CLASSES:
        return False
    if source == "MED-RT" and concept_type == "PE":
        return class_name in KEPT_PE_CLASSES
    return not (source == "MED-RT" and concept_type in ("PK", "APC"))


def class_match_keys(class_name: str, source: str) -> tuple[tuple[str, str], ...]:
    """`(key, form)` pairs: stored form first, then number variants, then (FDA-CYP
    only) label orders. Deduplicated by `fold`, so the first derivation wins."""
    base = strip_axis_tag(class_name)
    if not base:  # a bare '[EPC]' would otherwise yield the one-letter key 's'
        return ()
    candidates = [(base, FORM_STORED)]
    candidates += [(v, FORM_NUMBER) for v in number_variants(base)]
    if source == "FDA-CYP":
        candidates += [(v, FORM_LABEL_ORDER) for v in fda_cyp_label_orders(base)]
    seen: dict[str, tuple[str, str]] = {}
    for key, form in candidates:
        if key and fold(key) not in seen:
            seen[fold(key)] = (key, form)
    return tuple(seen.values())
