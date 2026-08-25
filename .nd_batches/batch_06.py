var TERMSX = [["nanuk_mroz_prima", "nanuk mrož prima"], ["nanuk_calippo_algida", "nanuk calippo algida"], ["nanuk_magnum_algida", "nanuk magnum algida"], ["nanuk_twister_algida", "nanuk twister algida"], ["hranolky_vlnky_mrazene_nowaco", "hranolky vlnky mražené nowaco"], ["instantni_polevky_vitana", "instantní polévky vitana"], ["limonada_coca_cola", "limonáda coca cola"], ["kofola", "kofola"]];
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