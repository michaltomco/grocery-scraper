var __ROWS = [["hranolky_vlnky_mrazene_nowaco", "hranolky"], ["instantni_polevky_vitana", "polévka"], ["limonada_coca_cola", "coca cola"], ["instantni_kava_jacobs_velvet", "káva"], ["instantni_kava_nescafe_crema", "káva"], ["instantni_kakao_granko_orion", "kakao"], ["kapsle_tassimo_jacobs", "káva"], ["bageta_cesnekova", "bageta"]];
var __o = {};
(async function () {
  for (const row of __ROWS) {
    var key = row[0], term = row[1];
    try {
      var d = await fetch('https://nutridata.cz/Eatable/Search/?filter=' + encodeURIComponent(term)).then(function (r) { return r.json(); });
      __o[key] = { fallback: term, items: d.slice(0, 6).map(function (x) { return {
        name: x.dbEatable.Name, guid: x.GUID_WEB,
        energy_kj: x.dbEatable.Energy, carbohydrates_g: x.dbEatable.Carbohydrates,
        fats_g: x.dbEatable.Fats, proteins_g: x.dbEatable.Proteins,
        fiber_g: x.dbEatable.Fiber, sugar_g: x.dbEatable.Sugar, sodium_mg: x.dbEatable.Sodium
      }; }) };
    } catch (e) { __o[key] = { fallback: term, error: String(e) }; }
  }
  return __o;
})();