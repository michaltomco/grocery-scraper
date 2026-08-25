var TERMSX = [["bageta_cesnekova", "bageta česneková"], ["rohlik_mlynarsky_zitny", "rohlík mlynářský žitný albert"], ["croissant", "croissant"], ["dalamanek", "dalamánek"], ["snek_s_naplni", "šnek s náplní"], ["toustovy_chleb", "toustový chléb albert"], ["bageta_francouzska", "bageta francouzská"], ["chleb_fit_den_penam", "chléb fit den penam"]];
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