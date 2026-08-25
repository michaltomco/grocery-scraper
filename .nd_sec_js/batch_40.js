var __ROWS = [["rohlik_mlynarsky_zitny", "rohlík"], ["snek_s_naplni", "šnek"], ["toustovy_chleb", "chléb toust"], ["bebe_brumik_opavia", "sušenky"], ["bonbony_bon_pari_nestle", "bonbony"], ["oplatky_horalky_sedita", "sušenky"], ["prazene_pistacie", "pistácie"], ["bonboniera_merci", "bonbony"]];
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