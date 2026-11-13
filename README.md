# Everything Energy

Energy-only research platform. ~205 hand-curated energy names mapped onto 23 sub-industry baskets in 10 districts, with a directed commodity-flow graph connecting them.

Two systems share that taxonomy and nothing else:

- **Energy Atlas** (`dashboard.html`) - descriptive: basket cohesion, factor exposures (CL · NG · CRACK · WTI-BRENT · SPX · TNX · URA · BDRY), pair-trade signals, residual stretches, factor-vs-idiosyncratic attribution.
- **Signal engine** (`signal_engine/`) - predictive: a point-in-time, basket-neutral composite of momentum, EV/EBITDA value, and post-earnings drift, ranked daily and held to a walk-forward backtest. Published at `signals.html`.

Nothing outside the energy taxonomy is scored, ingested, or published.

---

## Quickstart

> **One-shot pipeline → server.** Copy-paste from a fresh checkout:

```bash
# 1. dependencies (one-time)
pip install yaml pandas numpy yfinance statsmodels matplotlib seaborn pyarrow

# 2. run the full pipeline (rebuilds dashboard_data.json)
python3 basket_check.py        # intra-/cross-basket correlations
python3 driver_analysis_v5.py  # basket + name factor regressions
python3 phase3_analysis.py     # pairs · residuals · regime alerts
python3 phase4_analysis.py     # attribution + IVR (implied vs realized)
python3 consolidate_data.py    # merges all of the above into dashboard_data.json

# 3. launch the dashboard (custom dev server · adds /api/refresh)
python3 server.py
#   ↳ same static-file behavior as `python3 -m http.server 8000` plus a
#     POST /api/refresh endpoint the in-app "↻ refresh" button calls to
#     re-run consolidate_data.py without leaving the browser.

# 4. open in browser
open http://localhost:8000/dashboard.html
```

> The **↻ refresh** button in the top-bar re-runs the consolidator and reloads. Use it any time you've nudged YAML constituents, re-pulled prices, or re-run an upstream phase - no manual reload needed.

> Hard-refresh with **Cmd+Shift+R** after editing source files (atlas.jsx / app.jsx / dashboard.html). The JSON is cache-busted automatically; only the JS/CSS need a manual refresh.

If you already have `dashboard_data.json` (committed or shipped to you), you can skip steps 1–2 and go straight to `python3 server.py`.

---

## What you get

The dashboard renders an illustrated island. 23 sub-industry baskets are placed across 10 districts (Open Ocean · Port · Industrial · Oilfield · Equipment Yards · Quarry · Farmland · Power · Nuclear · Town).

Three view modes (toggle bottom-left):

| Mode | What edges represent |
|---|---|
| **Flow** | Commodity flow (taxonomy-driven) - molecule from upstream → downstream |
| **Correlation** *(default)* | Cross-basket weekly-return correlation. Color is a teal→neutral→gold gradient stretched across the live data's `[min ρ, max ρ]`. Width is bucketed (strong ≥ 0.7 / medium 0.5–0.7 / weak < 0.5). All pairs shown - no thresholding. |
| **Signal** | Active pair-trade signals where 60d residual `|z| > 0.5`. Gold = stretched, dashed grey = watch. |

Click any node to open the side drawer. Tabs:

- **Overview** - N constituents, 60d return, intra-basket correlation, R² to factors, IVR block (Realized / Factor-Implied / Gap)
- **Names** - sorted constituent table with 60d returns
- **Residuals** - sorted by `|z60d|`; `|z| > 1.5` rows highlighted gold (mean-reversion candidates)
- **Attribution** - 1m / 3m / YTD / 1y window selector + horizontal factor-contribution bars

The strip below the map shows top active signals (or, if no `|z| > 0.5` pairs, the top 8 strongest correlations as a fallback).

### Terminal (`terminal.html`)

A 2008-style workstation: white panels, small type, tabs across the top, numbers everywhere. Twelve default tabs (Overview, World Map, Supply Chain, Energy, Metals, Options, Volatility, Balances, Trade Flows, Markets, Data, Equities), each a grid of widgets.

**Layout is yours.** Drag a widget by its title bar, resize from its bottom-right corner, maximize or remove it from its header, add any widget from the list on the left, add, rename or delete tabs, lock the layout, or reset a tab. Twenty rows always fill the screen. The arrangement is saved in the browser (`localStorage`), per machine.

**Linked selection.** The Commodity and Country pickers in the bar under the tabs drive every widget in the visible tab: pick cobalt and the map, the sheet and the routes table all follow; click a country on the map and its sheet loads.

