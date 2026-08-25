var __ROWS = [["bonbony_zele_haribo", "želé"], ["chipsy_lay_s", "chipsy"], ["tatarska_omacka_hellmann_s", "tatarská"], ["dresink_hellmann_s", "dresink"], ["mandle_emma", "mandle"], ["musli_mysli_emco", "müsli"], ["brusinky_klikvy_susene_emma", "brusinky"], ["cerealie_skoricove_ctverecky_emma", "cereálie"]];
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