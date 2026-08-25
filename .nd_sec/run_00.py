import json, pathlib, sys
WS='/home/arch/.hermes/cache/browser-use/workspace/20260821_081654_9b17a2'
FB={"pivo_svetly_lezak_kralovsky_krusovice": "pivo", "nealkoholicke_pivo_ochucene_birell": "pivo", "bozkov_original_tuzemsky": "rum tuzemský", "nealkoholicke_pivo_ochucene_staropramen_cool": "pivo", "pivo_svetly_lezak_staropramen": "pivo", "pivo_svetly_lezak_zlaty_bazant": "pivo", "becherovka": "becherovka bylinný", "pivo_svetle_vycepni_branik": "pivo"}
LIT=json.dumps(FB, ensure_ascii=False)
rows=[]
for key,term in FB.items():
    rows.append([key,term])
LIT2=json.dumps(rows, ensure_ascii=False)
script=(
  'var __ROWS=' + LIT2 + ';\n'
  'var __o={};\n'
  '(async function(){for(const row of __ROWS){var key=row[0],term=row[1];\n'
  ' try{var d=await fetch(\'https://nutridata.cz/Eatable/Search/?filter=\'+encodeURIComponent(term)).then(r=>r.json());\n'
  '  __o[key]={fallback:term,items:d.slice(0,6).map(function(x){return {name:x.dbEatable.Name,guid:x.GUID_WEB,energy_kj:x.dbEatable.Energy,carbohydrates_g:x.dbEatable.Carbohydrates,fats_g:x.dbEatable.Fats,proteins_g:x.dbEatable.Proteins,fiber_g:x.dbEatable.Fiber,sugar_g:x.dbEatable.Sugar,sodium_mg:x.dbEatable.Sodium};});}\n'
  ' }catch(e){__o[key]={fallback:term,error:String(e)};}}\n'
  ' return __o;}()'
)
res=js(script)
mp=pathlib.Path(WS+'/nutridata_secondary_search.json')
ex=json.load(open(mp)) if mp.exists() else {}
ex.update(res); mp.write_text(json.dumps(ex,ensure_ascii=False,indent=2))
print('chunk',0,'merged',len(ex))
