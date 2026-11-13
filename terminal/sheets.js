/* Commodity and country sheets: who makes what, what share, where it goes,
   who refines it, what it is used for, and what an outage removes. */
"use strict";
(() => {
  const { F, esc } = T;
  let modelP = null;
  /* One indexed view of commodities.json shared by sheets, map and workbench. */
  T.model = () => modelP ||= T.loadAll("commodities", "market", "facilities", "pipelines").then(([C, M, FA, PI]) => {
    const empty = !C; if (empty) C = { meta: {}, countries: {}, commodities: [] };
    const com = {}, byCountry = {}, name = (iso) => (C.countries[iso] ? C.countries[iso].short || C.countries[iso].name : iso === "OTH" ? "Other countries" : iso);
    for (const c of C.commodities) { com[c.id] = c;
      const tot = c.world_production || c.producers.reduce((s, p) => s + (p.production || 0), 0); c._tot = tot;
      c.producers.sort((x, y) => (x.iso3 === "OTH") - (y.iso3 === "OTH") || (y.production || 0) - (x.production || 0));   // named countries first, the USGS 'other' bucket last
      c.producers.forEach((p, i) => { const has = typeof p.production === "number" && isFinite(p.production);   // USGS withholds some figures (company confidential)
        if (!has) p.production = null; p.share = has && tot ? p.production / tot : null; p.rank = i + 1; p.yoy = has && p.prev ? p.production / p.prev - 1 : null;
        if (p.iso3 !== "OTH") (byCountry[p.iso3] ||= []).push({ c, p }); });
      const sh = c.producers.filter((p) => p.iso3 !== "OTH").map((p) => p.share || 0);
      c._hhi = sh.reduce((s, x) => s + (x * 100) ** 2, 0); c._top3 = sh.slice(0, 3).reduce((s, x) => s + x, 0);
      const ct = (c.consumers || []).reduce((s, p) => s + (p.consumption || 0), 0);
      (c.consumers || []).forEach((p) => { if (p.share == null && ct) p.share = p.consumption / ct; }); }
    const px = {}; if (M) for (const s of M.series) px[s.symbol] = s;
    const fac = FA ? FA.features : [], pipes = PI ? PI.features : [];
    return { empty, C, com, list: C.commodities, byCountry, name, px, fac, pipes,
      countries: Object.keys(C.countries).sort((a, b) => name(a).localeCompare(name(b))) };
  });
  const unitFmt = (v) => F.big(v);

  /* ================= commodity ================= */
  T.app("commodity", { title: "Commodity Sheet", group: "Supply chain", w: 7, h: 11, open(win, arg) {
    win.add(`<div class="toolbar"><label>Commodity <select id="cs-c"></select></label>
      <label>Outage: <select id="cs-o"></select></label><label><input type="number" id="cs-p" value="50" min="0" max="100" step="10" style="width:50px"> % offline</label>
      <span id="cs-oi" style="font-family:var(--num)"></span><span style="flex:1"></span><button id="cs-map">Map ▸</button><button id="cs-wb">Data ▸</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`);
    win.add(`<div class="split stack fill" style="flex:1"><div class="fill in" style="flex:1.25" id="cs-l"></div>
      <div class="col" style="flex:1;margin-left:3px"><div class="tabs" id="cs-tabs"></div><div class="fill in" id="cs-r" style="background:#fff"></div></div></div>`);
    win.add(`<div class="status"><div class="thin" id="cs-s"></div></div>`);
    const q = (s) => win.el.querySelector(s); let Mo, c, tab = "refining";
    T.model().then((m) => { if (m.empty) { lcd.textContent = "commodities.json missing. Run scripts/build_commodities.py"; return; } Mo = m;
      const groups = [...new Set(m.list.map((x) => x.group))];
      q("#cs-c").innerHTML = groups.map((g) => `<optgroup label="${esc(g)}">${m.list.filter((x) => x.group === g).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</optgroup>`).join("");
      q("#cs-c").value = arg && m.com[arg] ? arg : T.ctx.commodity && m.com[T.ctx.commodity] ? T.ctx.commodity : m.com.cobalt ? "cobalt" : m.list[0].id; pick(); });
    win.onCtx = (k, v) => { if (k === "commodity" && Mo && Mo.com[v] && q("#cs-c").value !== v) { q("#cs-c").value = v; pick(); } };
    win.onArg = (a) => { if (Mo && a && Mo.com[a]) { q("#cs-c").value = a; pick(); } };
    function pick() { c = Mo.com[q("#cs-c").value]; win.setTitle("Commodity - " + c.name); win.setArg(c.id); T.setCtx("commodity", c.id, win);
      q("#cs-o").innerHTML = c.producers.filter((p) => p.iso3 !== "OTH").map((p) => `<option value="${p.iso3}">${esc(Mo.name(p.iso3))} (${F.share(p.share, 0)})</option>`).join("");
      const tabs = [["refining", "Processing"], ["trade", "Trade"], ["end_uses", "End uses"], ["consumers", "Consumers"], ["notes", "Notes"]].filter(([k]) => k === "notes" || (c[k] && c[k].length));
      if (!tabs.some(([k]) => k === tab)) tab = tabs[0][0];
      q("#cs-tabs").innerHTML = tabs.map(([k, l]) => `<button data-t="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join(""); draw(); }
    function draw() { const res = c.world_reserves, rp = res && c._tot ? res / c._tot : null, top = c.producers.find((p) => p.iso3 !== "OTH");
      const ticks = (c.price_tickers || []).map((t) => { const s = Mo.px[t.symbol]; return s ? `<span title="${esc(t.label)}">${esc(t.symbol)} <b>${F.auto(s.last)}</b> <span class="${s.chg_1m >= 0 ? "" : "r"}">${F.pct(s.chg_1m)} 1m</span></span>` : `<span class="k" title="${esc(t.label)}">${esc(t.symbol)}</span>`; }).join(" &nbsp; ");
      lcd.innerHTML = `<div class="kvs"><div><span>world output ${c.year}</span><b>${unitFmt(c._tot)} ${esc(c.unit)}</b></div><div><span>top producer</span><b>${top ? esc(Mo.name(top.iso3)) + " " + F.share(top.share, 0) : ""}</b></div>
        <div><span>top-3 share</span><b class="${c._top3 > 0.6 ? "a" : ""}">${F.share(c._top3, 0)}</b></div><div><span>HHI</span><b class="${c._hhi > 2500 ? "a" : ""}">${F.n(c._hhi, 0)}</b></div>
        <div><span>reserves</span><b>${res ? unitFmt(res) : "n/a"}</b></div><div><span>reserves / output</span><b>${rp ? F.n(rp, 0) + " yr" : "n/a"}</b></div>
        <div><span>US import reliance</span><b>${c.us_net_import_reliance != null ? F.share(c.us_net_import_reliance, 0) : "n/a"}</b></div></div>
        <div style="margin-top:3px"><span class="k">${esc(c.basis || "")}</span>${ticks ? " &nbsp;|&nbsp; " + ticks : ""}</div>`;
      T.table(q("#cs-l"), [{ k: "rank", t: "#", f: (v, r) => r.iso3 === "OTH" ? "" : v }, { k: (r) => Mo.name(r.iso3), t: "Producer", l: 1 }, { k: "production", t: c.unit, f: (v, r) => r.withheld ? `<span class="mut" title="withheld by USGS">W</span>` : unitFmt(v) }, { k: "share", t: "Share", f: (v) => F.share(v) }, { k: "share", t: "", bar: Math.max(...c.producers.map((p) => p.share || 0), 1e-9) },
        { k: "yoy", t: "YoY", f: (v) => F.pct(v, 0), c: F.cls }, { k: "reserves", t: "Reserves", f: unitFmt }, { k: (r) => r.reserves && r.production ? r.reserves / r.production : null, t: "R/P yr", f: (v) => v ? F.n(v, 0) : "" }],
        c.producers, { nosort: false, sort: 2, onRow: (r) => r.iso3 !== "OTH" && T.open("country", r.iso3) });
      const host = q("#cs-r"), conf = (v) => v === "low" ? "dn" : "mut";
      if (tab === "refining") T.table(host, [{ k: (r) => Mo.name(r.iso3), t: "Processor", l: 1 }, { k: "stage", t: "Stage", l: 1 }, { k: "share", t: "Share", f: (v) => F.share(v, 0) }, { k: "share", t: "", bar: true }, { k: "year", t: "Yr", f: (v) => v || "" }, { k: "confidence", t: "Conf", l: 1, c: conf }], c.refining, { onRow: (r) => T.open("country", r.iso3) });
      else if (tab === "trade") T.table(host, [{ k: (r) => Mo.name(r.from), t: "From", l: 1 }, { k: (r) => Mo.name(r.to), t: "To", l: 1 }, { k: "share", t: "Share", f: (v) => F.share(v, 0) }, { k: "what", t: "What", l: 1 }, { k: "year", t: "Yr", f: (v) => v || "" }, { k: "confidence", t: "Conf", l: 1, c: conf }], c.trade);
      else if (tab === "end_uses") { T.table(host, [{ k: "use", t: "End use (" + (c.end_use_basis || "") + ")", l: 1 }, { k: "share", t: "Share", f: (v) => F.share(v, 0) }, { k: "share", t: "", bar: true }], c.end_uses, { sort: 1 }); }
      else if (tab === "consumers") T.table(host, [{ k: (r) => Mo.name(r.iso3), t: "Consumer", l: 1 }, { k: "consumption", t: c.unit, f: unitFmt }, { k: "share", t: "Share", f: (v) => F.share(v) }, { k: "share", t: "", bar: Math.max(...c.consumers.map((p) => p.share || 0)) },
        { k: (r) => { const p = c.producers.find((x) => x.iso3 === r.iso3); return (p ? p.production : 0) - r.consumption; }, t: "Net (prod-cons)", f: (v) => F.sgn(v, 0), c: F.cls }], c.consumers, { sort: 1, onRow: (r) => T.open("country", r.iso3) });
      else { const src = (Mo.C.meta.sources || []).find((s) => s.id === c.source);
        host.innerHTML = `<div style="padding:8px;user-select:text;line-height:1.45"><b>What supply depends on</b><ul style="margin:4px 0 10px 18px;padding:0">${(c.key_inputs || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>n/a</li>"}</ul><b>Notes</b><p style="margin:4px 0 10px">${esc(c.notes || "n/a")}</p><b>Source</b><p style="margin:4px 0">${src ? `<a href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.title)}</a>` : esc(c.source || "")}</p></div>`; }
      const o = c.producers.find((p) => p.iso3 === q("#cs-o").value), pc = +q("#cs-p").value / 100;
      if (o) { const loss = o.share * pc, others = c.producers.filter((p) => p !== o).reduce((s, p) => s + (p.production || 0), 0);
        q("#cs-oi").innerHTML = `= <b class="dn">-${F.share(loss)}</b> of world supply (${unitFmt(o.production * pc)} ${esc(c.unit)}); everyone else must lift <b>${F.pct(others ? o.production * pc / others : 0)}</b> to cover`; }
      q("#cs-s").textContent = `${c.producers.length} producer rows · ${c.year} · ${c.source || ""} · HHI above 2,500 = highly concentrated`; }
    win.el.addEventListener("change", (ev) => { if (ev.target.id === "cs-c") pick(); else if (/^cs-[op]$/.test(ev.target.id)) draw(); });
    q("#cs-tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; q("#cs-tabs").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); draw(); };
    q("#cs-map").onclick = () => T.open("map", { commodity: c.id }); q("#cs-wb").onclick = () => T.open("grid", { table: "Production by country", filter: c.name });
  } });

  /* ================= country ================= */
  T.app("country", { title: "Country Sheet", group: "Supply chain", w: 5, h: 11, open(win, arg) {
    win.add(`<div class="toolbar"><label>Country <select id="co-c"></select></label><label>Outage <input type="number" id="co-p" value="50" min="0" max="100" step="10" style="width:50px"> % of output offline</label>
      <span style="flex:1"></span><button id="co-map">Map ▸</button><button id="co-wb">Data ▸</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`);
    win.add(`<div class="tabs" id="co-tabs"></div>`); const pane = win.add(`<div class="fill in" style="flex:1;background:#fff"></div>`);
    win.add(`<div class="status"><div class="thin" id="co-s"></div></div>`);
    const q = (s) => win.el.querySelector(s); let Mo, iso, tab = "prod";
    T.model().then((m) => { if (m.empty) { lcd.textContent = "commodities.json missing. Run scripts/build_commodities.py"; return; } Mo = m;
      q("#co-c").innerHTML = m.countries.map((k) => `<option value="${k}">${esc(m.name(k))}</option>`).join(""); set(arg && m.C.countries[arg] ? arg : T.ctx.country && m.C.countries[T.ctx.country] ? T.ctx.country : "COD"); });
    win.onCtx = (k, v) => { if (k === "country" && Mo && Mo.C.countries[v] && iso !== v) set(v); };
    win.onArg = (a) => { if (Mo && a) set(a); };
    function set(a) { if (!Mo.C.countries[a]) { lcd.innerHTML = `<span class="a">No production data for ${esc(a)}.</span>`; pane.innerHTML = ""; return; } iso = a; q("#co-c").value = a; win.setArg(a); T.setCtx("country", a, win); draw(); }
    function draw() { const pc = +q("#co-p").value / 100, prod = (Mo.byCountry[iso] || []).slice().sort((a, b) => (b.p.share || 0) - (a.p.share || 0));
      const ref = Mo.list.flatMap((c) => (c.refining || []).filter((r) => r.iso3 === iso).map((r) => ({ c, ...r })));
      const trade = Mo.list.flatMap((c) => (c.trade || []).filter((r) => r.from === iso || r.to === iso).map((r) => ({ c, ...r })));
      const cons = Mo.list.flatMap((c) => (c.consumers || []).filter((r) => r.iso3 === iso).map((r) => { const p = c.producers.find((x) => x.iso3 === iso); return { c, ...r, prod: p ? p.production : 0 }; }));
      const fac = Mo.fac.filter((f) => f.properties.country === iso).map((f) => f.properties), pipes = Mo.pipes.filter((f) => (f.properties.countries || []).includes(iso)).map((f) => f.properties);
      win.setTitle("Country - " + Mo.name(iso));
      const lead = prod.filter((x) => x.p.rank === 1), top10 = prod.filter((x) => x.p.rank <= 10), crit = prod.filter((x) => x.p.share >= 0.1);
      lcd.innerHTML = `<div class="kvs"><div><span>${esc(Mo.C.countries[iso].name)}</span><b>${iso}</b></div><div><span>commodities listed</span><b>${prod.length}</b></div><div><span>top-10 producer in</span><b>${top10.length}</b></div>
        <div><span>#1 producer of</span><b class="a">${lead.length ? lead.map((x) => esc(x.c.name)).join(", ") : "none"}</b></div><div><span>10%+ of world in</span><b>${crit.length ? crit.map((x) => esc(x.c.name) + " " + F.share(x.p.share, 0)).join(", ") : "none"}</b></div>
        <div><span>facilities mapped</span><b>${fac.length}</b></div><div><span>pipelines</span><b>${pipes.length}</b></div></div>`;
      const tabs = [["prod", `Produces (${prod.length})`], ["ref", `Processes (${ref.length})`], ["trade", `Trade (${trade.length})`], ["cons", `Consumes (${cons.length})`], ["infra", `Infrastructure (${fac.length + pipes.length})`]];
      q("#co-tabs").innerHTML = tabs.map(([k, l]) => `<button data-t="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("");
      const openC = (r) => T.open("commodity", r.c.id);
      if (tab === "prod") T.table(pane, [{ k: (r) => r.c.name, t: "Commodity", l: 1 }, { k: (r) => r.c.group, t: "Group", l: 1, c: () => "mut" }, { k: (r) => r.p.production, t: "Output", f: (v, r) => r.p.withheld ? `<span class="mut" title="withheld by USGS">W</span>` : unitFmt(v) }, { k: (r) => r.c.unit, t: "Unit", l: 1, c: () => "mut" },
        { k: (r) => r.p.share, t: "World share", f: (v) => F.share(v) }, { k: (r) => r.p.share, t: "", bar: true }, { k: (r) => r.p.rank, t: "Rank", f: (v) => "#" + v }, { k: (r) => r.p.yoy, t: "YoY", f: (v) => F.pct(v, 0), c: F.cls },
        { k: (r) => r.p.reserves && r.c.world_reserves ? r.p.reserves / r.c.world_reserves : null, t: "Reserves sh.", f: (v) => F.share(v, 0) },
        { k: (r) => -(r.p.share || 0) * pc, t: `World supply if ${F.n(pc * 100, 0)}% off`, f: (v) => v ? F.pct(v) : "", c: () => "dn" }, { k: (r) => r.c.year, t: "Yr", f: (v) => v }], prod, { sort: 4, onRow: openC });
      else if (tab === "ref") T.table(pane, [{ k: (r) => r.c.name, t: "Commodity", l: 1 }, { k: "stage", t: "Stage", l: 1 }, { k: "share", t: "World share", f: (v) => F.share(v, 0) }, { k: "share", t: "", bar: true }, { k: "year", t: "Yr", f: (v) => v || "" }, { k: "confidence", t: "Conf", l: 1 }], ref, { sort: 2, onRow: openC });
      else if (tab === "trade") T.table(pane, [{ k: (r) => r.c.name, t: "Commodity", l: 1 }, { k: (r) => r.from === iso ? "exports to" : "imports from", t: "", l: 1, c: () => "mut" }, { k: (r) => Mo.name(r.from === iso ? r.to : r.from), t: "Counterparty", l: 1 }, { k: "share", t: "Share", f: (v) => F.share(v, 0) }, { k: "what", t: "What", l: 1 }, { k: "year", t: "Yr", f: (v) => v || "" }, { k: "confidence", t: "Conf", l: 1 }], trade, { onRow: openC });
      else if (tab === "cons") T.table(pane, [{ k: (r) => r.c.name, t: "Commodity", l: 1 }, { k: "consumption", t: "Consumes", f: unitFmt }, { k: "prod", t: "Produces", f: unitFmt }, { k: (r) => r.prod - r.consumption, t: "Net", f: (v) => F.sgn(v, 0), c: F.cls }, { k: (r) => r.c.unit, t: "Unit", l: 1, c: () => "mut" }, { k: "share", t: "World demand sh.", f: (v) => F.share(v) }, { k: (r) => r.consumption ? r.prod / r.consumption : null, t: "Self-sufficiency", f: (v) => v == null ? "" : F.share(v, 0) }], cons, { onRow: openC });
      else T.table(pane, [{ k: "kind", t: "Kind", l: 1 }, { k: "name", t: "Name", l: 1 }, { k: "commodity", t: "Commodity", l: 1 }, { k: "capacity", t: "Capacity", f: (v) => v ? F.big(v) : "" }, { k: "capacity_unit", t: "Unit", l: 1, c: () => "mut" }, { k: "operator", t: "Operator", l: 1 }, { k: "status", t: "Status", l: 1 }],
        fac.concat(pipes.map((p) => ({ ...p, kind: "pipeline" }))), { sort: 3 });
      q("#co-s").textContent = prod.length ? `Largest exposure: ${prod[0].c.name} at ${F.share(prod[0].p.share)} of world output (${prod[0].c.year}). Click a row for the commodity sheet.` : "No listed production."; }
    win.el.addEventListener("change", (ev) => { if (ev.target.id === "co-c") set(ev.target.value); else if (ev.target.id === "co-p") draw(); });
    q("#co-tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; draw(); };
    q("#co-map").onclick = () => T.open("map", { country: iso }); q("#co-wb").onclick = () => T.open("grid", { table: "Production by country", filter: Mo.name(iso) });
  } });
})();
