#!/usr/bin/env python3
"""Research dairy nutrition from Ferpotravina.cz via strict exact-label search.

Strategy:
  1. Parse each retailer label to extract: producer, product type, variety,
     and flavor descriptors.
  2. Search Google (site:ferpotravina.cz) for pages matching producer + product type +
     distinctive variety/variant name.
  3. Fetch each candidate page, extract producer (Výrobce; fall back to Dodavatel
     when Výrobce is "neuvedeno"), parse nutrition table.
  4. STRICT MATCH: page producer must match label producer (via brand map),
     product type must be present, and flavor/lactose descriptors must be
     compatible. No generic fallbacks.
  5. Save results to JSON for merging into nutrition_cache.json.

Run via execute_code: from dairy_research import run_batch
"""
import json, re, sys
import requests
from hermes_tools import web_search

# ── Producer/brand registry ──
# Known Czech dairy producers/brands appearing in retailer labels.
DAIRY_PRODUCERS = sorted([
    'Mlékárna Valašské Meziříčí', 'Mlékárna Kunín', 'Jihočeské Madeta',
    'Mlékárna Čejetičky', 'Bohemilk', 'Olma', 'Pilos', 'Madeta', 'Madetka',
    'Zott', 'Danone', 'Activia', 'Philadelphia', 'Milbona', 'Galbani',
    'Président', 'Lactalis', 'Hamé', 'Bystřická', 'Jaroměřické',
    'Lada Podravka', 'Ehrmann', 'Müller', 'Jogurtík', 'Florian', 'Jogobella',
    'Olmíci', 'Pierot', 'Fantasia', 'Athentikos', 'Zvolenský', 'Cavalier',
    'Mlekovita', 'Apetito', 'Bublé', 'Veselá kráva', 'Sýýýr',
    'Sýrařův výběr', 'Liptov', 'Tatra', 'Albert', 'Penny',
    'Kri Kri', 'Amazake', 'Ola', 'Vesna', 'Bistro', 'Selský',
    'Farmářské', 'Podhale', 'Bílé', 'Krásno', 'Valašský',
    # Additional producers / store brands present in batch 2 labels
    'Hollandia', 'Milko', 'Česká chuť', 'Krajanka', 'Srdce Domova',
    'Alfa', 'Food Festival', 'Váhala', 'Chef Select', 'Caribbean Style',
    'Mlékárna Hlinsko', 'Mlékárna Pragolaktos', 'Molkerei Ammerlan',
    'Polabské mlékárny',
    # Batch 4 producers / brands
    'Král Sýrů', 'Nowaco', 'Gran Moravia', 'Bambino', 'Kiri',
    'Smetanito', 'Želetava', 'A.W.', "Kids Nature's Promise",
    'Bánovecké Milsy', 'Billa', 'Jaroměřický', 'Olešnický Moravia',
    'Bohušovická mlékárna', 'Havlík', 'Zaanlander',
], key=len, reverse=True)

# Brand-to-manufacturer mapping (Ferpotravina lists manufacturers, labels use brands)
VARIETY_NAMES = ['Cheddar', 'Gouda', 'Eidam', 'Edam', 'Feta', 'Brie',
    'Camembert', 'Parmesan', 'Emmental', 'Burrat', 'Mozzarella', 'Mascarpone',
    'Leerdammer', 'Gorgonzola', 'Pecorino', 'Grana', 'Provola', 'Ricotta',
    'Balkánský', 'Balkansky', 'Parenica', 'Olomoucké',
    'Niva', 'Hermelín', 'Romadur', 'Tvarůžky', 'Parenica', 'Uzený']

