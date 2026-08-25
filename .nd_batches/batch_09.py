var TERMSX = [["bebe_brumik_opavia", "bebe brumík opavia"], ["bonbony_bon_pari_nestle", "bonbony bon pari nestlé"], ["cokolada_studentska_pecet_orion", "čokoláda studentská pečeť orion"], ["oplatky_horalky_sedita", "oplatky horalky sedita"], ["prazene_pistacie", "pražené pistácie albert"], ["bonboniera_merci", "bonboniéra merci"], ["bonbony_jojo", "bonbony jojo"], ["bonbony_zele_haribo", "bonbony želé haribo"]];
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