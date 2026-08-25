var TERMSX = [["kecup_gurman_otma", "kečup gurmán otma"], ["kecup_heinz", "kečup heinz"], ["cerealie_nestle", "cereálie nestlé"], ["mandle_emma", "mandle emma"], ["musli_mysli_emco", "müsli mysli emco"], ["kase_ovesna_emco", "kaše ovesná emco"], ["musli_bonavita", "müsli bonavita"], ["brusinky_klikvy_susene_emma", "brusinky klikvy sušené emma"]];
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