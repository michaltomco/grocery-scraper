import json, pathlib

WS = '/home/arch/.hermes/cache/browser-use/workspace/20260821_081654_9b17a2'
terms = json.load(open('/home/arch/Projects/grocery-scraper/nonproduce_search_terms.json'))
lit = json.dumps([[k, t] for c, k, t in terms], ensure_ascii=False)

script = (
    "var TERMS2 = " + lit + ";\n"
    "var out2 = {};\n"
    "(async function () {\n"
    "  for (const pair of TERMS2) {\n"
    "    var key = pair[0], term = pair[1];\n"
    "    try {\n"
    "      var d = await fetch('https://nutridata.cz/Eatable/Search/?filter=' + encodeURIComponent(term)).then(function (r) { return r.json(); });\n"
    "      out2[key] = { term: term, items: d.slice(0, 8).map(function (x) { return {\n"
    "        name: x.dbEatable.Name, guid: x.GUID_WEB,\n"
    "        energy_kj: x.dbEatable.Energy, carbohydrates_g: x.dbEatable.Carbohydrates,\n"
    "        fats_g: x.dbEatable.Fats, proteins_g: x.dbEatable.Proteins,\n"
    "        fiber_g: x.dbEatable.Fiber, sugar_g: x.dbEatable.Sugar, sodium_mg: x.dbEatable.Sodium\n"
    "      }; }) };\n"
    "    } catch (e) { out2[key] = { term: term, error: String(e) }; }\n"
    "  }\n"
    "  return out2;\n"
    "})();"
)

result = js(script)
pathlib.Path(WS + '/nutridata_nonproduce_search2.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print('saved', len(result))
print('lucina', json.dumps(result.get('lucina'), ensure_ascii=False)[:300])