# Brand-to-manufacturer mapping (Ferpotravina lists manufacturers, labels use brands)
BRAND_TO_MANUFACTURER = {
    'president': 'lactalis',
    'activia': 'danone',
    'philadelphia': 'lactalis',
    'milbona': 'milbona',
    'galbani': 'galbani',
    'leerdammer': 'leerdammer',
    'madeta': 'madeta',
    'madetka': 'madeta',
    'pilos': 'pilos',
    'olma': 'olma',
    'zott': 'zott',
    'kri kri': 'kri kri',
    'buble': 'buble',
    'vesela kral': 'vesela kral',
    'ehrmann': 'ehrmann',
    'muller': 'muller',
    'danone': 'danone',
    'bohemilk': 'bohemilk',
    'mlekarna kunin': 'mlekarna kunin',
    'mlekarna valasske mezirici': 'mlekarna valasske mezirici',
    'jihostecke madeta': 'jihostecke madeta',
    'mlekarna Cejeticky': 'mlekarna cejeticky',
    'hamé': 'hamé',
    'bystřická': 'bystřická',
    'jaroměřické': 'jaroměřické',
    'lada podravka': 'lada podravka',
    'st. dalfour': 'st. dalfour',
    # Brand -> actual Ferpotravina manufacturer (for store/private-label brands)
    'tatra': 'mlekarna hlinsko',
    'pilos': 'mlekarna pragolaktos',
    'milbona': 'molkerei ammerlan',
    'milko': 'polabske mlekarny',
    'ceska chut': 'olma',
    'srdce domova': 'srdce domova',
}

CONTRADICT_WORDS = ['koren', 'omac', 'pomaz', 'dressing', 'citron', 'ocet',
    'majon', 'kari', 'pazit', 'omacka', 'salat', 'salad']

TYPE_KW = [
    (['mleko'], 'milk'), (['jogurt'], 'yogurt'),
    (['kefir', 'kefirovat'], 'kefir'), (['smetan'], 'cream'),
    (['tvaroh'], 'curd'), (['maslo'], 'butter'),
    (['syr'], 'cheese'), (['skyr'], 'skyr'),
    (['zerve'], 'cheese'), (['slehacka'], 'cream'),
    (['pomazankove', 'pomazanka'], 'cheese'), (['tapas'], 'cheese'),
    (['taveny', 'tazeny'], 'cheese'), (['parenicky', 'parenicka'], 'cheese'),
]

CZ_MAP = {'milk':'mléko','yogurt':'jogurt','kefir':'kefír','cream':'smetana',
           'sour_cream':'smetana','curd':'tvaroh','butter':'máslo',
           'cheese':'sýr','skyr':'skyr'}

FLAVOR_WORDS = ['bílý', 'bílá', 'bílé', 'řecký', 'řecké', 'řeckého',
    'polotučný', 'polotučná', 'polotučné', 'plnotučný', 'plnotučné',
    'světlý', 'světlá', 'světlé', 'light', 'bio', 'trvanlivé', 'čerstvé',
    'selské', 'z valašska', 'plátky']

SOURCE_FLAVOR_WORDS = ['malina', 'jahoda', 'banán', 'vanil', 'med', 'citron',
    'mandle', 'orechy', 'meruň', 'boru', 'švest', 'kiwi', 'mango',
    'jablko', 'kokos', 'chia', 'ovocný', 'ochucen', 'jablek', 'malin',
    'tresn', 'više']

LABEL_FLAVOR_WORDS = ['malina', 'jahoda', 'banán', 'vanil', 'med', 'citron', 'mandle',
    'orechy', 'meruň', 'boru', 'švest', 'kiwi', 'mango', 'jablko',
    'kokos', 'chia', 'ovocný', 'ochucen']

def normalize(s):
    """Normalize text: lowercase, collapse whitespace, strip accents."""
    import unicodedata
    s = s.lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', s.strip())

def extract_producer(label):
    nl = normalize(label)
    for p in DAIRY_PRODUCERS:
        if normalize(p) in nl:
            return p
    return None

def extract_product_type(label):
    nl = normalize(label)
    for keywords, ptype in TYPE_KW:
        for kw in keywords:
            if kw == 'mleka':
                # "mléka" must NOT match inside "mlékárna" (dairy factory)
                if re.search(r'mleka(?!r)', nl):
                    return ptype
            elif kw in nl:
                return ptype
    return None

