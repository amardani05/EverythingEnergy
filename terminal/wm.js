/* Shell + shared helpers. Everything hangs off one global, T.
   The screen is a row of tabs; each tab is a grid of widgets the user can
   drag, resize, add and remove, and the arrangement is remembered. Widgets
   are apps registered with T.app(id, {title, group, w, h, open(win, arg)}).
   Data files are fetched once through T.load(name) and shared. */
"use strict";
const T = (() => {
  const $ = (h) => { const t = document.createElement("template"); t.innerHTML = h.trim(); return t.content.firstElementChild; };
  const apps = {};

  /* ---------- formatting ---------- */
  const nf = (d) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const F = {
    n: (v, d = 2) => v == null || !isFinite(v) ? "" : nf(d).format(v),
    auto: (v) => { if (v == null || !isFinite(v)) return ""; const a = Math.abs(v);
      return nf(a >= 10000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6).format(v); },
    big: (v) => { if (v == null || !isFinite(v)) return ""; const a = Math.abs(v);
      return a >= 1e9 ? nf(2).format(v / 1e9) + "B" : a >= 1e6 ? nf(2).format(v / 1e6) + "M" : a >= 1e4 ? nf(0).format(v) : F.auto(v); },
    pct: (v, d = 1) => v == null || !isFinite(v) ? "" : (v >= 0 ? "+" : "") + nf(d).format(v * 100) + "%",
    share: (v, d = 1) => v == null || !isFinite(v) ? "" : nf(d).format(v * 100) + "%",
    sgn: (v, d = 2) => v == null || !isFinite(v) ? "" : (v >= 0 ? "+" : "") + nf(d).format(v),
    cls: (v) => v == null || !isFinite(v) || v === 0 ? "" : v > 0 ? "up" : "dn",
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  /* chart palette: blue, orange, teal, red, purple, grey */
  const C = ["#1f6fb5", "#e0711c", "#12998a", "#c8312b", "#7b52ab", "#6b7785", "#8a6d00", "#c2478f"];

  /* ---------- data ---------- */
  const FILES = { market: "market_data.json", options: "options_data.json", commodities: "commodities.json", flows: "flows_data.json",
    balance: "oil_balance.json", pipelines: "infra_pipelines.geojson", facilities: "infra_facilities.geojson",
    atlas: "dashboard_data.json", signals: "signals_latest.json" };
  const cache = {};
  function load(name) {
    return cache[name] ||= fetch(FILES[name] + "?t=" + Date.now()).then((r) => { if (!r.ok) throw new Error(FILES[name] + ": HTTP " + r.status); return r.json(); })
      .catch((e) => { console.warn("[data]", e.message); return null; });
  }
  const loadAll = (...names) => Promise.all(names.map(load));

  /* ---------- tables ---------- */
  /* cols: [{k, t, l(eft), f(ormat fn), c(lass fn), bar, tip}] ; opts: {onRow, sort, dir, nosort} */
  function table(host, cols, rows, opts = {}) {
    let sortK = opts.sort ?? null, dir = opts.dir || -1, data = rows.slice();
    const el = $(`<table class="g"><thead><tr></tr></thead><tbody></tbody></table>`);
    const val = (r, c) => (typeof c.k === "function" ? c.k(r) : r[c.k]);
    function draw() {
      if (sortK != null) { const c = cols[sortK]; data.sort((a, b) => { const x = val(a, c), y = val(b, c);
        if (x == null || x === "") return 1; if (y == null || y === "") return -1; return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir; }); }
      el.tHead.rows[0].innerHTML = cols.map((c, i) => `<th class="${c.l ? "l" : ""}${i === sortK ? " sorted" : ""}" data-i="${i}" title="${esc(c.tip || "")}">${esc(c.t)}${i === sortK ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
      el.tBodies[0].innerHTML = data.map((r, ri) => r._hd ? `<tr class="hd"><td colspan="${cols.length}">${esc(r._hd)}</td></tr>` :
        `<tr data-r="${ri}" class="${r._tot ? "tot" : ""}">` + cols.map((c) => { const v = val(r, c);
          if (c.bar) return `<td class="bar"><i style="width:${Math.max(0, Math.min(100, (v || 0) * 100 / (c.bar === true ? 1 : c.bar)))}%"></i></td>`;
          return `<td class="${c.l ? "l " : ""}${c.c ? c.c(v, r) || "" : ""}">${c.f ? c.f(v, r) : c.l ? esc(v ?? "") : F.auto(v)}</td>`; }).join("") + "</tr>").join("");
    }
    el.tHead.onclick = (e) => { const th = e.target.closest("th"); if (!th || opts.nosort) return; const i = +th.dataset.i; if (cols[i].bar) return;
      if (sortK === i) dir = -dir; else { sortK = i; dir = cols[i].l ? 1 : -1; } draw(); };
    el.tBodies[0].onclick = (e) => { const tr = e.target.closest("tr[data-r]"); if (!tr) return;
      el.querySelectorAll("tr.sel").forEach((x) => x.classList.remove("sel")); tr.classList.add("sel");
      if (opts.onRow) opts.onRow(data[+tr.dataset.r], e); };
    draw(); host.innerHTML = ""; host.appendChild(el);
    return { el, set(r) { data = r.slice(); draw(); }, rows: () => data };
  }

  /* ---------- canvas charts ---------- */
  function canvas(host, draw) {
    if (host._ro) host._ro.disconnect();
    const cv = document.createElement("canvas"); host.innerHTML = ""; host.appendChild(cv);
    const paint = () => { const w = host.clientWidth, h = host.clientHeight; if (w < 20 || h < 20) return; const dpr = window.devicePixelRatio || 1;
      cv.width = w * dpr; cv.height = h * dpr; cv.style.width = w + "px"; cv.style.height = h + "px";
      const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h); draw(g, w, h); };
    host._ro = new ResizeObserver(paint); host._ro.observe(host); paint();
    return { cv, paint };
  }
  const ticks = (lo, hi, n = 5) => { if (!(hi > lo)) return [lo]; const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    const st = (m >= 5 ? 10 : m >= 2 ? 5 : m >= 1 ? 2 : 1) * p, out = []; for (let v = Math.ceil(lo / st) * st; v <= hi + st * 1e-9; v += st) out.push(Math.abs(v) < st * 1e-9 ? 0 : v); return out; };
  const FONT = "10.5px Tahoma, 'Segoe UI', Arial, sans-serif";
  /* series: [{pts:[[x,y]...], color, w, dash, fill, dots, name}] ; o: {xfmt, yfmt, vlines:[{x,color,label,row}], zero, pad, xdom, ydom, legend} */
  function plot(g, w, h, series, o = {}) {
    const bg = "#ffffff", ax = "#d3dae3", tx = "#4a5563";
    const p = { l: 48, r: 10, t: o.legend ? 20 : 8, b: 20, ...(o.pad || {}) };
    g.fillStyle = bg; g.fillRect(0, 0, w, h);
    const all = series.flatMap((s) => s.pts).filter((q) => isFinite(q[0]) && isFinite(q[1]));
    g.font = FONT;
    if (!all.length) { g.fillStyle = tx; g.fillText("no data", 12, 20); return null; }
    let [x0, x1] = o.xdom || [Math.min(...all.map((q) => q[0])), Math.max(...all.map((q) => q[0]))];
    let [y0, y1] = o.ydom || [Math.min(...all.map((q) => q[1])), Math.max(...all.map((q) => q[1]))];
    if (o.zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
    if (!o.ydom) { const pd = (y1 - y0) * 0.06 || Math.abs(y1) * 0.05 || 1; y0 -= pd; y1 += pd; }
    if (x1 === x0) x1 = x0 + 1;
    const X = (v) => p.l + (v - x0) / (x1 - x0) * (w - p.l - p.r), Y = (v) => h - p.b - (v - y0) / (y1 - y0) * (h - p.t - p.b);
    g.lineWidth = 1; g.strokeStyle = ax; g.fillStyle = tx; g.setLineDash([1, 3]);
    for (const t of ticks(y0, y1, Math.max(2, Math.floor(h / 42)))) { g.beginPath(); g.moveTo(p.l, Math.round(Y(t)) + .5); g.lineTo(w - p.r, Math.round(Y(t)) + .5); g.stroke();
      g.textAlign = "right"; g.fillText((o.yfmt || F.auto)(t), p.l - 5, Y(t) + 3.5); }
    g.setLineDash([]);
    for (const t of o.xticks || ticks(x0, x1, Math.max(2, Math.floor(w / 95)))) { g.strokeStyle = ax; g.beginPath(); g.moveTo(Math.round(X(t)) + .5, h - p.b); g.lineTo(Math.round(X(t)) + .5, h - p.b + 4); g.stroke();
      g.textAlign = "center"; g.fillText((o.xfmt || F.auto)(t), X(t), h - 5); }
    if (y0 < 0 && y1 > 0) { g.strokeStyle = "#7d8896"; g.beginPath(); g.moveTo(p.l, Math.round(Y(0)) + .5); g.lineTo(w - p.r, Math.round(Y(0)) + .5); g.stroke(); }
    g.save(); g.beginPath(); g.rect(p.l, p.t, w - p.l - p.r, h - p.t - p.b); g.clip();
    for (const s of series) { const pts = s.pts.filter((q) => isFinite(q[0]) && isFinite(q[1])); if (!pts.length) continue;
      g.strokeStyle = s.color || C[0]; g.lineWidth = s.w || 1.4; g.setLineDash(s.dash || []); g.lineJoin = "round";
      if (s.dots) { g.fillStyle = s.color; pts.forEach((q) => { g.beginPath(); g.arc(X(q[0]), Y(q[1]), 1.8, 0, 6.3); g.fill(); }); if (s.dots === "only") continue; }
      g.beginPath(); pts.forEach((q, i) => (i ? g.lineTo(X(q[0]), Y(q[1])) : g.moveTo(X(q[0]), Y(q[1])))); g.stroke();
      if (s.fill) { g.lineTo(X(pts[pts.length - 1][0]), Y(0)); g.lineTo(X(pts[0][0]), Y(0)); g.closePath(); g.globalAlpha = 0.1; g.fillStyle = s.color; g.fill(); g.globalAlpha = 1; } }
    g.setLineDash([]);
    for (const v of o.vlines || []) { if (v.x < x0 || v.x > x1) continue; g.strokeStyle = v.color || C[1]; g.lineWidth = 1; g.setLineDash(v.dash || [3, 3]); g.beginPath(); g.moveTo(X(v.x), p.t); g.lineTo(X(v.x), h - p.b); g.stroke(); g.setLineDash([]);
      if (v.label) { g.fillStyle = v.color || C[1]; g.textAlign = "left"; g.fillText(v.label, X(v.x) + 3, p.t + 10 + (v.row || 0) * 12); } }
    g.restore();
    if (o.legend) { let lx = p.l + 2; g.textAlign = "left"; for (const s of series) { if (!s.name) continue; g.strokeStyle = s.color; g.lineWidth = 2; g.setLineDash(s.dash || []); g.beginPath(); g.moveTo(lx, 10.5); g.lineTo(lx + 14, 10.5); g.stroke(); g.setLineDash([]);
      g.fillStyle = tx; g.fillText(s.name, lx + 18, 14); lx += 30 + g.measureText(s.name).width; } }
    g.strokeStyle = "#b9c3ce"; g.lineWidth = 1; g.strokeRect(p.l + .5, p.t + .5, w - p.l - p.r, h - p.t - p.b);
    return { X, Y, x0, x1, y0, y1, p, ix: (px) => x0 + (px - p.l) / (w - p.l - p.r) * (x1 - x0) };
  }

  const tipEl = () => document.getElementById("tip");
  const tip = (ev, text) => { const t = tipEl(); if (!text) { t.style.display = "none"; return; } t.textContent = text; t.style.display = "block";
    t.style.left = Math.min(ev.clientX + 12, innerWidth - t.offsetWidth - 4) + "px"; t.style.top = Math.min(ev.clientY + 14, innerHeight - t.offsetHeight - 4) + "px"; };
  function csv(rows, cols) { const q = (v) => v == null ? "" : /[",\n\t]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
    return [cols.join(",")].concat(rows.map((r) => cols.map((c) => q(r[c])).join(","))).join("\n"); }
  function download(name, text) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); }

  /* ================= workspace: tabs of widget grids ================= */
  const KEY = "ee.terminal.layout.v3", ROWS = 20;
  const cellH = () => Math.max(30, Math.floor((document.getElementById("work").clientHeight - 8) / ROWS));   // 20 rows fill the screen at any window size
  let DEFAULTS = [], state = null, panes = {}, saveT = 0, uid = 0;
  const ctx = {};
  const store = { get() { try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* private mode: layout just will not persist */ } },
    clear() { try { localStorage.removeItem(KEY); } catch (e) { /* nothing to clear */ } } };
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const tabOf = (id) => state.tabs.find((t) => t.id === id);
  const active = () => panes[state.active];
  function save() { clearTimeout(saveT); saveT = setTimeout(() => { for (const t of state.tabs) { const p = panes[t.id]; if (!p) continue;
      t.items = p.wins.map((w) => { const n = w.item.gridstackNode || {}; return { app: w.id, arg: w.arg ?? null, x: n.x ?? 0, y: n.y ?? 0, w: n.w ?? 4, h: n.h ?? 6 }; }).sort((a, b) => a.y - b.y || a.x - b.x); }
    store.set(state); }, 250); }

  function addWidget(pane, it, auto) {
    const app = apps[it.app]; if (!app) return null;
    const item = $(`<div class="grid-stack-item" gs-min-w="2" gs-min-h="3"><div class="grid-stack-item-content wg"><div class="wh"><span class="tt"></span><button data-a="max" title="Maximize / restore">▢</button><button data-a="x" title="Remove widget">✕</button></div><div class="wb"></div></div></div>`);
    item.setAttribute("gs-w", it.w || app.w || 4); item.setAttribute("gs-h", it.h || app.h || 8); item.setAttribute("gs-id", "w" + (++uid));
    if (auto) item.setAttribute("gs-auto-position", "true"); else { item.setAttribute("gs-x", it.x || 0); item.setAttribute("gs-y", it.y || 0); }
    const body = item.querySelector(".wb"), el = item.querySelector(".wg");
    const w = { id: it.app, app, arg: it.arg ?? null, item, el, body, title: "", pane,
      setTitle(t) { w.title = t; item.querySelector(".tt").textContent = t; },
      add(node) { const n = typeof node === "string" ? $(node) : node; body.appendChild(n); return n; },
      setArg(a) { w.arg = a; save(); },
      resized() { if (w.onResize) w.onResize(); },
      close() { if (item.classList.contains("maxed")) maximize(w, false); pane.wins.splice(pane.wins.indexOf(w), 1); if (w.onClose) { try { w.onClose(); } catch (e) { console.error(e); } }
        pane.grid.removeWidget(item); emptyNote(pane); save(); } };
    pane.gridEl.appendChild(item); pane.grid.makeWidget(item); pane.wins.push(w); w.setTitle(app.title);
    item.querySelector(".wh").addEventListener("click", (e) => { const a = e.target.dataset.a; if (a === "x") w.close(); else if (a === "max") maximize(w); });
    item.querySelector(".wh").addEventListener("dblclick", (e) => { if (!e.target.dataset.a) maximize(w); });
    try { app.open(w, it.arg ?? undefined); } catch (e) { console.error(e); w.add(`<div class="pad">Failed to open: ${esc(e.message)}</div>`); }
    emptyNote(pane); return w;
  }
  function maximize(w, on) { const it = w.item, to = on ?? !it.classList.contains("maxed"), r = document.getElementById("work").getBoundingClientRect();
    document.querySelectorAll(".grid-stack-item.maxed").forEach((x) => x.classList.remove("maxed"));
    if (to) { for (const [k, v] of [["--mx", r.left], ["--my", r.top], ["--mw", r.width], ["--mh", r.height]]) it.style.setProperty(k, v + "px"); it.classList.add("maxed"); }
    try { w.pane.grid.movable(it, !to && !state.locked); w.pane.grid.resizable(it, !to && !state.locked); } catch (e) { /* older gridstack: header drag stays live */ }
    setTimeout(() => w.resized(), 30); }
  function emptyNote(pane) { let n = pane.el.querySelector(".empty"); if (pane.wins.length) { if (n) n.remove(); return; }
    if (!n) pane.el.appendChild($(`<div class="empty"><b>This tab is empty.</b><br>Add widgets from the list on the left. Drag a widget by its title bar, resize from its bottom-right corner. The layout is saved automatically.</div>`)); }
  function buildPane(tab) {
    const el = $(`<div class="pane"><div class="grid-stack"></div></div>`); document.getElementById("work").appendChild(el);
    const gridEl = el.querySelector(".grid-stack");
    const grid = GridStack.init({ column: 12, cellHeight: cellH(), margin: 3, float: false, animate: false, draggable: { handle: ".wh", cancel: "button" }, resizable: { handles: "se" }, alwaysShowResizeHandle: true, columnOpts: { breakpoints: [{ w: 700, c: 1 }] } }, gridEl);
    const pane = panes[tab.id] = { el, gridEl, grid, wins: [], tab };
    grid.batchUpdate(); for (const it of tab.items) addWidget(pane, it, false); grid.batchUpdate(false);
    grid.on("change added removed", save); grid.on("resizestop", (ev, item) => { const w = pane.wins.find((x) => x.item === item); if (w) w.resized(); });
    if (state.locked) { grid.enableMove(false); grid.enableResize(false); }
    emptyNote(pane); return pane;
  }
  function show(id) { if (!tabOf(id)) id = state.tabs[0].id; state.active = id;
    document.querySelectorAll(".grid-stack-item.maxed").forEach((x) => x.classList.remove("maxed"));
    const pane = panes[id] || buildPane(tabOf(id));
    for (const p of Object.values(panes)) p.el.classList.toggle("on", p === pane);
    renderTabs(); save(); setTimeout(() => pane.wins.forEach((w) => w.resized()), 30); }
  function renderTabs() { const bar = document.getElementById("tabs"); bar.innerHTML = "";
    for (const t of state.tabs) { const b = $(`<button class="tab${t.id === state.active ? " on" : ""}" title="Double-click to rename">${esc(t.name)}</button>`);
      b.onclick = () => show(t.id); b.ondblclick = () => renameTab(t.id); bar.appendChild(b); }
    const add = $(`<button class="tab add" title="New empty tab">+</button>`); add.onclick = newTab; bar.appendChild(add);
    const on = bar.querySelector(".tab.on"); if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
    document.body.classList.toggle("locked", !!state.locked); }
  function newTab() { const name = (prompt("Name for the new tab:", "My tab") || "").trim(); if (!name) return;
    const t = { id: "u" + Date.now().toString(36), name, custom: true, items: [] }; state.tabs.push(t); show(t.id); }
  function renameTab(id) { const t = tabOf(id), name = (prompt("Rename tab:", t.name) || "").trim(); if (!name) return; t.name = name; renderTabs(); save(); }
  function dropPane(id) { const p = panes[id]; if (!p) return; p.wins.slice().forEach((w) => { if (w.onClose) { try { w.onClose(); } catch (e) { console.error(e); } } }); p.grid.destroy(true); p.el.remove(); delete panes[id]; }
  function deleteTab() { if (state.tabs.length < 2) return; const t = tabOf(state.active); if (!confirm(`Delete the tab "${t.name}" and its layout?`)) return;
    dropPane(t.id); state.tabs = state.tabs.filter((x) => x !== t); show(state.tabs[0].id); }
  function resetTab() { const t = tabOf(state.active), d = DEFAULTS.find((x) => x.id === t.id); if (!confirm(`Reset "${t.name}" to its default layout?`)) return;
    dropPane(t.id); t.items = d ? clone(d.items) : []; if (d) t.name = d.name; show(t.id); }
  function resetAll() { if (!confirm("Reset every tab to the default layout? Custom tabs are removed.")) return;
    Object.keys(panes).forEach(dropPane); store.clear(); state = { active: DEFAULTS[0].id, locked: false, tabs: clone(DEFAULTS) }; show(state.active); renderSide(); }
  function toggleLock() { state.locked = !state.locked; for (const p of Object.values(panes)) { p.grid.enableMove(!state.locked); p.grid.enableResize(!state.locked); } renderTabs(); renderSide(); save(); }

  /* open = bring that widget up in the current tab (adding it if absent) and hand it the argument */
  function open(id, arg) { const pane = active(); if (!pane || !apps[id]) return null;
    if (typeof arg === "string" && (id === "commodity" || id === "country")) setCtx(id, arg);
    let w = pane.wins.find((x) => x.id === id);
    if (w) { if (w.onArg && arg !== undefined) w.onArg(arg); } else { w = addWidget(pane, { app: id, arg: arg ?? null }, true); save(); }
    if (w) { w.item.scrollIntoView({ block: "nearest" }); w.item.classList.add("flash"); setTimeout(() => w.item.classList.remove("flash"), 700); }
    return w; }
  /* linked context: commodity and country are shared by every widget in the visible tab */
  function setCtx(k, v, src) { if (v == null || ctx[k] === v) return; ctx[k] = v;
    const sel = document.getElementById("cx-" + k); if (sel && sel.value !== v) sel.value = v;
    const pane = active(); if (pane) pane.wins.forEach((w) => { if (w !== src && w.onCtx) { try { w.onCtx(k, v); } catch (e) { console.error(e); } } }); }

  function renderSide() { const side = document.getElementById("side"), groups = [...new Set(Object.values(apps).map((a) => a.group || "Other"))];
    side.innerHTML = groups.map((g) => `<h4>${esc(g)}</h4>` + Object.entries(apps).filter(([, a]) => (a.group || "Other") === g).map(([id, a]) => `<span class="it" data-add="${id}" title="Add to this tab">${esc(a.title)}</span>`).join("")).join("") +
      `<h4>Layout</h4><span class="it" data-act="lock">${state.locked ? "Unlock layout" : "Lock layout"}</span><span class="it" data-act="new">New tab</span><span class="it" data-act="rename">Rename tab</span><span class="it" data-act="delete">Delete tab</span><span class="it" data-act="reset">Reset this tab</span><span class="it" data-act="resetall">Reset everything</span>
       <div class="hint">Drag title bars to move, corners to resize. Saved in this browser.</div>
       <h4>Classic pages</h4><a class="it" href="dashboard.html">Atlas dashboard</a><a class="it" href="flows.html">Flows plate</a><a class="it" href="balances.html">Balances page</a><a class="it" href="signals.html">Equity signals</a><a class="it" href="methodology.html">Methodology</a>`;
    side.onclick = (e) => { const t = e.target.closest("[data-add],[data-act]"); if (!t) return;
      if (t.dataset.add) { const w = addWidget(active(), { app: t.dataset.add }, true); save(); if (w) { w.item.scrollIntoView({ block: "nearest" }); w.item.classList.add("flash"); setTimeout(() => w.item.classList.remove("flash"), 700); } return; }
      ({ lock: toggleLock, new: newTab, rename: () => renameTab(state.active), delete: deleteTab, reset: resetTab, resetall: resetAll })[t.dataset.act](); }; }

  function boot(defaults) {
    DEFAULTS = defaults; const saved = store.get();
    const ok = saved && Array.isArray(saved.tabs) && saved.tabs.length && saved.tabs.every((t) => t.id && Array.isArray(t.items));
    state = ok ? saved : { active: defaults[0].id, locked: false, tabs: clone(defaults) };
    for (const t of state.tabs) t.items = t.items.filter((it) => apps[it.app]);
    for (const d of defaults) if (!state.tabs.some((t) => t.id === d.id) && !ok) state.tabs.push(clone(d));
    renderSide(); show(state.active);
    document.getElementById("side-tog").onclick = () => { document.getElementById("side").classList.toggle("shut"); setTimeout(() => active().wins.forEach((w) => w.resized()), 30); };
    addEventListener("keydown", (e) => { if (e.key === "Escape") { const m = document.querySelector(".grid-stack-item.maxed"); if (m) { const w = active().wins.find((x) => x.item === m); if (w) maximize(w, false); } } });
    addEventListener("resize", () => { for (const p of Object.values(panes)) p.grid.cellHeight(cellH()); const m = document.querySelector(".grid-stack-item.maxed"); if (m) { const w = active().wins.find((x) => x.item === m); if (w) maximize(w, true); } });
  }
  return { wins: () => (active() ? active().wins : []), $, F, C, esc, app: (id, def) => { apps[id] = def; }, apps, open, setCtx, ctx, load, loadAll, table, canvas, plot, ticks, tip, csv, download, boot, FILES };
})();
