import json, pathlib
WS = '/home/arch/.hermes/cache/browser-use/workspace/20260821_081654_9b17a2'
script = open('/home/arch/Projects/grocery-scraper/.nd_batches/batch_07.py').read()
result = js(script)
merged_path = pathlib.Path(WS + '/nutridata_nonproduce_search3.json')
existing = json.load(open(merged_path)) if merged_path.exists() else {}
existing.update(result)
merged_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
print('batch 07', 'merged', len(existing), 'just', len(result))
