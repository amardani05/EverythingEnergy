/* US metals: imports by origin, COMEX warehouse stocks, Midwest premium. */
"use strict";
(() => {
  const { F, esc } = T;
  T.app("usmetals", { title: "US Metals", group: "Markets", w: 6, h: 10, open(win, arg) {
    win.add(`<div class="toolbar"><label>Metal <select class="um-m"><option value="copper">Copper (refined)</option><option value="aluminum">Aluminum (unwrought)</option></select></label></div>`);
    win.add(`<div class="tabs"><button data-t="i" class="on">Imports by origin</button><button data-t="h">Import history</button><button data-t="s">COMEX stocks</button><button data-t="p">Aluminum Midwest premium</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`), pane = win.add(`<div class="fill in" style="flex:1;background:var(--paper);min-height:0"></div>`), st = win.add(`<div class="status"><div></div></div>`).querySelector("div");
    const sel = win.el.querySelector(".um-m"); let D, tab = "i";
    const kv = (o) => { lcd.innerHTML = `<div class="kvs">${o.map(([k, v]) => `<div><span>${esc(k)}</span><b>${v}</b></div>`).join("")}</div>`; };
    const mon = (v) => new Date(v).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
    const pad = (s) => { pane.onmousemove = null; lcd.innerHTML = ""; st.textContent = ""; pane.innerHTML = `<div class="pad">${s}</div>`; };
    const sum = (a) => a.reduce((x, y) => x + y, 0);
    function clear() { if (pane._ro) { pane._ro.disconnect(); pane._ro = null; } pane.innerHTML = ""; }
    function draw() { if (!D) return; clear(); const m = sel.value; sel.disabled = false;
      if (tab === "p") { const P = D.premium; if (!P) return pad("No premium data."); const n = P.dates.length - 1;
        sel.disabled = true; kv([["Al Midwest premium $/lb", F.n(P.usd_lb[n], 3)], ["exchange price $/t", F.n(P.ali_usd_t[n], 0)], ["all-in US $/t", F.n(P.all_in_usd_t[n], 0)], ["premium share", F.pct(1 - P.ali_usd_t[n] / P.all_in_usd_t[n])], ["as of", P.dates[n]]]);
        const c = document.createElement("div"), t = document.createElement("div"); c.style.cssText = "height:55%"; t.style.cssText = "height:45%;overflow:auto"; pane.append(c, t);
        T.canvas(c, (g, w, h) => T.plot(g, w, h, [{ pts: P.dates.map((d, i) => [Date.parse(d), P.usd_lb[i]]), color: T.C[0], w: 1.6, name: "Aluminum Midwest premium (front AUP future), $/lb" }], { legend: true, xfmt: mon, yfmt: (v) => F.n(v, 2) }));
        T.table(t, [{ k: "month", t: "Delivery", l: 1 }, { k: "usd_lb", t: "$/lb", f: (v) => F.n(v, 3) }, { k: (r) => r.usd_lb * 2204.62262, t: "$/t", f: (v) => F.n(v, 0) }, { k: "date", t: "Last", l: 1, c: () => "mut" }], P.curve, { nosort: true });
        st.textContent = "Aluminum Midwest Transaction Premium (CME AUP futures via Yahoo, not the Platts assessment). There is no listed copper premium. The front series jumps when contracts roll."; return; }
      if (tab === "s") { const R = (D.stocks && D.stocks[m]) || []; if (!R.length) return pad(`<b>No COMEX ${m} stock data.</b><br><br>CME refuses automated downloads of its warehouse reports, and no free feed carries COMEX base-metal stocks. Until a source is wired in (see ROADMAP), you can load reports by hand: download ${m === "copper" ? "Copper" : "Aluminum"}_Stocks.xls from cmegroup.com (Delivery reports, Metals warehouse stocks), save it in <b>data_store/drops/comex/</b> and run scripts/us_metals.py.<br><br>Shanghai stocks for both metals are in the <a data-open="shfe">Exchange Stocks</a> widget.`);
        const x = R[R.length - 1], p = R[R.length - 2]; kv([["total, " + x.unit + "s", F.n(x.total, 0)], ["registered", F.n(x.registered, 0)], ["eligible", F.n(x.eligible, 0)], ["change", p ? F.sgn(x.total - p.total, 0) : "-"], ["as of", x.date]]);
        T.table(pane, [{ k: "date", t: "Date", l: 1 }, { k: "registered", t: "Registered", f: (v) => F.n(v, 0) }, { k: "eligible", t: "Eligible", f: (v) => F.n(v, 0) }, { k: "total", t: "Total", f: (v) => F.n(v, 0) }], R.slice().reverse(), { nosort: true });
        st.textContent = "CME depository reports, from saved files."; return; }
      const I = D.imports || []; if (!I.length) return pad("No import data."); const n = I.length - 1, L = I[n], ly = I.find((r) => r.month === `${+L.month.slice(0, 4) - 1}${L.month.slice(4)}`);
      const last = I.slice(-12), est = L[m].est || Object.values(L[m].origins).some((o) => o.est);
      kv([["imports " + L.month + ", t", F.n(L[m].t, 0)], ["vs year ago", ly ? F.pct(L[m].t / ly[m].t - 1) : "-"], [`last ${last.length} months, t`, F.n(sum(last.map((r) => r[m].t)), 0)], ["value $mn", F.n(L[m].usd / 1e6, 0)], ["unit value $/t", F.n(L[m].usd / L[m].t, 0)]]);
      st.textContent = `UN Comtrade (US Census data), general imports, HS ${m === "copper" ? "7403" : "7601"}. ${est ? "~ = weight estimated by Comtrade." : ""}`;
      if (tab === "h") { const c = document.createElement("div"); c.style.height = "100%"; pane.append(c);
        const tot = {}; for (const r of last) for (const [k, o] of Object.entries(r[m].origins)) tot[k] = (tot[k] || 0) + o.t;
        const top = Object.keys(tot).sort((a, b) => tot[b] - tot[a]).slice(0, 4);
        const ser = [{ name: "Total, kt", color: T.C[5], w: 1.8, pts: I.map((r) => [Date.parse(r.month + "-01"), r[m].t / 1000]) }, ...top.map((k, i) => ({ name: k, color: T.C[i], pts: I.map((r) => [Date.parse(r.month + "-01"), ((r[m].origins[k] || {}).t || 0) / 1000]) }))];
        T.canvas(c, (g, w, h) => T.plot(g, w, h, ser, { legend: true, zero: true, xfmt: mon, yfmt: (v) => F.n(v, 0) })); return; }
      const all = new Set(last.flatMap((r) => Object.keys(r[m].origins))), tt = sum(last.map((r) => r[m].t)), rows = [...all].map((k) => { const a = L[m].origins[k], b = ly && ly[m].origins[k], y = sum(last.map((r) => (r[m].origins[k] || {}).t || 0));
        return { k, t: a ? a.t : 0, e: a && a.est, sh: (a ? a.t : 0) / L[m].t, yoy: ly ? (a ? a.t : 0) - (b ? b.t : 0) : null, y, ys: y / tt }; });
      T.table(pane, [{ k: "k", t: "Origin", l: 1 }, { k: "t", t: L.month + " t", f: (v, r) => (r.e ? "~" : "") + F.n(v, 0) }, { k: "sh", t: "Month share", f: (v) => F.share(v, 0) }, { k: "yoy", t: "vs yr ago t", f: (v) => F.sgn(v, 0), c: F.cls }, { k: "y", t: `${last.length}m t`, f: (v) => F.n(v, 0) }, { k: "ys", t: `${last.length}m share`, f: (v) => F.share(v, 0) }, { k: "ys", t: "", bar: 1 }], rows, { sort: 1 });
    }
    pane.addEventListener("click", (e) => { const a = e.target.closest("[data-open]"); if (a) T.open(a.dataset.open, sel.value); });
    const set = (m) => { sel.value = m; win.setArg(m); draw(); };
    sel.onchange = () => set(sel.value); win.onArg = (m) => { if (m === "copper" || m === "aluminum") set(m); };
    win.el.querySelector(".tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b)); draw(); };
    T.load("usmetals").then((d) => { if (!d) { lcd.textContent = "us_metals.json missing. Run scripts/us_metals.py"; return; } D = d; set(arg === "aluminum" ? "aluminum" : "copper"); });
  } });
})();
