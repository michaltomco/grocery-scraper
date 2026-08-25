# Browser-side runner: reads terms.json content injected by the host, queries NutriData.
import json, pathlib

WS = '/home/arch/.hermes/cache/browser-use/workspace/20260821_081654_9b17a2'
# The host writes the terms list into a JS var before exec via template replacement.
TERMS_LITERAL = None

def run_batch(terms_slice):
    script = (
        "var __T = " + json.dumps(terms_slice, ensure_ascii=False) + ";\n"
        "var __o = {};\n"
        "(async function () {\n"
        "  for (const pair of __T) {\n"
        "    var key = pair[0], term = pair[1];\n"
        "    try {\n"
        "      var d = await fetch('https://nutridata.cz/Eatable/Search/?filter=' + encodeURIComponent(term)).then(function (r) { return r.json(); });\n"
        "      __o[key] = { term: term, items: d.slice(0, 8).map(function (x) { return {\n"
        "        name: x.dbEatable.Name, guid: x.GUID_WEB,\n"
        "        energy_kj: x.dbEatable.Energy, carbohydrates_g: x.dbEatable.Carbohydrates,\n"
        "        fats_g: x.dbEatable.Fats, proteins_g: x.dbEatable.Proteins,\n"
        "        fiber_g: x.dbEatable.Fiber, sugar_g: x.dbEatable.Sugar, sodium_mg: x.dbEatable.Sodium\n"
        "      }; }) };\n"
        "    } catch (e) { __o[key] = { term: term, error: String(e) }; }\n"
        "  }\n"
        "  return __o;\n"
        "})();"
    )
    return js(script)

merged_path = pathlib.Path(WS + '/nutridata_nonproduce_search3.json')
merged = json.load(open(merged_path)) if merged_path.exists() else {}
terms = json.load(open('/home/arch/Projects/grocery-scraper/.nd_batches/terms.json'))
for i in range(0, len(terms), 6):
    slice_ = terms[i:i+6]
    res = run_batch(slice_)
    merged.update(res)
    print('chunk', i, 'merged', len(merged))
merged_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
print('DONE', len(merged))