def extract_variety(label):
    nl = normalize(label)
    for v in sorted(VARIETY_NAMES, key=len, reverse=True):
        if normalize(v) in nl:
            return v
    return None

def extract_flavors(label):
    """Extract flavor/modifier words from the label."""
    nl = normalize(label)
    return [fw for fw in FLAVOR_WORDS if normalize(fw) in nl]

def producers_match(page_producer, label_producer):
    """Check if page producer matches label producer (brand map, accent-insensitive)."""
    if not page_producer or not label_producer:
        return False
    pp = normalize(page_producer.replace(' a.s.', '').replace(' s.r.o.', ''))
    lp = normalize(label_producer.replace(' a.s.', '').replace(' s.r.o.', ''))
    # Direct match
    if lp in pp or pp in lp:
        return True
    # Brand mapping
    for brand, mfr in BRAND_TO_MANUFACTURER.items():
        b, m = normalize(brand), normalize(mfr)
        if b in lp and m in pp:
            return True
        if m in lp and b in pp:
            return True
    # Token overlap (handle "alb sýr" vs "albert")
    lp_tokens = set(lp.split())
    pp_tokens = set(pp.split())
    return bool(lp_tokens & pp_tokens)

def parse_ferpotravina_table(html_text):
    """Parse nutrition table from Ferpotravina HTML."""
    values = {}
    table_idx = html_text.find('product-detail-nutrition')
    if table_idx < 0:
        table_idx = html_text.lower().find('nutriční hodnoty')
    if table_idx < 0:
        return None
    section = html_text[table_idx:table_idx + 5000]
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', section, re.S | re.I)
    for row in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S | re.I)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(cells) < 3:
            continue
        label = cells[0].lower()
        if not label or label in ('100 g', '1 porce', '% ddd', '') or label.startswith('---'):
            continue
        for i in range(1, len(cells)):
            val_cell = cells[i]
            nums = re.findall(r'[\d.,]+', val_cell)
            if not nums:
                continue
            try:
                val = float(nums[0].replace(',', '.'))
            except ValueError:
                continue
            unit = ('kcal' if 'kcal' in val_cell else 'kJ' if 'kj' in val_cell
                    else 'g' if 'g' in val_cell else 'mg' if 'mg' in val_cell else '')
            if label == 'energie':
                values['Calories'] = {'value': val, 'unit': unit if unit in ('kcal','kJ') else 'kcal'}
            elif 'bílkov' in label and unit == 'g':
                values['Protein'] = {'value': val, 'unit': unit}
            elif 'sachar' in label and 'cukry' not in label and unit == 'g':
                values['Carbs'] = {'value': val, 'unit': unit}
            elif ('z toho cukry' in label or label == 'cukry') and unit == 'g':
                values['Sugar'] = {'value': val, 'unit': unit}
            elif 'nasycen' in label and unit == 'g':
                values['Saturated Fat'] = {'value': val, 'unit': unit}
            elif label == 'tuky' and unit == 'g' and 'Saturated Fat' not in values:
                values['Fat'] = {'value': val, 'unit': unit}
            elif 'vlákn' in label and unit == 'g':
                values['Fiber'] = {'value': val, 'unit': unit}
            elif label == 'sůl' and unit == 'g':
                values['Sodium'] = {'value': val, 'unit': unit}
            elif 'vápní' in label and unit == 'mg':
                values['Calcium'] = {'value': val, 'unit': unit}
            elif 'železo' in label and unit == 'mg':
                values['Iron'] = {'value': val, 'unit': unit}
            elif 'hořč' in label and unit == 'mg':
                values['Magnesium'] = {'value': val, 'unit': unit}
            break
    return values if values else None

