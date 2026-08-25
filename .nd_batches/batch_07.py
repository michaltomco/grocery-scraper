var TERMSX = [["limonada_fanta", "limonáda fanta"], ["limonada_pepsi", "limonáda pepsi"], ["instantni_kava_jacobs_velvet", "instantní káva jacobs velvet"], ["instantni_kava_nescafe_crema", "instantní káva nescafé crema"], ["instantni_kakao_granko_orion", "instantní kakao granko orion"], ["kapsle_tassimo_jacobs", "kapsle tassimo jacobs"], ["ledovy_caj_lipton", "ledový čaj lipton"], ["croissant_days", "croissant 7 days"]];
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