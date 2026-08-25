var TERMSX = [["chipsy_lay_s", "chipsy lay's"], ["majoneza_hellmann_s", "majonéza hellmann's"], ["tatarska_omacka_hellmann_s", "tatarská omáčka hellmann's"], ["olivovy_olej_extra_panensky_exclusive_franz_josef_kaiser", "olivový olej extra panenský exclusive franz josef kaiser"], ["testoviny_panzani", "těstoviny panzani"], ["bujon_maggi", "bujón maggi"], ["bujon_masox_vitana", "bujon masox vitana"], ["dresink_hellmann_s", "dresink hellmann's"]];
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