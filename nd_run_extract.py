import json, pathlib

WS = '/home/arch/.hermes/cache/browser-use/workspace/20260821_081654_9b17a2'
terms = json.load(open('/home/arch/Projects/grocery-scraper/nonproduce_search_terms.json'))

# Build a compact literal the browser JS can consume without encoding issues.
lit = json.dumps([[k, t] for c, k, t in terms], ensure_ascii=False)

script = (
    "const TERMS = " + lit + ";\n"
    "const norm = s => s.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase();\n"
    "const out = {};\n"
    "async function run(){\n"
    "  for (const [key, term] of TERMS) {\n"
    "    try {\n"
    "      const d = await fetch('/Eatable/Search/?filter='+encodeURIComponent(term)).then(r=>r.json());\n"
    "      out[key] = {term, items: d.slice(0,8).map(x => ({\n"
    "        name: x.dbEatable.Name,\n"
    "        guid: x.GUID_WEB,\n"
    "        energy_kj: x.dbEatable.Energy,\n"
    "        carbohydrates_g: x.dbEatable.Carbohydrates,\n"
    "        fats_g: x.dbEatable.Fats,\n"
    "        proteins_g: x.dbEatable.Proteins,\n"
    "        fiber_g: x.dbEatable.Fiber,\n"
    "        sugar_g: x.dbEatable.Sugar,\n"
    "        sodium_mg: x.dbEatable.Sodium\n"
    "      }))};\n"
    "    } catch(e) { out[key] = {term, error: String(e)}; }\n"
    "  }\n"
    "  return out;\n"
    "}\n"
    "run();"
)

result = js(script)
pathlib.Path(WS + '/nutridata_nonproduce_search.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print('saved', len(result), 'keys')
