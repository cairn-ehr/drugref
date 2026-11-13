"""The pure half of #231's BAN/AAN names: MeSH preferred-concept entry terms.

A MeSH record keeps a drug's synonyms in its PREFERRED concept (Furosemide:
Frusemid, Fursemide, Frusemide) and puts brands and salts in other concepts
(Lasix, Furosemide Monosodium Salt). So the preferred concept's terms are a clean
synonym set, and its own UNII says which moiety they name. No database here.
"""
import uuid

from drugref.ingest import mesh_synonyms

# A trimmed copy of the real D005665 record (desc2026): the preferred concept with
# one permuted term added, a brand concept and a salt concept with its own CAS.
DESC = """<DescriptorRecordSet>
<DescriptorRecord DescriptorClass="1">
 <DescriptorUI>D005665</DescriptorUI>
 <DescriptorName><String>Furosemide</String></DescriptorName>
 <ConceptList>
  <Concept PreferredConceptYN="Y">
   <ConceptUI>M0008900</ConceptUI>
   <RegistryNumberList><RegistryNumber>7LXU5N7ZO5</RegistryNumber></RegistryNumberList>
   <TermList>
    <Term IsPermutedTermYN="N"><String>Furosemide</String></Term>
    <Term IsPermutedTermYN="N"><String>Frusemide</String></Term>
    <Term IsPermutedTermYN="Y"><String>Frusemide, Permuted</String></Term>
   </TermList>
  </Concept>
  <Concept PreferredConceptYN="N">
   <ConceptUI>M0008901</ConceptUI>
   <RegistryNumberList><RegistryNumber>0</RegistryNumber></RegistryNumberList>
   <TermList><Term IsPermutedTermYN="N"><String>Lasix</String></Term></TermList>
  </Concept>
  <Concept PreferredConceptYN="N">
   <ConceptUI>M0330297</ConceptUI>
   <RegistryNumberList><RegistryNumber>SALTUNII01</RegistryNumber></RegistryNumberList>
   <TermList>
    <Term IsPermutedTermYN="N"><String>Furosemide Monosodium Salt</String></Term>
   </TermList>
  </Concept>
 </ConceptList>
</DescriptorRecord>
<DescriptorRecord DescriptorClass="1">
 <DescriptorUI>D000001</DescriptorUI>
 <DescriptorName><String>Calcimycin</String></DescriptorName>
 <ConceptList>
  <Concept PreferredConceptYN="Y">
   <ConceptUI>M0000001</ConceptUI>
   <RegistryNumberList><RegistryNumber>52665-69-7</RegistryNumber></RegistryNumberList>
   <TermList><Term IsPermutedTermYN="N"><String>Calcimycin</String></Term></TermList>
  </Concept>
 </ConceptList>
</DescriptorRecord>
</DescriptorRecordSet>
"""

SUPP = """<SupplementalRecordSet>
<SupplementalRecord SCRClass="1">
 <SupplementalRecordUI>C000002</SupplementalRecordUI>
 <SupplementalRecordName><String>temocillin</String></SupplementalRecordName>
 <ConceptList>
  <Concept PreferredConceptYN="Y">
   <ConceptUI>M0000002</ConceptUI>
   <RegistryNumberList><RegistryNumber>03QB156W6I</RegistryNumber></RegistryNumberList>
   <TermList><Term IsPermutedTermYN="N"><String>Temocilline</String></Term></TermList>
  </Concept>
 </ConceptList>
</SupplementalRecord>
</SupplementalRecordSet>
"""

FURO, FURO2, TEMO = (uuid.UUID(int=0xB000 + i) for i in range(3))


def _records(tmp_path, name, text, tag):
    path = tmp_path / name
    path.write_text(text)
    return list(mesh_synonyms.synonym_records(path, tag))


def test_only_the_preferred_concepts_unpermuted_terms_and_own_unii(tmp_path):
    """Lasix (brand) and the salt's name and UNII belong to other concepts and
    stay out; so does the permuted term (a word-order variant)."""
    furo, calci = _records(tmp_path, "desc.xml", DESC, "DescriptorRecord")
    assert furo == mesh_synonyms.SynonymRecord(
        record_ui="D005665", uniis=frozenset({"7LXU5N7ZO5"}),
        terms=("Furosemide", "Frusemide"))
    # A CAS-only record carries no UNII: it can link to nothing.
    assert calci.uniis == frozenset()


def test_a_supplementary_record_is_read_by_its_own_ui_tag(tmp_path):
    (temo,) = _records(tmp_path, "supp.xml", SUPP, "SupplementalRecord")
    assert (temo.record_ui, temo.terms) == ("C000002", ("Temocilline",))


def test_link_keeps_every_moiety_a_unii_points_to(tmp_path):
    """The owner's rule for search names (#231): a name is never dropped for
    pointing at two moieties -- the clinician picks from the list."""
    records = _records(tmp_path, "desc.xml", DESC, "DescriptorRecord")
    links = mesh_synonyms.link(records, {"7LXU5N7ZO5": [FURO, FURO2]}, excluded=())
    assert sorted(links.rows) == sorted([
        (FURO, "Furosemide", "D005665"), (FURO, "Frusemide", "D005665"),
        (FURO2, "Furosemide", "D005665"), (FURO2, "Frusemide", "D005665")])
    assert (links.records_linked, links.records_without_unii) == (1, 1)


def test_link_counts_a_record_whose_unii_the_registry_lacks(tmp_path):
    records = _records(tmp_path, "supp.xml", SUPP, "SupplementalRecord")
    links = mesh_synonyms.link(records, {}, excluded=())
    assert links.rows == () and links.records_unlinked == 1


def test_link_keeps_the_first_of_two_spellings_differing_only_in_case():
    """Desc is read before supp, so a descriptor's spelling wins."""
    records = [mesh_synonyms.SynonymRecord("D1", frozenset({"U1"}), ("Frusemide",)),
               mesh_synonyms.SynonymRecord("C1", frozenset({"U1"}), ("FRUSEMIDE",))]
    links = mesh_synonyms.link(records, {"U1": [FURO]}, excluded=())
    assert links.rows == ((FURO, "Frusemide", "D1"),)


def test_link_applies_the_exclusion_list_by_folded_name():
    """The owner's call (a) on #231: the shipped exclusions apply to search too,
    so 'Glucose' must not come back for dextrose through MeSH."""
    records = [mesh_synonyms.SynonymRecord("D1", frozenset({"U1"}),
                                           ("Dextrose", "Glucose"))]
    links = mesh_synonyms.link(records, {"U1": [FURO]}, excluded=("glucose",))
    assert links.rows == ((FURO, "Dextrose", "D1"),)
    assert links.excluded == ("Glucose",)


def test_the_summary_line_says_what_was_dropped():
    links = mesh_synonyms.SynonymLinks(rows=((FURO, "X", "D1"),), records_linked=1,
                                       records_without_unii=2, records_unlinked=3,
                                       excluded=("Glucose",))
    assert str(links) == ("1 synonyms from 1 linked records; 2 records without a "
                          "UNII, 3 with no registry moiety; excluded: ['Glucose']")
