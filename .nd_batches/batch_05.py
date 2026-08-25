var TERMSX = [["syr_hermelin_kral_syru", "sýr hermelín král sýrů"], ["syr_hermelin_sedlcansky", "sýr hermelín sedlčanský"], ["syr_mozzarella_galbani", "sýr mozzarella galbani"], ["actimel_danone", "actimel danone"], ["bily_jogurt_reckeho_typu_athentikos_mlekarna_kunin", "bílý jogurt řeckého typu athentikos mlékárna kunín"], ["nanuk_misa", "nanuk míša"], ["pizza_mrazena_ristorante_dr_oetker", "pizza mražená ristorante dr. oetker"], ["zmrzlina_v_kelimku_haagen_dazs", "zmrzlina v kelímku häagen-dazs"]];
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