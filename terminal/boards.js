/* Small information boards: dense tables that summarise one dataset each
   and click through to the detailed widgets. */
"use strict";
(() => {
  const { F, esc } = T;

  /* Implied vs realized across every option underlying. */
  T.app("volmon", { title: "Vol Monitor", group: "Options", w: 6, h: 8, open(win) {
    const body = win.add(`<div class="body"></div>`); win.add(`<div class="status"><div>IV = at-the-money implied, nearest expiry to 30 and 90 days. Click a row for the chain.</div><div id="vm-a"></div></div>`);
    T.load("options").then((D) => { if (!D) { body.innerHTML = `<div class="pad">options_data.json missing. Run scripts/market_snapshot.py</div>`; return; }
      const near = (u, d) => u.expiries.filter((e) => e.atm).reduce((b, e) => (!b || Math.abs(e.dte - d) < Math.abs(b.dte - d) ? e : b), null);
      const rows = D.underlyings.map((u0) => { const u = T.optx.enrich(u0, D.meta.rate), a = near(u, 30), b = near(u, 90), rv = u.rv["20"];
        return { symbol: u.symbol, proxy: u.proxy_for, spot: u.spot, iv30: a ? a.atm : null, d30: a ? a.dte : null, iv90: b && b !== a ? b.atm : null, rv10: u.rv["10"], rv20: rv, rv60: u.rv["60"],
          prem: a && rv ? a.atm - rv : null, ratio: a && rv ? a.atm / rv : null, slope: a && b && b !== a ? b.atm - a.atm : null, move: a ? a.atm * Math.sqrt(a.dte / 365) : null, strad: a && a.straddle ? a.straddle / u.spot : null }; });
      const v = (x) => x == null ? "" : F.n(x * 100, 1);
      T.table(body, [{ k: "symbol", t: "Symbol", l: 1, f: (x) => `<b>${esc(x)}</b>` }, { k: "proxy", t: "Proxy for", l: 1, c: () => "mut" }, { k: "spot", t: "Spot", f: (x) => F.n(x) },
        { k: "iv30", t: "IV ~30d", f: v, tip: "at-the-money implied vol, expiry nearest 30 days" }, { k: "iv90", t: "IV ~90d", f: v }, { k: "slope", t: "Term", f: (x) => x == null ? "" : F.sgn(x * 100, 1), c: F.cls, tip: "90d minus 30d implied: negative = backwardated vol (stress priced near-term)" },
        { k: "rv10", t: "RV10", f: v }, { k: "rv20", t: "RV20", f: v }, { k: "rv60", t: "RV60", f: v },
        { k: "prem", t: "IV-RV20", f: (x) => x == null ? "" : F.sgn(x * 100, 1), c: F.cls, tip: "volatility risk premium: positive = options rich to recent realized" }, { k: "ratio", t: "IV/RV", f: (x) => x == null ? "" : F.n(x, 2) },
        { k: "move", t: "±1σ to exp", f: (x) => x == null ? "" : F.n(x * 100, 1) + "%" }, { k: "strad", t: "Straddle", f: (x) => x == null ? "" : F.n(x * 100, 1) + "%", tip: "at-the-money straddle price as a percent of spot" }],
        rows, { sort: 9, onRow: (r) => T.open("chain", r.symbol) });
      win.el.querySelector("#vm-a").textContent = "quotes " + D.meta.as_of; });
  } });

  /* Who controls what: one row per commodity. */
  T.app("concentration", { title: "Supply Concentration", group: "Supply chain", w: 6, h: 8, open(win) {
    const body = win.add(`<div class="body"></div>`); win.add(`<div class="status"><div>HHI above 2,500 = highly concentrated. Click a row to load that commodity everywhere in this tab.</div></div>`);
    T.model().then((m) => { if (m.empty) { body.innerHTML = `<div class="pad">commodities.json missing. Run scripts/build_commodities.py</div>`; return; }
      const rows = m.list.filter((c) => c.producers.length).map((c) => { const t = c.producers.find((p) => p.iso3 !== "OTH"), r = (c.refining || []).slice().sort((a, b) => (b.share || 0) - (a.share || 0))[0];
        return { id: c.id, name: c.name, group: c.group, top: t ? m.name(t.iso3) : "", share: t ? t.share : null, top3: c._top3, hhi: c._hhi, ref: r ? m.name(r.iso3) : "", refs: r ? r.share : null, rp: c.world_reserves && c._tot ? c.world_reserves / c._tot : null, us: c.us_net_import_reliance ?? null, yr: c.year }; });
      T.table(body, [{ k: "name", t: "Commodity", l: 1, f: (x) => `<b>${esc(x)}</b>` }, { k: "group", t: "Group", l: 1, c: () => "mut" }, { k: "top", t: "Top producer", l: 1 }, { k: "share", t: "Share", f: (x) => F.share(x, 0) }, { k: "share", t: "", bar: true },
        { k: "top3", t: "Top 3", f: (x) => F.share(x, 0) }, { k: "hhi", t: "HHI", f: (x) => F.n(x, 0), c: (x) => (x > 2500 ? "dn" : "") }, { k: "ref", t: "Top processor", l: 1 }, { k: "refs", t: "Share", f: (x) => F.share(x, 0) },
        { k: "rp", t: "Reserve yrs", f: (x) => x ? F.n(x, 0) : "" }, { k: "us", t: "US import rel.", f: (x) => x == null ? "" : F.share(x, 0) }, { k: "yr", t: "Yr", f: (x) => x }], rows, { sort: 6, onRow: (r) => T.open("commodity", r.id) }); });
  } });

  /* Biggest moves across everything quoted. */
  T.app("movers", { title: "Movers", group: "Markets", w: 3, h: 8, open(win) {
    win.add(`<div class="toolbar"><span id="mv-r">${[["chg_1d", "1 day"], ["chg_1m", "1 month"], ["chg_1y", "1 year"]].map(([k, l], i) => `<button data-k="${k}"${i === 0 ? ' class="down"' : ""}>${l}</button>`).join("")}</span></div>`);
    const body = win.add(`<div class="body"></div>`); let key = "chg_1d", M;
    const draw = () => { const rows = M.series.filter((s) => s[key] != null && s.group !== "macro").slice().sort((a, b) => b[key] - a[key]), mx = Math.max(...rows.map((r) => Math.abs(r[key])), 1e-9);
      T.table(body, [{ k: "symbol", t: "Symbol", l: 1, f: (x) => `<b>${esc(x)}</b>` }, { k: "name", t: "Name", l: 1 }, { k: "last", t: "Last", f: (x) => F.auto(x) }, { k: key, t: "Chg", f: (x) => F.pct(x, key === "chg_1d" ? 2 : 1), c: F.cls }, { k: (r) => Math.abs(r[key]), t: "", bar: mx }],
        rows, { nosort: true, onRow: (r) => T.open("chart", r.symbol) }); };
    T.load("market").then((m) => { if (!m) { body.innerHTML = `<div class="pad">market_data.json missing.</div>`; return; } M = m; draw(); });
    win.el.querySelector("#mv-r").onclick = (e) => { const b = e.target.closest("button"); if (!b || !M) return; key = b.dataset.k; win.el.querySelectorAll("#mv-r button").forEach((x) => x.classList.toggle("down", x === b)); draw(); };
  } });

  /* Largest physical trade routes for the linked commodity. */
  const FLOW_KIND = { crude_oil: ["crude", "products"], natural_gas: ["lng", "pipe_gas"], coal: ["coal"], uranium: ["uranium"] };
  T.app("routes", { title: "Trade Routes", group: "Supply chain", w: 5, h: 8, open(win, arg) {
    win.add(`<div class="toolbar"><label>Flow <select id="rt-k"><option value="crude">Crude oil</option><option value="products">Refined products</option><option value="lng">LNG</option><option value="pipe_gas">Pipeline gas</option><option value="coal">Coal</option><option value="uranium">Uranium</option></select></label><span id="rt-n" class="mut"></span></div>`);
    const body = win.add(`<div class="body"></div>`); win.add(`<div class="status"><div>Calendar 2025 baseline, before the 2026 Hormuz disruption. Largest inter-regional routes, not total trade.</div></div>`);
    const sel = win.el.querySelector("#rt-k"); let FL;
    const draw = () => { const hub = Object.fromEntries(FL.hubs.map((h) => [h.id, h.name.replace(/\s*\(.*\)$/, "")])), ck = Object.fromEntries(FL.chokepoints.map((c) => [c.id, c.name.replace(/^Strait of /, "").replace(/ Canal.*| Straits.*| \(.*/, "")]));
      const rows = FL.flows.filter((f) => f.commodity === sel.value), tot = rows.reduce((s, r) => s + r.volume, 0), mx = Math.max(...rows.map((r) => r.volume), 1e-9); win.setArg(sel.value);
      T.table(body, [{ k: (r) => hub[r.from], t: "From", l: 1 }, { k: (r) => hub[r.to], t: "To", l: 1 }, { k: "volume", t: rows[0] ? rows[0].unit : "", f: (x) => F.auto(x) }, { k: "volume", t: "", bar: mx }, { k: (r) => r.volume / tot, t: "Of mapped", f: (x) => F.share(x, 0) },
        { k: (r) => (r.via || []).map((v) => ck[v] || v).join(", "), t: "Via", l: 1, c: () => "mut" }, { k: "year", t: "Yr", f: (x) => x }, { k: "confidence", t: "Conf", l: 1, c: (x) => (x === "low" ? "dn" : "mut") }], rows, { sort: 2 });
      win.el.querySelector("#rt-n").textContent = `${rows.length} routes · ${F.auto(tot)} ${rows[0] ? rows[0].unit : ""} mapped`; };
    T.load("flows").then((f) => { if (!f) { body.innerHTML = `<div class="pad">flows_data.json missing.</div>`; return; } FL = f;
      const want = arg || (FLOW_KIND[T.ctx.commodity] || [])[0]; if (want && [...sel.options].some((o) => o.value === want)) sel.value = want; draw(); });
    sel.onchange = () => FL && draw();
    win.onCtx = (k, v) => { if (k === "commodity" && FL && FLOW_KIND[v] && !FLOW_KIND[v].includes(sel.value)) { sel.value = FLOW_KIND[v][0]; draw(); } };
  } });

  T.app("chokepoints", { title: "Chokepoints", group: "Supply chain", w: 5, h: 7, open(win) {
    const body = win.add(`<div class="body"></div>`); win.add(`<div class="status"><div>Oil transit, EIA first-half 2025 averages. Exposure columns sum the mapped routes that pass each point.</div></div>`);
    T.load("flows").then((FL) => { if (!FL) { body.innerHTML = `<div class="pad">flows_data.json missing.</div>`; return; }
      const sum = (id, k) => FL.flows.filter((f) => f.commodity === k && (f.via || []).includes(id)).reduce((s, f) => s + f.volume, 0), tot = (k) => FL.flows.filter((f) => f.commodity === k).reduce((s, f) => s + f.volume, 0) || 1;
      const rows = FL.chokepoints.map((c) => ({ name: c.name, oil: c.oil_mbd ?? null, crude: sum(c.id, "crude"), cs: sum(c.id, "crude") / tot("crude"), lng: sum(c.id, "lng"), ls: sum(c.id, "lng") / tot("lng"), coal: sum(c.id, "coal"), note: c.note || "" }));
      T.table(body, [{ k: "name", t: "Chokepoint", l: 1, f: (x) => `<b>${esc(x)}</b>` }, { k: "oil", t: "Oil transit mb/d", f: (x) => x == null ? "" : F.n(x, 1) }, { k: "oil", t: "", bar: Math.max(...rows.map((r) => r.oil || 0), 1) },
        { k: "crude", t: "Mapped crude", f: (x) => x ? F.n(x, 2) : "" }, { k: "cs", t: "Share", f: (x) => x ? F.share(x, 0) : "" }, { k: "lng", t: "Mapped LNG mtpa", f: (x) => x ? F.n(x, 1) : "" }, { k: "ls", t: "Share", f: (x) => x ? F.share(x, 0) : "" },
        { k: "coal", t: "Coal Mt", f: (x) => x ? F.n(x, 0) : "" }, { k: "note", t: "Note", l: 1, c: () => "mut" }], rows, { sort: 1 }); });
  } });
})();