def extract_field(html, field_name_cz):
    """Extract a table field from Ferpotravina detail page."""
    pattern = rf'<th[^>]*>\s*{field_name_cz}\s*:</th>\s*<td[^>]*>(.*?)</td>'
    m = re.search(pattern, html, re.S | re.I)
    if m:
        val = m.group(1).strip()
        a_match = re.search(r'<a[^>]*href[^>]*>([^<]+)</a>', val)
        if a_match:
            val = a_match.group(1).strip()
        val = re.sub(r'<[^>]+>', '', val).strip()
        val = val.replace(', a.s.', '').replace(', s.r.o.', '').replace('a.s.', '').replace('s.r.o.', '').strip()
        return val if val and val.lower() != 'neuvedeno' else None
    return None

def extract_source_product(html, title_text):
    """Extract product name from Ferpotravina page.

    Combines <h1> (short brand name) with product-type keywords from the
    <title> tag to ensure the full product identity is captured for type
    checking (e.g. ``<h1>Activia Bílá</h1>`` + title ``- Jogurt``).
    """
    h1_text = None
    for tag in ['h1', 'h2']:
        m = re.search(rf'<{tag}[^>]*>([^<]+)</{tag}>', html, re.I)
        if m and 'FÉR' not in m.group(1) and m.group(1).strip():
            h1_text = m.group(1).strip()
            break
    title_product = None
    m = re.search(r'<title>([^<]+)<', html)
    if m:
        t = m.group(1).replace('Podrobné informace o potravině ', '').replace(' - FÉR potravina', '').strip()
        if t and 'FÉR potravina' not in t:
            title_product = t
    # If h1 exists but title has extra type info, combine them
    if h1_text and title_product:
        h1_norm = normalize(h1_text)
        title_norm = normalize(title_product)
        # Check if title has words not in h1
        extra = set(title_norm.split()) - set(h1_norm.split())
        if extra:
            return f"{h1_text} {' '.join(sorted(extra))}"
    if h1_text:
        return h1_text
    if title_product:
        return title_product
    return title_text or None

def fetch_ferpotravina(url):
    """Fetch Ferpotravina page, extract source_product, producer, nutrition."""
    try:
        r = requests.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
        })
        if r.status_code != 200 or len(r.text) < 1000:
            return None, None, None
        html = r.text
        producer = extract_field(html, 'Výrobce')
        if not producer:
            producer = extract_field(html, 'Dodavatel')
        title_match = re.search(r'<title>([^<]+)<', html)
        title_text = title_match.group(1) if title_match else ''
        source_product = extract_source_product(html, title_text)

        # If source product doesn't contain a type keyword, try to extract it
        # from the URL path or breadcrumb (Ferpotravina category path)
        if source_product:
            sp_norm = normalize(source_product)
            if not any(kw in sp_norm for kws, _ in TYPE_KW for kw in kws):
                # Try URL path (e.g. /jogurt/... -> "jogurt")
                path_match = re.search(r'ferpotravina\.cz/([^/]+)/', url)
                if path_match:
                    cat = path_match.group(1)
                    if cat in ('jogurt', 'jogurty', 'mléko', 'mleko', 'kefir',
                               'smetana', 'tvaroh', 'maslo', 'syr', 'skyr'):
                        source_product = f"{source_product} {cat}"
                # Try breadcrumb
                if not any(kw in normalize(source_product) for kws, _ in TYPE_KW for kw in kws):
                    bc_match = re.search(r'href="/[^"]*"[^>]*>\s*([^<]+?)\s*</a>', html)
                    if bc_match:
                        bc = bc_match.group(1).strip()
                        bc_norm = normalize(bc)
                        if any(kw in bc_norm for kws, _ in TYPE_KW for kw in kws):
                            source_product = f"{source_product} {bc}"

        values = parse_ferpotravina_table(html)
        return source_product, producer, values
    except Exception as e:
        print(f"  fetch error: {e}", file=sys.stderr, flush=True)
        return None, None, None

