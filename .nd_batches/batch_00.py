var TERMSX = [["pivo_svetly_lezak_kralovsky_krusovice", "pivo světlý ležák královský 12° krušovice"], ["cider_frisco", "cider frisco"], ["nealkoholicke_pivo_ochucene_birell", "nealkoholické pivo ochucené birell"], ["bozkov_original_tuzemsky", "božkov original tuzemský"], ["nealkoholicke_pivo_ochucene_staropramen_cool", "nealkoholické pivo ochucené staropramen cool"], ["pivo_svetly_lezak_staropramen", "pivo světlý ležák 12° staropramen"], ["pivo_svetly_lezak_zlaty_bazant", "pivo světlý ležák 12° zlatý bažant"], ["becherovka", "becherovka"]];
var outX = {};
(async function () {
  for (const pair of TERMSX) {
    var key = pair[0], term = pair[1];
    try {
      var d = await fetch('https://nutridata.cz/Eatable/Search/?filter=' + encodeURIComponent(term)).then(function (r) { return r.json(); });
      outX[key] = { term: term, items: d.slice(0, 8).map(function (x) { return {
        name: x.dbEatable.Name, guid: x.GUID_WEB,
        energy_kj: x.dbEatable.Energy, carbohydrates_g: x.dbEatable.Carbohydrates,
        fats_g: x.dbEatable.Fats, proteins_g: x.dbEatable.Proteins,
        fiber_g: x.dbEatable.Fiber, sugar_g: x.dbEatable.Sugar, sodium_mg: x.dbEatable.Sodium
      }; }) };
    } catch (e) { outX[key] = { term: term, error: String(e) }; }
  }
  return outX;
})();