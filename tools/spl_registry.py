"""Load drugref's own vocabularies and holdings for the 5c.3 measurement.

**Throwaway spike code.** Split out of ``tools/spl_ddi_spike.py`` under CLAUDE.md
rule 4: the runner had grown past the ~500-line guideline, and this is the one
piece of it that talks to a database.
"""
from __future__ import annotations

import pathlib
from collections.abc import Mapping
from dataclasses import dataclass

from tools.spl_entity_match import (
    Entry,
    Vocabulary,
    build_vocabulary,
    fold,
    name_variants,
)


@dataclass(frozen=True)
class Registry:
    """drugref's own vocabularies and holdings, loaded once for the measurement."""

    vocabulary: Vocabulary
    moiety_uuid_by_name: Mapping[str, str]
    unii_to_moiety: Mapping[str, str]
    held_exact: set[tuple[str, str]]
    held_candidate: set[tuple[str, str]]
    class_members: Mapping[str, int]
    class_count: int
    excluded_common_words: tuple[str, ...] = ()
    suppress_terms: tuple[str, ...] = ()


def load_suppress_terms(path: pathlib.Path) -> tuple[str, ...]:
    """Read the measured suppression list, ignoring comments and blanks."""
    return tuple(
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def load_registry(
    dsn: str,
    *,
    common_words: frozenset[str] | None = None,
    suppress_terms: tuple[str, ...] = (),
) -> Registry:
    """Read the registry, the class vocabulary and the pairs drugref holds.

    Moiety names follow FDA-CYP's precedent exactly -- ``display_name``, exact
    and case-insensitive -- so this measurement's resolution behaviour is the
    same one the shipped code already uses, rather than a more generous variant
    that would flatter the yield.

    ``common_words`` drops SINGLE-TOKEN moiety names that are also ordinary
    English -- 'lead', 'iron', 'alcohol'. It exists so the pair count can be
    reported as a RANGE between two reproducible endpoints rather than as one
    number resting on somebody's judgement about which names are real. Both ends
    are wrong in a known direction: keeping every name over-counts (a label
    saying 'lead to hypotension' scores the metal), and dropping all 463 of them
    under-counts (amphetamine and adenosine are perfectly good drugs). The truth
    is between, and saying so is more honest than picking one.
    """
    import psycopg

    entries: list[Entry] = []
    moiety_uuid_by_name: dict[str, str] = {}
    unii_to_moiety: dict[str, str] = {}
    held_exact: set[tuple[str, str]] = set()
    held_candidate: set[tuple[str, str]] = set()
    class_members: dict[str, int] = {}
    excluded_names: list[str] = []
    entries.extend(
        Entry(kind="suppress", key=term, display=term) for term in suppress_terms
    )

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moiety_uuid, display_name FROM drugref.substance_moiety"
            )
            for moiety_uuid, display_name in cur:
                moiety_uuid_by_name[display_name] = str(moiety_uuid)
                if common_words is not None:
                    folded = fold(display_name)
                    if " " not in folded and folded in common_words:
                        excluded_names.append(display_name)
                        continue
                entries.append(
                    Entry(kind="moiety", key=display_name, display=display_name)
                )

            # Member counts come along because issue #102's real question is not
            # "does drugref have a class for this band" but "does that class
            # contain anybody" -- 'CYP1A2 strong inhibitor [FDA-CYP]' exists and
            # is EMPTY, and a design that checked only for existence would not
            # notice.
            cur.execute(
                "SELECT c.class_uuid, c.class_name, c.source, c.concept_type, "
                "       count(m.moiety_uuid) AS members "
                "  FROM drugref.substance_class c "
                "  LEFT JOIN drugref.class_membership m USING (class_uuid) "
                " GROUP BY c.class_uuid, c.class_name, c.source, c.concept_type"
            )
            classes = cur.fetchall()
            for _class_uuid, class_name, source, concept_type, members in classes:
                # MED-RT's PK axis is broken out from the rest of MED-RT because
                # that axis -- not the therapeutic or chemical ones -- is what
                # issue #102 is about.
                axis = f"{source}-PK" if source == "MED-RT" and concept_type == "PK" \
                    else source
                class_members[class_name] = members
                for variant in name_variants(class_name):
                    entries.append(
                        Entry(
                            kind="class", key=variant,
                            display=class_name, source=axis,
                        )
                    )

            cur.execute(
                "SELECT value, moiety_uuid FROM drugref.identity_claim "
                "WHERE scheme = 'UNII' AND superseded_by IS NULL"
            )
            for value, moiety_uuid in cur:
                unii_to_moiety[value] = str(moiety_uuid)

            cur.execute("SELECT moiety_lo, moiety_hi FROM drugref.exact_ddi_pair")
            for lo, hi in cur:
                held_exact.add((str(lo), str(hi)))

            cur.execute(
                "SELECT subject_moiety, partner_moiety FROM drugref.ddi_candidate_pair"
            )
            for subject, partner in cur:
                a, b = str(subject), str(partner)
                held_candidate.add((a, b) if a < b else (b, a))

    return Registry(
        vocabulary=build_vocabulary(entries),
        moiety_uuid_by_name=moiety_uuid_by_name,
        unii_to_moiety=unii_to_moiety,
        held_exact=held_exact,
        held_candidate=held_candidate,
        class_members=class_members,
        class_count=len(classes),
        excluded_common_words=tuple(sorted(excluded_names)),
        suppress_terms=suppress_terms,
    )


