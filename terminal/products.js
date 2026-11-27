/* US refined products balance: stocks, demand and days of cover against the
   5-year seasonal range; weekly balance bridge. Source EIA WPSR via oil_balance.json. */
"use strict";
(() => {
  const { F, esc } = T;
  const NAMES = { gasoline: "Gasoline", distillate: "Distillate", jet: "Jet fuel" };
  const dt = (z) => new Date(z).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
  T.app("products", { title: "Products Balance", group: "Models", w: 7, h: 11, open(win, arg) {
    win.add(`<div class="toolbar"><label>Product <select class="pb-p">${Object.entries(NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
      <label>Since <select class="pb-y"><option value="2">2 years</option><option value="4" selected>4 years</option><option value="9">all</option></select></label></div>`);
    win.add(`<div class="tabs"><button data-t="o" class="on">Overview</button><button data-t="s">Stocks</button><button data-t="c">Days of cover</button><button data-t="d">Demand</button><button data-t="b">Balance</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`), pane = win.add(`<div class="fill in" style="flex:1;background:var(--paper);min-height:0"></div>`), st = win.add(`<div class="status"><div></div></div>`).querySelector("div");
    const q = (s) => win.el.querySelector(s); let P, prod = "distillate", tab = "o";
    const kv = (o) => { lcd.innerHTML = `<div class="kvs">${o.map(([k, v]) => `<div><span>${esc(k)}</span><b>${v}</b></div>`).join("")}</div>`; };
    const clear = () => { pane.onmousemove = pane.onmouseleave = null; T.tip({}, null); pane.innerHTML = ""; };
    const rng = (v) => (v == null ? "-" : v < 0 ? "below 5y low" : v > 1 ? "above 5y high" : F.pct(v, 0).replace("+", "") + " of range");
    function chart(p, keys, o) { const y0 = Date.UTC(new Date().getUTCFullYear() - +q(".pb-y").value, 0, 1), idx = p.dates.map((d, i) => i).filter((i) => Date.parse(p.dates[i]) >= y0);
      const pts = (k) => idx.map((i) => [Date.parse(p.dates[i]), p[k][i] == null ? NaN : p[k][i]]);
      const ser = keys.map(([k, name, c, extra]) => ({ name, color: T.C[c], pts: pts(k), ...extra }));
      const c = document.createElement("div"); c.style.height = "100%"; pane.append(c);
      T.canvas(c, (g, w, h) => { c._sx = T.plot(g, w, h, ser, { legend: true, xfmt: dt, ...o }); });
      c.onmousemove = (ev) => { if (!c._sx) return; const x = c._sx.ix(ev.offsetX); let j = idx[0]; for (const i of idx) if (Math.abs(Date.parse(p.dates[i]) - x) < Math.abs(Date.parse(p.dates[j]) - x)) j = i;
        T.tip(ev, `week to ${p.dates[j]}` + keys.map(([k, name]) => `\n${name} ${p[k][j] == null ? "-" : F.n(p[k][j], 2)}`).join("")); };
      c.onmouseleave = () => T.tip({}, null); }
    const band = (k, unit) => [[k, NAMES[prod], 0, { w: 2 }], [`${k}_avg`, "5y avg", 2, { dash: [4, 3] }], [`${k}_min`, "5y low", 5, { dash: [1, 3] }], [`${k}_max`, "5y high", 5, { dash: [1, 3] }]];
    function draw() { if (!P) return; clear(); const S = P.summary, p = P.products[prod], s = S[prod];
      if (tab === "o") { const U = P.utilization, u = U.value[U.value.length - 1];
        kv([["week to", s.date], ["refinery utilization", u == null ? "-" : F.n(u, 1) + "%"], ["4w chg", U.value.length > 4 ? F.sgn(u - U.value[U.value.length - 5], 1) : "-"]]);
        T.table(pane, [{ k: "k", t: "Product", l: 1, f: (v) => `<b>${NAMES[v]}</b>` }, { k: "stocks", t: "Stocks mb", f: (v) => F.n(v, 1) }, { k: "stocks_vs_5y", t: "vs 5y avg", f: (v) => F.sgn(v, 1), c: F.cls },
          { k: "stocks_in_range", t: "5y range", f: rng }, { k: "build", t: "Build mb/d", f: (v) => F.sgn(v, 2), c: F.cls }, { k: "build_4w", t: "4w avg", f: (v) => F.sgn(v, 2), c: F.cls },
          { k: "demand_4w", t: "Demand 4w", f: (v) => F.n(v, 2) }, { k: "supplied_vs_5y", t: "wk vs 5y", f: (v) => F.sgn(v, 2), c: F.cls }, { k: "cover", t: "Days cover", f: (v) => F.n(v, 1) }, { k: "cover_vs_5y", t: "vs 5y", f: (v) => F.sgn(v, 1), c: F.cls },
          { k: "net_exports", t: "Net exports", f: (v) => F.sgn(v, 2) }], Object.keys(NAMES).map((k) => ({ k, ...S[k] })), { nosort: true });
        st.textContent = "US, EIA weekly. 5y = same week over the prior five years. Demand = product supplied. Pick a product above for its charts."; return; }
      kv([["stocks mb", F.n(s.stocks, 1)], ["vs 5y avg", F.sgn(s.stocks_vs_5y, 1)], ["5y range", rng(s.stocks_in_range)], ["days cover", F.n(s.cover, 1)], ["demand 4w", F.n(s.demand_4w, 2)], ["week to", s.date]]);
      if (tab === "s") { chart(p, band("stocks"), { yfmt: (v) => F.n(v, 0) }); st.textContent = "Million barrels, total US stocks."; }
      else if (tab === "c") { chart(p, band("cover"), { yfmt: (v) => F.n(v, 0) }); st.textContent = "Days: stocks over 4-week average product supplied."; }
      else if (tab === "d") { chart(p, [["supplied", "weekly", 1, { w: 1 }], ["demand_4w", "4w avg", 0, { w: 2 }], ["supplied_avg", "5y avg", 2, { dash: [4, 3] }]], { yfmt: (v) => F.n(v, 1) }); st.textContent = "mb/d product supplied (implied demand). Weekly numbers are noisy; use the 4w line."; }
      else { chart(p, [["build", "stock build", 0, { w: 1.6 }], ["modelled", "prod + imp - exp - supplied", 1, { dash: [4, 3] }], ["adjustment", "residual", 5, { w: 1 }]], { yfmt: (v) => F.n(v, 1), zero: true });
        st.textContent = prod === "gasoline" ? "mb/d. Gasoline residual sits near -0.4: finished production excludes blending components and ethanol. Watch changes, not the level." : "mb/d. Residual is near zero because EIA derives demand from this identity."; }
    }
    function set(k) { if (!NAMES[k]) return; prod = k; q(".pb-p").value = k; win.setArg(k); win.setTitle(`Products Balance - ${NAMES[k]}`); draw(); }
    q(".pb-p").onchange = () => set(q(".pb-p").value); q(".pb-y").onchange = draw; win.onArg = set;
    win.el.querySelector(".tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((z) => z.classList.toggle("on", z === b)); draw(); };
    T.load("balance").then((d) => { if (!d || !d.products) { lcd.textContent = "No products in oil_balance.json. Run scripts/oil_balance.py"; return; } P = d.products; set(NAMES[arg] ? arg : prod); });
  } });
})();