| Widget | What it does |
|---|---|
| World Map | MapLibre map, dark or light, flat or globe. Shade countries by production, reserves, processing or consumption share. Trunk pipelines, refineries, LNG, mines, smelters and enrichment always shown; the dense US pipeline network and smaller sites appear as you zoom in. Sea-routed trade flows and chokepoints. Click a country for its sheet, a line or dot for details. |
| Commodity Sheet | Producers with share, YoY and reserves; processing shares; trade links; end uses; HHI and top-3 concentration; an outage calculator (country X loses N% -> share of world supply gone). |
| Country Sheet | Everything one country produces, processes, trades and consumes, with world share and rank, plus its mapped infrastructure. |
| Supply Concentration | One row per commodity: top producer, top-3 share, HHI, top processor, reserve life, US import reliance. |
| Trade Routes, Chokepoints | Largest physical routes by commodity, and what share of mapped trade passes each chokepoint. |
| Quote Board, Movers, Chart | Energy and metals futures, vol indices, ETF proxies: last, 1d/1m/1y, 52-week range, realized vol; price chart with overlay and rolling vol. |
| Vol Monitor | Implied vs realized for every option underlying: ATM IV at about 30 and 90 days, term slope, RV10/20/60, IV minus RV, implied move. |
| Options Desk | Listed chains with implied vol solved from mid, Black-Scholes fair value at a vol you choose, edge, greeks, smile, term structure, surface. Click a bid or ask to send a leg to the builder. |
| Strategy Builder | Multi-leg P&L at expiry, today, and any day/vol scenario; breakevens, max gain/loss, probability of profit; convexity (delta/gamma profiles, a Taylor split of P&L by sigma move, the daily move needed to cover theta); spot x vol grid; greeks by leg. 18 presets including calendars. |
| Oil Balance | The balance model as tables. |
| Data Workbench | Every dataset as a table: filter, `Where` expressions, formula columns (`production / reserves`, `Z("share")`, `MA("close", 20)`), group-by, charts, copy to spreadsheet, CSV. |

Code: `terminal/wm.js` (shell, tables, charts), one file per widget family, `terminal/wm.css`. Layout engine is GridStack 10 from a CDN; country shapes are vendored in `terminal/world-50m.json`.

Data files and the scripts that rebuild them:

```bash
.venv/bin/python scripts/market_snapshot.py     # market_data.json + options_data.json (Yahoo; nightly)
.venv/bin/python scripts/build_commodities.py   # commodities.json (USGS, EIA international, WNA + curated)
.venv/bin/python scripts/build_infra.py         # infra_pipelines.geojson + infra_facilities.geojson (EIA, USGS + curated)
```

Limits worth knowing: options are American-style ETF and equity proxies (USO for WTI, GLD for gold, and so on) priced with the European Black-Scholes approximation, because Yahoo carries no chains for the futures themselves; quotes are delayed snapshots. The pricing engine is `terminal/bs.js`, tested by `tests/test_bs.mjs`.

### Flows (`flows.html`)

Atlas Plate II: a world map of the largest crude, products, LNG, pipeline gas, coal and uranium trade routes. Arcs are routed over a sea-lane graph and forced through the chokepoints each route depends on; click a chokepoint to see what share of mapped trade passes it. Data is the curated, sourced `flows_data.json` (mostly calendar 2025, each flow carrying its year, source and a confidence grade). It is a baseline of what normally moves, not a live tracker.

### Balances (`balances.html`)

A barebones oil supply/demand model, `stock build = supply - demand + unexplained`:

- **Global liquids (monthly)** from the EIA Short-Term Energy Outlook. A variance bridge shows which supply and demand blocs moved the implied build; a one-variable fit of observed OECD stock changes on the implied build gives the error bar.
- **US crude (weekly)** from the Weekly Petroleum Status Report: production + imports - exports - refinery inputs, bridged against the five-year seasonal normal, with EIA's adjustment as the residual.

```bash
.venv/bin/python scripts/oil_balance.py     # key-free EIA downloads -> oil_balance.json
```

The nightly job runs it; each STEO release is archived to `macro_series` so forecast error accumulates. Model code and tests: `signal_engine/macro/oil_balance.py`, `tests/test_oil_balance.py`.

---

## File layout

| File | Role |
|---|---|
| `dashboard.html` | HTML shell · all CSS · SVG `<defs>` (filters, patterns, markers) |
| `app.jsx` | React app · map SVG · edges (D3) · drawer · screener · signals strip |
| `atlas.jsx` | Data hydrator · loads `dashboard_data.json` → `window.NODES` etc. · holds node `(x,y)` overlay |
| `districts.jsx` | Illustrated island components (terrain, motifs) - **don't modify** without illustrator's input |
| `dashboard_data.json` | Pipeline output (~450 KB) consumed at page load |
| `map_config.json` | Optional district-label overrides |
| `energy_taxonomy.yaml` | Source of truth for baskets + constituents (drives every analysis script) |

