/* ============================================================
   Atlas data · hydrates from dashboard_data.json + map_config.json
   Replaces the old hardcoded constants.

   Loads:
     - dashboard_data.json (real analysis output from consolidate_data.py)
     - map_config.json     (district structure, optional)

   Exposes the same window globals the existing JSX consumes:
     NODES, NODE_BY_ID, DISTRICTS, FLOW_EDGES, DISTRICT_CORR,
     SIGNAL_PAIRS, DESCRIPTIONS, NODE_CORR_PAIRS, MOST_CORR, BETA_XLE

   Also exposes window.ATLAS_READY (a Promise) so app.jsx can wait
   for hydration before rendering.
   ============================================================ */

(function () {
  /* ---- Layout overlay --------------------------------------------------
     Positions for each node ON the illustrated island (SVG coords).
     The island map and district shapes are designed by the illustrator;
     this dict places each sub-industry within its district.
     Edit here when you want to move a node visually — analysis
     re-runs do NOT touch this.
     -------------------------------------------------------------------- */
  const POSITIONS = {
    upstream_offshore_drillers: { x: 320, y: 150, district: "ocean" },
    ofs_offshore:               { x: 1180, y: 150, district: "ocean" },
    tanker_shipping:            { x: 200, y: 220, district: "ocean" },

    lng_terminals:              { x: 320, y: 320, district: "port" },
    downstream_refiners:        { x: 320, y: 510, district: "port" },
    petrochem:                  { x: 320, y: 700, district: "port" },

    // Industrial · break out of the (gpt → pipelines → iocs) straight line so
    // bow-curves to upstream/downstream don't pile on top of each other.
    midstream_gpt:              { x: 460, y: 400, district: "industrial" },
    midstream_pipelines:        { x: 700, y: 540, district: "industrial" },
    iocs_integrated:            { x: 820, y: 410, district: "industrial" },

    upstream_oil_eandp:         { x: 540, y: 700, district: "oilfield" },
    upstream_gas_eandp:         { x: 800, y: 700, district: "oilfield" },
    ofs_onshore:                { x: 670, y: 760, district: "oilfield" },

    ofs_equipment:              { x: 920, y: 490, district: "equipment" },

    // Quarry · push minerals further east so the right side of the island
    // gets used.
    coal:                       { x: 870, y: 290, district: "quarry" },
    minerals_royalty:           { x: 1090, y: 270, district: "quarry" },

    // Farmland · biofuels nudged east into the open right-side land.
    downstream_biofuels:        { x: 1290, y: 380, district: "farmland" },

    // Power district · two rows, spread further east now that the right side
    // has room. Both rows still flush on the island.
    power_ipps_merchant:           { x: 950, y: 640, district: "power" },
    power_utilities_regulated_gas: { x: 1110, y: 640, district: "power" },
    power_renewables:              { x: 950, y: 750, district: "power" },
    power_utilities_regulated_ldc: { x: 1110, y: 760, district: "power" },

    // Nuclear · pushed further east for breathing room from electric
    // utilities (which moved to x=1110).
    uranium_nuclear_fuel:       { x: 1240, y: 660, district: "nuclear" },
    nuclear_smr_developers:     { x: 1310, y: 760, district: "nuclear" },

    downstream_retail:          { x: 590, y: 285, district: "town" },
  };

  /* District labels & subtitles. */
  const DISTRICT_META = {
    ocean:      { label: "Open Ocean",        sub: "Offshore Activity",   labelX: 110,  labelY: 90  },
    port:       { label: "Port & Waterfront", sub: "Refinery Row",        labelX: 320,  labelY: 290 },
    industrial: { label: "Industrial Inland", sub: "Midstream",           labelX: 540,  labelY: 310 },
    oilfield:   { label: "Oilfield Basin",    sub: "Upstream",            labelX: 770,  labelY: 500 },
    equipment:  { label: "Equipment Yards",   sub: "Drilling Hardware",   labelX: 720,  labelY: 360 },
    quarry:     { label: "Quarry & Mine",     sub: "Coal · Minerals",     labelX: 1010, labelY: 240 },
    farmland:   { label: "Farmland Edge",     sub: "Biofuels",            labelX: 1180, labelY: 420 },
    power:      { label: "Power District",    sub: "Generation",          labelX: 980,  labelY: 620 },
    nuclear:    { label: "Nuclear Quarter",   sub: "Fuel Cycle · SMR",    labelX: 1230, labelY: 600 },
    town:       { label: "Town",              sub: "Retail · C-Stores",   labelX: 690,  labelY: 250 },
  };

  /* Commodity-flow edges. [from, to, commodity_class].
     Classes: crude · gas · products · lng · coal · nuclear · electrons · services · feedstock */
  const FLOW_EDGES = [
    // Upstream → midstream
    ["upstream_oil_eandp",          "midstream_pipelines",          "crude"],
    ["upstream_gas_eandp",          "midstream_gpt",                "gas"],
    ["upstream_gas_eandp",          "midstream_pipelines",          "gas"],
    ["midstream_gpt",               "midstream_pipelines",          "gas"],
    ["midstream_gpt",               "petrochem",                    "gas"],
    // Midstream → downstream
    ["midstream_pipelines",         "downstream_refiners",          "crude"],
    ["midstream_pipelines",         "lng_terminals",                "gas"],
    ["midstream_pipelines",         "petrochem",                    "gas"],
    ["midstream_pipelines",         "power_utilities_regulated_gas","gas"],
    // Gas E&P → LNG (also through pipelines but kept as direct linkage)
    ["upstream_gas_eandp",          "lng_terminals",                "gas"],
    ["lng_terminals",               "tanker_shipping",              "lng"],
    // Refined products downstream
    ["downstream_refiners",         "downstream_retail",            "products"],
    ["downstream_refiners",         "petrochem",                    "products"],
    ["downstream_refiners",         "tanker_shipping",              "products"],
    // Service flows
    ["upstream_offshore_drillers",  "ofs_offshore",                 "services"],
    ["upstream_offshore_drillers",  "iocs_integrated",              "services"],
    ["ofs_onshore",                 "upstream_oil_eandp",           "services"],
    ["ofs_equipment",               "upstream_oil_eandp",           "services"],
    ["ofs_equipment",               "ofs_onshore",                  "services"],
    // IOCs as integrated crude flows
    ["iocs_integrated",             "midstream_pipelines",          "crude"],
    ["iocs_integrated",             "downstream_refiners",          "crude"],
    ["iocs_integrated",             "petrochem",                    "crude"],
    // Feedstock / minerals
    ["downstream_biofuels",         "downstream_refiners",          "feedstock"],
    ["minerals_royalty",            "upstream_oil_eandp",           "feedstock"],
    // Solid fuels & nuclear cycle
    ["coal",                        "power_ipps_merchant",          "coal"],
    ["coal",                        "power_utilities_regulated_gas","coal"],
    ["uranium_nuclear_fuel",        "power_utilities_regulated_gas","nuclear"],
    ["uranium_nuclear_fuel",        "nuclear_smr_developers",       "nuclear"],
    // Electricity
    ["power_renewables",            "power_utilities_regulated_gas","electricity"],
  ];

  async function hydrate() {
    // Cache-bust the JSON · the browser keeps serving stale copies after a
     // pipeline re-run otherwise.
    const dataResp = await fetch("dashboard_data.json?v=" + Date.now(), { cache: "no-store" });
    if (!dataResp.ok) throw new Error("failed to load dashboard_data.json");
    const data = await dataResp.json();

    /* NODES from dashboard_data.json + POSITIONS overlay */
    const NODES = [];
    const SKIPPED = [];
    for (const n of data.nodes || []) {
      const pos = POSITIONS[n.id];
      if (!pos) { SKIPPED.push(n.id); continue; }

      const repTk = n.representative_ticker;
      const repC = (n.constituents || []).find(c => c.ticker === repTk);
      const r60 = repC?.return_60d ?? null;

      // 1Y return: cumulative_returns is a weekly series of cumulative basket
      // returns. Take the diff between last value and ~52 samples ago.
      const cum = n.cumulative_returns || [];
      let r1y = null;
      if (cum.length >= 52) {
        const last = cum[cum.length - 1]?.value;
        const prior = cum[cum.length - 52]?.value;
        if (last != null && prior != null) r1y = last - prior;
      }

      NODES.push({
        id: n.id,
        name: (n.display_name || n.id).replace(/—/g, "·").replace(/–/g, "·"),
        layer: n.layer,
        ticker: repTk || "",
        n: n.n_constituents || (n.constituents?.length ?? 0),
        r60: r60,
        r1y: r1y,
        intra: n.intra_corr?.mean ?? null,
        district: pos.district,
        x: pos.x,
        y: pos.y,
        r2: n.r2 ?? null,
        dominant_driver: n.dominant_driver ?? null,
        basket_loadings: n.basket_loadings || {},
        constituents: n.constituents || [],
        attribution: n.attribution || {},
        ivr: n.ivr || {},
        // Backtest Lab needs the weekly cumulative-return series. The JSON
        // carries it under cumulative_returns; we MUST forward it onto the
        // NODES global or runBacktest will see 0 bars and bail.
        cumulative_returns: n.cumulative_returns || [],
      });
    }
    if (SKIPPED.length) {
      console.warn("[atlas] nodes in dashboard_data.json not on map (no POSITIONS entry):", SKIPPED);
    }
    const NODE_BY_ID = Object.fromEntries(NODES.map(n => [n.id, n]));
    const LIVE_IDS = new Set(NODES.map(n => n.id));

    const DESCRIPTIONS = {};
    for (const n of data.nodes || []) {
      const desc = (n.description || "").trim().replace(/—/g, "·").replace(/–/g, "·");
      DESCRIPTIONS[n.id] = desc || "·";
    }
    // Also strip em dashes from constituent names
    for (const n of NODES) {
      n.constituents = (n.constituents || []).map(c => ({
        ...c,
        name: (c.name || "").replace(/—/g, "·").replace(/–/g, "·"),
        sub_tag: (c.sub_tag || "").replace(/—/g, "·").replace(/–/g, "·"),
      }));
    }

    /* SIGNAL_PAIRS — filtered to |z|>0.5 for the atlas signal mode.
       ALL_PAIRS — raw set for the dashboard's Active Signals section. */
    const _toPair = p => ({
      a: p.long, b: p.short,
      long: p.long, short: p.short,           // dashboard wording
      z: p.z_60d, z_60d: p.z_60d,
      thesis: p.thesis,
      ret_3m: p.ret_3m, ret_1m: p.ret_1m,
      corr_now: p.corr_now,
      corr_min: p.corr_min, corr_max: p.corr_max,
    });
    const ALL_PAIRS = (data.pairs || [])
      .filter(p => LIVE_IDS.has(p.long) && LIVE_IDS.has(p.short))
      .map(_toPair);
    const SIGNAL_PAIRS = ALL_PAIRS.filter(p => p.z != null && Math.abs(p.z) > 0.5);

    /* NODE_CORR_PAIRS from cross_basket_correlation matrix.
       Filter to live ids, dedupe symmetric, drop self-corr.
       NO magnitude threshold — UI handles encoding of weak pairs. */
    const NODE_CORR_PAIRS = [];
    const seen = new Set();
    const corrMatrix = data.cross_basket_correlation || {};
    for (const [a, row] of Object.entries(corrMatrix)) {
      if (!LIVE_IDS.has(a)) continue;
      for (const [b, c] of Object.entries(row || {})) {
        if (a === b) continue;
        if (!LIVE_IDS.has(b)) continue;
        if (c == null) continue;
        const key = [a, b].sort().join("|");
        if (seen.has(key)) continue;
        seen.add(key);
        NODE_CORR_PAIRS.push([a, b, +Number(c).toFixed(3)]);
      }
    }

    /* DISTRICT_CORR: mean of node-pair corr per district-pair */
    const districtPairs = {};
    for (const [a, b, c] of NODE_CORR_PAIRS) {
      const da = NODE_BY_ID[a]?.district, db = NODE_BY_ID[b]?.district;
      if (!da || !db || da === db) continue;
      const key = [da, db].sort().join("|");
      if (!districtPairs[key]) districtPairs[key] = [];
      districtPairs[key].push(c);
    }
    const DISTRICT_CORR = Object.entries(districtPairs).map(([key, arr]) => {
      const [da, db] = key.split("|");
      const avg = arr.reduce((s, x) => s + x, 0) / arr.length;
      return [da, db, +avg.toFixed(3)];
    });

    /* MOST_CORR per node */
    const MOST_CORR = {};
    for (const n of NODES) {
      let best = null;
      for (const [a, b, c] of NODE_CORR_PAIRS) {
        const partner = a === n.id ? b : b === n.id ? a : null;
        if (!partner) continue;
        if (best == null || Math.abs(c) > Math.abs(best[1])) best = [partner, c];
      }
      if (best) MOST_CORR[n.id] = best;
    }

    /* BETA_XLE proxy from basket SPX beta until phase4 emits real XLE betas */
    const BETA_XLE = {};
    for (const n of NODES) {
      const spx = n.basket_loadings?.SPX?.beta;
      if (spx != null && !Number.isNaN(spx)) BETA_XLE[n.id] = +spx.toFixed(2);
    }

    /* DISTRICTS = local meta + optional overrides from map_config.json */
    let mapConfig = null;
    try {
      const r = await fetch("map_config.json");
      if (r.ok) mapConfig = await r.json();
    } catch (_) {}
    const DISTRICTS = { ...DISTRICT_META };
    if (mapConfig?.districts) {
      for (const d of mapConfig.districts) {
        if (DISTRICTS[d.id]) {
          DISTRICTS[d.id] = { ...DISTRICTS[d.id], label: d.label || DISTRICTS[d.id].label };
        }
      }
    }

    /* Flat ticker dictionary for the Lookup tab.
       Each entry: { ticker, name, node_id, basket_name } */
    const ALL_TICKERS = [];
    const seenTk = new Set();
    for (const n of NODES) {
      for (const c of n.constituents || []) {
        if (!c.ticker || seenTk.has(c.ticker)) continue;
        seenTk.add(c.ticker);
        ALL_TICKERS.push({
          ticker: c.ticker,
          name: c.name || "",
          node_id: n.id,
          basket_name: n.name,
        });
      }
    }
    ALL_TICKERS.sort((a, b) => a.ticker.localeCompare(b.ticker));

    /* CONSTITUENT_BY_TICKER — quick lookup for the stock drawer.
       Stores a reference back to its parent node id for "back to basket". */
    const CONSTITUENT_BY_TICKER = {};
    for (const n of NODES) {
      for (const c of n.constituents || []) {
        if (!c.ticker) continue;
        CONSTITUENT_BY_TICKER[c.ticker] = { ...c, node_id: n.id };
      }
    }

    window.NODES = NODES;
    window.NODE_BY_ID = NODE_BY_ID;
    window.DISTRICTS = DISTRICTS;
    // Per-ticker weekly cumulative returns · used by the Backtest Lab for
    // single-name backtests and ticker legs in pair-spread.
    window.WEEKLY_INDEX = data.weekly_index || [];
    window.WEEKLY_RETURNS_BY_TICKER = data.weekly_returns_by_ticker || {};

    /* Name-level pair signals · z-score of cumulative spread over a 26w
       rolling window. We pre-filter by 5y |corr| (only cointegrated-ish
       pairs are pair-trade candidates), then compute the latest spread z
       and sort by |z|. Output shape matches phase3 basket-basket pairs:
         { long, short, z, corr_now, ret_3m }
       so the UI can use one card component for all three categories. */
    console.time("[atlas] name signals");
    const _idx = data.weekly_index || [];
    const _N = _idx.length;
    const _dateToIdx = Object.create(null);
    for (let i = 0; i < _idx.length; i++) _dateToIdx[_idx[i]] = i;

    // Aligned weekly returns per ticker (from cumulative)
    const _tRets = Object.create(null);
    for (const tk of Object.keys(data.weekly_returns_by_ticker || {})) {
      const cum = data.weekly_returns_by_ticker[tk];
      const ret = new Array(_N).fill(null);
      for (let i = 1; i < cum.length; i++) {
        if (cum[i - 1] == null || cum[i] == null) continue;
        const a = 1 + cum[i - 1], b = 1 + cum[i];
        if (a > 0) ret[i] = b / a - 1;
      }
      _tRets[tk] = ret;
    }
    // Aligned weekly returns per basket (from node.cumulative_returns)
    const _bRets = Object.create(null);
    for (const n of (data.nodes || [])) {
      const cum = n.cumulative_returns || [];
      const ret = new Array(_N).fill(null);
      let prev = null, prevI = -1;
      for (const p of cum) {
        const i = _dateToIdx[p.date];
        if (i == null) continue;
        if (prev != null && i > prevI) {
          const a = 1 + prev, b = 1 + p.value;
          if (a > 0) ret[i] = b / a - 1;
        }
        prev = p.value;
        prevI = i;
      }
      _bRets[n.id] = ret;
    }

    function _corrPair(a, b) {
      let n = 0, sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
      const len = Math.min(a.length, b.length);
      for (let i = 0; i < len; i++) {
        const av = a[i], bv = b[i];
        if (av == null || bv == null || !Number.isFinite(av) || !Number.isFinite(bv)) continue;
        n++;
        sa += av; sb += bv;
        saa += av * av; sbb += bv * bv;
        sab += av * bv;
      }
      if (n < 30) return null;
      const ma = sa / n, mb = sb / n;
      const va = saa / n - ma * ma;
      const vb = sbb / n - mb * mb;
      const cov = sab / n - ma * mb;
      return va > 0 && vb > 0 ? cov / Math.sqrt(va * vb) : null;
    }

    /* Compute the spread signal between two return series (a, b) given their
       string ids. Returns the long/short normalized so `long` is the recent
       underperformer (mean-reversion bet); `z` is positive magnitude of the
       dislocation. */
    function _spreadSignal(retA, retB, idA, idB, corrPrecomputed) {
      const len = Math.min(retA.length, retB.length);
      if (len < 30) return null;
      // Cumulative spread = cum(A - B)
      const cumArr = new Float64Array(len);
      let acc = 0;
      for (let i = 0; i < len; i++) {
        const ra = retA[i], rb = retB[i];
        if (ra != null && rb != null && Number.isFinite(ra) && Number.isFinite(rb)) {
          acc += ra - rb;
        }
        cumArr[i] = acc;
      }
      // Rolling z over the last 26 weeks
      const win = 26;
      if (len < win) return null;
      let sum = 0;
      for (let i = len - win; i < len; i++) sum += cumArr[i];
      const mean = sum / win;
      let v = 0;
      for (let i = len - win; i < len; i++) v += (cumArr[i] - mean) ** 2;
      const sd = Math.sqrt(v / (win - 1));
      if (sd === 0) return null;
      const lastZ = (cumArr[len - 1] - mean) / sd;
      // 3m cumulative spread return (last 13 weeks of cum diff)
      const w3 = Math.min(13, len - 1);
      const ret3m = w3 > 0 ? cumArr[len - 1] - cumArr[len - 1 - w3] : null;
      // Normalize: long the underperformer, short the outperformer
      const out = lastZ > 0
        ? { long: idB, short: idA, z: +lastZ.toFixed(3) }
        : { long: idA, short: idB, z: +(-lastZ).toFixed(3) };
      if (corrPrecomputed != null) out.corr_now = +corrPrecomputed.toFixed(3);
      if (ret3m != null) out.ret_3m = +ret3m.toFixed(4);
      return out;
    }

    // Stock-Stock · pre-filter top 500 by |corr|, then z, sort by |z|
    const _stocks = Object.keys(_tRets);
    const _ssCorrCands = [];
    for (let i = 0; i < _stocks.length; i++) {
      const a = _stocks[i], aR = _tRets[a];
      for (let j = i + 1; j < _stocks.length; j++) {
        const b = _stocks[j];
        const c = _corrPair(aR, _tRets[b]);
        if (c != null && Math.abs(c) >= 0.4) _ssCorrCands.push({ a, b, c });
      }
    }
    _ssCorrCands.sort((x, y) => Math.abs(y.c) - Math.abs(x.c));
    const _ssTop = _ssCorrCands.slice(0, 500);
    const _ssSignals = [];
    for (const p of _ssTop) {
      const sig = _spreadSignal(_tRets[p.a], _tRets[p.b], p.a, p.b, p.c);
      if (sig) _ssSignals.push(sig);
    }
    _ssSignals.sort((x, y) => Math.abs(y.z) - Math.abs(x.z));
    const STOCK_STOCK_SIGNALS = _ssSignals.slice(0, 100);

    // Basket-Stock · skip own-basket constituents
    const _tkrToBasket = Object.create(null);
    for (const n of (data.nodes || [])) {
      for (const c of (n.constituents || [])) {
        if (!_tkrToBasket[c.ticker]) _tkrToBasket[c.ticker] = new Set();
        _tkrToBasket[c.ticker].add(n.id);
      }
    }
    const _bsCorrCands = [];
    for (const bid of Object.keys(_bRets)) {
      const bR = _bRets[bid];
      for (const tk of _stocks) {
        if (_tkrToBasket[tk] && _tkrToBasket[tk].has(bid)) continue;
        const c = _corrPair(bR, _tRets[tk]);
        if (c != null && Math.abs(c) >= 0.4) _bsCorrCands.push({ a: bid, b: tk, c });
      }
    }
    _bsCorrCands.sort((x, y) => Math.abs(y.c) - Math.abs(x.c));
    const _bsTop = _bsCorrCands.slice(0, 500);
    const _bsSignals = [];
    for (const p of _bsTop) {
      const sig = _spreadSignal(_bRets[p.a], _tRets[p.b], p.a, p.b, p.c);
      if (sig) _bsSignals.push(sig);
    }
    _bsSignals.sort((x, y) => Math.abs(y.z) - Math.abs(x.z));
    const BASKET_STOCK_SIGNALS = _bsSignals.slice(0, 100);

    window.STOCK_STOCK_SIGNALS = STOCK_STOCK_SIGNALS;
    window.BASKET_STOCK_SIGNALS = BASKET_STOCK_SIGNALS;
    console.timeEnd("[atlas] name signals");
    console.log(`[atlas] name signals: stock×stock ${STOCK_STOCK_SIGNALS.length}, basket×stock ${BASKET_STOCK_SIGNALS.length}`);

    window.FLOW_EDGES = FLOW_EDGES;
    window.DISTRICT_CORR = DISTRICT_CORR;
    window.SIGNAL_PAIRS = SIGNAL_PAIRS;
    window.ALL_PAIRS = ALL_PAIRS;
    window.REGIME_ALERTS = data.regime_alerts || [];
    window.ALL_TICKERS = ALL_TICKERS;
    window.CONSTITUENT_BY_TICKER = CONSTITUENT_BY_TICKER;
    window.DESCRIPTIONS = DESCRIPTIONS;
    window.NODE_CORR_PAIRS = NODE_CORR_PAIRS;
    window.MOST_CORR = MOST_CORR;
    window.BETA_XLE = BETA_XLE;
    window.ATLAS_META = data.meta || {};
    window.ATLAS_RAW = data;

    console.log(`[atlas] hydrated: ${NODES.length} nodes, ${ALL_PAIRS.length} pairs (${SIGNAL_PAIRS.length} live signals), ${NODE_CORR_PAIRS.length} corr pairs, ${(data.regime_alerts || []).length} regime alerts, ${ALL_TICKERS.length} tickers`);

    // Hide (don't remove) the boot loading screen so we can re-use the same
    // element later when the user fires a pipeline refresh from the top bar.
    const boot = document.getElementById("boot-loading");
    if (boot) boot.style.display = "none";

    return true;
  }

  window.ATLAS_READY = hydrate().catch(err => {
    console.error("[atlas] hydration failed:", err);
    document.body.innerHTML =
      '<div style="padding:48px;font-family:JetBrains Mono;color:#c4544a">' +
      'ATLAS HYDRATION FAILED: ' + (err.message || err) +
      '<br><br>Place <code>dashboard_data.json</code> next to this HTML and serve via http (not file://).</div>';
    throw err;
  });
})();
