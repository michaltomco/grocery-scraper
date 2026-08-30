"""Exact nutrition source harness.

The harness deliberately separates source retrieval from identity validation.
It accepts retailer/manufacturer URLs supplied by discovery code and checks
Open Food Facts by EAN first. No candidate is accepted without an exact product
identity signal and a parsed nutrition table.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from typing import Iterable

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "grocery-scraper/1.0 (exact nutrition source harness)"}
OFF_PRODUCT = "https://world.openfoodfacts.org/api/v2/product/{ean}.json"
NUTRIENT_FIELDS = {
    "energy-kcal_100g": ("kcal", "Calories"), "energy-kj_100g": ("kJ", "Energy"),
    "proteins_100g": ("g", "Protein"), "carbohydrates_100g": ("g", "Carbs"),
    "fat_100g": ("g", "Fat"), "saturated-fat_100g": ("g", "Saturated fat"),
    "fiber_100g": ("g", "Fiber"), "sugars_100g": ("g", "Sugars"),
    "salt_100g": ("g", "Salt"), "sodium_100g": ("g", "Sodium"),
}

@dataclass(frozen=True)
class NutritionCandidate:
    source: str
    source_product: str
    source_url: str
    values: dict
    match_method: str


def norm(value: str) -> str:
    value = html.unescape(str(value or "")).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def weight_grams(label: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|g)\s*$", label or "", re.I)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return value * 1000 if m.group(2).lower() == "kg" else value


def meaningful_tokens(label: str) -> set[str]:
    ignored = {"product", "produkt", "food", "potravina", "g", "kg", "ml", "l"}
    return {x for x in norm(label).split() if len(x) >= 4 and x not in ignored}


def identity_matches(label: str, source_product: str, brand: str = "") -> bool:
    label_tokens = meaningful_tokens(label)
    source_tokens = meaningful_tokens(source_product)
    if not label_tokens or not source_tokens:
        return False
    shared = {x for x in label_tokens if x in source_tokens}
    # Require all distinctive tokens for short labels; allow one missing token
    # only for longer retailer labels with an explicit brand match.
    required = len(label_tokens) if len(label_tokens) <= 3 else len(label_tokens) - 1
    if len(shared) < required:
        return False
    return not brand or norm(brand) in norm(source_product)


def off_values(product: dict) -> dict:
    nutriments = product.get("nutriments", {})
    values = {}
    for key, (unit, label) in NUTRIENT_FIELDS.items():
        value = nutriments.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            values[label] = {"value": round(float(value), 2), "unit": unit}
    return values


def lookup_ean(ean: str, label: str, session: requests.Session | None = None) -> NutritionCandidate | None:
    if not re.fullmatch(r"\d{8}|\d{12,14}", str(ean or "")):
        return None
    client = session or requests.Session()
    url = OFF_PRODUCT.format(ean=ean)
    try:
        response = client.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    product = payload.get("product", {})
    name = product.get("product_name") or product.get("product_name_en") or ""
    values = off_values(product)
    if payload.get("status") != 1 or not values or not identity_matches(label, name):
        return None
    return NutritionCandidate("Open Food Facts", name, url, values, "ean")


def parse_nutrition_table(page_html: str) -> tuple[str, dict]:
    soup = BeautifulSoup(page_html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    headings = [x.get_text(" ", strip=True) for x in soup.select("h1,h2,h3")]
    source_product = headings[0] if headings else title
    values = {}
    aliases = {
        "calories": ("kcal", "Calories"), "energie": ("kJ", "Energy"), "energy": ("kJ", "Energy"),
        "bílkoviny": ("g", "Protein"), "protein": ("g", "Protein"), "sacharidy": ("g", "Carbs"),
        "carbohydrates": ("g", "Carbs"), "tuky": ("g", "Fat"), "fat": ("g", "Fat"),
        "cukry": ("g", "Sugars"), "sugars": ("g", "Sugars"), "sůl": ("g", "Salt"), "salt": ("g", "Salt"),
    }
    for row in soup.select("tr"):
        cells = [x.get_text(" ", strip=True) for x in row.select("th,td")]
        if len(cells) < 2:
            continue
        key = norm(cells[0])
        match = next((v for k, v in aliases.items() if k in key), None)
        number = re.search(r"(\d+(?:[,.]\d+)?)", cells[1])
        if match and number:
            values[match[1]] = {"value": float(number.group(1).replace(",", ".")), "unit": match[0]}
    return source_product, values


def lookup_page(label: str, url: str, brand: str = "", session: requests.Session | None = None) -> NutritionCandidate | None:
    client = session or requests.Session()
    try:
        response = client.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return None
    source_product, values = parse_nutrition_table(response.text)
    if not values or not identity_matches(label, source_product, brand):
        return None
    return NutritionCandidate("retailer_or_manufacturer", source_product, response.url, values, "source_page")


def find_exact(label: str, ean: str = "", source_urls: Iterable[str] = (), brand: str = "", session: requests.Session | None = None) -> dict:
    """Return a cache-ready exact result, or a deterministic not_found result."""
    candidate = lookup_ean(ean, label, session) if ean else None
    if candidate is None:
        for url in source_urls:
            candidate = lookup_page(label, url, brand, session)
            if candidate:
                break
    if candidate is None:
        return {"status": "not_found", "values": {}, "schema_version": 13}
    result = asdict(candidate)
    result.update(status="found", provenance="exact_match", schema_version=13)
    return result
