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


def is_matchable(source: str, concept_type: str) -> bool:
    """False only for MED-RT's PK axis (never matched); FDA-CYP is also 'PK'
    but is matched, hence the source check."""
    return not (source == "MED-RT" and concept_type == "PK")


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
