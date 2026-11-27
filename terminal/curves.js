/* Futures curves: shape vs the past, spreads between any two contracts, cracks.
   Comparisons hold the contract fixed; no rolling front month is rebuilt. */
"use strict";
(() => {
  const { F, esc } = T;
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const lab = (d) => `${MON[+d.slice(5) - 1]} ${d.slice(2, 4)}`, x = (d) => +d.slice(0, 4) * 12 + +d.slice(5) - 1;
  const xf = (v) => { const r = Math.round(v); return Math.abs(v - r) > 0.01 ? "" : `${MON[((r % 12) + 12) % 12]} ${String(Math.floor(r / 12)).slice(2)}`; };
  const dp = (p) => (Math.abs(p) >= 500 ? 1 : Math.abs(p) >= 20 ? 2 : 4);
  T.app("curves", { title: "Futures Curves", group: "Markets", w: 7, h: 12, open(win, arg) {
    win.add(`<div class="toolbar"><label>Market <select class="fc-r"></select></label><span class="fc-legs" hidden><label>Buy <select class="fc-a"></select></label><label>Sell <select class="fc-b"></select></label></span></div>`);
    win.add(`<div class="tabs"><button data-t="c" class="on">Curve</button><button data-t="t">Table</button><button data-t="s">Spread history</button><button data-t="k">Cracks</button></div>`);
    const lcd = win.add(`<div class="lcd"></div>`), pane = win.add(`<div class="fill in" style="flex:1;background:var(--paper);min-height:0"></div>`), st = win.add(`<div class="status"><div></div></div>`).querySelector("div");
    const q = (s) => win.el.querySelector(s); let D, R, root, tab = "c", leg = {};
    const kv = (o) => { lcd.innerHTML = `<div class="kvs">${o.map(([k, v]) => `<div><span>${esc(k)}</span><b>${v}</b></div>`).join("")}</div>`; };
    const clear = () => { if (pane._ro) { pane._ro.disconnect(); pane._ro = null; } pane.onmousemove = pane.onmouseleave = null; T.tip({}, null); pane.innerHTML = ""; };
    const chart = (ser, o, tip) => { const c = document.createElement("div"); c.style.height = "100%"; pane.append(c); T.canvas(c, (g, w, h) => { c._sx = T.plot(g, w, h, ser, { legend: true, ...o }); });
      if (tip) { c.onmousemove = (ev) => { if (c._sx) T.tip(ev, tip(c._sx.ix(ev.offsetX))); }; c.onmouseleave = () => T.tip({}, null); } };
    const near = (arr, v, f) => arr.reduce((b, r) => (Math.abs(f(r) - v) < Math.abs(f(b) - v) ? r : b));
    const PAST = [["w1", "1 week ago", 1], ["m1", "1 month ago", 2], ["y1", "1 year ago", 5]];
    function draw() { if (!R) return; clear(); q(".fc-legs").hidden = tab !== "s";
      const f = R.curve[R.front], n = dp(f.close), sp = (p) => (p ? `${F.sgn(p.spread, n)}` : "-"), ry = (p) => (p && p.roll != null ? F.pct(p.roll) : "-");
      if (tab === "k") { const K = D.cracks; if (!K) { lcd.innerHTML = ""; pane.innerHTML = `<div class="pad">No cracks: WTI, RBOB and ULSD are not on the same session.</div>`; st.textContent = ""; return; }
        const r = K.rows[0]; kv([[`3-2-1 ${lab(r.delivery)} $/bbl`, F.n(r.c321, 2)], ["vs 1 week", r.w1 == null ? "-" : F.sgn(r.c321 - r.w1, 2)], ["vs 1 month", r.m1 == null ? "-" : F.sgn(r.c321 - r.m1, 2)], ["RBOB crack", F.n(r.rbob, 2)], ["ULSD crack", F.n(r.ulsd, 2)], ["as of", K.asof]]);
        T.table(pane, [{ k: "delivery", t: "Delivery", l: 1, f: lab }, { k: "rbob", t: "RBOB", f: (v) => F.n(v, 2) }, { k: "ulsd", t: "ULSD", f: (v) => F.n(v, 2) }, { k: "c321", t: "3-2-1", f: (v) => `<b>${F.n(v, 2)}</b>` },
          ...[["w1", "vs 1w"], ["m1", "vs 1m"], ["y1", "vs 1y"]].map(([k, t]) => ({ k: (z) => (z[k] == null ? null : z.c321 - z[k]), t, f: (v) => (v == null ? "-" : F.sgn(v, 2)), c: F.cls }))], K.rows, { nosort: true });
        st.textContent = "$/bbl against WTI, same delivery month. All markets; ignores the selector above."; return; }
      kv([[`${R.front_rule} ${lab(f.delivery)}`, F.n(f.close, n)], [R.next ? `to ${lab(R.next.to)}` : "next", sp(R.next)], ["roll, ann.", ry(R.next)], [R.year ? `to ${lab(R.year.to)}` : "12m+", sp(R.year)], ["roll, ann.", ry(R.year)], ["shape", R.year || R.next ? ((R.year || R.next).spread > 0 ? "backwardation" : "contango") : "-"], ["as of", R.asof]]);
      if (tab === "c") { const ser = [{ name: R.asof, color: T.C[0], w: 2, dots: 1, pts: R.curve.map((r) => [x(r.delivery), r.close]) },
          ...PAST.filter(([k]) => R.compare[k]).map(([k, t, c]) => ({ name: t, color: T.C[c], dash: [4, 3], pts: R.curve.filter((r) => r[k] != null).map((r) => [x(r.delivery), r[k]]) }))];
        chart(ser, { xfmt: xf, yfmt: (v) => F.n(v, Math.min(n, 2)) }, (v) => { const r = near(R.curve, v, (z) => x(z.delivery)); return `${lab(r.delivery)}  ${F.n(r.close, n)}` + PAST.map(([k, t]) => (r[k] == null ? "" : `\n${t} ${F.n(r[k], n)} (${F.sgn(r.close - r[k], n)})`)).join(""); });
        st.textContent = `${R.unit}. Dashed lines price the same contracts in the past.`; }
      else if (tab === "t") { T.table(pane, [{ k: "delivery", t: "Delivery", l: 1, f: (v, r) => (r === f ? `<b>${lab(v)}</b>` : lab(v)) }, { k: "close", t: "Close", f: (v) => F.n(v, n) }, ...[["w1", "1w chg"], ["m1", "1m chg"], ["y1", "1y chg"]].map(([k, t]) => ({ k: (r) => (r[k] == null ? null : r.close - r[k]), t, f: (v) => (v == null ? "-" : F.sgn(v, n)), c: F.cls })),
          { k: "spread", t: "To next", f: (v) => (v == null ? "-" : F.sgn(v, n)), c: F.cls }, { k: "roll", t: "Roll ann.", f: (v) => (v == null ? "-" : F.pct(v)), c: F.cls }, { k: "volume", t: "Vol (5d med)", f: (v) => F.n(v, 0) }], R.curve, { nosort: true });
        st.textContent = `${R.unit}. Bold = front (${R.front_rule}). Low volume means a close may be stale.`; }
      else { const H = R.history, a = H.closes[leg.a], b = H.closes[leg.b]; if (!a || !b || leg.a === leg.b) { pane.innerHTML = `<div class="pad">Pick two different contracts.</div>`; st.textContent = ""; return; }
        const pts = H.dates.map((d, i) => [Date.parse(d), a[i] == null || b[i] == null ? NaN : a[i] - b[i]]), ok = pts.filter((p) => isFinite(p[1])); if (!ok.length) { pane.innerHTML = `<div class="pad">No common dates.</div>`; st.textContent = ""; return; }
        const v = ok.map((p) => p[1]), L = v[v.length - 1], lo = Math.min(...v), hi = Math.max(...v), mean = v.reduce((s, z) => s + z, 0) / v.length;
        kv([[`${lab(leg.a)} - ${lab(leg.b)}`, F.sgn(L, n)], ["1 day", v.length > 1 ? F.sgn(L - v[v.length - 2], n) : "-"], ["low", F.sgn(lo, n)], ["high", F.sgn(hi, n)], ["mean", F.sgn(mean, n)], ["percentile", F.pct(v.filter((z) => z <= L).length / v.length, 0).replace("+", "")], ["days", v.length]]);
        chart([{ name: `${root} ${lab(leg.a)} minus ${lab(leg.b)}`, color: T.C[0], w: 1.6, pts }], { xfmt: (z) => new Date(z).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }), yfmt: (z) => F.n(z, Math.min(n, 3)) }, (z) => { const p = near(ok, z, (w) => w[0]); return `${new Date(p[0]).toISOString().slice(0, 10)}\n${F.sgn(p[1], n)}`; });
        st.textContent = `${R.unit}. Two fixed contracts, up to one year; far months trade thinly early on.`; }
    }
    function set(r) { if (!D || !D.roots[r]) return; root = r; R = D.roots[r]; q(".fc-r").value = r; win.setArg(r); win.setTitle(`Futures Curves - ${R.name}`);
      const ks = Object.keys(R.history.closes).sort(), o = ks.map((k) => `<option value="${k}">${lab(k)}</option>`).join(""); q(".fc-a").innerHTML = o; q(".fc-b").innerHTML = o;
      leg.a = ks.includes(R.curve[R.front].delivery) ? R.curve[R.front].delivery : ks[0]; leg.b = R.next && ks.includes(R.next.to) ? R.next.to : ks.find((k) => k > leg.a); q(".fc-a").value = leg.a; q(".fc-b").value = leg.b; draw(); }
    q(".fc-r").onchange = () => set(q(".fc-r").value); q(".fc-a").onchange = () => { leg.a = q(".fc-a").value; draw(); }; q(".fc-b").onchange = () => { leg.b = q(".fc-b").value; draw(); };
    win.onArg = set;
    win.el.querySelector(".tabs").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; tab = b.dataset.t; win.el.querySelectorAll(".tabs button").forEach((z) => z.classList.toggle("on", z === b)); draw(); };
    T.load("curves").then((d) => { if (!d) { lcd.textContent = "curves.json missing. Run scripts/curve_archive.py --emit"; return; } D = d;
      const g = [...new Set(Object.values(d.roots).map((v) => v.group))]; q(".fc-r").innerHTML = g.map((z) => `<optgroup label="${esc(z)}">${Object.entries(d.roots).filter(([, v]) => v.group === z).map(([k, v]) => `<option value="${k}">${k}  ${esc(v.name)}</option>`).join("")}</optgroup>`).join("");
      set(d.roots[arg] ? arg : Object.keys(d.roots)[0]); });
  } });
})();