### Pipeline scripts

| Script | Output | Reads |
|---|---|---|
| `basket_check.py` | `basket_results/{intra_basket_correlation,cross_basket_correlation,…}.csv` | YAML + `price_cache.parquet` (re-fetches from Yahoo if stale) |
| `driver_analysis_v5.py` | `drivers_results/{basket_loadings,name_loadings}.csv`, `basket_results/representative_tickers.csv` | YAML + price cache + driver cache |
| `phase3_analysis.py` | `phase3_results/{pairs_table,name_residuals,regime_change_alerts}.csv` | YAML + price cache |
| `phase4_analysis.py` | `phase4_results/{attribution_baskets,attribution_names,ivr_snapshot}.csv` | YAML + price + driver caches + `drivers_results/basket_loadings.csv` |
| `consolidate_data.py` | `dashboard_data.json` | All of the above |

The price/driver Parquet caches (`price_cache.parquet`, `driver_cache.parquet`) auto-refresh from Yahoo via `yfinance` if missing. First run will be slow (~minutes for ~250 tickers + 5y of history); subsequent runs are seconds.

---

## Data contract (top of `dashboard_data.json`)

```jsonc
{
  "meta":   { "generated_at", "taxonomy_version", "n_nodes", "n_constituents", "lookback_years" },
  "drivers": ["CL", "NG", "CRACK_321", "WTI_BRENT", "SPX", "TNX", "URA", "BDRY"],
  "nodes":  [ /* 23 NodeObj - see ARCHITECTURE.md for full schema */ ],
  "pairs":  [ /* taxonomy-driven long/short pair signals with z, thesis, ret_3m */ ],
  "regime_alerts": [ /* rolling-beta breaks */ ],
  "cross_basket_correlation": { "<id>": { "<id>": <ρ> } }
}
```

Each `NodeObj` carries: `display_name`, `description`, `representative_ticker`, `n_constituents`, `intra_corr`, `basket_loadings`, `r2`, `dominant_driver`, `constituents[]` (with `residual_z60d`, `return_60d`, `name_loadings`), `attribution.{1m,3m,ytd,1y}.{actual,factor,idio,contribs}`, and `ivr.{actual_4w, implied_4w, gap_4w}`.

See `ARCHITECTURE.md` for design rationale and the deferred-feature list.

---

## Development notes

- **No build step.** React + Babel-standalone + D3 are loaded from CDN. JSX is transpiled in the browser.
- **Adding/moving a node visually:** edit `POSITIONS` in `atlas.jsx`. Analysis re-runs do not touch this overlay.
- **Adding a constituent:** edit `energy_taxonomy.yaml`, then re-run the pipeline (steps 2–3 above).
- **Hard refresh after edits:** `Cmd+Shift+R` with DevTools open and "Disable cache" checked.
- **Three-font system:** EB Garamond (serif headers/names) · Inter (UI) · JetBrains Mono (numerics).
- **Anti-goals:** no playful gradients, no rounded SaaS cards, no light mode. This is an analyst workstation.

## Deploying to Vercel

The frontend is a static bundle (HTML + JSX-transpiled-in-browser + JSON) - no build step. The repo is wired for Vercel:

- `vercel.json` serves `.jsx` files with `Content-Type: application/javascript` (required so Babel-standalone can pick them up) and redirects `/` → `/dashboard.html`.
- `.vercelignore` keeps the Python pipeline, parquet caches, and intermediate `*_results/` CSVs out of the deploy. Only `dashboard.html`, the three `.jsx` files, `dashboard_data.json`, `map_config.json`, and `icons/` ship to prod.
- The ↻ refresh button is hidden on non-localhost hosts - pipeline reruns are local-only. To update the hosted snapshot, re-run the pipeline locally, then commit the new `dashboard_data.json`.

Deploy: `vercel` (preview) or `vercel --prod`. No env vars or build command needed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "ATLAS HYDRATION FAILED" red banner | You opened `dashboard.html` via `file://`. Always serve via `python3 -m http.server`. |
| Stale data after pipeline re-run | Hard refresh; if still wrong, check that `consolidate_data.py` printed `[done] wrote dashboard_data.json`. |
| Empty signals strip | No pairs at `|z| > 0.5` right now. The strip falls back to top correlations. Re-run `phase3_analysis.py` to recompute. |
| Node missing from map | `[atlas] nodes in dashboard_data.json not on map (no POSITIONS entry):` warning in console - add the id to `POSITIONS` in `atlas.jsx`. |
