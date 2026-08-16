"""Pure CIMA and BDPM parser tests for the population-source spike."""
import json

from drugref.ingest import regulatory_population as regulatory


def test_cima_page_and_product_keep_product_scope_and_ingredients():
    page = regulatory.parse_cima_page(json.dumps({
        "totalFilas": 2,
        "pagina": 1,
        "tamanioPagina": 200,
        "resultados": [{
            "nregistro": "51347",
            "nombre": "EXAMPLE 1 mg",
            "docs": [{"tipo": 1, "secc": True, "fecha": 123}],
            "vtm": {"id": 9, "nombre": "drug a + drug b"},
            "viasAdministracion": [{"nombre": "VIA ORAL"}],
            "dosis": "1 mg/2 mg",
        }],
    }).encode())
    assert (page.total_rows, page.page, len(page.products)) == (2, 1, 1)
    assert page.products[0].is_combination
    assert page.products[0].has_segmented_smpc

    product = regulatory.parse_cima_product({
        "nregistro": "51347",
        "nombre": "EXAMPLE 1 mg",
        "principiosActivos": [
            {"id": 1, "codigo": "1A", "nombre": "DRUG A", "cantidad": "1", "unidad": "mg"},
            {"id": 2, "codigo": "2A", "nombre": "DRUG B", "cantidad": "2", "unidad": "mg"},
        ],
        "viasAdministracion": [{"nombre": "VIA ORAL"}],
        "formaFarmaceutica": {"nombre": "COMPRIMIDO"},
        "dosis": "1 mg/2 mg",
    })
    assert [ingredient.name for ingredient in product.ingredients] == ["DRUG A", "DRUG B"]
    assert product.routes == ("VIA ORAL",)


def test_cima_sections_strip_html_but_keep_raw_source_and_subsections():
    payload = json.dumps([
        {"seccion": "4.6", "titulo": "Fertilidad, embarazo y lactancia", "contenido": "<div>&nbsp;</div>"},
        {"seccion": "4.6.1", "titulo": "Embarazo", "contenido": "<p>Use only if <b>needed</b>.</p>"},
        {"seccion": "4.6.2", "titulo": "Lactancia", "contenido": "<p>Milk statement.</p>"},
    ]).encode()

    sections = regulatory.parse_cima_sections(payload)

    assert [section.code for section in sections] == ["4.6", "4.6.1", "4.6.2"]
    assert sections[1].text == "Use only if needed."
    assert sections[1].raw_html.startswith("<p>")
    assert sections[2].population_context == "lactation"


def test_cima_documented_no_sections_error_is_an_empty_counted_response():
    payload = b'{"error":"No existen secciones para el medicamento indicado"}'
    assert regulatory.parse_cima_sections(payload) == ()


def test_bdpm_bulk_parsers_decode_cp1252_and_preserve_composition_scope(tmp_path):
    specialties = tmp_path / "CIS.txt"
    specialties.write_bytes(
        "123\tMÉDICAMENT, comprimé\tcomprimé\torale\tAutorisation active\n".encode("cp1252"))
    compositions = tmp_path / "COMPO.txt"
    compositions.write_bytes(
        "123\tcomprimé\t00001\tMÉTFORMINE\t500 mg\tun comprimé\tSA\t1\n".encode("cp1252"))

    specialty = list(regulatory.iter_bdpm_specialties(specialties))[0]
    ingredient = list(regulatory.iter_bdpm_compositions(compositions))[0]

    assert specialty.cis == "123"
    assert specialty.name == "MÉDICAMENT, comprimé"
    assert ingredient.name == "MÉTFORMINE"
    assert ingredient.dose == "500 mg"


def test_bdpm_rcp_extracts_only_sections_4_3_and_4_6_and_update_date():
    source = b"""<html><body>
      <p>ANSM - Mis &agrave; jour le : 09/01/2025</p>
      <p><a id="RcpContreindications">4.3. Contre-indications</a></p>
      <p>Contraindication text.</p>
      <p><a id="RcpMisesEnGarde">4.4. Mises en garde</a></p>
      <p>Warning text.</p>
      <p><a id="RcpFertGrossAllait">4.6. Fertilit&eacute;, grossesse et allaitement</a></p>
      <p>Pregnancy and milk text.</p>
      <p><a id="RcpConduite">4.7. Conduite</a></p>
      <p>Driving text.</p>
    </body></html>"""

    rcp = regulatory.parse_bdpm_rcp(source)

    assert rcp.revised == "2025-01-09"
    assert rcp.section_4_3 == "4.3. Contre-indications Contraindication text."
    assert rcp.section_4_6 == (
        "4.6. Fertilité, grossesse et allaitement Pregnancy and milk text.")
    assert "Warning" not in rcp.section_4_3
    assert "Driving" not in rcp.section_4_6


def test_even_sample_is_deterministic_and_includes_both_ends():
    values = list(range(20))
    assert regulatory.even_sample(values, 5) == [0, 5, 10, 14, 19]
    assert regulatory.even_sample(values, 50) == values


def test_normalized_names_are_measurement_keys_not_source_rewrites():
    assert regulatory.normalized_name("  Ácido   Fólico  ") == "acido folico"
