"""MeSH preferred-concept entry terms as search-only moiety names (#231).

WHY. Australian and British clinicians type the old BAN/AAN names: lignocaine,
frusemide, adrenaline, amoxycillin. UNII files those only as common names (TYPE
cn, next to impurity and salt rows), so the official names of db/070 never reach
them. MeSH has them as entry terms.

WHICH TERMS. A MeSH record's PREFERRED concept is the drug itself; brands and
salts are separate concepts (Furosemide: preferred Furosemide, Frusemid,
Fursemide, Frusemide; Lasix and Furosemide Monosodium Salt elsewhere). So we take
the preferred concept's terms and link them by THAT concept's own UNII, never by
the other UNIIs on the record (those belong to the salts and isomers). Measured on
drugref_names, 2026-11-14: linking by every UNII on the record gave 1,179
descriptor names pointing at more than one moiety; linking by the preferred
concept's own UNII gave 0. Permuted terms ("Trinitrate, Glyceryl") are word-order
variants and are skipped: about half of all terms, and noise for a word-start search.

SEARCH ONLY. These names include misspellings (Acetominophen) and abbreviations
(APAP). That helps a search box, but they must never become SPL label-match keys,
so nothing on the SPL side reads them. A name that two moieties share is KEPT for
both (the owner's call on #231): a search shows a list and the clinician chooses.

PURE: reads files, returns records. The orchestrator (mesh_synonym_run.py) reads
the registry and writes.
"""
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from drugref.ingest.mesh import StrPath, iter_records, registry_keys
from drugref.ingest.spl_match import fold

# The record UI's tag for each of the two record files.
_UI_TAG = {"DescriptorRecord": "DescriptorUI",
           "SupplementalRecord": "SupplementalRecordUI"}


@dataclass(frozen=True)
class SynonymRecord:
    """One MeSH record's preferred concept: its UNIIs and its unpermuted terms,
    in file order."""
    record_ui: str
    uniis: frozenset[str]
    terms: tuple[str, ...]


@dataclass(frozen=True)
class SynonymLinks:
    """What `link` decided.

    * rows -- (moiety, name, record UI) to store, one per moiety and name
    * records_linked -- records whose UNII the registry holds
    * records_without_unii -- records whose preferred concept carries no UNII
      (most of MeSH: diseases and other non-chemicals, plus CAS-only or '0');
      they can link to nothing
    * records_unlinked -- records with a UNII no registry moiety claims
      (chemicals that are not registry drugs)
    * excluded -- names left out by the shipped exclusion list
    """
    rows: tuple[tuple[uuid.UUID, str, str], ...]
    records_linked: int
    records_without_unii: int
    records_unlinked: int
    excluded: tuple[str, ...]

    def __str__(self) -> str:
        return (f"{len(self.rows):,} synonyms from {self.records_linked:,} linked "
                f"records; {self.records_without_unii:,} records without a UNII, "
                f"{self.records_unlinked:,} with no registry moiety; "
                f"excluded: {sorted(self.excluded)}")


def synonym_records(path: StrPath, record_tag: str) -> Iterator[SynonymRecord]:
    """Stream each record's preferred concept from a desc or supp file.

    `record_tag` is "DescriptorRecord" or "SupplementalRecord". A record with no
    preferred concept (none in the 2026 release) yields nothing.

    Raises ValueError, once the file is read, if it held no `record_tag` at all
    (the wrong file: desc passed as --supp would otherwise drop every supplementary
    synonym from a run that still succeeds), or if a record lacks its UI.
    """
    ui_tag = _UI_TAG[record_tag]
    found = 0
    for el in iter_records(path, record_tag):
        found += 1
        record_ui = el.findtext(ui_tag)
        if not record_ui:
            raise ValueError(f"a {record_tag} in {path} has no {ui_tag}")
        for concept in el.findall("ConceptList/Concept"):
            if concept.get("PreferredConceptYN") != "Y":
                continue
            uniis, _cas = registry_keys(
                r.text for r in concept.findall("RegistryNumberList/RegistryNumber")
                if r.text)
            terms = tuple(t.findtext("String") or "" for t in concept.findall(
                "TermList/Term") if t.get("IsPermutedTermYN") != "Y")
            yield SynonymRecord(record_ui=record_ui, uniis=frozenset(uniis),
                                terms=tuple(t for t in terms if t))
    if not found:
        raise ValueError(f"no {record_tag} in {path}: is it the right MeSH file?")


def link(records: Iterable[SynonymRecord],
         unii_index: Mapping[str, Sequence[uuid.UUID]],
         excluded: Iterable[str]) -> SynonymLinks:
    """Pair each record's terms with every moiety claiming one of its UNIIs.

    Within one moiety, spellings that differ only in case keep the FIRST seen
    (callers pass descriptors before supplementary records). Spellings that differ
    in punctuation ("Ro 20-1724", "Ro 20 1724") are both kept: Cairn searches raw
    text. The exclusion list is compared folded, as the SPL side compares it.
    """
    excluded_keys = {fold(term) for term in excluded}
    rows: list[tuple[uuid.UUID, str, str]] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    dropped: list[str] = []
    linked = without_unii = unlinked = 0
    for record in records:
        if not record.uniis:
            without_unii += 1
            continue
        moieties = sorted({m for u in record.uniis for m in unii_index.get(u, ())})
        if not moieties:
            unlinked += 1
            continue
        linked += 1
        for term in record.terms:
            if fold(term) in excluded_keys:
                dropped.append(term)
                continue
            for moiety in moieties:
                if (moiety, term.casefold()) not in seen:
                    seen.add((moiety, term.casefold()))
                    rows.append((moiety, term, record.record_ui))
    return SynonymLinks(rows=tuple(rows), records_linked=linked,
                        records_without_unii=without_unii,
                        records_unlinked=unlinked, excluded=tuple(dropped))
