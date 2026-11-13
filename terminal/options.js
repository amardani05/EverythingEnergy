/* Options desk (chain, IV, fair value) and strategy builder (multi-leg P&L,
   greeks, convexity, scenario grid). All pricing goes through BS (bs.js). */
"use strict";
(() => {
  const { $, F, esc } = T;
  const book = { symbol: null, legs: [], listeners: new Set() };   // shared between the two windows
  const touch = () => book.listeners.forEach((f) => f());
  T.book = book;

  const mid = (b, a, l) => (b > 0 && a > 0 && a >= b ? (b + a) / 2 : l > 0 ? l : null);
  const stale = (b, a) => !(b > 0 && a > 0 && a >= b);
  function enrich(u, rate) {            // solve IV once per underlying
    if (u._done) return u; u._done = true;
    for (const e of u.expiries) { const Tm = e.dte / 365, fwd = u.spot * Math.exp((rate - u.div_yield) * Tm); e.T = Tm; e.fwd = fwd;
      for (const r of e.rows) { r.cm = mid(r.cb, r.ca, r.cl); r.pm = mid(r.pb, r.pa, r.pl);
        r.civ = r.cm ? BS.impliedVol(r.cm, u.spot, r.k, Tm, rate, u.div_yield, "c") : null;
        r.piv = r.pm ? BS.impliedVol(r.pm, u.spot, r.k, Tm, rate, u.div_yield, "p") : null;
        r.iv = r.k >= fwd ? (r.civ ?? r.piv) : (r.piv ?? r.civ); }      // out-of-the-money side is the liquid one
      const ok = e.rows.filter((r) => r.iv != null && !stale(r.k >= fwd ? r.cb : r.pb, r.k >= fwd ? r.ca : r.pa));
      const pool = ok.length >= 2 ? ok : e.rows.filter((r) => r.iv != null);
      const lo = pool.filter((r) => r.k <= fwd).pop(), hi = pool.find((r) => r.k > fwd);
      e.atm = lo && hi ? lo.iv + (hi.iv - lo.iv) * (fwd - lo.k) / (hi.k - lo.k) : (lo || hi || {}).iv ?? null;
      const near = e.rows.reduce((b, r) => (Math.abs(r.k - fwd) < Math.abs(b.k - fwd) ? r : b), e.rows[0]);
      e.straddle = near.cm && near.pm ? near.cm + near.pm : null; e.atmK = near.k; }
    return u;
  }
  T.optx = { enrich };
  const ivAt = (e, k) => { const r = e.rows.find((x) => x.k === k); return (r && r.iv) || e.atm || 0.4; };

  /* ================= chain ================= */
  T.app("chain", { title: "Options Desk", group: "Options", w: 7, h: 14, open(win, arg) {
    win.add(`<div class="toolbar">
      <label>Underlying <select id="oc-u"></select></label><label>Expiry <select id="oc-e"></select></label>
      <label>Fair-value vol <select id="oc-v"><option value="rv20">Realized 20d</option><option value="rv60">Realized 60d</option><option value="rv10">Realized 10d</option><option value="rv120">Realized 120d</option><option value="atm">ATM implied</option><option value="x">Custom</option></select></label>
      <input type="number" id="oc-vx" step="0.5" value="30" title="vol, percent"> %
      <label>Strikes ± <input type="number" id="oc-w" value="20" step="5" style="width:48px"> %</label>
      <button id="oc-s">Strategy ▸</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`);
    const sp = win.add(`<div class="split stack fill" style="flex:1"><div class="col" style="flex:1.7"><div class="fill in" id="oc-t"></div></div>
      <div class="col" style="flex:1;margin-left:3px"><div class="tabs"><button data-t="smile" class="on">Smile</button><button data-t="term">Term</button><button data-t="surf">Surface</button></div><div class="fill in" id="oc-c" style="background:#fff"></div></div></div>`);
    const st = win.add(`<div class="status"><div class="thin">Click an ASK to buy, a BID to sell: the leg goes to the Strategy window.</div><div class="thin" id="oc-asof"></div></div>`);
    const q = (s) => win.el.querySelector(s);
    let D, u, e, tab = "smile";
    T.load("options").then((d) => { if (!d) { lcd.textContent = "options_data.json missing. Run: .venv/bin/python scripts/market_snapshot.py"; return; }
      D = d; q("#oc-u").innerHTML = d.underlyings.map((x) => `<option value="${x.symbol}">${x.symbol}  ${esc(x.proxy_for)}</option>`).join("");
      q("#oc-asof").textContent = "quotes " + d.meta.as_of + " (delayed)";
      if (arg && d.underlyings.some((x) => x.symbol === arg)) q("#oc-u").value = arg; else if (book.symbol) q("#oc-u").value = book.symbol;
      pickU(); });
    win.onArg = (a) => { if (D && a) { q("#oc-u").value = a; pickU(); } };
    function pickU() { u = enrich(D.underlyings.find((x) => x.symbol === q("#oc-u").value), D.meta.rate); win.setArg(u.symbol);
      q("#oc-e").innerHTML = u.expiries.map((x, i) => `<option value="${i}">${x.date}  ${x.dte}d</option>`).join("");
      q("#oc-e").value = Math.min(2, u.expiries.length - 1); draw(); }
    function vol() { const m = q("#oc-v").value; const v = m === "x" ? +q("#oc-vx").value / 100 : m === "atm" ? e.atm : u.rv[m.slice(2)];
      if (m !== "x" && v) q("#oc-vx").value = (v * 100).toFixed(1); return v || 0.3; }
    function draw() {
      e = u.expiries[+q("#oc-e").value]; const r = D.meta.rate, y = u.div_yield, sig = vol(), wd = +q("#oc-w").value / 100;
      win.setTitle(`Options Desk - ${u.symbol} ${e.date}`);
      const mv = e.atm ? u.spot * e.atm * Math.sqrt(e.T) : null, rv = u.rv["20"];
      lcd.innerHTML = `<div class="kvs">
        <div><span>${u.symbol} spot</span><b>${F.n(u.spot)}</b></div><div><span>forward</span><b>${F.n(e.fwd)}</b></div><div><span>days</span><b>${e.dte}</b></div>
        <div><span>ATM IV</span><b class="a">${e.atm ? F.n(e.atm * 100, 1) : "n/a"}</b></div><div><span>RV 10/20/60</span><b>${[10, 20, 60].map((k) => u.rv[k] ? F.n(u.rv[k] * 100, 0) : "-").join("/")}</b></div>
        <div><span>IV - RV20</span><b class="${e.atm && rv && e.atm < rv ? "r" : ""}">${e.atm && rv ? F.sgn((e.atm - rv) * 100, 1) : "n/a"}</b></div>
        <div><span>implied ±1σ</span><b>${mv ? "±" + F.n(mv) + " (" + F.n(mv / u.spot * 100, 1) + "%)" : "n/a"}</b></div>
        <div><span>${F.auto(e.atmK)} straddle</span><b>${e.straddle ? F.n(e.straddle) : "n/a"}</b></div>
        <div><span>rate / yield</span><b>${F.n(r * 100)} / ${F.n(y * 100)}</b></div><div><span>fair vol</span><b class="a">${F.n(sig * 100, 1)}</b></div></div>`;
      const rows = e.rows.filter((x) => Math.abs(x.k / u.spot - 1) <= wd).map((x) => {
        const cf = BS.price(u.spot, x.k, e.T, r, y, sig, "c"), pf = BS.price(u.spot, x.k, e.T, r, y, sig, "p"), g = BS.greeks(u.spot, x.k, e.T, r, y, x.iv || sig, "c");
        return { ...x, cf, pf, ce: x.cm != null ? x.cm - cf : null, pe: x.pm != null ? x.pm - pf : null, cd: g.delta, gm: g.gamma, vg: g.vega / 100, atm: x.k === e.atmK }; });
      const px = (v) => v == null ? "" : F.n(v), ivf = (v) => v == null ? "" : F.n(v * 100, 1), edge = (v) => v == null ? "" : F.sgn(v);
      const tb = T.table(q("#oc-t"), [
        { k: "coi", t: "C OI", f: (v) => F.n(v || 0, 0), c: () => "mut" }, { k: "cb", t: "C bid", f: px, c: () => "dn" }, { k: "ca", t: "C ask", f: px, c: () => "up" },
        { k: "civ", t: "C IV", f: ivf }, { k: "cf", t: "fair", f: px, c: () => "mut" }, { k: "ce", t: "edge", f: edge, c: F.cls, tip: "market mid minus Black-Scholes fair value at your vol. Positive: market is rich." },
        { k: "cd", t: "Δ", f: (v) => F.n(v, 2), c: () => "mut" },
        { k: "k", t: "STRIKE", f: (v, x) => `<b>${F.auto(v)}</b>${x.atm ? " ◄" : ""}` },
        { k: "gm", t: "Γ", f: (v) => F.n(v, 4), c: () => "mut" }, { k: "vg", t: "vega", f: (v) => F.n(v, 3), c: () => "mut", tip: "per vol point" },
        { k: "pe", t: "edge", f: edge, c: F.cls }, { k: "pf", t: "fair", f: px, c: () => "mut" }, { k: "piv", t: "P IV", f: ivf },
        { k: "pb", t: "P bid", f: px, c: () => "dn" }, { k: "pa", t: "P ask", f: px, c: () => "up" }, { k: "poi", t: "P OI", f: (v) => F.n(v || 0, 0), c: () => "mut" },
      ], rows, { nosort: true, onRow(x, ev) { const td = ev.target.closest("td"); if (!td) return; const i = td.cellIndex;
        const map = { 1: ["c", -1, x.cb], 2: ["c", 1, x.ca], 13: ["p", -1, x.pb], 14: ["p", 1, x.pa] }[i]; if (!map || !(map[2] > 0)) return;
        addLeg(u, e, map[0], x.k, map[1], map[2], map[0] === "c" ? x.civ : x.piv); } });
      const at = [...tb.el.tBodies[0].rows].find((tr) => tr.textContent.includes("◄")); if (at) at.scrollIntoView({ block: "center" });
      chart();
    }
    function chart() { const host = q("#oc-c");
      if (tab === "smile") T.canvas(host, (g, w, h) => { const pts = (f) => e.rows.filter((x) => x[f] != null).map((x) => [x.k, x[f] * 100]);
        T.plot(g, w, h, [{ pts: pts("piv").filter((p) => p[0] <= e.fwd * 1.02), color: T.C[3], dots: true, name: "put IV" }, { pts: pts("civ").filter((p) => p[0] >= e.fwd * 0.98), color: T.C[0], dots: true, name: "call IV" },
          { pts: [[e.rows[0].k, u.rv["20"] * 100], [e.rows[e.rows.length - 1].k, u.rv["20"] * 100]], color: T.C[1], dash: [4, 3], name: "RV20" }],
          { legend: true, vlines: [{ x: u.spot, color: "#333", label: "spot" }], yfmt: (v) => F.n(v, 0) }); });
      else if (tab === "term") T.canvas(host, (g, w, h) => { const ex = u.expiries.filter((x) => x.atm);
        T.plot(g, w, h, [{ pts: ex.map((x) => [x.dte, x.atm * 100]), color: T.C[0], dots: true, name: "ATM IV" },
          ...[["10", T.C[3]], ["20", T.C[1]], ["60", T.C[2]], ["120", T.C[4]]].filter(([k]) => u.rv[k]).map(([k, c]) => ({ pts: [[0, u.rv[k] * 100], [ex[ex.length - 1].dte, u.rv[k] * 100]], color: c, dash: [4, 3], name: "RV" + k }))],
          { legend: true, yfmt: (v) => F.n(v, 0), xfmt: (v) => F.n(v, 0) + "d" }); });
      else { const ms = [0.8, 0.9, 0.95, 1, 1.05, 1.1, 1.2];
        const at = (x, m) => { const k = u.spot * m, rs = x.rows.filter((r) => r.iv != null), lo = rs.filter((r) => r.k <= k).pop(), hi = rs.find((r) => r.k > k);
          return lo && hi ? lo.iv + (hi.iv - lo.iv) * (k - lo.k) / (hi.k - lo.k) : null; };
        host.style.background = "#fff";
        T.table(host, [{ k: "date", t: "Expiry", l: 1 }, { k: "dte", t: "d", f: (v) => v }, ...ms.map((m) => ({ k: (x) => at(x, m), t: (m * 100) + "%", f: (v) => v == null ? "" : F.n(v * 100, 1) }))], u.expiries, { nosort: true }); return; }
      host.style.background = "#fff";
    }
    win.el.addEventListener("change", (ev) => { if (ev.target.id === "oc-u") pickU(); else if (/^oc-/.test(ev.target.id)) { if (ev.target.id === "oc-vx") q("#oc-v").value = "x"; draw(); } });
    win.el.querySelector(".tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b)); chart(); };
    q("#oc-s").onclick = () => { setBook(u); T.open("strat"); };
  } });

  function setBook(u) { if (book.symbol !== u.symbol) { book.symbol = u.symbol; book.u = u; book.legs = []; } book.u = u; }
  function addLeg(u, e, type, k, qty, entry, iv) { setBook(u);
    book.legs.push({ type, k, T: e.T, dte: e.dte, exp: e.date, qty, iv: iv || e.atm || 0.4, entry, mult: 100 }); T.open("strat"); touch(); }

  /* ================= strategy ================= */
  const PRESETS = {                       // [type, strike as multiple of spot, qty, expiry offset]
    "Long call": [["c", 1, 1, 0]], "Long put": [["p", 1, 1, 0]], "Covered call": [["u", 1, 1, 0], ["c", 1.05, -1, 0]], "Protective put": [["u", 1, 1, 0], ["p", 0.95, 1, 0]],
    "Collar": [["u", 1, 1, 0], ["p", 0.95, 1, 0], ["c", 1.05, -1, 0]], "Long straddle": [["c", 1, 1, 0], ["p", 1, 1, 0]], "Short straddle": [["c", 1, -1, 0], ["p", 1, -1, 0]],
    "Long strangle": [["p", 0.95, 1, 0], ["c", 1.05, 1, 0]], "Short strangle": [["p", 0.92, -1, 0], ["c", 1.08, -1, 0]],
    "Bull call spread": [["c", 1, 1, 0], ["c", 1.07, -1, 0]], "Bear put spread": [["p", 1, 1, 0], ["p", 0.93, -1, 0]],
    "Call butterfly": [["c", 0.95, 1, 0], ["c", 1, -2, 0], ["c", 1.05, 1, 0]], "Iron condor": [["p", 0.9, 1, 0], ["p", 0.95, -1, 0], ["c", 1.05, -1, 0], ["c", 1.1, 1, 0]],
    "Risk reversal": [["p", 0.95, -1, 0], ["c", 1.05, 1, 0]], "Call ratio 1x2": [["c", 1, 1, 0], ["c", 1.07, -2, 0]], "Put backspread 1x2": [["p", 1, -1, 0], ["p", 0.93, 2, 0]],
    "Call calendar": [["c", 1, -1, 0], ["c", 1, 1, 1]], "Diagonal": [["c", 1.05, -1, 0], ["c", 1, 1, 1]],
  };
  T.app("strat", { title: "Strategy Builder", group: "Options", w: 6, h: 14, open(win) {
    win.add(`<div class="toolbar"><label>Underlying <select id="sb-u"></select></label><label>Preset <select id="sb-p">${Object.keys(PRESETS).map((k) => `<option${k === "Long straddle" ? " selected" : ""}>${k}</option>`).join("")}</select></label>
      <label>Expiry <select id="sb-e"></select></label><button id="sb-b">Build</button><button id="sb-add">+ Leg</button><button id="sb-clr">Clear</button><button id="sb-oc">◂ Chain</button></div>`);
    const legsEl = win.add(`<div class="in" style="max-height:132px;overflow:auto;flex:none"></div>`);
    win.add(`<div class="toolbar"><label>Days forward <input type="range" id="sb-d" min="0" max="30" value="0" style="width:130px"> <span id="sb-dv" style="font-family:var(--num);width:34px;display:inline-block">0d</span></label>
      <label>Vol shift <input type="range" id="sb-v" min="-30" max="30" value="0" style="width:130px"> <span id="sb-vv" style="font-family:var(--num);width:54px;display:inline-block">+0 pts</span></label>
      <label>Range ± <input type="number" id="sb-r" value="25" step="5" style="width:48px"> %</label></div>`);
    const lcd = win.add(`<div class="lcd"></div>`);
    win.add(`<div class="tabs"><button data-t="pnl" class="on">P&amp;L curve</button><button data-t="cvx">Convexity</button><button data-t="grid">Scenario grid</button><button data-t="grk">Greeks by leg</button></div>`);
    const pane = win.add(`<div class="fill in" style="flex:1;background:#fff"></div>`);
    const stat = win.add(`<div class="status"><div class="thin" id="sb-st"></div><div class="thin">x100 multiplier · European BSM</div></div>`);
    const q = (s) => win.el.querySelector(s);
    let D, tab = "pnl";
    T.load("options").then((d) => { if (!d) { lcd.textContent = "options_data.json missing."; return; } D = d;
      q("#sb-u").innerHTML = d.underlyings.map((x) => `<option value="${x.symbol}">${x.symbol}  ${esc(x.proxy_for)}</option>`).join("");
      if (!book.u) setBook(enrich(d.underlyings[0], d.meta.rate)); sync(); if (!book.legs.length) build(); else draw(); });
    const on = () => { if (D) { sync(); draw(); } }; book.listeners.add(on); win.onClose = () => book.listeners.delete(on);
    function sync() { q("#sb-u").value = book.symbol; const cur = q("#sb-e").value;
      q("#sb-e").innerHTML = book.u.expiries.map((x, i) => `<option value="${i}">${x.date}  ${x.dte}d</option>`).join(""); q("#sb-e").value = cur && cur < book.u.expiries.length ? cur : Math.min(2, book.u.expiries.length - 1); }
    const nearK = (e, k) => e.rows.reduce((b, r) => (Math.abs(r.k - k) < Math.abs(b.k - k) ? r : b), e.rows[0]);
    function mkLeg(type, e, k, qty) { const u = book.u; if (type === "u") return { type, k: 0, T: 0, dte: 0, exp: "", qty, iv: 0, entry: u.spot, mult: 100 };
      const r = nearK(e, k), m = type === "c" ? r.cm : r.pm, iv = (type === "c" ? r.civ : r.piv) || ivAt(e, r.k);
      const px = m ?? BS.price(u.spot, r.k, e.T, D.meta.rate, u.div_yield, iv, type);
      const side = qty > 0 ? (type === "c" ? r.ca : r.pa) : (type === "c" ? r.cb : r.pb);
      return { type, k: r.k, T: e.T, dte: e.dte, exp: e.date, qty, iv, entry: side > 0 ? side : px, mult: 100 }; }
    function build() { const u = book.u, i = +q("#sb-e").value;
      book.legs = PRESETS[q("#sb-p").value].map(([ty, m, qty, off]) => mkLeg(ty, u.expiries[Math.min(u.expiries.length - 1, i + (off ? 2 : 0))], u.spot * m, qty)); draw(); }
    function drawLegs() { const u = book.u, o = { r: D.meta.rate, q: u.div_yield };
      legsEl.innerHTML = `<table class="g"><thead><tr><th>Qty</th><th class="l">Type</th><th>Strike</th><th class="l">Expiry</th><th>Entry</th><th>IV %</th><th>Model</th><th>Δ</th><th>Γ</th><th>Vega</th><th>Θ/day</th><th></th></tr></thead><tbody>` +
        book.legs.map((l, i) => { const g = BS.legGreeks(l, u.spot, o), m = l.qty * l.mult;
          return `<tr><td><input type="number" data-i="${i}" data-f="qty" value="${l.qty}" step="1" style="width:40px"></td>
          <td class="l"><select data-i="${i}" data-f="type"><option value="c"${l.type === "c" ? " selected" : ""}>Call</option><option value="p"${l.type === "p" ? " selected" : ""}>Put</option><option value="u"${l.type === "u" ? " selected" : ""}>Stock</option></select></td>
          <td>${l.type === "u" ? "" : `<input type="number" data-i="${i}" data-f="k" value="${l.k}" step="any" style="width:56px">`}</td>
          <td class="l">${l.type === "u" ? "" : `<select data-i="${i}" data-f="exp">${u.expiries.map((x) => `<option${x.date === l.exp ? " selected" : ""} value="${x.date}">${x.date.slice(2)} · ${x.dte}d</option>`).join("")}</select>`}</td>
          <td><input type="number" data-i="${i}" data-f="entry" value="${(+l.entry).toFixed(2)}" step="0.01" style="width:54px"></td>
          <td>${l.type === "u" ? "" : `<input type="number" data-i="${i}" data-f="iv" value="${(l.iv * 100).toFixed(1)}" step="0.5" style="width:46px">`}</td>
          <td>${F.n(BS.legValue(l, u.spot, o))}</td><td>${F.n(g.delta * m, 1)}</td><td>${F.n(g.gamma * m, 2)}</td><td>${F.n(g.vega * m / 100, 1)}</td><td>${F.n(g.theta * m / 365, 1)}</td>
          <td><button data-i="${i}" data-f="del" style="padding:0 5px">x</button></td></tr>`; }).join("") + `</tbody></table>`; }
    legsEl.addEventListener("change", (ev) => { const t = ev.target, l = book.legs[+t.dataset.i]; if (!l) return; const u = book.u, f = t.dataset.f;
      if (f === "qty") l.qty = +t.value || 0; else if (f === "entry") l.entry = +t.value; else if (f === "iv") l.iv = Math.max(0.01, +t.value / 100);
      else if (f === "k") { const e = u.expiries.find((x) => x.date === l.exp); const r = nearK(e, +t.value); Object.assign(l, mkLeg(l.type, e, r.k, l.qty)); }
      else if (f === "exp") Object.assign(l, mkLeg(l.type, u.expiries.find((x) => x.date === t.value), l.k, l.qty));
      else if (f === "type") Object.assign(l, mkLeg(t.value, u.expiries.find((x) => x.date === l.exp) || u.expiries[+q("#sb-e").value], l.k || u.spot, l.qty));
      draw(); });
    legsEl.addEventListener("click", (ev) => { if (ev.target.dataset.f === "del") { book.legs.splice(+ev.target.dataset.i, 1); draw(); } });

    function ctx() { const u = book.u, hz = BS.horizonDays(book.legs), days = Math.min(+q("#sb-d").value, hz || 0);
      return { u, hz, days, base: { r: D.meta.rate, q: u.div_yield }, volAdd: +q("#sb-v").value / 100, rng: +q("#sb-r").value / 100 }; }
    function draw() { if (!D) return; const c = ctx(), { u, hz } = c, legs = book.legs;
      q("#sb-d").max = Math.max(1, hz); q("#sb-dv").textContent = c.days + "d"; q("#sb-vv").textContent = F.sgn(c.volAdd * 100, 0) + " pts";
      drawLegs(); win.setTitle(`Strategy Builder - ${u.symbol}${legs.length ? " (" + legs.length + (legs.length === 1 ? " leg)" : " legs)") : ""}`);
      if (!legs.length) { lcd.textContent = "No legs. Pick a preset and Build, or click bid/ask in the chain."; pane.innerHTML = ""; return; }
      const now = { ...c.base, days: 0 }, exp = { ...c.base, days: hz }, sc = { ...c.base, days: c.days, volAdd: c.volAdd };
      const cost = legs.reduce((s, l) => s + l.qty * l.mult * l.entry, 0), optCost = legs.filter((l) => l.type !== "u").reduce((s, l) => s + l.qty * l.mult * l.entry, 0);
      const wide = BS.curve(legs, exp, u.spot * 0.02, u.spot * 4, 1200), ys = wide.map((p) => p[1]), tl = BS.tails(legs, c.base, u.spot);
      const be = BS.breakevens(wide), maxP = tl.upSlope > 0 ? Infinity : Math.max(...ys, tl.atZero), maxL = tl.upSlope < 0 ? -Infinity : Math.min(...ys, tl.atZero);
      const g = BS.bookGreeks(legs, u.spot, now), opts = legs.filter((l) => l.type !== "u"), atm = opts.length ? opts.reduce((s, l) => s + l.iv, 0) / opts.length : (u.rv["20"] || 0.3);
      const Th = hz / 365, sd = u.spot * atm * Math.sqrt(Math.max(Th, 1 / 365));
      // probability of profit at the horizon: lognormal, risk-neutral drift, average leg IV
      let pop = 0; { const mu = Math.log(u.spot) + (c.base.r - c.base.q - 0.5 * atm * atm) * Th, sg = atm * Math.sqrt(Math.max(Th, 1e-6));
        for (let i = 1; i < wide.length; i++) { const a = wide[i - 1], b = wide[i]; if ((a[1] + b[1]) / 2 > 0) pop += BS.cdf((Math.log(b[0]) - mu) / sg) - BS.cdf((Math.log(a[0]) - mu) / sg); } }
      const thDay = g.theta / 365, gam1 = 0.5 * g.gamma * (u.spot * 0.01) ** 2, beMove = g.gamma !== 0 && thDay * g.gamma < 0 ? Math.sqrt(-2 * thDay / g.gamma) / u.spot : null;
      const fm = (v) => v === Infinity ? "unlimited" : v === -Infinity ? "unlimited" : F.n(v, 0);
      lcd.innerHTML = `<div class="kvs">
        <div><span>${optCost >= 0 ? "net debit" : "net credit"}</span><b>${F.n(Math.abs(optCost), 0)}</b></div><div><span>max profit</span><b>${fm(maxP)}</b></div><div><span>max loss</span><b class="r">${fm(maxL)}</b></div>
        <div><span>breakevens</span><b>${be.length ? be.slice(0, 4).map((x) => F.n(x)).join("  ") : "none"}</b></div><div><span>P(profit) @${hz}d</span><b>${F.share(pop, 0)}</b></div><div><span>±1σ to ${hz}d</span><b>±${F.n(sd)}</b></div>
        <div><span>delta</span><b>${F.sgn(g.delta, 1)}</b></div><div><span>gamma</span><b>${F.sgn(g.gamma, 3)}</b></div><div><span>vega /pt</span><b>${F.sgn(g.vega / 100, 1)}</b></div><div><span>theta /day</span><b>${F.sgn(thDay, 1)}</b></div>
        <div><span>Γ P&L, 1% move</span><b>${F.sgn(gam1, 1)}</b></div><div><span>move to cover θ</span><b class="a">${beMove ? F.n(beMove * 100, 2) + "%/day" : "n/a"}</b></div>
        <div><span>implied daily σ</span><b>${F.n(atm / Math.sqrt(252) * 100, 2)}%</b></div><div><span>realized daily σ</span><b>${u.rv["20"] ? F.n(u.rv["20"] / Math.sqrt(252) * 100, 2) + "%" : "n/a"}</b></div></div>`;
      q("#sb-st").textContent = `spot ${F.n(u.spot)} · horizon = first expiry (${hz}d) · scenario line: T+${c.days}d, vol ${F.sgn(c.volAdd * 100, 0)} pts · position cost incl. underlying ${F.n(cost, 0)}`;
      const lo = u.spot * (1 - c.rng), hi = u.spot * (1 + c.rng);
      if (tab === "pnl") { pane.style.background = "#fff"; T.canvas(pane, (gx, w, h) => { const sx = T.plot(gx, w, h, [
          { pts: BS.curve(legs, exp, lo, hi), color: T.C[0], w: 1.8, name: `expiry (${hz}d)`, fill: true }, { pts: BS.curve(legs, now, lo, hi), color: T.C[2], name: "today" },
          { pts: BS.curve(legs, sc, lo, hi), color: T.C[1], dash: [5, 3], name: `T+${c.days}d, vol ${F.sgn(c.volAdd * 100, 0)}` }],
          { legend: true, zero: true, yfmt: (v) => F.n(v, 0), vlines: [{ x: u.spot, color: "#333", label: "spot" }, { x: u.spot - sd, color: "#8c96a3", label: "-1σ", row: 1 }, { x: u.spot + sd, color: "#8c96a3", label: "+1σ", row: 1 },
            ...be.filter((x) => x > lo && x < hi).map((x) => ({ x, color: T.C[3], label: F.n(x), row: 2 }))] });
        pane.onmousemove = (ev) => { if (!sx) return; const S = sx.ix(ev.offsetX); if (S < lo || S > hi) return T.tip(ev, null);
          T.tip(ev, `spot ${F.n(S)} (${F.pct(S / u.spot - 1)})\nexpiry ${F.sgn(BS.pnl(legs, S, exp), 0)}\ntoday  ${F.sgn(BS.pnl(legs, S, now), 0)}\nscen   ${F.sgn(BS.pnl(legs, S, sc), 0)}`); }; pane.onmouseleave = () => T.tip({}, null); }); }
      else if (tab === "cvx") convexity(c, sc, lo, hi, atm);
      else if (tab === "grid") scenario(c);
      else byLeg(c, now);
    }
    /* Convexity: delta and gamma profiles, and a Taylor split of the P&L for
       sigma-sized moves so you can see how much is delta, gamma, and the rest. */
    function convexity(c, sc, lo, hi, atm) { const { u } = c, legs = book.legs; pane.style.background = "#fff"; pane.onmousemove = null;
      pane.innerHTML = `<div class="split" style="height:100%"><div style="flex:1.1;background:#fff" id="cx-a"></div><div class="fill" style="flex:1" id="cx-b"></div></div>`;
      const xs = []; for (let i = 0; i <= 120; i++) xs.push(lo + (hi - lo) * i / 120);
      const gk = xs.map((S) => BS.bookGreeks(legs, S, sc));
      const gmax = Math.max(...gk.map((x) => Math.abs(x.gamma))) || 1, dmax = Math.max(...gk.map((x) => Math.abs(x.delta))) || 1;
      T.canvas(pane.querySelector("#cx-a"), (g, w, h) => T.plot(g, w, h, [{ pts: xs.map((S, i) => [S, gk[i].delta]), color: T.C[0], name: "delta" },
        { pts: xs.map((S, i) => [S, gk[i].gamma * dmax / gmax]), color: T.C[1], name: `gamma (x${F.auto(dmax / gmax)})` },
        { pts: xs.map((S, i) => [S, gk[i].vega / 100 * dmax / (Math.max(...gk.map((x) => Math.abs(x.vega / 100))) || 1)]), color: T.C[4], dash: [4, 3], name: "vega (scaled)" }],
        { legend: true, zero: true, yfmt: (v) => F.n(v, 0), vlines: [{ x: u.spot, color: "#333", label: "spot" }] }));
      const g0 = BS.bookGreeks(legs, u.spot, sc), base = BS.pnl(legs, u.spot, sc), d1 = u.spot * atm / Math.sqrt(252), rows = [];
      for (const k of [-3, -2, -1, -0.5, 0.5, 1, 2, 3]) { const dS = k * d1, act = BS.pnl(legs, u.spot + dS, sc) - base, dl = g0.delta * dS, gm = 0.5 * g0.gamma * dS * dS;
        rows.push({ k, dS, pct: dS / u.spot, act, dl, gm, res: act - dl - gm, cvx: act - dl }); }
      T.table(pane.querySelector("#cx-b"), [{ k: "k", t: "σ (1d)", f: (v) => F.sgn(v, 1) }, { k: "pct", t: "move", f: (v) => F.pct(v, 2) }, { k: "act", t: "P&L", f: (v) => F.sgn(v, 0), c: F.cls },
        { k: "dl", t: "delta", f: (v) => F.sgn(v, 0), c: () => "mut" }, { k: "gm", t: "½Γ·dS²", f: (v) => F.sgn(v, 0), c: F.cls, tip: "second-order (gamma) contribution" },
        { k: "res", t: "higher", f: (v) => F.sgn(v, 0), c: () => "mut", tip: "what delta and gamma do not explain: speed, and vol/time cross terms" },
        { k: "cvx", t: "convexity", f: (v) => F.sgn(v, 0), c: F.cls, tip: "P&L after delta hedging at inception: actual minus delta" }], rows, { nosort: true });
      pane.querySelector("#cx-b").insertAdjacentHTML("beforeend", `<div class="note" style="margin:4px">One-day sigma moves at ${F.n(atm * 100, 1)} vol = ${F.n(d1)} per σ. "Convexity" is what a delta-hedged book earns from the move itself. Compare with theta ${F.sgn(g0.theta / 365, 1)}/day: long gamma pays when realized moves exceed the cover rate above. Vanna ${F.sgn(g0.vanna / 100, 2)} (Δ per vol pt), volga ${F.sgn(g0.volga / 1e4, 2)} (vega per vol pt).</div>`); }
    function scenario(c) { const { u } = c, legs = book.legs; pane.style.background = "#fff"; pane.onmousemove = null;
      const moves = [-0.3, -0.2, -0.15, -0.1, -0.05, -0.02, 0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3], vols = [-15, -10, -5, 0, 5, 10, 15, 25];
      const cell = (m, v) => BS.pnl(legs, u.spot * (1 + m), { ...c.base, days: c.days, volAdd: v / 100 }), all = moves.flatMap((m) => vols.map((v) => cell(m, v))), mx = Math.max(...all.map(Math.abs)) || 1;
      pane.innerHTML = `<table class="g"><thead><tr><th class="l">Spot \\ Vol (T+${c.days}d)</th>${vols.map((v) => `<th>${F.sgn(v, 0)} pts</th>`).join("")}</tr></thead><tbody>` +
        moves.map((m) => `<tr><td class="l"><b>${F.pct(m, 0)}</b> &nbsp;${F.n(u.spot * (1 + m))}</td>${vols.map((v) => { const x = cell(m, v), a = Math.min(1, Math.abs(x) / mx) * 0.55;
          return `<td style="background:${x >= 0 ? `rgba(0,128,0,${a})` : `rgba(192,0,0,${a})`}">${F.sgn(x, 0)}</td>`; }).join("")}</tr>`).join("") + `</tbody></table>`; }
    function byLeg(c, now) { const { u } = c; pane.style.background = "#fff"; pane.onmousemove = null;
      const rows = book.legs.map((l) => { const g = BS.legGreeks(l, u.spot, now), m = l.qty * l.mult;
        return { name: `${l.qty > 0 ? "+" : ""}${l.qty} ${l.type === "u" ? u.symbol : F.auto(l.k) + (l.type === "c" ? " C " : " P ") + l.exp}`, val: m * BS.legValue(l, u.spot, now), pl: m * (BS.legValue(l, u.spot, now) - l.entry), d: g.delta * m, g: g.gamma * m, v: g.vega * m / 100, t: g.theta * m / 365, va: g.vanna * m / 100, vo: g.volga * m / 1e4, dd: g.delta * m * u.spot }; });
      const tot = { name: "TOTAL", _tot: 1 }; for (const k of ["val", "pl", "d", "g", "v", "t", "va", "vo", "dd"]) tot[k] = rows.reduce((s, r) => s + r[k], 0);
      T.table(pane, [{ k: "name", t: "Leg", l: 1 }, { k: "val", t: "Value", f: (v) => F.n(v, 0) }, { k: "pl", t: "P&L vs entry", f: (v) => F.sgn(v, 0), c: F.cls }, { k: "d", t: "Delta", f: (v) => F.sgn(v, 1) }, { k: "dd", t: "$ delta", f: (v) => F.sgn(v, 0) },
        { k: "g", t: "Gamma", f: (v) => F.sgn(v, 3) }, { k: "v", t: "Vega/pt", f: (v) => F.sgn(v, 1) }, { k: "t", t: "Theta/day", f: (v) => F.sgn(v, 1) }, { k: "va", t: "Vanna/pt", f: (v) => F.sgn(v, 2) }, { k: "vo", t: "Volga/pt", f: (v) => F.sgn(v, 2) }], rows.concat([tot]), { nosort: true }); }
    win.el.addEventListener("input", (ev) => { if (/^sb-[dvr]$/.test(ev.target.id)) draw(); });
    q("#sb-u").onchange = () => { setBook(enrich(D.underlyings.find((x) => x.symbol === q("#sb-u").value), D.meta.rate)); sync(); build(); };
    q("#sb-b").onclick = build; q("#sb-clr").onclick = () => { book.legs = []; draw(); };
    q("#sb-add").onclick = () => { book.legs.push(mkLeg("c", book.u.expiries[+q("#sb-e").value], book.u.spot, 1)); draw(); };
    q("#sb-oc").onclick = () => T.open("chain", book.symbol);
    win.el.querySelectorAll(".tabs")[0].onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b)); draw(); };
    win.onResize = () => {};
  } });
})();
