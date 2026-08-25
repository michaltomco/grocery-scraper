var TERMSX = [["pivo_svetle_vycepni_branik", "pivo světlé výčepní 10° braník"], ["okurka", "okurky giana"], ["rajcata", "rajčata sekaná gustona"], ["tunakovy_salat_exclusive_franz_josef_kaiser", "tuňákový salát exclusive franz josef kaiser"], ["zeli", "zelí červené kysané albert"], ["fazole_giana", "fazole giana"], ["pomazanka_hame", "pomazánka hamé"], ["salat_insalatissime_rio_mare", "salát insalatissime rio mare"]];
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