def search_product(label):
    """Search for exact nutrition data for a single dairy product label."""
    producer = extract_producer(label)
    ptype = extract_product_type(label)
    variety = extract_variety(label)
    # Infer product type from a cheese variety when the label has no explicit type word
    if not ptype and variety:
        ptype = 'cheese'
    if not producer and not ptype:
        return {'product_name': label, 'source_url': None, 'source_product': None,
                'producer': None, 'values': {}, 'status': 'not_found',
                'match_reason': f"producer={producer} type={ptype}"}
    cz_ptype = CZ_MAP.get(ptype, ptype)

    # Build a descriptive phrase from the label (minus producer and weight) so the
    # search targets the actual product page, not a generic category listing.
    label_clean = normalize(label)
    label_clean = re.sub(r'\s*\d+[,.]?\d*\s*(g|kg|ml|l)\b', '', label_clean)
    if producer:
        label_clean = re.sub(re.escape(normalize(producer)), '', label_clean)
    label_clean = re.sub(r'\s+', ' ', label_clean).strip()

    if producer and label_clean:
        query = f'site:ferpotravina.cz "{producer}" "{label_clean}"'
    elif producer:
        query = f'site:ferpotravina.cz "{producer}" {cz_ptype}'
    elif label_clean:
        query = f'site:ferpotravina.cz "{label_clean}"'
    else:
        query = f'site:ferpotravina.cz {cz_ptype}'

    search_res = web_search(query=query, limit=8)

    for r in search_res.get('data', {}).get('web', []):
        url = r['url']
        if 'ferpotravina.cz' not in url:
            continue
        if '/detail-firmy/' in url:
            continue
        if re.search(r'/[^/]+/(nejnovejsi|2|3|4|5|6)\b', url):
            continue

        source_product, page_producer, values = fetch_ferpotravina(url)
        if not values or not page_producer:
            continue
        # STRICT producer match (only when a label producer was identified)
        if producer and not producers_match(page_producer, producer):
            continue
        # Product type must be in source product name
        # Exception: if a cheese variety was matched, the variety name itself
        # is sufficient evidence of the product type (e.g. "Cheddar" implies cheese)
        sp = normalize(source_product or '')
        type_in_sp = any(kw in sp for kws, _ in TYPE_KW for kw in kws)
        variety_in_sp = variety and normalize(variety) in sp
        if not type_in_sp and not variety_in_sp:
            continue
        # Contradiction check
        if any(c in sp for c in CONTRADICT_WORDS):
            continue

        # Flavor/lactose compatibility
        label_core = normalize(label)
        label_core = re.sub(r'\s*\d+[,.]?\d*\s*(g|kg|ml|l)\b', '', label_core)

        label_plain = any(normalize(f) in label_core for f in ['bílý', 'bílá', 'bílé'])
        # "neočucené/neochucené" (unflavored) must NOT be treated as having flavor
        label_unflavored = ('nechucene' in label_core or 'neocucene' in label_core
                            or 'neochucene' in label_core or 'nechucene' in label_core)
        label_has_flavor = any(normalize(fw) in label_core for fw in LABEL_FLAVOR_WORDS) and not label_unflavored
        label_no_lactose = 'laktoz' in label_core or 'bez laktozy' in label_core

        source_is_plain = not any(normalize(fw) in sp for fw in SOURCE_FLAVOR_WORDS)

        if label_plain and not source_is_plain:
            continue
        if not label_has_flavor and not source_is_plain:
            continue
        source_no_lactose = 'bez laktozy' in sp or 'laktoz' in sp
        if not label_no_lactose and source_no_lactose:
            continue
        # Reverse lactose check: label says "without lactose" but source doesn't confirm
        if label_no_lactose and not source_no_lactose:
            continue
        # Product type consistency: label's specific type must appear in source
        # e.g. "kefírové mléko" should not match "acidofilní mléko"
        # Allow "mleko" (generic milk) to match across subtypes, but specific types
        # (kefir, jogurt, smetana, tvaroh, maslo, syr, skyr) must match
        label_specific_types = [kw for kws, t in TYPE_KW for kw in kws
                                if kw in label_core and t != 'milk']
        source_specific_types = [kw for kws, t in TYPE_KW for kw in kws
                                 if kw in sp and t != 'milk']
        if label_specific_types:
            # Label has a specific type — source must contain at least one matching it
            # Exception: if a cheese variety was matched, "sýr" (cheese) in the label
            # doesn't need to appear in the source name (e.g. "Président Cheddar" is cheese
            # even without "sýr" in the name)
            if variety and variety.lower() in normalize(sp):
                pass  # variety match is sufficient
            elif not any(kw in sp for kw in label_specific_types):
                continue

        # Variant conflict check: detect non-matching variant keywords
        # e.g. label has "klasik" but source has "protein", or vice versa
        SOURCE_VARIANTS = ['protein', 'chia', 'bio', 'light', 'fitness', 'low', 'zero']
        LABEL_VARIANTS = ['klasik', 'classic', 'protein', 'chia', 'bio', 'light',
                          'fitness', 'low', 'zero', 'extra']
        label_variants = [v for v in LABEL_VARIANTS if v in label_core]
        source_variants = [v for v in SOURCE_VARIANTS if v in sp]
        if label_variants and source_variants:
            # Both have variants — they must share at least one keyword
            if not set(label_variants) & set(source_variants):
                continue

        # Cream subtype consistency: cooking/whipping/sour/to-coffee cream must
        # match between label and source. Prevents e.g. "na vaření" 12% matching
        # "ke šlehání" 33%, or "zakysaná" 16% matching "ke šlehání" 33%.
        CREAM_SUBTYPES = ['slehani', 'vareni', 'zakysan', 'kysana', 'kavy']
        label_cream = [s for s in CREAM_SUBTYPES if s in label_core]
        source_cream = [s for s in CREAM_SUBTYPES if s in sp]
        if label_cream and source_cream and set(label_cream) != set(source_cream):
            continue

        # Name similarity: source product must share meaningful tokens with label
        # (excludes producer, type, weight — prevents "Olmíci Haribo" matching "Klasik jogurt")
        STOP_TOKENS = set()
        for kws, _ in TYPE_KW:
            STOP_TOKENS.update(kw for kw in kws)
        STOP_TOKENS.update(['bílý', 'bílá', 'bílé', 'g', 'kg', 'ml', 'l', 'pln', 'tuč',
                           'polo', 'tuku', 'může', 'mléko', 'jogurt', 'kefir', 'smetan',
                           'tvaroh', 'máslo', 'sýr', 'syr', 'skyr'])
        label_tokens = set(t for t in label_core.split() if t not in STOP_TOKENS
                          and t not in normalize(producer).split() and len(t) > 2)
        # Source tokens include both the source product name AND the page producer
        # (Ferpotravina product names often omit the producer, so we check both)
        source_tokens = set(t for t in sp.split() if len(t) > 2)
        source_tokens.update(t for t in normalize(page_producer or '').split() if len(t) > 2)
        if label_tokens and source_tokens:
            shared = label_tokens & source_tokens
            if len(shared) < 1:
                continue

        return {'product_name': label, 'source_url': url,
                'source_product': source_product or r.get('title', ''),
                'producer': page_producer, 'values': values,
                'status': 'found',
                'match_reason': f'producer={producer} type={ptype} variety={variety}'}

    return {'product_name': label, 'source_url': None, 'source_product': None,
            'producer': producer, 'values': {}, 'status': 'not_found',
            'match_reason': f'no exact match for {producer} {cz_ptype}'}


def run_batch(labels, out_path):
    """Process a batch of labels and save results to JSON."""
    results = []
    for i, label in enumerate(labels):
        label = label.strip()
        if not label:
            continue
        print(f"[{i+1}/{len(labels)}] {label}", file=sys.stderr, flush=True)
        res = search_product(label)
        results.append(res)
        if res['status'] == 'found':
            print(f"  FOUND: {res.get('source_product','-')[:40]}", file=sys.stderr, flush=True)
        else:
            print(f"  NOT FOUND ({res.get('match_reason','')})", file=sys.stderr, flush=True)
    json.dump(results, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    found = sum(1 for r in results if r['status'] == 'found')
    print(f"\nDone: {found} found, {len(results)-found} not_found out of {len(labels)}", file=sys.stderr)
    return results
