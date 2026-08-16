"""Pure CIMA/BDPM parsers for the pregnancy and lactation source spike.

These functions retain product and section scope. They do not translate regulatory
language into Drugref recommendations or moiety-level contraindications.
"""
from __future__ import annotations

import csv
import html
import json
import pathlib
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class CimaListingProduct:
    registration: str
    name: str
    vtm_id: str | None
    vtm_name: str
    routes: tuple[str, ...]
    dose: str
    document_revision: int | None
    has_segmented_smpc: bool
    is_combination: bool


@dataclass(frozen=True)
class CimaPage:
    total_rows: int
    page: int
    page_size: int
    products: tuple[CimaListingProduct, ...]


@dataclass(frozen=True)
class CimaIngredient:
    source_id: str
    source_code: str
    name: str
    amount: str
    unit: str


@dataclass(frozen=True)
class CimaProduct:
    registration: str
    name: str
    ingredients: tuple[CimaIngredient, ...]
    routes: tuple[str, ...]
    formulation: str
    dose: str


@dataclass(frozen=True)
class CimaSection:
    code: str
    title: str
    raw_html: str
    text: str
    population_context: str


@dataclass(frozen=True)
class BdpmSpecialty:
    cis: str
    name: str
    formulation: str
    routes: str
    status: str


@dataclass(frozen=True)
class BdpmComposition:
    cis: str
    formulation: str
    ingredient_code: str
    name: str
    dose: str
    dose_reference: str
    ingredient_kind: str
    order: str


@dataclass(frozen=True)
class BdpmRcp:
    revised: str | None
    section_4_3: str
    section_4_6: str


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean_text(parts: list[str]) -> str:
    value = " ".join(" ".join(parts).split())
    return re.sub(r"\s+([.,;:!?])", r"\1", value)


def strip_html(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    return _clean_text(parser.parts)


def _json(payload: bytes | str) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload)


def parse_cima_page(payload: bytes | str) -> CimaPage:
    data = _json(payload)
    products = []
    for row in data.get("resultados", []):
        docs = row.get("docs") or []
        smpc = next((doc for doc in docs if doc.get("tipo") == 1), None)
        vtm = row.get("vtm") or {}
        vtm_name = str(vtm.get("nombre") or "")
        products.append(CimaListingProduct(
            registration=str(row.get("nregistro") or ""),
            name=str(row.get("nombre") or ""),
            vtm_id=str(vtm["id"]) if vtm.get("id") is not None else None,
            vtm_name=vtm_name,
            routes=tuple(
                str(route.get("nombre") or "")
                for route in row.get("viasAdministracion") or []),
            dose=str(row.get("dosis") or ""),
            document_revision=smpc.get("fecha") if smpc else None,
            has_segmented_smpc=bool(smpc and smpc.get("secc")),
            is_combination="+" in vtm_name,
        ))
    return CimaPage(
        total_rows=int(data.get("totalFilas") or 0),
        page=int(data.get("pagina") or 0),
        page_size=int(data.get("tamanioPagina") or 0),
        products=tuple(products),
    )


def parse_cima_product(data: dict[str, Any]) -> CimaProduct:
    ingredients = tuple(CimaIngredient(
        source_id=str(row.get("id") or ""),
        source_code=str(row.get("codigo") or ""),
        name=str(row.get("nombre") or ""),
        amount=str(row.get("cantidad") or ""),
        unit=str(row.get("unidad") or ""),
    ) for row in data.get("principiosActivos") or [])
    formulation = data.get("formaFarmaceutica") or {}
    return CimaProduct(
        registration=str(data.get("nregistro") or ""),
        name=str(data.get("nombre") or ""),
        ingredients=ingredients,
        routes=tuple(
            str(row.get("nombre") or "")
            for row in data.get("viasAdministracion") or []),
        formulation=str(formulation.get("nombre") or ""),
        dose=str(data.get("dosis") or ""),
    )


def _cima_context(code: str, title: str) -> str:
    folded = normalized_name(title)
    if code.startswith("4.6.1") or "embarazo" in folded:
        return "pregnancy"
    if code.startswith("4.6.2") or "lactancia" in folded:
        return "lactation"
    return "mixed"


def parse_cima_sections(payload: bytes | str) -> tuple[CimaSection, ...]:
    data = _json(payload)
    if isinstance(data, dict) and "error" in data:
        return ()
    if not isinstance(data, list):
        raise ValueError("CIMA section response is neither a list nor an error object")
    sections = []
    for row in data:
        code = str(row.get("seccion") or "")
        title = str(row.get("titulo") or "")
        raw_html = str(row.get("contenido") or "")
        sections.append(CimaSection(
            code=code,
            title=title,
            raw_html=raw_html,
            text=strip_html(raw_html),
            population_context=_cima_context(code, title),
        ))
    return tuple(sections)


def _rows(path: str | pathlib.Path):
    with open(path, encoding="cp1252", newline="") as stream:
        yield from csv.reader(stream, delimiter="\t")


def iter_bdpm_specialties(path: str | pathlib.Path):
    for row in _rows(path):
        if len(row) < 5:
            continue
        yield BdpmSpecialty(*row[:5])


def iter_bdpm_compositions(path: str | pathlib.Path):
    for row in _rows(path):
        if len(row) < 8:
            continue
        yield BdpmComposition(*row[:8])


class _RcpParser(HTMLParser):
    STARTS = {
        "RcpContreindications": "4.3",
        "RcpFertGrossAllait": "4.6",
    }
    STOPS = {
        "RcpMisesEnGarde": "4.3",
        "RcpConduite": "4.6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.all_text: list[str] = []
        self.sections: dict[str, list[str]] = {"4.3": [], "4.6": []}
        self.current: str | None = None

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        element_id = dict(attrs).get("id")
        if element_id in self.STARTS:
            self.current = self.STARTS[element_id]
        elif element_id in self.STOPS and self.current == self.STOPS[element_id]:
            self.current = None

    def handle_data(self, data: str) -> None:
        self.all_text.append(data)
        if self.current:
            self.sections[self.current].append(data)


def parse_bdpm_rcp(payload: bytes | str) -> BdpmRcp:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    parser = _RcpParser()
    parser.feed(payload)
    parser.close()
    all_text = _clean_text(parser.all_text)
    date_match = re.search(r"Mis à jour le\s*:\s*(\d{2})/(\d{2})/(\d{4})", all_text)
    revised = None
    if date_match:
        day, month, year = date_match.groups()
        revised = f"{year}-{month}-{day}"
    return BdpmRcp(
        revised=revised,
        section_4_3=_clean_text(parser.sections["4.3"]),
        section_4_6=_clean_text(parser.sections["4.6"]),
    )


def even_sample(values: list[T], size: int) -> list[T]:
    """Return a deterministic sample spanning both ends of a sorted population."""

    if size <= 0:
        return []
    if len(values) <= size:
        return list(values)
    if size == 1:
        return [values[0]]
    indexes = [round(i * (len(values) - 1) / (size - 1)) for i in range(size)]
    return [values[index] for index in indexes]


def normalized_name(value: str) -> str:
    """A lossy measurement key; never suitable for automatic identity admission."""

    decomposed = unicodedata.normalize("NFKD", html.unescape(value))
    unaccented = "".join(char for char in decomposed
                         if not unicodedata.combining(char))
    words = re.sub(r"[^a-z0-9]+", " ", unaccented.casefold())
    return " ".join(words.split())
