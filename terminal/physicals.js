/* Front-page board: every physical commodity in one table. Output, who mines it,
   who processes it, how concentrated it is, and the quoted price where one exists. */
"use strict";
(() => {
  const { F, esc } = T;
  const QUOTE = { crude_oil: "CL=F", natural_gas: "NG=F", copper: "HG=F", aluminum: "ALI=F", gold: "GC=F", silver: "SI=F", platinum: "PL=F", palladium: "PA=F" };
  T.app("physicals", { title: "Physicals Board", group: "Supply chain", w: 8, h: 8, open(win) {
    const body = win.add(`<div class="body"></div>`);
    win.add(`<div class="status"><div>Click a row to load that commodity on the map and sheet. HHI above 2,500 = highly concentrated. Price = front future, where quoted.</div></div>`);
    Promise.all([T.model(), T.load("market")]).then(([m, mk]) => {
      if (m.empty) { body.innerHTML = `<div class="pad">commodities.json missing. Run scripts/build_commodities.py</div>`; return; }
      const px = Object.fromEntries(((mk && mk.series) || []).map((s) => [s.symbol, s]));
      const top = (l) => (l || []).filter((x) => x.iso3 !== "OTH").sort((a, b) => (b.share || 0) - (a.share || 0))[0];
      const rows = m.list.map((c) => { const p = top(c.producers.slice()), r = top((c.refining || []).slice()), s = px[QUOTE[c.id]], prev = c.world_production_prev;
        return { id: c.id, name: c.name, group: c.group, out: c._tot, unit: c.unit, yoy: prev && c._tot ? c._tot / prev - 1 : null, p: p ? m.name(p.iso3) : "", ps: p ? p.share : null,
          r: r ? m.name(r.iso3) : "", rs: r ? r.share : null, top3: c._top3, hhi: c._hhi, last: s ? s.last : null, chg: s ? s.chg_1d : null, yr: c.year }; });
      T.table(body, [{ k: "name", t: "Commodity", l: 1, f: (x) => `<b>${esc(x)}</b>` }, { k: "out", t: "World output", f: F.big }, { k: "unit", t: "Unit", l: 1, c: () => "mut" }, { k: "yoy", t: "YoY", f: (x) => F.pct(x), c: F.cls },
        { k: "p", t: "Top producer", l: 1 }, { k: "ps", t: "Share", f: (x) => F.share(x, 0) }, { k: "r", t: "Top processor", l: 1 }, { k: "rs", t: "Share", f: (x) => F.share(x, 0) },
        { k: "top3", t: "Top 3", f: (x) => F.share(x, 0) }, { k: "hhi", t: "HHI", f: (x) => F.n(x, 0), c: (x) => (x > 2500 ? "dn" : "") },
        { k: "last", t: "Price", f: F.auto }, { k: "chg", t: "1d", f: (x) => F.pct(x), c: F.cls }, { k: "yr", t: "Yr", f: (x) => x }],
        rows, { onRow: (r) => T.setCtx("commodity", r.id, win) });
    });
  } });
})();
