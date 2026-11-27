/* US natural gas balance: weekly storage vs the 5-year range, end-of-season
   projection, STEO monthly supply/demand with LNG and power burn. Source oil_balance.json. */
"use strict";
(() => {
  const { F, esc } = T;
  const dt = (z) => new Date(z).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
  const mo = (p) => Date.UTC(+p.slice(0, 4), +p.slice(4) - 1, 15);
  T.app("gas", { title: "Gas Balance", group: "Models", w: 7, h: 11, open(win, arg) {
    win.add(`<div class="toolbar"><label>Region <select class="gb-r"></select></label>
      <label>Since <select class="gb-y"><option value="2">2 years</option><option value="4" selected>4 years</option><option value="9">all</option></select></label></div>`);
    win.add(`<div class="tabs"><button data-t="o" class="on">Overview</button><button data-t="s">Storage</button><button data-t="w">Weekly change</button><button data-t="m">Monthly balance</button><button data-t="l">LNG and power</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`), pane = win.add(`<div class="fill in" style="flex:1;background:var(--paper);min-height:0"></div>`), st = win.add(`<div class="status"><div></div></div>`).querySelector("div");
    const q = (s) => win.el.querySelector(s); let G, reg = "l48", tab = "o";
    const kv = (o) => { lcd.innerHTML = `<div class="kvs">${o.map(([k, v]) => `<div><span>${esc(k)}</span><b>${v}</b></div>`).join("")}</div>`; };
    const clear = () => { T.tip({}, null); pane.innerHTML = ""; };
    const rng = (v) => (v == null ? "-" : v < 0 ? "below 5y low" : v > 1 ? "above 5y high" : F.pct(v, 0).replace("+", "") + " of range");
    const since = () => Date.UTC(new Date().getUTCFullYear() - +q(".gb-y").value, 0, 1);
    function chart(xs, cols, o, fmt) { const idx = xs.map((x, i) => i).filter((i) => xs[i] >= (o.from ?? since()));
      const ser = cols.map(([arr, name, c, extra]) => ({ name, color: T.C[c], pts: idx.map((i) => [xs[i], arr[i] == null ? NaN : arr[i]]), ...extra }));
      const c = document.createElement("div"); c.style.height = "100%"; pane.append(c);
      T.canvas(c, (g, w, h) => { c._sx = T.plot(g, w, h, ser, { legend: true, xfmt: dt, ...o }); });
      c.onmousemove = (ev) => { if (!c._sx || !idx.length) return; const x = c._sx.ix(ev.offsetX); let j = idx[0]; for (const i of idx) if (Math.abs(xs[i] - x) < Math.abs(xs[j] - x)) j = i;
        T.tip(ev, fmt(j) + cols.map(([arr, name]) => `\n${name} ${arr[j] == null ? "-" : F.n(arr[j], o.dp ?? 1)}`).join("")); };
      c.onmouseleave = () => T.tip({}, null); }
    function draw() { if (!G) return; clear(); const S = G.storage, s = S.summary[reg], r = S.regions[reg], E = S.end_of_season, M = G.monthly;
      const xs = r.dates.map((d) => Date.parse(d)), wk = (j) => `week to ${r.dates[j]}`;
      const head = [["stocks Bcf", F.n(s.stocks, 0)], ["week chg", F.sgn(s.change, 0)], ["5y avg chg", F.sgn(s.change_avg, 0)], ["vs 5y avg", F.sgn(s.vs_5y, 0)], ["vs 1y", F.sgn(s.vs_1y, 0)], ["5y range", rng(s.in_range)], ["week to", s.date]];
      if (tab === "o") { kv([["L48 stocks Bcf", F.n(S.summary.l48.stocks, 0)], ["vs 5y avg", F.sgn(S.summary.l48.vs_5y, 0)], [`projected ${E.date}`, F.n(E.projected, 0)], ["vs 5y at that week", E.vs_5y == null ? "-" : F.sgn(E.vs_5y, 0)], ["weeks left", E.weeks], ["week to", S.summary.l48.date]]);
        T.table(pane, [{ k: "k", t: "Region", l: 1, f: (v) => (v === "l48" ? `<b>${S.labels[v]}</b>` : S.labels[v]) }, { k: "stocks", t: "Stocks Bcf", f: (v) => F.n(v, 0) }, { k: "change", t: "Week chg", f: (v) => F.sgn(v, 0), c: F.cls }, { k: "change_avg", t: "5y avg chg", f: (v) => F.sgn(v, 0), c: F.cls },
          { k: "vs_5y", t: "vs 5y", f: (v) => F.sgn(v, 0), c: F.cls }, { k: "vs_5y_pct", t: "vs 5y %", f: (v) => (v == null ? "-" : F.pct(v, 1)), c: F.cls }, { k: "vs_1y", t: "vs 1y", f: (v) => F.sgn(v, 0), c: F.cls }, { k: "in_range", t: "5y range", f: rng }],
          Object.keys(S.labels).map((k) => ({ k, ...S.summary[k] })), { nosort: true });
        st.textContent = `EIA weekly storage. Projection adds the 5-year average weekly change for each week left in the season. 5y = same week, prior five years.`; return; }
      if (tab === "s") { kv(head); chart(xs, [[r.stocks, S.labels[reg], 0, { w: 2 }], [r.stocks_avg, "5y avg", 2, { dash: [4, 3] }], [r.stocks_min, "5y low", 5, { dash: [1, 3] }], [r.stocks_max, "5y high", 5, { dash: [1, 3] }], [r.stocks_1y, "1 year ago", 1, { w: 1 }]], { yfmt: (v) => F.n(v, 0), dp: 0 }, wk); st.textContent = "Bcf, working gas in underground storage."; return; }
      if (tab === "w") { kv(head); chart(xs, [[r.change, "net change", 0, { w: 1.6 }], [r.change_avg, "5y avg", 2, { dash: [4, 3] }]], { yfmt: (v) => F.n(v, 0), zero: true, dp: 0 }, wk); st.textContent = "Bcf per week. Positive = injection."; return; }
      const mx = M.periods.map(mo), fc = M.periods.indexOf(M.periods.find((p, i) => M.status[i] === "fcst")), from = Date.UTC(new Date().getUTCFullYear() - +q(".gb-y").value, 0, 1);
      const vl = fc >= 0 ? [{ x: mx[fc], label: "STEO forecast", color: T.C[5] }] : [], mt = (j) => `${M.periods[j].slice(0, 4)}-${M.periods[j].slice(4)} ${M.status[j] === "fcst" ? "(forecast)" : ""}`;
      const i = Math.max(0, fc - 1), y = Math.max(0, i - 12), f = (a, k) => (a[k] == null ? "-" : F.n(a[k], 1));
      if (tab === "m") { kv([[`supply ${mt(i)}`, f(M.supply, i)], ["demand", f(M.demand, i)], ["implied chg", F.sgn(M.implied_change[i], 1)], ["reported chg", M.reported_change[i] == null ? "-" : F.sgn(M.reported_change[i], 1)], ["balancing", M.balancing[i] == null ? "-" : F.sgn(M.balancing[i], 1)], ["last history", M.hist_end.slice(0, 4) + "-" + M.hist_end.slice(4)]]);
        chart(mx, [[M.supply, "supply", 0, { w: 2 }], [M.demand, "demand", 1, { w: 2 }], [M.implied_change, "implied storage chg", 2, { w: 1 }], [M.balancing, "balancing item", 5, { w: 1, dash: [2, 2] }]], { from, vlines: vl, zero: true, yfmt: (v) => F.n(v, 0) }, mt);
        st.textContent = "Bcf/d, EIA STEO. Supply = dry production + supplemental + imports; demand = consumption + exports. Balancing = reported storage change minus implied."; return; }
      kv([[`LNG exports ${mt(i)}`, f(M.exports_lng, i)], ["vs 1y", F.sgn(M.exports_lng[i] - M.exports_lng[y], 1)], ["power burn", f(M.power, i)], ["vs 1y", F.sgn(M.power[i] - M.power[y], 1)], ["industrial", f(M.industrial, i)], ["Henry Hub", M.henry_hub[i] == null ? "-" : "$" + F.n(M.henry_hub[i], 2)]]);
      chart(mx, [[M.exports_lng, "LNG exports", 0, { w: 2 }], [M.power, "power burn", 1, { w: 2 }], [M.industrial, "industrial", 2, { w: 1 }], [M.other_demand, "res + comm + other", 3, { w: 1 }]], { from, vlines: vl, yfmt: (v) => F.n(v, 0) }, mt);
      st.textContent = "Bcf/d, EIA STEO monthly. Weekly LNG feedgas is not in free data; this is monthly exports.";
    }
    function set(k) { if (!G || !G.storage.labels[k]) return; reg = k; q(".gb-r").value = k; win.setArg(k); win.setTitle(`Gas Balance - ${G.storage.labels[k]}`); draw(); }
    q(".gb-r").onchange = () => set(q(".gb-r").value); q(".gb-y").onchange = draw; win.onArg = set;
    win.el.querySelector(".tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((z) => z.classList.toggle("on", z === b)); draw(); };
    T.load("balance").then((d) => { if (!d || !d.gas) { lcd.textContent = "No gas in oil_balance.json. Run scripts/oil_balance.py"; return; } G = d.gas;
      q(".gb-r").innerHTML = Object.entries(G.storage.labels).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join(""); set(G.storage.labels[arg] ? arg : "l48"); });
  } });
})();
