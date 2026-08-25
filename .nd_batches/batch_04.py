var TERMSX = [["veprova_pecene_bez_kosti", "vepřová pečeně bez kosti"], ["veprova_plec_bez_kosti", "vepřová plec bez kosti"], ["debrecinka_kureci", "debrecínka kuřecí"], ["kureci_ctvrtky", "kuřecí čtvrtky"], ["lucina", "lučina"], ["syr_taveny_smetanito_zeletava", "sýr tavený smetanito želetava"], ["syr_a_krup_vesela_krava", "sýr a křup veselá kráva"], ["syr_eidam", "sýr eidam 45% albert"]];
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