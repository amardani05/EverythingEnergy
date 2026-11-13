/* World map: production shading by commodity, pipelines, facilities, trade
   routes and chokepoints. Click a country for its sheet, a line or dot for
   its details. No tile server: the basemap is Natural Earth country shapes. */
"use strict";
(() => {
  const { F, esc } = T;
  const WORLD = "terminal/world-50m.json";   // Natural Earth 1:50m via world-atlas 2.0.2, vendored so the map does not wait on a CDN
  /* sea-lane graph (same one flows.html uses): routes follow water and are
     forced through the chokepoints a flow lists. [lon, lat] */
  const SEA = { us_gulf: [-90, 26], fl_strait: [-80.5, 24], us_east: [-72, 37], caribbean: [-68, 14.5], guyana: [-55, 9], brazil_ne: [-33, -5], santos: [-41, -25], plate: [-50, -37], horn: [-68, -57.5], chile: [-76, -32], peru: [-82, -8],
    panama: [-79.5, 9], pac_camerica: [-96, 12], us_west: [-124, 35], bc: [-131, 51], alaska: [-150, 57], aleutian: [-175, 51], npac_w: [165, 46], hawaii: [-160, 27], pac_mid: [170, 28], spac_e: [-125, -18], spac_w: [175, -18],
    natl_mid: [-40, 42], natl_s: [-28, 18], satl_mid: [-12, -22], canaries: [-17, 29], dakar: [-19, 13], w_africa: [2, 2], angola: [9, -11], cape: [18.5, -36], biscay: [-9, 46.5], channel: [-3, 49.6], north_sea: [3.5, 54], norway: [3, 63],
    barents: [34, 71.5], danish: [11.3, 56.6], baltic: [19, 57], gulf_finland: [27, 59.9], gibraltar: [-5.6, 35.95], w_med: [5, 38.3], c_med: [15.5, 35.3], adriatic: [16.5, 42], e_med: [28, 34], aegean: [25.5, 38.5],
    turkish: [28.2, 40.7], black_sea: [34.5, 43.5], suez: [32.5, 30.2], red_sea: [38.5, 20], bab: [43.4, 12.5], gulf_aden: [51, 13], arabian_sea: [62, 17], hormuz: [56.6, 26.4], gulf: [51.5, 27.3], india_w: [70.5, 19],
    sri_lanka: [80.5, 5], bay_bengal: [88, 15.5], e_africa: [47, -4], mozambique: [41.5, -18], io_south: [62, -28], malacca_n: [96, 6.5], malacca: [101.2, 2.6], singapore: [104.6, 1.1], sunda: [105.6, -6.6], lombok: [115.8, -9],
    nw_australia: [114.5, -20.5], sw_australia: [113.5, -35], s_australia: [135, -37.5], newcastle: [153, -32], coral: [154, -17], png: [147.5, -11], makassar: [118.6, -2], celebes: [124, 5], phil_e: [130, 12], scs_s: [109.5, 6], scs_n: [114.5, 17.5],
    taiwan: [120, 24], ecs: [124, 30.5], yellow: [122.5, 36.5], korea: [129, 34.3], japan_s: [136, 32.5], japan_e: [142.5, 36.5], sakhalin: [143.5, 47], japan_sea: [134, 40.5] };
  const LANES = "us_gulf-fl_strait fl_strait-us_east fl_strait-caribbean us_gulf-caribbean us_gulf-panama caribbean-panama caribbean-guyana guyana-brazil_ne brazil_ne-santos santos-plate plate-horn horn-chile chile-peru peru-panama panama-pac_camerica pac_camerica-us_west us_west-bc bc-alaska alaska-aleutian bc-aleutian aleutian-npac_w npac_w-japan_e npac_w-sakhalin pac_camerica-hawaii us_west-hawaii hawaii-pac_mid pac_mid-japan_e pac_mid-phil_e peru-spac_e chile-spac_e spac_e-spac_w spac_w-coral spac_w-phil_e panama-spac_e us_east-natl_mid fl_strait-natl_mid natl_mid-biscay natl_mid-channel natl_mid-gibraltar caribbean-natl_s guyana-natl_s natl_s-canaries natl_s-natl_mid brazil_ne-natl_s brazil_ne-dakar canaries-gibraltar canaries-biscay canaries-dakar dakar-w_africa w_africa-angola angola-cape brazil_ne-satl_mid santos-satl_mid satl_mid-cape satl_mid-angola santos-cape us_east-natl_s biscay-channel channel-north_sea north_sea-norway norway-barents north_sea-danish danish-baltic baltic-gulf_finland biscay-gibraltar gibraltar-w_med w_med-c_med c_med-adriatic c_med-e_med e_med-aegean aegean-turkish turkish-black_sea e_med-suez c_med-aegean suez-red_sea red_sea-bab bab-gulf_aden gulf_aden-arabian_sea gulf_aden-e_africa arabian_sea-hormuz hormuz-gulf arabian_sea-india_w india_w-sri_lanka arabian_sea-sri_lanka gulf_aden-sri_lanka sri_lanka-bay_bengal sri_lanka-malacca_n bay_bengal-malacca_n malacca_n-malacca malacca-singapore singapore-scs_s scs_s-scs_n scs_n-taiwan taiwan-ecs ecs-yellow ecs-korea korea-japan_s japan_s-japan_e scs_n-phil_e ecs-japan_s korea-japan_sea japan_sea-sakhalin sakhalin-japan_e cape-mozambique mozambique-e_africa e_africa-arabian_sea cape-io_south io_south-sri_lanka io_south-sunda io_south-sw_australia mozambique-io_south io_south-india_w sunda-singapore sunda-scs_s sunda-lombok lombok-nw_australia lombok-makassar makassar-celebes celebes-phil_e phil_e-japan_s phil_e-taiwan makassar-scs_s celebes-scs_n nw_australia-sw_australia sw_australia-s_australia s_australia-newcastle newcastle-coral coral-png png-phil_e png-celebes nw_australia-sunda".split(" ").map((x) => x.split("-"));
  const CHOKE_NODE = { hormuz: "hormuz", malacca: "malacca", suez: "suez", bab_el_mandeb: "bab", turkish: "turkish", danish: "danish", panama: "panama", cape: "cape" };
  const R = Math.PI / 180, hav = (a, b) => { const dl = (b[0] - a[0]) * R, dp = (b[1] - a[1]) * R, h = Math.sin(dp / 2) ** 2 + Math.cos(a[1] * R) * Math.cos(b[1] * R) * Math.sin(dl / 2) ** 2; return 2 * Math.asin(Math.sqrt(h)); };
  const ADJ = {}; for (const [a, b] of LANES) { const w = hav(SEA[a], SEA[b]); (ADJ[a] ||= []).push([b, w]); (ADJ[b] ||= []).push([a, w]); }
  function shortest(src, dst) { if (src === dst) return [src]; const dist = { [src]: 0 }, prev = {}, done = new Set(), q = [src];
    while (q.length) { q.sort((a, b) => dist[a] - dist[b]); const u = q.shift(); if (done.has(u)) continue; done.add(u); if (u === dst) break;
      for (const [v, w] of ADJ[u] || []) { const nd = dist[u] + w; if (nd < (dist[v] ?? Infinity)) { dist[v] = nd; prev[v] = u; q.push(v); } } }
    const path = [dst]; while (path[0] !== src && prev[path[0]]) path.unshift(prev[path[0]]); return path[0] === src ? path : [src, dst]; }
  const nearestSea = (pt) => Object.keys(SEA).reduce((b, k) => (hav(SEA[k], pt) < hav(SEA[b], pt) ? k : b), "us_gulf");
  /* keep longitudes continuous across the antimeridian (MapLibre draws coordinates beyond +/-180), then Chaikin-smooth */
  function smooth(pts, rounds = 2) { const u = [pts[0].slice()];
    for (let i = 1; i < pts.length; i++) { let lon = pts[i][0]; const p = u[i - 1][0]; while (lon - p > 180) lon -= 360; while (lon - p < -180) lon += 360; u.push([lon, pts[i][1]]); }
    let cur = u; for (let k = 0; k < rounds && cur.length > 2; k++) { const nx = [cur[0]]; for (let i = 0; i < cur.length - 1; i++) { const a = cur[i], b = cur[i + 1];
      nx.push([a[0] * .75 + b[0] * .25, a[1] * .75 + b[1] * .25], [a[0] * .25 + b[0] * .75, a[1] * .25 + b[1] * .75]); } nx.push(cur[cur.length - 1]); cur = nx; } return cur; }

  /* A gentle curve drawn in map space. Great circles look wrong on a flat map once they near the poles. */
  function bow(a, b, n = 24) { let bl = b[0]; while (bl - a[0] > 180) bl -= 360; while (bl - a[0] < -180) bl += 360;
    const mx = (a[0] + bl) / 2, my = (a[1] + b[1]) / 2, dx = bl - a[0], dy = b[1] - a[1], k = 0.16, cx = mx - dy * k, cy = Math.max(-60, Math.min(72, my + Math.abs(dx) * k)), out = [];
    for (let i = 0; i <= n; i++) { const t = i / n, u = 1 - t; out.push([u * u * a[0] + 2 * u * t * cx + t * t * bl, u * u * a[1] + 2 * u * t * cy + t * t * b[1]]); } return out; }
  /* Cut a route where it crosses the date line and bring every piece back into -180..180. Left whole, the
     renderer joins the two halves with a line straight across the map. */
  function splitAtDateLine(coords) { const band = (x) => Math.floor((x + 180) / 360), parts = []; let cur = [];
    for (let i = 0; i < coords.length; i++) { const p = coords[i];
      if (i) { const q = coords[i - 1], bq = band(q[0]), bp = band(p[0]);
        if (bq !== bp) { const east = bp > bq, edge = (east ? bq + 1 : bq) * 360 - 180, t = (edge - q[0]) / (p[0] - q[0]), lat = q[1] + t * (p[1] - q[1]);
          cur.push([east ? 180 : -180, lat]); parts.push(cur); cur = [[east ? -180 : 180, lat]]; } }
      cur.push([p[0] - band(p[0]) * 360, p[1]]); }
    parts.push(cur); return parts.filter((x) => x.length > 1); }
  function seaRoute(a, b, via = []) { const stops = [nearestSea(a), ...via, nearestSea(b)]; let nodes = [stops[0]];
    for (let i = 1; i < stops.length; i++) nodes = nodes.concat(shortest(stops[i - 1], stops[i]).slice(1)); return smooth([a, ...nodes.map((n) => SEA[n]), b]); }
  const FLOW_OF = { crude_oil: ["crude", "products"], natural_gas: ["lng", "pipe_gas"], coal: ["coal"], uranium: ["uranium"] };
  const FAC_OF = { crude_oil: ["crude", "products", "oil", "petroleum"], natural_gas: ["gas", "lng", "natural_gas"], aluminum: ["aluminum", "alumina", "bauxite"], bauxite: ["bauxite", "alumina", "aluminum"], iron_ore: ["iron_ore", "iron", "steel"], rare_earths: ["rare_earths"], platinum: ["platinum", "pgm", "palladium"], palladium: ["palladium", "pgm", "platinum"] };
  const PIPE_COL = { crude: "#e89a2c", products: "#d9553f", gas: "#3ea6dc", hgl: "#a67bd9" };
  const KINDS = [["refinery", "Refineries", "#d9553f"], ["oil_terminal", "Oil terminals", "#e89a2c"], ["lng_export", "LNG export", "#5fd0f0"], ["lng_import", "LNG import", "#2f86b3"], ["gas_storage", "Gas storage", "#2c6c9c"],
    ["mine", "Mines", "#f2c230"], ["smelter", "Smelters", "#f08a3c"], ["metal_refinery", "Metal refineries", "#f5b071"], ["coal_mine", "Coal mines", "#9aa3ad"], ["enrichment", "Enrichment", "#9ad94c"], ["conversion", "Conversion", "#6fb53a"]];
  const CUTS = [0.01, 0.03, 0.07, 0.15, 0.3, 0.5];
  const THEME = {
    dark: { ocean: "#0e1622", land: "#1c2534", border: "#3b4860", trace: "#27344a", ramp: ["#4a5230", "#7a7228", "#ad8a28", "#d97f2a", "#e8562c", "#ff3b30"], sel: "#ffffff", bubble: "#ffd23c", route: { crude: "#f3c47c", products: "#f28f62", lng: "#cfeaf5", pipe_gas: "#b9e6a6", coal: "#d0d0d0", uranium: "#d9b3f2", metal: "#ffd23c" } },
    light: { ocean: "#d8e4ef", land: "#f4f2ec", border: "#aab4bf", trace: "#ebe6d6", ramp: ["#f1e3b4", "#ecd08a", "#e8b25f", "#e08a3f", "#d55f2b", "#b8271f"], sel: "#0f3f78", bubble: "#1f5fa8", route: { crude: "#9a5a00", products: "#b8432c", lng: "#1f7fa8", pipe_gas: "#3c8a3c", coal: "#555555", uranium: "#7b52ab", metal: "#1f5fa8" } } };
  const WORLD_BOUNDS = [[-168, -56], [178, 76]];

  T.app("map", { title: "World Map", group: "Supply chain", w: 7, h: 12, open(win, arg) {
    win.add(`<div class="toolbar"><label>Commodity <select id="mp-c"></select></label>
      <label>Shade by <select id="mp-m"><option value="production">production share</option><option value="reserves">reserves share</option><option value="refining">processing share</option><option value="consumption">consumption share</option></select></label>
      <button id="mp-lay">Layers ▾</button><label title="Hide sites, pipelines and routes that carry other commodities"><input type="checkbox" id="mp-match" checked>filter sites</label>
      <span style="flex:1"></span><button id="mp-theme">Light map</button><button id="mp-globe">Globe</button><button id="mp-world">Reset</button><button id="mp-sheet">Sheet ▸</button></div>`);
    const body = win.add(`<div class="body" style="overflow:hidden"><div id="mp-map" style="position:absolute;inset:0"></div><div id="mp-lab" style="position:absolute;inset:0;overflow:hidden;pointer-events:none;z-index:2"></div>
      <div class="layers out" id="mp-layers" hidden></div><div class="legend out" id="mp-leg"></div></div>`);
    win.add(`<div class="status"><div id="mp-s">Loading map...</div><div id="mp-h" style="max-width:46%"></div></div>`);
    const q = (s) => win.el.querySelector(s);
    const L = { shade: true, bubbles: true, flows: true, chokes: true, labels: true, pipe_crude: true, pipe_gas: true, pipe_products: false, pipe_hgl: false,
      refinery: true, oil_terminal: true, lng_export: true, lng_import: false, gas_storage: false, mine: true, smelter: true, metal_refinery: true, coal_mine: false, enrichment: true, conversion: true };
    let Mo, FL, map, CF = null, ready = false, labels = [], n3iso = {}, globe = false, theme = "dark", selN3 = null, quiet = false, raf = 0;
    const a0 = arg && typeof arg === "object" ? arg : {};
    if (!window.maplibregl) { q("#mp-s").textContent = "MapLibre failed to load (offline?)."; return; }
    map = new maplibregl.Map({ container: q("#mp-map"), style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": THEME.dark.ocean } }] },
      bounds: WORLD_BOUNDS, fitBoundsOptions: { padding: 6 }, minZoom: 0.2, maxZoom: 9, attributionControl: false, dragRotate: false, pitchWithRotate: false, renderWorldCopies: false });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    win.map = map;
    const ro = new ResizeObserver(() => { map.resize(); place(); }); ro.observe(body);
    win.onResize = () => { map.resize(); place(); }; win.onClose = () => { ro.disconnect(); map.remove(); };

    Promise.all([T.model(), T.load("flows"), fetch(WORLD).then((r) => r.json()).catch(() => null), new Promise((res) => map.on("load", res))]).then(([m, fl, world]) => {
      if (!world) { q("#mp-s").textContent = "Could not load country shapes."; return; } Mo = m; FL = fl;
      for (const [iso, c] of Object.entries(m.C.countries)) n3iso[c.iso_n3] = iso;
      const countries = topojson.feature(world, world.objects.countries);
      countries.features.forEach((f) => { f.properties.n3 = String(f.id).padStart(3, "0"); f.properties.iso = n3iso[f.properties.n3] || ""; });
      /* Countries that straddle the antimeridian (Russia, Fiji) come with a vertex or two on the wrong side of it,
         which a flat renderer draws as a streak across the whole map. Keep each ring's longitudes continuous. */
      const fixRing = (ring) => { for (let i = 1; i < ring.length; i++) { const d = ring[i][0] - ring[i - 1][0]; if (d > 180) ring[i][0] -= 360; else if (d < -180) ring[i][0] += 360; } };
      for (const f of countries.features) { if (f.properties.n3 === "010" || !f.geometry) continue;   // Antarctica legitimately spans every longitude
        const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates; polys.forEach((poly) => poly.forEach(fixRing)); }
      CF = countries; const E = { type: "FeatureCollection", features: [] }, cur = ["==", ["get", "origin"], "curated"], off = ["in", ["coalesce", ["get", "status"], "operating"], ["literal", ["idle", "damaged", "construction"]]];
      map.addSource("countries", { type: "geojson", data: countries });
      map.addLayer({ id: "land", type: "fill", source: "countries", paint: { "fill-color": THEME.dark.land } });
      map.addLayer({ id: "borders", type: "line", source: "countries", paint: { "line-color": THEME.dark.border, "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.3, 4, 0.8] } });
      map.addSource("flows", { type: "geojson", data: E });
      map.addLayer({ id: "flows", type: "line", source: "flows", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ["get", "color"], "line-opacity": 0.5, "line-width": ["get", "w"] } });
      map.addSource("pipes", { type: "geojson", data: m.pipes.length ? { type: "FeatureCollection", features: m.pipes } : E });
      for (const [k, col] of Object.entries(PIPE_COL)) { const is = ["==", ["get", "commodity"], k];
        /* the dense US network only appears once you zoom in; the hand-placed trunk lines are always shown */
        map.addLayer({ id: "pipe_" + k + "_us", type: "line", source: "pipes", minzoom: 2.6, filter: ["all", is, ["!", cur]], paint: { "line-color": col, "line-opacity": 0.75, "line-width": ["interpolate", ["linear"], ["zoom"], 2.6, 0.5, 6, 1.4, 9, 2.4] } });
        map.addLayer({ id: "pipe_" + k, type: "line", source: "pipes", filter: ["all", is, cur, ["!", off]], layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": col, "line-opacity": 0.95, "line-width": ["interpolate", ["linear"], ["zoom"], 0, 1, 4, 2, 8, 3] } });
        map.addLayer({ id: "pipe_" + k + "_off", type: "line", source: "pipes", filter: ["all", is, cur, off], paint: { "line-color": col, "line-opacity": 0.8, "line-width": 1.3, "line-dasharray": [2, 2] } }); }
      map.addSource("bubbles", { type: "geojson", data: E });
      map.addLayer({ id: "bubbles", type: "circle", source: "bubbles", paint: { "circle-radius": ["get", "r"], "circle-color": THEME.dark.bubble, "circle-opacity": 0.14, "circle-stroke-color": THEME.dark.bubble, "circle-stroke-width": 1, "circle-stroke-opacity": 0.9 } });
      map.addSource("sel", { type: "geojson", data: E }); map.addLayer({ id: "sel", type: "line", source: "sel", paint: { "line-color": THEME.dark.sel, "line-width": 1.8 } });
      map.addSource("fac", { type: "geojson", data: m.fac.length ? { type: "FeatureCollection", features: m.fac } : E });
      const facPaint = (r0, r1, r2) => ({ "circle-radius": ["interpolate", ["linear"], ["zoom"], 0, r0, 4, r1, 8, r2], "circle-color": ["match", ["get", "kind"], ...KINDS.flatMap((k) => [k[0], k[2]]), "#ffffff"], "circle-stroke-color": "#10151c", "circle-stroke-width": 0.7 });
      map.addLayer({ id: "fac_all", type: "circle", source: "fac", minzoom: 2.6, paint: facPaint(2, 3.2, 6) });
      map.addLayer({ id: "fac_major", type: "circle", source: "fac", paint: facPaint(2.6, 4.2, 7) });
      if (!m.empty) { const groups = [...new Set(m.list.map((x) => x.group))];
        q("#mp-c").innerHTML = groups.map((g) => `<optgroup label="${esc(g)}">${m.list.filter((x) => x.group === g).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</optgroup>`).join("");
        const want = a0.commodity || T.ctx.commodity; q("#mp-c").value = want && m.com[want] ? want : m.com.crude_oil ? "crude_oil" : m.list[0].id; }
      layerPanel(); ready = true; refresh(); if (a0.country) focusCountry(a0.country, true);
      map.on("move", () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; place(); }); });
      map.on("mousemove", (ev) => { const hit = map.queryRenderedFeatures(ev.point, { layers: live(["fac_major", "fac_all", ...pipeIds(), "flows", "land"]) })[0]; map.getCanvas().style.cursor = hit ? "pointer" : "";
        q("#mp-h").textContent = hit ? describe(hit) : ""; });
      map.on("click", (ev) => { const pad = [[ev.point.x - 4, ev.point.y - 4], [ev.point.x + 4, ev.point.y + 4]];
        const hit = map.queryRenderedFeatures(pad, { layers: live(["fac_major", "fac_all", ...pipeIds(), "flows"]) })[0];
        if (hit) { new maplibregl.Popup({ closeButton: true, maxWidth: "320px" }).setLngLat(ev.lngLat).setHTML(popup(hit)).addTo(map); return; }
        const c = map.queryRenderedFeatures(ev.point, { layers: ["land"] })[0]; if (!c) return;
        select(c.properties.n3);
        if (c.properties.iso) { quiet = true; T.open("country", c.properties.iso); quiet = false; } else q("#mp-s").textContent = `${c.properties.name}: no production data listed.`; });
    });
    const pipeIds = () => Object.keys(PIPE_COL).flatMap((k) => ["pipe_" + k, "pipe_" + k + "_us", "pipe_" + k + "_off"]);
    const live = (ids) => ids.filter((id) => map.getLayer(id) && map.getLayoutProperty(id, "visibility") !== "none");
    function select(n3) { selN3 = n3; map.getSource("sel").setData({ type: "FeatureCollection", features: n3 && CF ? CF.features.filter((f) => f.properties.n3 === n3) : [] }); }
    function focusCountry(iso, fly) { const k = Mo && Mo.C.countries[iso]; if (!k) return; select(k.iso_n3); if (fly && k.lon != null) map.flyTo({ center: [k.lon, k.lat], zoom: Math.max(map.getZoom(), 2.8), duration: 500 }); }
    function describe(f) { const p = f.properties, id = f.layer.id;
      if (id === "land") { const c = Mo && Mo.com[q("#mp-c").value], v = c && val(c, p.iso); return p.name + (v ? `: ${F.share(v)} of world ${q("#mp-m").value}` : ""); }
      if (id === "flows") return `${p.label}: ${p.vol}`;
      return `${p.name}${p.kind ? " · " + p.kind.replace(/_/g, " ") : " · " + p.commodity + " pipeline"}${p.capacity ? " · " + F.big(+p.capacity) + " " + (p.capacity_unit || "") : ""}`; }
    function popup(f) { const p = f.properties; let cs = ""; try { cs = p.countries ? JSON.parse(p.countries).map((x) => Mo.name(x)).join(", ") : ""; } catch (e) { cs = String(p.countries || ""); }
      const rows = Object.entries({ Kind: p.kind ? p.kind.replace(/_/g, " ") : f.layer.id === "flows" ? "trade route" : "pipeline", Commodity: p.commodity, Capacity: p.capacity ? F.big(+p.capacity) + " " + (p.capacity_unit || "") : p.vol, Operator: p.operator, Status: p.status,
        Country: p.country && Mo ? Mo.name(p.country) : cs, Via: p.via, Source: p.origin || p.src, Note: p.note }).filter(([, v]) => v);
      return `<div style="color:#1c232c"><b>${esc(p.name || p.label)}</b><table style="margin-top:4px;font-size:11px">${rows.map(([k, v]) => `<tr><td style="color:#5d6876;padding-right:10px;vertical-align:top">${k}</td><td>${esc(v)}</td></tr>`).join("")}</table></div>`; }
    function val(c, iso) { const m = q("#mp-m").value; if (!iso) return 0;
      if (m === "production") { const p = c.producers.find((x) => x.iso3 === iso); return p ? p.share || 0 : 0; }
      if (m === "reserves") { const p = c.producers.find((x) => x.iso3 === iso); return p && p.reserves && c.world_reserves ? p.reserves / c.world_reserves : 0; }
      if (m === "refining") return (c.refining || []).filter((x) => x.iso3 === iso).reduce((s, x) => Math.max(s, x.share || 0), 0);
      const p = (c.consumers || []).find((x) => x.iso3 === iso); return p ? p.share || 0 : 0; }
    function layerPanel() { const cb = (k, label, sw) => `<label><input type="checkbox" data-k="${k}"${L[k] ? " checked" : ""}>${sw || ""}${label}</label>`;
      q("#mp-layers").innerHTML = `<h5 style="margin-top:0">Data</h5>${cb("shade", "Country shading")}${cb("bubbles", "Producer bubbles")}${cb("labels", "Labels")}${cb("flows", "Trade routes")}${cb("chokes", "Chokepoints")}
        <h5>Pipelines</h5>${Object.entries(PIPE_COL).map(([k, c]) => cb("pipe_" + k, k === "hgl" ? "NGL" : k[0].toUpperCase() + k.slice(1), `<span class="sw" style="background:${c}"></span>`)).join("")}
        <h5>Sites</h5>${KINDS.map(([k, l, c]) => cb(k, l, `<span class="dot" style="background:${c}"></span>`)).join("")}`;
      q("#mp-layers").onchange = (ev) => { L[ev.target.dataset.k] = ev.target.checked; refresh(); }; }
    /* labels are plain HTML placed by hand so overlapping ones can be dropped, most important first */
    function place() { if (!ready) return; const host = q("#mp-lab"), W = host.clientWidth, H = host.clientHeight, kept = [], ctr = map.getCenter();
      for (const l of labels) { let ok = L.labels && (l.kind !== "ck" || L.chokes);
        if (ok && globe && hav([ctr.lng, ctr.lat], l.at) > 1.35) ok = false;      // behind the globe
        let x = 0, y = 0, w = 0; if (ok) { const p = map.project(l.at); x = p.x + 7; y = p.y - 7; w = l.w; ok = x > 2 && y > 2 && x + w < W - 2 && y + 13 < H - 2; }
        if (ok) ok = !kept.some((k) => x < k.x + k.w + 4 && k.x < x + w + 4 && y < k.y + 15 && k.y < y + 15);
        if (ok) { kept.push({ x, y, w }); l.el.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`; l.el.style.display = ""; } else l.el.style.display = "none"; } }
    function setLabels(list) { const host = q("#mp-lab"); host.innerHTML = ""; labels = list.sort((a, b) => b.prio - a.prio);
      for (const l of labels) { const el = document.createElement("div"); el.className = "mlabel " + (l.kind || ""); el.style.position = "absolute"; el.style.left = el.style.top = "0"; el.textContent = l.text; host.appendChild(el); l.el = el; l.w = el.offsetWidth; }
      place(); }
    function refresh() { if (!ready) return; const c = Mo && Mo.com[q("#mp-c").value], match = q("#mp-match").checked, mode = q("#mp-m").value, th = THEME[theme], list = [];
      /* shading */
      const shade = (v) => { let col = th.trace; CUTS.forEach((t, i) => { if (v >= t) col = th.ramp[i]; }); return col; };
      const pairs = []; if (c && L.shade) for (const iso of Object.keys(Mo.C.countries)) { const v = val(c, iso); if (v > 0.0005) pairs.push(Mo.C.countries[iso].iso_n3, shade(v)); }
      map.setPaintProperty("bg", "background-color", th.ocean); map.setPaintProperty("borders", "line-color", th.border); map.setPaintProperty("sel", "line-color", th.sel);
      map.setPaintProperty("land", "fill-color", pairs.length ? ["match", ["get", "n3"], ...pairs, th.land] : th.land);
      map.setPaintProperty("bubbles", "circle-color", th.bubble); map.setPaintProperty("bubbles", "circle-stroke-color", th.bubble);
      body.classList.toggle("light", theme === "light");
      /* bubbles + leader labels */
      const ranked = c ? Object.keys(Mo.C.countries).map((iso) => ({ iso, v: val(c, iso), k: Mo.C.countries[iso] })).filter((x) => x.v > 0.0005 && x.k.lon != null).sort((a, b) => b.v - a.v) : [];
      map.getSource("bubbles").setData({ type: "FeatureCollection", features: L.bubbles ? ranked.map((x) => ({ type: "Feature", geometry: { type: "Point", coordinates: [x.k.lon, x.k.lat] }, properties: { r: 2.5 + Math.sqrt(x.v) * 20 } })) : [] });
      ranked.slice(0, 10).forEach((x, i) => list.push({ at: [x.k.lon, x.k.lat], text: `${Mo.name(x.iso)} ${F.share(x.v, x.v < 0.1 ? 1 : 0)}`, prio: 200 - i }));
      /* pipelines + sites */
      const pk = c && match ? (FAC_OF[c.id] || [c.id]) : null;
      for (const k of Object.keys(PIPE_COL)) { const on = L["pipe_" + k] && (!pk || pk.includes(k) || (k === "hgl" && pk.includes("gas")));
        for (const id of ["pipe_" + k, "pipe_" + k + "_us", "pipe_" + k + "_off"]) map.setLayoutProperty(id, "visibility", on ? "visible" : "none"); }
      const kinds = KINDS.map((k) => k[0]).filter((k) => L[k]), f = [["in", ["get", "kind"], ["literal", kinds]]];
      if (pk) f.push(["any", ["in", ["get", "commodity"], ["literal", pk]], ...pk.map((x) => ["in", x, ["coalesce", ["get", "also"], ["literal", []]]])]);   // polymetallic mines list by-products in "also"
      map.setFilter("fac_major", ["all", ["==", ["get", "origin"], "curated"], ...f]); map.setFilter("fac_all", ["all", ["!=", ["get", "origin"], "curated"], ...f]);
      /* trade routes: sea-routed energy flows, great-circle arcs for metals */
      const feats = [], chokes = new Set();
      const push = (coords, props) => feats.push({ type: "Feature", geometry: { type: "MultiLineString", coordinates: splitAtDateLine(coords) }, properties: props });
      if (L.flows && c) { const kinds2 = FLOW_OF[c.id];
        if (kinds2 && FL) { const hub = Object.fromEntries(FL.hubs.map((h) => [h.id, h]));
          for (const k2 of kinds2) { const sub = FL.flows.filter((x) => x.commodity === k2), unit = sub[0] ? sub[0].unit : "",
            mx = Math.max(...FL.flows.filter((x) => kinds2.includes(x.commodity) && x.unit === unit).map((x) => x.volume), 1e-9);   // one width scale per unit, so products are not drawn as fat as crude
            for (const x of sub) { const a = hub[x.from], b = hub[x.to]; let coords;
              if (x.mode && x.mode !== "sea") coords = bow([a.lon, a.lat], [b.lon, b.lat]);
              else coords = seaRoute([a.lon, a.lat], [b.lon, b.lat], (x.via || []).map((v) => CHOKE_NODE[v]).filter(Boolean));
              (x.via || []).forEach((v) => chokes.add(v));
              push(coords, { color: th.route[k2], w: 0.7 + 3.3 * Math.sqrt(x.volume / mx), label: `${a.name.replace(/\s*\(.*\)$/, "")} → ${b.name.replace(/\s*\(.*\)$/, "")}`, vol: `${F.auto(x.volume)} ${x.unit} (${x.year})`, commodity: k2, via: (x.via || []).join(", "), note: x.note || "", src: x.source || "" }); } } }
        else for (const t of (c.trade || []).filter((t) => (t.share || 0) >= 0.1)) { const a = Mo.C.countries[t.from], b = Mo.C.countries[t.to]; if (!a || !b || a.lon == null || b.lon == null) continue;
          const A = [a.lon, a.lat], B = [b.lon, b.lat];   // neighbours trade overland; anything farther goes by ship
          push(hav(A, B) < 0.6 ? bow(A, B) : seaRoute(A, B), { color: th.route.metal, w: 0.7 + 2.8 * (t.share || 0.2), label: `${Mo.name(t.from)} → ${Mo.name(t.to)}`, vol: t.share ? F.share(t.share, 0) + " of exports" : "", commodity: c.name, note: t.what || "", src: t.source || "" }); } }
      map.getSource("flows").setData({ type: "FeatureCollection", features: feats });
      if (L.chokes && FL) FL.chokepoints.filter((k) => chokes.has(k.id) || (c && c.id === "crude_oil")).forEach((k, i) => list.push({ at: [k.lon, k.lat], kind: "ck", prio: 100 - i,
        text: `◆ ${k.name.replace(/^Strait of /, "").replace(/ Canal.*| Straits.*| \(.*/, "")}${k.oil_mbd ? " " + F.n(k.oil_mbd, 1) : ""}` }));
      setLabels(list);
      /* legend + status */
      q("#mp-leg").innerHTML = L.shade && c ? `<b>${esc(c.name)}</b>, ${mode} share` + CUTS.map((t, i) => `<i style="background:${th.ramp[i]}"></i>${F.n(t * 100, 0)}%+`).join("") : "shading off";
      q("#mp-theme").textContent = theme === "dark" ? "Light map" : "Dark map";
      if (c) { const top = c.producers.filter((p) => p.iso3 !== "OTH").slice(0, 4).map((p) => `${Mo.name(p.iso3)} ${F.share(p.share, 0)}`).join(", ");
        q("#mp-s").textContent = `${c.name} ${c.year}: ${F.big(c._tot)} ${c.unit}. ${top}. Top-3 ${F.share(c._top3, 0)}. Zoom in for the US pipeline network and smaller sites.`; win.setTitle("World Map - " + c.name); }
      else q("#mp-s").textContent = "commodities.json missing: map shows infrastructure only."; }
    function setCommodity(id, tell) { if (!Mo || !Mo.com[id]) return; q("#mp-c").value = id; win.setArg({ commodity: id }); if (tell) T.setCtx("commodity", id, win); refresh(); }
    win.onArg = (a) => { if (!ready || !a) return; if (a.commodity) setCommodity(a.commodity, true); if (a.country) focusCountry(a.country, true); };
    win.onCtx = (k, v) => { if (!ready) return; if (k === "commodity" && q("#mp-c").value !== v) setCommodity(v, false); else if (k === "country") focusCountry(v, !quiet); };
    win.el.addEventListener("change", (ev) => { if (ev.target.id === "mp-c") setCommodity(ev.target.value, true); else if (/^mp-(m|match)$/.test(ev.target.id)) refresh(); });
    q("#mp-lay").onclick = () => { const p = q("#mp-layers"); p.hidden = !p.hidden; q("#mp-lay").classList.toggle("down", !p.hidden); };
    q("#mp-theme").onclick = () => { theme = theme === "dark" ? "light" : "dark"; refresh(); };
    q("#mp-world").onclick = () => { select(null); globe ? map.flyTo({ center: [20, 15], zoom: 1.2, duration: 400 }) : map.fitBounds(WORLD_BOUNDS, { padding: 6, duration: 400 }); };
    q("#mp-sheet").onclick = () => T.open("commodity", q("#mp-c").value);
    q("#mp-globe").onclick = () => { globe = !globe; try { map.setProjection({ type: globe ? "globe" : "mercator" }); q("#mp-globe").classList.toggle("down", globe); if (globe) map.flyTo({ center: [20, 15], zoom: 1.2, duration: 0 }); else map.fitBounds(WORLD_BOUNDS, { padding: 6, duration: 0 }); setTimeout(place, 50); }
      catch (e) { globe = false; q("#mp-s").textContent = "Globe view is not available in this MapLibre build."; } };
  } });
})();
