"""Retailer and manufacturer nutrition-source collector.

This module discovers candidate product pages from configured search endpoints,
extracts structured nutrition data, and applies strict label/brand/weight gates.
It never silently falls back to generic nutrition.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, asdict
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "grocery-scraper/1.0 (nutrition research)"}

@dataclass(frozen=True)
class SourceSite:
    name: str
    kind: str
    domain: str
    search_url: str

SOURCE_SITES = (
    SourceSite("Tesco CZ", "retailer", "nakup.itesco.cz", "https://nakup.itesco.cz/groceries/cs-CZ/search?query={query}"),
    SourceSite("Billa CZ", "retailer", "billa.cz", "https://www.billa.cz/online-nakup"),
    SourceSite("Albert CZ", "retailer", "albert.cz", "https://www.albert.cz/shop/search?query={query}"),
    SourceSite("Madeta", "manufacturer", "madeta.cz", "https://www.madeta.cz/?s={query}"),
    SourceSite("Olma", "manufacturer", "olma.cz", "https://www.olma.cz/?s={query}"),
    SourceSite("Emco", "manufacturer", "emco.cz", "https://www.emco.cz/?s={query}"),
    SourceSite("Danone CZ", "manufacturer", "danone.cz", "https://www.danone.cz/?s={query}"),
    SourceSite("Nestlé CZ", "manufacturer", "nestle.cz", "https://www.nestle.cz/search?query={query}"),
    SourceSite("Orkla CZ", "manufacturer", "orkla.cz", "https://www.orkla.cz/?s={query}"),
    SourceSite("Hamé", "manufacturer", "hame.cz", "https://www.hame.cz/?s={query}"),
)

NUTRIENT_ALIASES = {
    "energy": ("kcal", "Calories"), "energie": ("kJ", "Energy"),
    "calories": ("kcal", "Calories"), "kcal": ("kcal", "Calories"),
    "protein": ("g", "Protein"), "bílkoviny": ("g", "Protein"),
    "carbohydrates": ("g", "Carbs"), "sacharidy": ("g", "Carbs"),
    "fat": ("g", "Fat"), "tuky": ("g", "Fat"),
    "sugars": ("g", "Sugars"), "cukry": ("g", "Sugars"),
    "salt": ("g", "Salt"), "sůl": ("g", "Salt"),
    "fiber": ("g", "Fiber"), "vláknina": ("g", "Fiber"),
}


def norm(value: str) -> str:
    value = html.unescape(str(value or "")).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def weight_grams(label: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|g)\s*$", label or "", re.I)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return value * 1000 if m.group(2).lower() == "kg" else value


def tokens(value: str) -> set[str]:
    ignored = {"produkt", "products", "product", "potravina", "g", "kg", "ml", "l"}
    return {x for x in norm(value).split() if len(x) >= 4 and x not in ignored}


def exact_identity(label: str, source_name: str, brand: str = "") -> bool:
    required = tokens(label)
    available = tokens(source_name)
    if not required or not available:
        return False
    shared = required & available
    # Generic labels are not accepted against a more specific source variant.
    distinctive = required - {"syr", "smetana", "jogurt", "mleko", "maslo", "napoj", "chleb", "tycinka"}
    source_distinctive = available - {"syr", "smetana", "jogurt", "mleko", "maslo", "napoj", "chleb", "tycinka"}
    if not distinctive and source_distinctive:
        return False
    if brand and distinctive <= {norm(brand)} and source_distinctive - distinctive:
        return False
    if distinctive and not distinctive.issubset(available):
        return False
    if brand and norm(brand) not in norm(source_name):
        return False
    return len(shared) >= max(1, min(len(required), 2))


def parse_values(soup: BeautifulSoup) -> dict:
    values = {}
    for row in soup.select("tr"):
        cells = [x.get_text(" ", strip=True) for x in row.select("th,td")]
        if len(cells) < 2:
            continue
        key = norm(cells[0])
        alias = next((v for k, v in NUTRIENT_ALIASES.items() if k in key), None)
        match = re.search(r"(\d+(?:[.,]\d+)?)", cells[1])
        if alias and match:
            values[alias[1]] = {"value": float(match.group(1).replace(",", ".")), "unit": alias[0]}
    return values


def parse_page(page_html: str, url: str) -> tuple[str, dict, str]:
    soup = BeautifulSoup(page_html, "html.parser")
    product = ""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        records = data if isinstance(data, list) else [data]
        for item in records:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if item_type == "Product" or (isinstance(item_type, list) and "Product" in item_type):
                product = item.get("name", "") or product
    if not product:
        headings = [x.get_text(" ", strip=True) for x in soup.select("h1,h2")]
        product = headings[0] if headings else (soup.title.get_text(" ", strip=True) if soup.title else "")
    return product, parse_values(soup), url


def discover(site: SourceSite, label: str, session: requests.Session | None = None) -> list[str]:
    client = session or requests.Session()
    url = site.search_url.format(query=quote(label))
    try:
        response = client.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    links = []
    for a in soup.select("a[href]"):
        absolute = urljoin(response.url, str(a["href"]))
        if urlparse(absolute).netloc.endswith(site.domain) and absolute not in links:
            links.append(absolute)
    return links[:20]


def research(label: str, brand: str = "", sites: tuple[SourceSite, ...] = SOURCE_SITES, session: requests.Session | None = None) -> list[dict]:
    client = session or requests.Session()
    results = []
    for site in sites:
        for url in discover(site, label, client):
            try:
                response = client.get(url, headers=HEADERS, timeout=20)
                response.raise_for_status()
            except requests.RequestException:
                continue
            source_name, values, final_url = parse_page(response.text, response.url)
            if values and exact_identity(label, source_name, brand):
                candidate = NutritionCandidate(site.name, site.kind, source_name, final_url, values)
                results.append(asdict(candidate))
    return results

@dataclass(frozen=True)
class NutritionCandidate:
    source: str
    source_kind: str
    source_product: str
    source_url: str
    values: dict
