/* Data workbench: every dataset in the terminal as a table you can filter,
   extend with formulas, group, chart and export, without leaving the tool. */
"use strict";
(() => {
  const { F, esc } = T;
  let catP = null;
  /* Catalog: name -> {rows, note}. Column keys are identifiers so they can be
     used directly in formulas. */
  T.catalog = () => catP ||= Promise.all([T.model(), T.loadAll("market", "flows", "balance", "options", "atlas", "signals")]).then(([Mo, [M, FL, B, O, A, S]]) => {
    const cat = {};
    if (Mo && !Mo.empty) { const nm = Mo.name;
      cat["Production by country"] = { note: "commodities.json · USGS MCS, EIA, WNA", rows: Mo.list.flatMap((c) => c.producers.map((p) => ({ commodity: c.name, group: c.group, country: nm(p.iso3), iso3: p.iso3, year: c.year, production: p.production, unit: c.unit, share: p.share, rank: p.rank, prev: p.prev ?? null, yoy: p.yoy, reserves: p.reserves ?? null, reserves_share: p.reserves && c.world_reserves ? p.reserves / c.world_reserves : null }))) };
      cat["Commodity summary"] = { note: "one row per commodity", rows: Mo.list.map((c) => { const t = c.producers.find((p) => p.iso3 !== "OTH"); return { commodity: c.name, group: c.group, year: c.year, world_production: c._tot, unit: c.unit, top_producer: t ? nm(t.iso3) : "", top_share: t ? t.share : null, top3_share: c._top3, hhi: Math.round(c._hhi), world_reserves: c.world_reserves ?? null, reserve_years: c.world_reserves && c._tot ? c.world_reserves / c._tot : null, us_import_reliance: c.us_net_import_reliance ?? null, n_producers: c.producers.length }; }) };
      cat["Consumption by country"] = { rows: Mo.list.flatMap((c) => (c.consumers || []).map((p) => { const pr = c.producers.find((x) => x.iso3 === p.iso3); return { commodity: c.name, country: nm(p.iso3), iso3: p.iso3, year: c.year, consumption: p.consumption, production: pr ? pr.production : 0, net: (pr ? pr.production : 0) - p.consumption, unit: c.unit, share: p.share }; })) };
      cat["Processing shares"] = { rows: Mo.list.flatMap((c) => (c.refining || []).map((r) => ({ commodity: c.name, country: nm(r.iso3), iso3: r.iso3, stage: r.stage, share: r.share, year: r.year ?? null, confidence: r.confidence || "", source: r.source || "" }))) };
      cat["Trade links"] = { rows: Mo.list.flatMap((c) => (c.trade || []).map((r) => ({ commodity: c.name, from: nm(r.from), to: nm(r.to), share: r.share ?? null, what: r.what || "", year: r.year ?? null, confidence: r.confidence || "" }))) };
      cat["End uses"] = { rows: Mo.list.flatMap((c) => (c.end_uses || []).map((r) => ({ commodity: c.name, use: r.use, share: r.share, basis: c.end_use_basis || "" }))) };
      if (Mo.fac.length) cat["Facilities"] = { note: "infra_facilities.geojson", rows: Mo.fac.map((f) => ({ name: f.properties.name, kind: f.properties.kind, commodity: f.properties.commodity, country: f.properties.country ? nm(f.properties.country) : "", capacity: f.properties.capacity ?? null, capacity_unit: f.properties.capacity_unit || "", operator: f.properties.operator || "", status: f.properties.status || "", origin: f.properties.origin, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] })) };
      if (Mo.pipes.length) cat["Pipelines"] = { note: "infra_pipelines.geojson", rows: Mo.pipes.map((f) => ({ name: f.properties.name, commodity: f.properties.commodity, status: f.properties.status || "", capacity: f.properties.capacity ?? null, capacity_unit: f.properties.capacity_unit || "", operator: f.properties.operator || "", countries: (f.properties.countries || []).join(" "), origin: f.properties.origin })) }; }
    if (M) { cat["Prices: latest"] = { note: "market_data.json · Yahoo", rows: M.series.map((s) => ({ symbol: s.symbol, name: s.name, group: s.group, last: s.last, unit: s.unit, chg_1d: s.chg_1d, chg_1m: s.chg_1m, chg_1y: s.chg_1y, lo_52w: s.lo_52w, hi_52w: s.hi_52w, rv10: s.rv["10"], rv20: s.rv["20"], rv60: s.rv["60"], rv120: s.rv["120"] })) };
      cat["Prices: history"] = { note: "daily closes, 2 years", rows: M.series.flatMap((s) => s.dates.map((d, i) => ({ date: d, symbol: s.symbol, close: s.close[i], ret: i ? s.close[i] / s.close[i - 1] - 1 : null }))) }; }
    if (O) cat["Option chains"] = { note: "options_data.json · IV solved from mid", rows: O.underlyings.flatMap((u) => u.expiries.flatMap((e) => e.rows.flatMap((r) => ["c", "p"].map((ty) => { const b = r[ty + "b"], a = r[ty + "a"], mid = b > 0 && a > 0 ? (a + b) / 2 : r[ty + "l"] || null, Tm = e.dte / 365;
      const iv = mid ? BS.impliedVol(mid, u.spot, r.k, Tm, O.meta.rate, u.div_yield, ty) : null, g = iv ? BS.greeks(u.spot, r.k, Tm, O.meta.rate, u.div_yield, iv, ty) : {};
      return { symbol: u.symbol, expiry: e.date, dte: e.dte, type: ty === "c" ? "call" : "put", strike: r.k, moneyness: r.k / u.spot, bid: b ?? null, ask: a ?? null, mid, iv, delta: g.delta ?? null, gamma: g.gamma ?? null, vega: g.vega != null ? g.vega / 100 : null, theta_day: g.theta != null ? g.theta / 365 : null, oi: r[ty + "oi"] || 0, volume: r[ty + "v"] || 0, spot: u.spot }; })))) };
    if (FL) { const hub = Object.fromEntries(FL.hubs.map((h) => [h.id, h.name]));
      cat["Trade routes"] = { note: "flows_data.json · 2025 baseline", rows: FL.flows.map((f) => ({ commodity: f.commodity, from: hub[f.from], to: hub[f.to], volume: f.volume, unit: f.unit, year: f.year, mode: f.mode || "", via: (f.via || []).join(" "), confidence: f.confidence })) };
      cat["Chokepoints"] = { rows: FL.chokepoints.map((c) => ({ name: c.name, oil_mbd: c.oil_mbd ?? null, year: c.year, note: c.note || "" })) }; }
    if (B) { const G = B.global, U = B.us_weekly;
      cat["Oil balance: global monthly"] = { note: "oil_balance.json · EIA STEO, mb/d", rows: G.periods.map((p, i) => ({ month: p.slice(0, 4) + "-" + p.slice(4), status: G.status[i], supply: G.supply_total[i], demand: G.demand_total[i], implied_build: G.implied_build[i], oecd_build: G.oecd_build[i], unexplained: G.unexplained[i], oecd_stocks: G.oecd_stocks[i], brent: G.brent[i], wti: G.wti[i],
        ...Object.fromEntries(Object.keys(G.supply).map((k) => ["sup_" + k, G.supply[k][i]])), ...Object.fromEntries(Object.keys(G.demand).map((k) => ["dem_" + k, G.demand[k][i]])) })) };
      cat["Oil balance: US weekly"] = { note: "EIA WPSR, mb/d", rows: U.dates.map((d, i) => ({ week: d, production: U.production[i], imports: U.imports[i], exports: U.exports[i], refinery_inputs: U.refinery_inputs[i], modelled_build: U.modelled_build[i], adjustment: U.adjustment[i], build: U.build[i], build_norm: U.build_norm[i], stocks_comm: U.stocks_comm[i] })) }; }
    if (A) cat["Equity baskets"] = { note: "dashboard_data.json", rows: A.nodes.map((n) => ({ basket: n.display_name, layer: n.layer, names: n.n_constituents, dominant_driver: n.dominant_driver, r2: n.r2, intra_corr: n.intra_corr ? n.intra_corr.mean : null, iv_gap_4w: n.ivr ? n.ivr.gap_4w : null, ...Object.fromEntries(Object.entries(n.basket_loadings || {}).map(([k, v]) => ["beta_" + k.toLowerCase(), v])) })) };
    if (S && S.top) cat["Equity signals"] = { note: "signals_latest.json", rows: S.top };
    return cat;
  });

  const isNum = (v) => typeof v === "number" && isFinite(v);
  const fmtCell = (k) => (v) => v == null ? "" : !isNum(v) ? esc(v) : /share|yoy|chg_|^ret$|reliance|moneyness|^iv$|^rv\d|gap|self/.test(k) && Math.abs(v) < 20 ? (/(yoy|chg_|^ret$|gap)/.test(k) ? F.pct(v, 2) : F.n(v * 100, 2) + "%") : /year|rank|^dte$|names|hhi|^oi$|volume/.test(k) ? String(Math.round(v)) : F.auto(v);
  const HELP = `Formulas are JavaScript over column names, one row at a time:
  production / reserves            share > 0.1 && group == "battery metals"
  log(close)   abs(x)  round(x, 2)  min(a, b)  max(a, b)  sqrt(x)  exp(x)
Column functions take a quoted column name and see all filtered rows:
  Z("production")   z-score         RANK("share")    1 = largest
  SHARE("volume")   fraction of sum CUM("build")     running total
  LAG("close", 1)   n rows back     PCT("close", 5)  change over n rows
  MA("close", 20)   moving average  SD("ret", 20)    rolling st. dev.
Row order for LAG/PCT/CUM/MA/SD is the table's order; filter to one series first.`;

  T.app("grid", { title: "Data Workbench", group: "Tools", w: 12, h: 14, open(win, arg) {
    win.add(`<div class="toolbar"><label>Table <select id="wb-t"></select></label><label>Find <input type="text" id="wb-f" placeholder="text" style="width:110px"></label>
      <label>Where <input type="text" id="wb-w" placeholder='share > 0.1 && year == 2024' style="width:230px"></label>
      <label>Group by <select id="wb-g"><option value="">(none)</option></select></label><select id="wb-a"><option>sum</option><option>mean</option><option>max</option><option>min</option><option>count</option></select></div>`);
    win.add(`<div class="toolbar"><label>New column <input type="text" id="wb-cn" placeholder="name" style="width:80px"> = <input type="text" id="wb-ce" placeholder='production / reserves   or   Z("share")' style="width:270px"></label><button id="wb-add">Add</button><span id="wb-cols"></span>
      <span style="flex:1"></span><button id="wb-help">?</button><button id="wb-copy">Copy</button><button id="wb-csv">CSV</button></div>`);
    win.add(`<div class="toolbar"><label>Chart: X <select id="wb-x"></select></label><label>Y <select id="wb-y"></select></label><label>Series <select id="wb-s"><option value="">(none)</option></select></label>
      <select id="wb-k"><option value="auto">auto</option><option value="line">line</option><option value="bar">bar</option><option value="scatter">scatter</option></select><button id="wb-ch">Chart</button><button id="wb-hide" hidden>Hide chart</button></div>`);
    const chart = win.add(`<div class="in" style="height:220px;flex:none;background:#fff;display:none"></div>`);
    const pane = win.add(`<div class="fill in" style="flex:1;background:#fff"></div>`);
    win.add(`<div class="status"><div class="thin" id="wb-st"></div><div class="thin" id="wb-agg" style="min-width:300px"></div></div>`);
    const q = (s) => win.el.querySelector(s); let CAT, base = [], view = [], cols = [], computed = {}, charted = false;
    T.catalog().then((c) => { CAT = c; const names = Object.keys(c); if (!names.length) { pane.innerHTML = `<div class="pad">No datasets loaded.</div>`; return; }
      q("#wb-t").innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join(""); if (arg) apply(arg); else pick(); });
    win.onArg = (a) => { if (CAT && a) apply(a); };
    function apply(a) { if (a.table && CAT[a.table]) q("#wb-t").value = a.table; q("#wb-f").value = a.filter || ""; pick(); win.setArg({ table: q("#wb-t").value, filter: a.filter || "" }); }
    function pick() { const t = CAT[q("#wb-t").value]; base = t.rows; computed = {}; q("#wb-w").value = ""; q("#wb-cols").innerHTML = "";
      const ks = Object.keys(base[0] || {}); q("#wb-g").innerHTML = `<option value="">(none)</option>` + ks.filter((k) => base.some((r) => typeof r[k] === "string")).map((k) => `<option>${k}</option>`).join("");
      win.setTitle("Data Workbench - " + q("#wb-t").value); run(true); }
    function colFns(rows) { const col = (k) => rows.map((r) => r[k]), memo = {}, m = (key, f) => (memo[key] ||= f());
      const stats = (k) => m("st" + k, () => { const v = col(k).filter(isNum), mu = v.reduce((a, b) => a + b, 0) / (v.length || 1), sd = Math.sqrt(v.reduce((a, b) => a + (b - mu) ** 2, 0) / Math.max(1, v.length - 1)); return { mu, sd, sum: v.reduce((a, b) => a + b, 0) }; });
      return (i) => ({ Z: (k) => { const s = stats(k); return isNum(rows[i][k]) && s.sd ? (rows[i][k] - s.mu) / s.sd : null; }, SHARE: (k) => { const s = stats(k); return isNum(rows[i][k]) && s.sum ? rows[i][k] / s.sum : null; },
        RANK: (k) => { const o = m("rk" + k, () => { const idx = rows.map((r, j) => [r[k], j]).filter((x) => isNum(x[0])).sort((a, b) => b[0] - a[0]), out = {}; idx.forEach((x, n) => (out[x[1]] = n + 1)); return out; }); return o[i] ?? null; },
        CUM: (k) => m("cu" + k, () => { let s = 0; return rows.map((r) => (s += isNum(r[k]) ? r[k] : 0)); })[i], LAG: (k, n = 1) => (rows[i - n] ? rows[i - n][k] : null),
        PCT: (k, n = 1) => (rows[i - n] && isNum(rows[i - n][k]) && rows[i - n][k] !== 0 && isNum(rows[i][k]) ? rows[i][k] / rows[i - n][k] - 1 : null),
        MA: (k, n = 20) => { if (i < n - 1) return null; let s = 0; for (let j = i - n + 1; j <= i; j++) { if (!isNum(rows[j][k])) return null; s += rows[j][k]; } return s / n; },
        SD: (k, n = 20) => { if (i < n - 1) return null; const v = []; for (let j = i - n + 1; j <= i; j++) { if (!isNum(rows[j][k])) return null; v.push(rows[j][k]); } const mu = v.reduce((a, b) => a + b, 0) / n; return Math.sqrt(v.reduce((a, b) => a + (b - mu) ** 2, 0) / (n - 1)); } }); }
    const raw = (expr) => new Function("r", "f", "with(Math){with(f){with(r){return (" + expr + ")}}}");
    /* blanks become NaN inside formulas, so a missing input gives a blank result instead of silently counting as zero */
    const blank = (r) => { const o = {}; for (const k in r) o[k] = r[k] == null ? NaN : r[k]; return o; };
    const compile = (expr) => { const fn = raw(expr); return (r, f) => fn(blank(r), f); };
    function run(resetAxes) { const st = q("#wb-st"); let rows = base; const find = q("#wb-f").value.trim().toLowerCase(), where = q("#wb-w").value.trim();
      try {
        if (find) rows = rows.filter((r) => Object.values(r).some((v) => v != null && String(v).toLowerCase().includes(find)));
        if (where) { const fn = compile(where), fs = colFns(rows); rows = rows.filter((r, i) => { try { return !!fn(r, fs(i)); } catch (e) { return false; } }); }
        if (Object.keys(computed).length) { rows = rows.map((r) => ({ ...r }));
          for (const [name, expr] of Object.entries(computed)) { const fn = compile(expr), fs = colFns(rows); rows.forEach((r, i) => { let v; try { v = fn(r, fs(i)); } catch (e) { v = null; } r[name] = typeof v === "number" && !isFinite(v) ? null : v ?? null; }); } }
        const gb = q("#wb-g").value, agg = q("#wb-a").value;
        if (gb) { const groups = new Map(); rows.forEach((r) => { const k = r[gb] ?? ""; (groups.get(k) || groups.set(k, []).get(k)).push(r); });
          const nums = Object.keys(rows[0] || {}).filter((k) => k !== gb && rows.some((r) => isNum(r[k])));
          rows = [...groups].map(([k, rs]) => { const o = { [gb]: k, n: rs.length }; for (const c of nums) { const v = rs.map((r) => r[c]).filter(isNum); if (!v.length) { o[c] = null; continue; }
            o[c] = agg === "sum" ? v.reduce((a, b) => a + b, 0) : agg === "mean" ? v.reduce((a, b) => a + b, 0) / v.length : agg === "max" ? Math.max(...v) : agg === "min" ? Math.min(...v) : v.length; } return o; }); }
      } catch (e) { st.textContent = "Formula error: " + e.message; st.style.color = "#c00000"; return; }
      st.style.color = ""; view = rows; cols = Object.keys(rows[0] || base[0] || {});
      const CAP = 800, shown = rows.slice(0, CAP);
      T.table(pane, cols.map((k) => ({ k, t: k, l: !rows.some((r) => isNum(r[k])), f: fmtCell(k), c: (v) => (isNum(v) && /yoy|chg_|^ret$|build|net|gap|adjust|edge|unexpl/.test(k) ? F.cls(v) : "") })), shown, { onRow: rowClick });
      st.textContent = `${F.n(rows.length, 0)} rows${rows.length > CAP ? ` (showing first ${CAP}; filter or group to narrow)` : ""} of ${F.n(base.length, 0)} · ${cols.length} columns · ${CAT[q("#wb-t").value].note || ""}`;
      const nums = cols.filter((k) => rows.some((r) => isNum(r[k]))), keep = (id, opts, def) => { const el = q(id), cur = el.value; el.innerHTML = opts; if (!resetAxes && [...el.options].some((o) => o.value === cur)) el.value = cur; else if (def != null) el.value = def; };
      const dateCol = cols.find((k) => /^(date|week|month)$/.test(k));
      keep("#wb-x", cols.map((k) => `<option>${k}</option>`).join(""), dateCol || cols[0]); keep("#wb-y", nums.map((k) => `<option>${k}</option>`).join(""), nums.find((k) => /production|close|volume|last|implied_build|build|share|iv/.test(k)) || nums[0]);
      keep("#wb-s", `<option value="">(none)</option>` + cols.filter((k) => !nums.includes(k)).map((k) => `<option>${k}</option>`).join(""), "");
      agg(); if (charted) drawChart(); }
    function agg() { const y = q("#wb-y").value, v = view.map((r) => r[y]).filter(isNum); if (!v.length) { q("#wb-agg").textContent = ""; return; } const s = v.reduce((a, b) => a + b, 0), mu = s / v.length;
      q("#wb-agg").textContent = `${y}: Σ ${F.auto(s)} · avg ${F.auto(mu)} · min ${F.auto(Math.min(...v))} · max ${F.auto(Math.max(...v))} · n ${v.length}`; }
    function rowClick(r) { if (r.iso3 && r.iso3 !== "OTH") T.open("country", r.iso3); else if (r.symbol && CAT["Prices: latest"] && CAT["Prices: latest"].rows.some((x) => x.symbol === r.symbol)) T.open("chart", r.symbol); }
    function drawChart() { const xk = q("#wb-x").value, yk = q("#wb-y").value, sk = q("#wb-s").value; let kind = q("#wb-k").value; charted = true; chart.style.display = "block"; q("#wb-hide").hidden = false;
      const xs = view.map((r) => r[xk]), isDate = xs.every((v) => typeof v === "string" && /^\d{4}-\d{2}/.test(v)), xNum = xs.every(isNum);
      if (kind === "auto") kind = isDate ? "line" : xNum ? "scatter" : "bar";
      const PAL = T.C;
      T.canvas(chart, (g, w, h) => {
        if (kind === "bar") { const rows = view.filter((r) => isNum(r[yk])).slice().sort((a, b) => b[yk] - a[yk]).slice(0, 30), mx = Math.max(...rows.map((r) => Math.abs(r[yk])), 1e-12);
          g.fillStyle = "#fff"; g.fillRect(0, 0, w, h); g.font = "10.5px Tahoma, Arial, sans-serif"; const lw = Math.min(190, w * 0.3), bh = Math.max(6, Math.min(16, (h - 8) / rows.length));
          rows.forEach((r, i) => { const y = 4 + i * bh, len = Math.abs(r[yk]) / mx * (w - lw - 80); g.fillStyle = "#4a5563"; g.textAlign = "right"; g.fillText(String(r[xk] ?? "").slice(0, 28) + (sk ? " " + String(r[sk]).slice(0, 10) : ""), lw - 4, y + bh - 3);
            g.fillStyle = r[yk] >= 0 ? T.C[0] : T.C[3]; g.fillRect(lw, y + 1, Math.max(1, len), bh - 2); g.fillStyle = "#1c232c"; g.textAlign = "left"; g.fillText(fmtCell(yk)(r[yk]), lw + len + 4, y + bh - 3); }); return; }
        const X = (v) => (isDate ? Date.parse(v) : xNum ? v : null), groups = new Map();
        view.forEach((r, i) => { const k = sk ? String(r[sk]) : yk; (groups.get(k) || groups.set(k, []).get(k)).push([X(r[xk]) ?? i, r[yk]]); });
        const ser = [...groups].slice(0, 8).map(([k, pts], i) => ({ pts: pts.sort((a, b) => a[0] - b[0]), color: PAL[i], name: k, dots: kind === "scatter" ? "only" : false }));
        T.plot(g, w, h, ser, { legend: true, xfmt: isDate ? (v) => new Date(v).toLocaleDateString("en-US", { month: "short", year: "2-digit" }) : F.auto, yfmt: (v) => fmtCell(yk)(v).replace(/<[^>]+>/g, "") }); }); }
    win.el.addEventListener("change", (ev) => { const id = ev.target.id; if (id === "wb-t") pick(); else if (/^wb-[fwga]$/.test(id)) run(); else if (/^wb-[xysk]$/.test(id)) { agg(); if (charted) drawChart(); } });
    q("#wb-f").addEventListener("input", () => run()); q("#wb-w").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    const addCol = () => { const n = q("#wb-cn").value.trim().replace(/\W+/g, "_"), e = q("#wb-ce").value.trim(); if (!n || !e) return; computed[n] = e; q("#wb-cn").value = q("#wb-ce").value = "";
      q("#wb-cols").innerHTML = Object.entries(computed).map(([k, v]) => `<button data-del="${esc(k)}" title="${esc(v)} (click to remove)" style="padding:1px 5px">${esc(k)} x</button>`).join(" "); run(); };
    q("#wb-add").onclick = addCol; q("#wb-ce").addEventListener("keydown", (e) => { if (e.key === "Enter") addCol(); });
    q("#wb-cols").onclick = (ev) => { const k = ev.target.dataset.del; if (!k) return; delete computed[k]; ev.target.remove(); run(); };
    q("#wb-ch").onclick = drawChart; q("#wb-hide").onclick = () => { charted = false; chart.style.display = "none"; q("#wb-hide").hidden = true; };
    q("#wb-help").onclick = () => { pane.innerHTML = `<pre style="padding:10px;margin:0;font:11px/1.5 var(--num);user-select:text">${esc(HELP)}</pre>`; };
    q("#wb-csv").onclick = () => T.download(q("#wb-t").value.replace(/\W+/g, "_").toLowerCase() + ".csv", T.csv(view, cols));
    q("#wb-copy").onclick = () => { const tsv = [cols.join("\t")].concat(view.map((r) => cols.map((c) => r[c] ?? "").join("\t"))).join("\n");
      navigator.clipboard.writeText(tsv).then(() => { q("#wb-st").textContent = `Copied ${view.length} rows (tab-separated: paste into a spreadsheet).`; }, () => { q("#wb-st").textContent = "Clipboard blocked by the browser; use CSV."; }); };
  } });
})();
