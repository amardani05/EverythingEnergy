/* Exchange stocks: SHFE weekly warehouse stocks for base metals, deliverable and
   on warrant. COMEX stocks live in the US Metals widget. Source metals_stocks.json. */
"use strict";
(() => {
  const { F, esc } = T;
  const dt = (z) => new Date(z).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit", timeZone: "UTC" });
  const t = (v) => (v == null ? "-" : F.n(v, 0)), ts = (v) => (v == null ? "-" : F.sgn(v, 0));
  T.app("shfe", { title: "Exchange Stocks", group: "Supply chain", w: 7, h: 10, open(win, arg) {
    win.add(`<div class="toolbar"><label>Metal <select class="sx-m"></select></label></div>`);
    win.add(`<div class="tabs"><button data-t="o" class="on">Overview</button><button data-t="s">Stocks</button><button data-t="w">Weekly change</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`), pane = win.add(`<div class="fill in" style="flex:1;background:var(--paper);min-height:0"></div>`), st = win.add(`<div class="status"><div></div></div>`).querySelector("div");
    const q = (s) => win.el.querySelector(s); let D, metal = "copper", tab = "o";
    const kv = (o) => { lcd.innerHTML = `<div class="kvs">${o.map(([k, v]) => `<div><span>${esc(k)}</span><b>${v}</b></div>`).join("")}</div>`; };
    const note = () => `SHFE weekly, tonnes. Archive starts ${D.meta.first} (${D.meta.weeks} weeks); 5-year norms appear as it grows. No report in Lunar New Year week.`;
    function chart(s, cols) { const xs = s.dates.map((d) => Date.parse(d));
      const ser = cols.map(([k, name, c, extra]) => ({ name, color: T.C[c], pts: xs.map((x, i) => [x, s[k][i] == null ? NaN : s[k][i]]), ...extra })).filter((z) => z.pts.some((p) => isFinite(p[1])));
      const c = document.createElement("div"); c.style.height = "100%"; pane.append(c);
      T.canvas(c, (g, w, h) => { c._sx = T.plot(g, w, h, ser, { legend: true, xfmt: dt, yfmt: (v) => F.n(v, 0), zero: true }); });
      c.onmousemove = (ev) => { if (!c._sx) return; const x = c._sx.ix(ev.offsetX); let j = 0; xs.forEach((z, i) => { if (Math.abs(z - x) < Math.abs(xs[j] - x)) j = i; });
        T.tip(ev, `week to ${s.dates[j]}` + cols.map(([k, name]) => `\n${name} ${t(s[k][j])}`).join("")); };
      c.onmouseleave = () => T.tip({}, null); }
    function draw() { if (!D) return; T.tip({}, null); pane.innerHTML = ""; const S = D.summary, s = D.metals[metal], v = S[metal];
      if (tab === "o") { kv([["week to", D.meta.as_of], ["weeks archived", D.meta.weeks], ["since", D.meta.first]]);
        T.table(pane, [{ k: "k", t: "Metal", l: 1, f: (z) => `<b>${D.labels[z]}</b>` }, { k: "deliverable", t: "Stocks t", f: t }, { k: "chg_1w", t: "1w", f: ts, c: F.cls }, { k: "chg_4w", t: "4w", f: ts, c: F.cls }, { k: "vs_1y", t: "vs 1y", f: ts, c: F.cls },
          { k: "warrant", t: "On warrant", f: t }, { k: "warrant_chg_1w", t: "warrant 1w", f: ts, c: F.cls }, { k: "warrant_share", t: "warrant %", f: (z) => (z == null ? "-" : F.pct(z, 0).replace("+", "")) }],
          Object.keys(D.labels).map((k) => ({ k, ...S[k] })), { nosort: true });
        st.textContent = note(); return; }
      kv([["stocks t", t(v.deliverable)], ["1w", ts(v.chg_1w)], ["4w", ts(v.chg_4w)], ["vs 1y", ts(v.vs_1y)], ["on warrant", t(v.warrant)], ["warrant 1w", ts(v.warrant_chg_1w)], ["week to", v.date]]);
      if (tab === "s") chart(s, [["deliverable", "deliverable stocks", 0, { w: 2 }], ["warrant", "on warrant", 1, { w: 1.6 }], ["deliverable_avg", "5y avg", 2, { dash: [4, 3] }], ["deliverable_min", "5y low", 5, { dash: [1, 3] }], ["deliverable_max", "5y high", 5, { dash: [1, 3] }]]);
      else chart(s, [["deliverable_chg", "stocks change", 0, { w: 1.6 }], ["warrant_chg", "warrant change", 1, { w: 1 }]]);
      st.textContent = note() + " On warrant = registered for delivery against futures.";
    }
    function set(k) { if (!D || !D.labels[k]) return; metal = k; q(".sx-m").value = k; win.setArg(k); win.setTitle(`Exchange Stocks - ${D.labels[k]}`); draw(); }
    q(".sx-m").onchange = () => set(q(".sx-m").value); win.onArg = set;
    win.el.querySelector(".tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((z) => z.classList.toggle("on", z === b)); draw(); };
    T.load("shfe").then((d) => { if (!d) { lcd.textContent = "metals_stocks.json missing. Run scripts/shfe_stocks.py"; return; } D = d;
      q(".sx-m").innerHTML = Object.entries(d.labels).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join(""); set(d.labels[arg] ? arg : "copper"); });
  } });
})();
