/* Quote board, price chart, oil balance. */
"use strict";
(() => {
  const { F, esc } = T;
  const GROUPS = ["energy", "base metals", "precious metals", "macro", "equities + ETFs"];

  T.app("quotes", { title: "Quote Board", group: "Markets", w: 5, h: 10, open(win) {
    const body = win.add(`<div class="body in"></div>`); const st = win.add(`<div class="status"><div class="thin">Click a row to chart it.</div><div class="thin" id="qb-asof"></div></div>`);
    T.loadAll("market", "options").then(([m, o]) => { if (!m) { body.innerHTML = `<div class="pad">market_data.json missing. Run scripts/market_snapshot.py</div>`; return; }
      const has = new Set(o ? o.underlyings.map((u) => u.symbol) : []);
      const rows = []; for (const g of GROUPS) { const rs = m.series.filter((s) => s.group === g); if (!rs.length) continue; rows.push({ _hd: g.toUpperCase() });
        rs.forEach((s) => rows.push({ ...s, pos: (s.last - s.lo_52w) / ((s.hi_52w - s.lo_52w) || 1), rv20: s.rv["20"], rv60: s.rv["60"], opt: has.has(s.symbol) })); }
      T.table(body, [{ k: "symbol", t: "Symbol", l: 1, f: (v) => `<b>${esc(v)}</b>` }, { k: "name", t: "Name", l: 1, f: (v, r) => esc(v) + (r.proxy_for ? ` <span class="mut">(${esc(r.proxy_for)})</span>` : "") },
        { k: "last", t: "Last", f: (v) => F.auto(v) }, { k: "unit", t: "Unit", l: 1, c: () => "mut" }, { k: "chg_1d", t: "1d", f: (v) => F.pct(v, 2), c: F.cls }, { k: "chg_1m", t: "1m", f: (v) => F.pct(v), c: F.cls }, { k: "chg_1y", t: "1y", f: (v) => F.pct(v), c: F.cls },
        { k: "lo_52w", t: "52w lo", f: (v) => F.auto(v), c: () => "mut" }, { k: "pos", t: "range", bar: true }, { k: "hi_52w", t: "52w hi", f: (v) => F.auto(v), c: () => "mut" },
        { k: "rv20", t: "RV20", f: (v) => v ? F.n(v * 100, 0) : "" }, { k: "rv60", t: "RV60", f: (v) => v ? F.n(v * 100, 0) : "" }, { k: "opt", t: "Opt", f: (v) => v ? "●" : "" }],
        rows, { nosort: true, onRow: (r) => T.open("chart", r.symbol) });
      win.el.querySelector("#qb-asof").textContent = "close " + m.series[0].dates.slice(-1)[0] + " · Yahoo"; });
  } });

  T.app("chart", { title: "Chart", group: "Markets", w: 6, h: 9, open(win, arg) {
    win.add(`<div class="toolbar"><label>Symbol <select id="ch-y"></select></label><span id="ch-r">${["3m", "6m", "1y", "2y"].map((r) => `<button data-r="${r}"${r === "1y" ? ' class="down"' : ""}>${r}</button>`).join("")}</span>
      <label><input type="checkbox" id="ch-log">log</label><label>Overlay <select id="ch-o"><option value="">none</option></select></label><button id="ch-opt" hidden>Options ▸</button><button id="ch-wb">Data ▸</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`);
    const a = win.add(`<div style="flex:2.2;min-height:0"></div>`), b = win.add(`<div class="in" style="flex:1;min-height:0"></div>`);
    const q = (x) => win.el.querySelector(x); let M, O, s, range = "1y";
    T.loadAll("market", "options").then(([m, o]) => { if (!m) { lcd.textContent = "market_data.json missing. Run scripts/market_snapshot.py"; return; } M = m; O = o;
      const groups = [...new Set(m.series.map((x) => x.group))], opts = groups.map((g) => `<optgroup label="${esc(g)}">${m.series.filter((x) => x.group === g).map((x) => `<option value="${esc(x.symbol)}">${esc(x.symbol)}  ${esc(x.name)}</option>`).join("")}</optgroup>`).join("");
      q("#ch-y").innerHTML = opts; q("#ch-o").innerHTML = `<option value="">none</option>` + opts;
      set(arg && m.series.some((x) => x.symbol === arg) ? arg : "CL=F"); });
    win.onArg = (sym) => { if (M && sym && M.series.some((x) => x.symbol === sym)) set(sym); };
    function set(sym) { s = M.series.find((x) => x.symbol === sym) || M.series[0]; q("#ch-y").value = s.symbol; win.setArg(s.symbol); win.setTitle(`Chart - ${s.symbol} ${s.name}`);
      const has = O && O.underlyings.some((u) => u.symbol === s.symbol); q("#ch-opt").hidden = !has; draw(); }
    const dfmt = (v) => new Date(v).toLocaleDateString("en-US", { month: "short", year: "2-digit" }), N = { "3m": 63, "6m": 126, "1y": 252, "2y": 9999 };
    function draw() { if (!s) return; const t = s.dates.map((d) => Date.parse(d)), k = Math.max(0, t.length - N[range]), lg = q("#ch-log").checked, ov = M.series.find((x) => x.symbol === q("#ch-o").value && x.symbol !== s.symbol);
      const rv = s.close.map((_, i) => { if (i < 20) return NaN; let sm = 0, sq = 0; for (let j = i - 19; j <= i; j++) { const r = Math.log(s.close[j] / s.close[j - 1]); sm += r; sq += r * r; } return Math.sqrt((sq - sm * sm / 20) / 19 * 252) * 100; });
      const ser = [{ pts: t.slice(k).map((x, i) => [x, lg ? Math.log(s.close[k + i]) : s.close[k + i]]), color: T.C[0], w: 1.6, name: s.symbol }];
      if (ov) { const map = new Map(ov.dates.map((d, i) => [d, ov.close[i]])), b0 = map.get(s.dates[k]) || ov.close[0], a0 = s.close[k];
        ser.push({ pts: s.dates.slice(k).map((d, i) => [t[k + i], map.has(d) ? (lg ? Math.log(map.get(d) / b0 * a0) : map.get(d) / b0 * a0) : NaN]), color: T.C[1], name: ov.symbol + " (rebased)" }); }
      const hov = (host, f) => { host.onmousemove = (ev) => { if (!host._sx) return; const x = host._sx.ix(ev.offsetX); let i = t.findIndex((v) => v >= x); if (i < 0) i = t.length - 1; T.tip(ev, f(i)); }; host.onmouseleave = () => T.tip({}, null); };
      T.canvas(a, (g, w, h) => { a._sx = T.plot(g, w, h, ser, { legend: true, xfmt: dfmt, yfmt: (v) => F.auto(lg ? Math.exp(v) : v) }); });
      T.canvas(b, (g, w, h) => { b._sx = T.plot(g, w, h, [{ pts: t.slice(k).map((x, i) => [x, rv[k + i]]), color: T.C[1], name: "20-day realized vol, %" }], { legend: true, xfmt: dfmt, yfmt: (v) => F.n(v, 0) }); });
      hov(a, (i) => `${s.dates[i]}\n${s.symbol} ${F.auto(s.close[i])}`); hov(b, (i) => `${s.dates[i]}\nRV20 ${F.n(rv[i], 1)}%`);
      const c0 = s.close[k], c1 = s.close[s.close.length - 1], seg = s.close.slice(k);
      lcd.innerHTML = `<div class="kvs"><div><span>last · ${esc(s.unit)}</span><b>${F.auto(c1)}</b></div><div><span>1 day</span><b class="${s.chg_1d < 0 ? "r" : ""}">${F.pct(s.chg_1d, 2)}</b></div><div><span>${range} change</span><b class="${c1 < c0 ? "r" : ""}">${F.pct(c1 / c0 - 1)}</b></div>
        <div><span>${range} high</span><b>${F.auto(Math.max(...seg))}</b></div><div><span>${range} low</span><b>${F.auto(Math.min(...seg))}</b></div><div><span>RV 10 / 20</span><b>${[10, 20].map((x) => s.rv[x] ? F.n(s.rv[x] * 100, 0) : "-").join(" / ")}</b></div><div><span>RV 60 / 120</span><b>${[60, 120].map((x) => s.rv[x] ? F.n(s.rv[x] * 100, 0) : "-").join(" / ")}</b></div><div><span>as of</span><b>${s.dates[s.dates.length - 1]}</b></div></div>`; }
    q("#ch-r").onclick = (ev) => { const bt = ev.target.closest("button"); if (!bt) return; range = bt.dataset.r; q("#ch-r").querySelectorAll("button").forEach((x) => x.classList.toggle("down", x === bt)); draw(); };
    q("#ch-log").onchange = draw; q("#ch-o").onchange = draw; q("#ch-y").onchange = () => set(q("#ch-y").value);
    q("#ch-opt").onclick = () => T.open("chain", s.symbol); q("#ch-wb").onclick = () => T.open("grid", { table: "Prices: history", filter: s.symbol });
  } });

  T.app("balance", { title: "Oil Balance", group: "Models", w: 7, h: 10, open(win) {
    win.add(`<div class="tabs"><button data-t="g" class="on">Global liquids (monthly)</button><button data-t="b">Variance bridge</button><button data-t="u">US crude (weekly)</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`), pane = win.add(`<div class="fill in" style="flex:1;background:#fff"></div>`);
    win.add(`<div class="status"><div class="thin">mb/d · stock build = supply - demand · source EIA STEO + WPSR</div><div class="thin"><a href="balances.html">full page</a></div></div>`);
    T.load("balance").then((D) => { if (!D) { lcd.textContent = "oil_balance.json missing. Run scripts/oil_balance.py"; return; }
      const G = D.global, U = D.us_weekly, M = D.stock_model, iL = G.periods.indexOf(G.last_hist.supply); let tab = "g";
      const pl = (p) => p.slice(0, 4) + "-" + p.slice(4);
      lcd.innerHTML = `<div class="kvs"><div><span>implied build ${pl(G.periods[iL])}</span><b>${F.sgn(G.implied_build[iL])}</b></div><div><span>supply</span><b>${F.n(G.supply_total[iL])}</b></div><div><span>demand</span><b>${F.n(G.demand_total[iL])}</b></div>
        <div><span>OECD stocks mb</span><b>${F.n(G.oecd_stocks[iL], 0)}</b></div><div><span>model σ</span><b class="a">±${F.n(M.sigma)}</b></div><div><span>pass-through β</span><b>${F.n(M.beta)}</b></div>
        <div><span>US build wk ${U.dates.slice(-1)[0].slice(5)}</span><b>${F.sgn(U.build.slice(-1)[0])}</b></div><div><span>vs normal</span><b>${F.sgn(U.build.slice(-1)[0] - U.build_norm.slice(-1)[0])}</b></div><div><span>STEO release</span><b>${D.meta.steo_released}</b></div></div>`;
      function draw() {
        if (tab === "g") { const rows = G.periods.map((p, i) => ({ p: pl(p), st: G.status[i], s: G.supply_total[i], d: G.demand_total[i], b: G.implied_build[i], o: G.oecd_build[i], x: G.unexplained[i], k: G.oecd_stocks[i], br: G.brent[i],
            ...Object.fromEntries(Object.keys(G.supply).map((k) => ["s_" + k, G.supply[k][i]])) })).slice(-48).reverse();
          T.table(pane, [{ k: "p", t: "Month", l: 1 }, { k: "st", t: "", l: 1, c: () => "mut" }, { k: "s", t: "Supply", f: (v) => F.n(v) }, { k: "d", t: "Demand", f: (v) => F.n(v) }, { k: "b", t: "Build", f: (v) => F.sgn(v), c: F.cls },
            { k: "o", t: "OECD obs", f: (v) => F.sgn(v), c: F.cls }, { k: "x", t: "Unexpl.", f: (v) => F.sgn(v), c: () => "mut" }, { k: "k", t: "OECD mb", f: (v) => F.n(v, 0) }, { k: "br", t: "Brent", f: (v) => F.n(v, 1) },
            ...Object.keys(G.supply).map((k) => ({ k: "s_" + k, t: G.labels.supply[k], f: (v) => F.n(v) }))], rows, { nosort: true }); }
        else if (tab === "b") { const rows = []; for (const [side, sg] of [["supply", 1], ["demand", -1]]) { rows.push({ _hd: side.toUpperCase() + " (year on year)" });
            for (const k of Object.keys(G[side])) { const a = G[side][k][iL], b = G[side][k][iL - 12]; rows.push({ n: G.labels[side][k], a, b, d: a - b, e: sg * (a - b) }); } }
          rows.push({ n: "Implied build", a: G.implied_build[iL], b: G.implied_build[iL - 12], d: G.implied_build[iL] - G.implied_build[iL - 12], e: G.implied_build[iL] - G.implied_build[iL - 12], _tot: 1 });
          T.table(pane, [{ k: "n", t: pl(G.periods[iL]) + " vs year ago", l: 1 }, { k: "a", t: "Now", f: (v) => F.n(v) }, { k: "b", t: "Year ago", f: (v) => F.n(v) }, { k: "d", t: "Change", f: (v) => F.sgn(v) }, { k: "e", t: "Effect on build", f: (v) => F.sgn(v), c: F.cls }, { k: (r) => Math.abs(r.e), t: "", bar: 4 }], rows, { nosort: true }); }
        else { const n = U.dates.length, rows = []; for (let i = n - 1; i >= Math.max(0, n - 78); i--) rows.push({ d: U.dates[i], p: U.production[i], m: U.imports[i], x: U.exports[i], r: U.refinery_inputs[i], mb: U.modelled_build[i], a: U.adjustment[i], b: U.build[i], nb: U.build_norm[i], z: (U.build[i] - U.build_norm[i]) / (U.build_sd[i] || NaN), s: U.stocks_comm[i] });
          T.table(pane, [{ k: "d", t: "Week", l: 1 }, { k: "p", t: "Prod", f: (v) => F.n(v) }, { k: "m", t: "Imports", f: (v) => F.n(v) }, { k: "x", t: "Exports", f: (v) => F.n(v) }, { k: "r", t: "Refinery in", f: (v) => F.n(v) }, { k: "mb", t: "Modelled", f: (v) => F.sgn(v), c: F.cls },
            { k: "a", t: "Adjust", f: (v) => F.sgn(v), c: () => "mut" }, { k: "b", t: "Build", f: (v) => F.sgn(v), c: F.cls }, { k: "nb", t: "Normal", f: (v) => F.sgn(v), c: () => "mut" }, { k: "z", t: "z", f: (v) => F.sgn(v, 1), c: F.cls }, { k: "s", t: "Comm. stocks mb", f: (v) => F.n(v, 1) }], rows, { nosort: true }); } }
      win.el.querySelector(".tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b)); draw(); }; draw(); });
  } });
})();
