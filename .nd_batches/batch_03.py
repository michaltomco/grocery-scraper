var TERMSX = [["olivy_exclusive_franz_josef_kaiser", "olivy exclusive franz josef kaiser"], ["omacky_otma", "omáčky otma"], ["omeleta_bramborova_se_zeleninou_fresh_bistro", "omeleta bramborová se zeleninou albert fresh bistro"], ["salam_herkules", "salám herkules tesco"], ["kureci_prsni_rizky", "kuřecí prsní řízky"], ["kureci_stehna", "kuřecí stehna albert"], ["mlete_maso_mix", "mleté maso mix"], ["sunka_amalka_nejvyssi_jakosti_krasno", "šunka amálka nejvyšší jakosti krásno"]];
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