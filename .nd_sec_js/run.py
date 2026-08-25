import json, pathlib, sys
WS='/home/arch/.hermes/cache/browser-use/workspace/20260821_081654_9b17a2'
idx=sys.argv[1]
script=open('/home/arch/Projects/grocery-scraper/.nd_sec_js/batch_'+idx+'.js').read()
res=js(script)
mp=pathlib.Path(WS+'/nutridata_secondary_search.json')
ex=json.load(open(mp)) if mp.exists() else {}
ex.update(res); mp.write_text(json.dumps(ex,ensure_ascii=False,indent=2))
print('batch',idx,'merged',len(ex))
