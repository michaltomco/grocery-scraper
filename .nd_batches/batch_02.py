var TERMSX = [["sproty_uzene_v_oleji_vyberove_nekton", "šproty uzené v oleji výběrové nekton"], ["tunak_steak_exclusive_franz_josef_kaiser", "tuňák steak exclusive franz josef kaiser"], ["oblozena_bageta_fresh_bistro", "obložená bageta albert fresh bistro"], ["rajcata", "sušená rajčata v oleji exclusive franz josef kaiser"], ["salat_ledovy", "salát ledový albert"], ["utopenci_viva", "utopenci viva"], ["dyne_hokkaido", "polévka krémová z dýně a mrkviček albert fresh bistro"], ["noky_panzani", "noky panzani"]];
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