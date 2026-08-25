"""Strict Ferpotravina research for the Zdravá výživa category.
Run inside Hermes execute_code, where hermes_tools.web_search is available.
"""
import json, re, sys
from pathlib import Path
from hermes_tools import web_search
from dairy_research import fetch_ferpotravina, normalize, producers_match

BRANDS = sorted([
    "Emma", "Bonavita", "Nestlé", "Emco", "oho!", "Racio", "Srdce Domova",
    "Garden Gourmet", "Veggie Garden Gourmet", "Pan Hrášek", "Topnatur", "Dr. Snack",
    "Dr. Ensa", "Nice Bites", "Tesco", "Semix", "Allnature", "Caribbean Style",
    "Body&Future", "Alpro", "Valsoia", "Dr. Oetker", "Goodies", "Free From",
    "Nature's Promise", "Kids Nature's Promise", "Wake & Go", "Day Up", "Bob Snail",
    "Bear Fruits", "Go Nuts", "Alesto", "Lunter", "Bombus", "4Slim", "Clever",
    "Corny", "Loves", "George Stephen", "Muesli bar", "Super Ovoce",
], key=len, reverse=True)

STOP = set("a na v bez g kg ml l ks 100 125 130 150 180 200 250 300 340 375 400 425 450 500 550 60 65 70 75 85 90 120 150 23 24 25 30 35 40 50 55 58 1000".split())
GENERIC = set("produkt produkty zdrava vyziva cerealie musli tycinka tycinky snack mix smes ovoce orechy orechove ovesne ovesny napoj rostlinne rostliny veganske bio bez lepku ochucene".split())

# Product-family words that should remain in the source identity.
FAMILY = {
    "cerealie": ["cerealie", "cereal"], "musli": ["musli", "muesli"],
    "tycinka": ["tycinka", "bar"], "mandle": ["mandle", "almond"],
    "orechy": ["orech", "nuts", "nut"], "kase": ["kase", "porridge"],
    "napoj": ["napoj", "drink", "milk"], "mleko": ["mleko", "milk"],
    "jogurt": ["jogurt", "yogurt"], "tofu": ["tofu"],
}

def producer(label):
    n=normalize(label)
    return next((b for b in BRANDS if normalize(b) in n), None)

def clean_label(label, brand):
    n=normalize(label)
    n=re.sub(r'\b\d+[,.]?\d*\s*(g|kg|ml|l)\b','',n)
    if brand: n=n.replace(normalize(brand),' ')
    n=re.sub(r'\s+',' ',n).strip()
    return n

def meaningful_tokens(text):
    n=normalize(text).replace('&',' ')
    return {t for t in re.findall(r"[a-z0-9]+", n) if len(t)>2 and t not in STOP and t not in GENERIC}

def family_tokens(label):
    n=normalize(label)
    out=[]
    for fam, words in FAMILY.items():
        if any(w in n for w in words): out.extend(words)
    return set(out)

def search_health(label):
    brand=producer(label)
    core=clean_label(label,brand)
    if not brand:
        return {'product_name':label,'source_url':None,'source_product':None,'producer':None,'values':{},'status':'not_found','match_reason':'no recognized brand'}
    query=f'site:ferpotravina.cz "{brand}" "{core}"'
    res=web_search(query=query,limit=8)
    label_tokens=meaningful_tokens(core)
    fam=family_tokens(label)
    for item in res.get('data',{}).get('web',[]):
        url=item.get('url','')
        if 'ferpotravina.cz' not in url or '/detail-firmy/' in url: continue
        sp,page_prod,values=fetch_ferpotravina(url)
        if not sp or not values: continue
        source_text=normalize(sp+' '+url)
        # Require the brand in page producer, product name, or canonical URL.
        brand_norm=normalize(brand)
        if brand_norm not in normalize(page_prod or '') and brand_norm not in source_text:
            continue
        source_tokens=meaningful_tokens(sp+' '+url)
        # Every distinctive label token must be represented, allowing Czech word stems.
        missing=[t for t in label_tokens if not (t in source_tokens or len(t)>4 and t[:5] in ' '.join(source_tokens))]
        if missing: continue
        # Family must be represented; this prevents milk-drink/cereal/bar swaps.
        if fam and not any(t in source_text for t in fam): continue
        # Reject explicit conflicting variants.
        if 'bez lepku' in normalize(label) and 'bez lepku' not in source_text and 'gluten' not in source_text: continue
        return {'product_name':label,'source_url':url,'source_product':sp,'producer':page_prod,'values':values,'status':'found','match_reason':f'brand={brand} exact token/family match'}
    return {'product_name':label,'source_url':None,'source_product':None,'producer':brand,'values':{},'status':'not_found','match_reason':f'no exact match for {brand} {core}'}

def run_batch(labels,out_path):
    out=[]
    for i,label in enumerate(labels):
        print(f'[{i+1}/{len(labels)}] {label}',file=sys.stderr,flush=True)
        r=search_health(label); out.append(r)
        print(' FOUND '+str(r.get('source_product')) if r['status']=='found' else ' NOT FOUND',file=sys.stderr,flush=True)
    json.dump(out,open(out_path,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
    return out
