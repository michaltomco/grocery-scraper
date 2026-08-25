"""Persistent nutrition lookups for canonical produce names.

Values are normalized to a per-100 g basis and cached locally so rebuilding the
site does not repeatedly call the remote service.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "nutrition_cache.json"
API_URL = "https://world.openfoodfacts.org/cgi/search.pl"
USDA_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
CACHE_VERSION = 13
HEADERS = {"User-Agent": "grocery-scraper/1.0 (nutrition cache)"}
NUTRIENTS = {
    "energy-kcal_100g": ("kcal", "Calories"),
    "energy-kj_100g": ("kJ", "Energy"),
    "proteins_100g": ("g", "Protein"),
    "carbohydrates_100g": ("g", "Carbs"),
    "fat_100g": ("g", "Fat"),
    "saturated-fat_100g": ("g", "Saturated fat"),
    "monounsaturated-fat_100g": ("g", "Monounsaturated fat"),
    "polyunsaturated-fat_100g": ("g", "Polyunsaturated fat"),
    "trans-fat_100g": ("g", "Trans fat"),
    "omega-3-fat_100g": ("g", "Omega-3 fat"),
    "omega-6-fat_100g": ("g", "Omega-6 fat"),
    "omega-9-fat_100g": ("g", "Omega-9 fat"),
    "fiber_100g": ("g", "Fiber"),
    "sugars_100g": ("g", "Sugars"),
    "starch_100g": ("g", "Starch"),
    "salt_100g": ("g", "Salt"),
    "sodium_100g": ("g", "Sodium"),
    "alcohol_100g": ("g", "Alcohol"),
    "cholesterol_100g": ("mg", "Cholesterol"),
    "calcium_100g": ("mg", "Calcium"),
    "iron_100g": ("mg", "Iron"),
    "magnesium_100g": ("mg", "Magnesium"),
    "phosphorus_100g": ("mg", "Phosphorus"),
    "vitamin-c_100g": ("mg", "Vitamin C"),
    "potassium_100g": ("mg", "Potassium"),
    "zinc_100g": ("mg", "Zinc"),
    "copper_100g": ("mg", "Copper"),
    "manganese_100g": ("mg", "Manganese"),
    "selenium_100g": ("µg", "Selenium"),
    "vitamin-a_100g": ("µg", "Vitamin A"),
    "vitamin-d_100g": ("µg", "Vitamin D"),
    "vitamin-e_100g": ("mg", "Vitamin E"),
    "vitamin-k_100g": ("µg", "Vitamin K"),
    "vitamin-b1_100g": ("mg", "Vitamin B1"),
    "vitamin-b2_100g": ("mg", "Vitamin B2"),
    "vitamin-pp_100g": ("mg", "Vitamin B3"),
    "pantothenic-acid_100g": ("mg", "Vitamin B5"),
    "vitamin-b6_100g": ("mg", "Vitamin B6"),
    "biotin_100g": ("µg", "Vitamin B7 (Biotin)"),
    "folates_100g": ("µg", "Vitamin B9"),
    "vitamin-b12_100g": ("µg", "Vitamin B12"),
}

# Open Food Facts contains both Czech and English names. These aliases make
# generic Czech produce much more likely to find a useful generic entry.
QUERY_ALIASES = {
    "avokado": "avocado",
    "banany": "banana",
    "bataty": "sweet potato orange flesh raw",
    "boruvky": "blueberry",
    "brambory": "potato",
    "ananas": "pineapple raw",
    "citrony_bio_nature_s_promise": "lemons",
    "broskve": "peach",
    "hrozny": "grapes",
    "hliva_ustricna": "oyster mushroom",
    "jarni_cibule_svazek": "spring onions",
    "jablka": "apple",
    "jahody": "strawberries",
    "mandarinky": "mandarin",
    "merunky": "apricot",
    "mrkev": "carrot",
    "nektarinky": "nectarine",
    "okurka": "cucumber",
    "paprika": "green sweet pepper raw",
    "pomerance": "oranges",
    "rajcata": "red tomatoes",
    "rajcata_cherry": "red tomatoes",
    "rajcata_cherry_kerikova": "red tomatoes",
    "rajcata_kerikova": "red tomatoes",
    "meloun_vodni": "watermelon raw",
    "zampiony": "button mushrooms",
    "paprika_cervena": "red bell pepper raw",
    "paprika_bila": "white pepper raw",
    "dyne_hokkaido": "pumpkin raw",
    "dyne_hokkaido_ceska_farma": "pumpkin raw",
    "sojove_klicky_menu_inspirace": "mung bean sprouts raw",
    "salat_rodinny_mix": "lettuce raw",
    "salat_polnicek": "lamb lettuce raw",
    "salat_krupavy_crispy": "iceberg lettuce raw",
}

# These are multiple-ingredient or prepared products. A single generic USDA
# ingredient profile would be more misleading than an explicit no-data state.
UNSUPPORTED_NUTRITION_PRODUCTS = {
    "mix_do_polevek_a_omacek_menu_inspirace",
    "mix_farmarsky_menu_inspirace",
    "mix_zeleninovy_menu_inspirace",
    "mrkev_kvetak",
    "salat_baby_listy_mix",
    "salat_fresco_mix",
    "salat_party_mix_ready",
    "salat_rodinny",
    "salat_rodinny_mix",
    "salat_s_cervenou_repou",
    "salat_saporito",
    "salat_sezonni_ready",
}

SUBSTRING_ALIASES = {
    "brambor": "potato", "brokolice": "broccoli", "cibul": "onion",
    "citron": "lemon", "cuketa": "zucchini", "dyne": "pumpkin",
    "fiky": "fig", "hrus": "pear", "kukuric": "corn", "kvetak": "cauliflower",
    "lilek": "eggplant", "limet": "lime", "malin": "raspberry", "mang": "mango",
    "meloun": "melon", "ostruz": "blackberry", "porek": "leek", "redkv": "radish",
    "rukola": "arugula", "salat": "lettuce", "svest": "plum", "rajcat": "tomato",
    "paprik": "pepper", "celer": "celery", "cesnek": "garlic", "mrkev": "carrot",
}


def resolve_nutrition_query(product: str) -> str | None:
    if product in UNSUPPORTED_NUTRITION_PRODUCTS or product.startswith("kompot_"):
        return None
    query = QUERY_ALIASES.get(product)
    if query is not None:
        return query
    return next(
        (value for key, value in SUBSTRING_ALIASES.items() if key in product),
        product.replace("_", " "),
    )

USDA_LABELS = {
    "energy": "Calories", "protein": "Protein", "carbohydrate": "Carbs",
    "total lipid": "Fat", "fiber": "Fiber", "calcium": "Calcium",
    "iron": "Iron", "magnesium": "Magnesium", "phosphorus": "Phosphorus",
    "potassium": "Potassium", "sodium": "Sodium", "zinc": "Zinc",
    "copper": "Copper", "manganese": "Manganese", "selenium": "Selenium",
    "vitamin a": "Vitamin A", "vitamin c": "Vitamin C", "vitamin d": "Vitamin D",
    "vitamin e": "Vitamin E", "vitamin k": "Vitamin K", "thiamin": "Vitamin B1",
    "riboflavin": "Vitamin B2", "niacin": "Vitamin B3", "pantothenic acid": "Vitamin B5",
    "vitamin b-6": "Vitamin B6", "biotin": "Vitamin B7 (Biotin)",
    "folate": "Vitamin B9", "vitamin b-12": "Vitamin B12",
    "linolenic acid": "Omega-3 fat", "linoleic acid": "Omega-6 fat",
    "oleic acid": "Omega-9 fat", "18:3 n-3": "Omega-3 fat",
    "18:2": "Omega-6 fat", "18:1": "Omega-9 fat",
}

# Open Food Facts search is product-oriented, not an ingredient database. A
# produce term such as "lime" can therefore rank a snack or drink above a raw
# lime simply because its nutrition label has more fields. These markers reject
# obviously processed results before the generic nutrient-count ranking.
PROCESSED_FOOD_MARKERS = {
    "chip", "chips", "tortilla", "snack", "flavored", "flavoured",
    "juice", "drink", "soda", "sauce", "seasoning", "powder", "candy",
    "cereal", "cookie", "crisp", "crackers", "cheese", "yogurt", "kefir",
}
USDA_PROCESSED_MARKERS = PROCESSED_FOOD_MARKERS | {
    "beverage", "restaurant", "macaroni", "prepared", "mixed species",
}
OPTIONAL_QUERY_TERMS = {
    "raw", "fresh", "without", "skin", "flesh", "bell", "white", "orange",
}


def candidate_matches_query(text: str, query: str) -> bool:
    terms = [
        term for term in re.findall(r"[a-z]+", query.lower())
        if len(term) > 2 and term not in OPTIONAL_QUERY_TERMS
    ]
    normalized = text.lower()
    return not terms or all(
        term in normalized
        or term.rstrip("s") in normalized
        or (len(term) > 4 and term[:5] in normalized)
        for term in terms
    )


def is_raw_produce_candidate(item: dict, query: str = "") -> bool:
    text = " ".join(
        [
            str(item.get("product_name", "")),
            " ".join(str(tag) for tag in item.get("categories_tags", [])),
        ]
    ).lower()
    return not any(marker in text for marker in PROCESSED_FOOD_MARKERS) and candidate_matches_query(
        text, query
    )


def is_raw_usda_candidate(food: dict, query: str) -> bool:
    description = str(food.get("description", "")).lower()
    return (
        "raw" in description
        and not any(marker in description for marker in USDA_PROCESSED_MARKERS)
        and candidate_matches_query(description, query)
    )


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        value = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# Curated NutriData.cz records for grocery categories other than fresh produce.
# These were hand-verified against the authenticated NutriData food database and
# stored with the exact source entry name + URL so the dashboard can show the
# source for personal checking. They are never auto-ranked by nutrient count;
# each mapping was chosen by a human reviewer (provenance: exact_match or
# category_proxy).
NUTRIDATA_PATH = ROOT / "nutridata_nonproduce_nutrition.json"


def _load_nutridata() -> dict:
    if not NUTRIDATA_PATH.exists():
        return {}
    try:
        data = json.loads(NUTRIDATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for key, rec in data.items():
        if rec.get("status") != "ok":
            continue
        nutrients = rec.get("nutrients", {})
        values = {}
        # Map NutriData per-100 g fields onto the dashboard's label vocabulary.
        mapping = {
            "energy_kj": ("kJ", "Energy"),
            "carbohydrates_g": ("g", "Carbs"),
            "fats_g": ("g", "Fat"),
            "proteins_g": ("g", "Protein"),
            "fiber_g": ("g", "Fiber"),
            "sugar_g": ("g", "Sugars"),
            "sodium_mg": ("mg", "Sodium"),
        }
        for src, (unit, label) in mapping.items():
            v = nutrients.get(src)
            if isinstance(v, (int, float)):
                values[label] = {"value": round(float(v), 2), "unit": unit}
        if not values:
            continue
        out[key] = {
            "status": "found",
            "query": rec.get("source_name", ""),
            "source": "NutriData.cz",
            "source_product": rec.get("source_name", ""),
            "source_url": rec.get("source_url", ""),
            "provenance": rec.get("provenance", ""),
            "values": values,
            "schema_version": CACHE_VERSION,
            "nutridata_curated": True,
        }
    return out


def save_cache(cache: dict) -> None:
    temporary = CACHE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(CACHE_PATH)


def _number(value):
    try:
        value = float(value)
        return round(value, 2) if value >= 0 else None
    except (TypeError, ValueError):
        return None


def _extract(product: dict) -> dict:
    nutriments = product.get("nutriments", {})
    values = {}
    for key, (default_unit, label) in NUTRIENTS.items():
        value = _number(nutriments.get(key))
        if value is not None:
            unit = nutriments.get(f"{key}_unit") or default_unit
            values[label] = {"value": value, "unit": unit}
    return values


def fetch_usda_nutrition(query: str) -> dict:
    """Use USDA generic food data when Open Food Facts lacks micronutrients."""
    params = {
        "api_key": os.environ.get("USDA_API_KEY", "DEMO_KEY"),
        "query": query if "raw" in query else f"{query} raw",
        "dataType": "SR Legacy,Foundation",
        "pageSize": 50,
    }
    try:
        response = requests.get(USDA_API_URL, params=params, headers=HEADERS, timeout=15)
        response.raise_for_status()
        foods = response.json().get("foods", [])
    except (requests.RequestException, ValueError):
        return {}
    candidates = []
    for food in foods:
        description = str(food.get("description", "")).lower()
        if not is_raw_usda_candidate(food, query):
            continue
        if "grape" in query.lower() and ("leaf" in description or "leav" in description or "tomato" in description):
            continue
        if "sweet potato" in query.lower() and ("sweet potato" not in description or any(term in description for term in ("leaf", "leav", "cooked", "baked", "boiled"))):
            continue
        if "lemon" in query.lower() and "juice" in description:
            continue
        query_lower = query.lower()
        if "pineapple" in query_lower and "pineapple" not in description:
            continue
        if "strawberr" in query_lower and ("strawberr" not in description or "raw" not in description or any(term in description for term in ("kefir", "yogurt", "juice", "drink", "pastr", "toaster"))):
            continue
        if "green" in query_lower and "pepper" in query_lower and not all(term in description for term in ("pepper", "sweet", "green")):
            continue
        if "red" in query_lower and "pepper" in query_lower and not all(term in description for term in ("pepper", "sweet", "red")):
            continue
        if "oyster" in query_lower and "oyster" not in description:
            continue
        if "mushroom" in query_lower and "white" not in query_lower and ("mushroom" not in description or "white" not in description):
            continue
        values = {}
        for nutrient in food.get("foodNutrients", []):
            name = nutrient.get("nutrientName", "").lower()
            # FoodData Central exposes both Vitamin A, RAE and Vitamin A, IU.
            # RAE is the dietary-comparison unit; IU is ambiguous without
            # knowing whether its source is retinol or provitamin-A carotenoid.
            # Never let the IU value overwrite the direct RAE measurement.
            if "vitamin a, iu" in name:
                continue
            label = "Vitamin A" if "vitamin a, rae" in name else next(
                (label for key, label in USDA_LABELS.items() if key in name), None
            )
            value = _number(nutrient.get("value"))
            if label and value is not None:
                unit = str(nutrient.get("unitName", "")).strip().lower()
                if label == "Calories" and unit == "kj":
                    value = round(value / 4.184, 2)
                    unit = "kcal"
                values[label] = {"value": value, "unit": unit}
        if values:
            candidates.append((len(values), food, values))
    if not candidates:
        return {}
    def candidate_score(item):
        _, food, _ = item
        description = str(food.get("description", "")).lower()
        grape_fruit = "grape" in query.lower() and "grape" in description and "leaf" not in description and "leav" not in description
        return (grape_fruit, item[0])

    _, food, values = max(candidates, key=candidate_score)
    return {"values": values, "source": "USDA FoodData Central", "source_product": food.get("description", "")}


def fetch_nutrition(product: str) -> dict:
    query = resolve_nutrition_query(product)
    if query is None:
        return {
            "status": "not_found",
            "query": "",
            "values": {},
            "schema_version": CACHE_VERSION,
        }
    # USDA is the canonical source for generic produce profiles. Open Food
    # Facts is used only below when USDA cannot return a matching record.
    primary = fetch_usda_nutrition(query)
    if primary:
        return {
            "status": "found", "query": query, "source": primary["source"],
            "source_product": primary.get("source_product", ""),
            "values": primary["values"], "schema_version": CACHE_VERSION,
        }
    params = {
        "search_terms": query,
        "search_simple": "1",
        "action": "process",
        "json": "1",
        "page_size": "20",
        "fields": "product_name,nutriments,categories_tags",
    }
    try:
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
        response.raise_for_status()
        products = response.json().get("products", [])
    except (requests.RequestException, ValueError):
        fallback = fetch_usda_nutrition(query)
        if fallback:
            return {
                "status": "found", "query": query, "source": fallback["source"],
                "source_product": fallback.get("source_product", ""),
                "values": fallback["values"], "schema_version": CACHE_VERSION,
            }
        return {"status": "error", "query": query, "values": {}, "schema_version": CACHE_VERSION}

    candidates = []
    for item in products:
        if not is_raw_produce_candidate(item, query):
            continue
        values = _extract(item)
        if values:
            candidates.append((len(values), item, values))
    if not candidates:
        fallback = fetch_usda_nutrition(query)
        if fallback:
            return {
                "status": "found", "query": query, "source": fallback["source"],
                "source_product": fallback.get("source_product", ""),
                "values": fallback["values"], "schema_version": CACHE_VERSION,
            }
        return {"status": "not_found", "query": query, "values": {}, "schema_version": CACHE_VERSION}
    _, item, values = max(candidates, key=lambda candidate: candidate[0])
    result = {
        "status": "found",
        "query": query,
        "source": "Open Food Facts",
        "source_product": item.get("product_name", ""),
        "values": values,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": CACHE_VERSION,
    }
    # Open Food Facts product records often omit vitamins/minerals. Fill only
    # missing fields from USDA's generic food composition data.
    micronutrient_labels = {
        "Vitamin A", "Vitamin B1", "Vitamin B2", "Vitamin B3", "Vitamin B5",
        "Vitamin B6", "Vitamin B7 (Biotin)", "Vitamin B9 (Folate)", "Vitamin B12",
        "Vitamin C", "Vitamin D", "Vitamin E", "Vitamin K", "Calcium", "Iron",
        "Magnesium", "Phosphorus", "Potassium", "Sodium", "Zinc", "Copper",
        "Manganese", "Selenium",
    }
    nonzero_micros = {
        label for label in micronutrient_labels
        if label in values and values[label].get("value", 0) != 0
    }
    if query in QUERY_ALIASES.values() or len(nonzero_micros) < 5:
        fallback = fetch_usda_nutrition(query)
        if fallback:
            # USDA is the canonical generic-produce source. Once it becomes
            # available, replace the entire Open Food Facts fallback rather
            # than retaining potentially incompatible branded-product macros.
            return {
                "status": "found",
                "query": query,
                "source": fallback["source"],
                "source_product": fallback.get("source_product", ""),
                "values": fallback["values"],
                "schema_version": CACHE_VERSION,
            }
    return result


def get_many(products: set[str]) -> dict:
    cache = load_cache()
    # Hand-curated NutriData.cz records for non-produce categories take priority
    # over the generic USDA/Open Food Facts produce pipeline: they were verified
    # by a human and labelled with the exact source entry for personal checking.
    # Overwrite any older auto-fetched entry so the curated source always wins.
    curated = _load_nutridata()
    if curated:
        for key, entry in curated.items():
            cache[key] = entry
    changed = False
    for product in sorted(products):
        # Successful and definitive misses are cached. Transient request
        # failures remain retryable on the next build. A historical bug stored
        # FoodData Central Vitamin A, IU values as dietary Vitamin A; refresh
        # those entries so the direct Vitamin A, RAE value replaces them.
        entry = cache.get(product, {})
        # Curated NutriData records are frozen: never re-fetch, never refresh.
        if entry.get("nutridata_curated"):
            continue
        vitamin_a = entry.get("values", {}).get("Vitamin A", {})
        legacy_vitamin_a_iu = str(vitamin_a.get("unit", "")).lower() in {"iu", "i.u."}
        source = str(entry.get("source", ""))
        source_product = str(entry.get("source_product", ""))
        expected_query = resolve_nutrition_query(product) or ""
        legacy_query_mismatch = str(entry.get("query", "")) != expected_query
        legacy_processed_open_food = source.startswith("Open Food Facts") and not is_raw_produce_candidate(
            {"product_name": source_product}, str(entry.get("query", ""))
        )
        legacy_bad_usda = source.startswith("USDA FoodData Central") and not is_raw_usda_candidate(
            {"description": source_product}, str(entry.get("query", ""))
        )
        legacy_mixed_source = source == "Open Food Facts + USDA FoodData Central"
        if (
            not product
            or (
                entry.get("schema_version") == CACHE_VERSION
                and entry.get("status") in {"found", "not_found"}
                and not legacy_vitamin_a_iu
                and not legacy_query_mismatch
                and not legacy_processed_open_food
                and not legacy_bad_usda
                and not legacy_mixed_source
            )
        ):
            continue
        print(f"Looking up nutrition: {product}")
        cache[product] = fetch_nutrition(product)
        changed = True
    if changed:
        save_cache(cache)
    return cache
