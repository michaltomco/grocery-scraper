import json, pathlib
WS='/home/arch/.hermes/cache/browser-use/workspace/20260821_081654_9b17a2'
FB={"orechy_vlasske_emma": "vlašské ořechy"}
script=("var __FB="+json.dumps(FB,ensure_ascii=False)+";\n"
        "var __o={};\n"
        "(async function(){for(const k in __FB){var term=__FB[k];\n"
        " try{var d=await fetch('https://nutridata.cz/Eatable/Search/?filter='+encodeURIComponent(term)).then(r=>r.json());\n"
        "  __o[k]={fallback:term,items:d.slice(0,6).map(x=>({name:x.dbEatable.Name,guid:x.GUID_WEB,energy_kj:x.dbEatable.Energy,carbohydrates_g:x.dbEatable.Carbohydrates,fats_g:x.dbEatable.Fats,proteins_g:x.dbEatable.Proteins,fiber_g:x.dbEatable.Fiber,sugar_g:x.dbEatable.Sugar,sodium_mg:x.dbEatable.Sodium}));}\n"
        " }catch(e){__o[k]={fallback:term,error:String(e)};}}\n"
        " return __o;})();")
res=js(script)
mp=pathlib.Path(WS+'/nutridata_fallback_search.json')
ex=json.load(open(mp)) if mp.exists() else {}
ex.update(res); mp.write_text(json.dumps(ex,ensure_ascii=False,indent=2))
print('chunk',56,'merged',len(ex))
