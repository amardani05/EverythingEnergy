# IMA Energy Atlas

Institutional research dashboard mapping ~205 energy names across the S&P 500/400/600 onto 23 sub-industry baskets in 10 districts. Tracks basket cohesion, factor exposures (CL · NG · CRACK · WTI–BRENT · SPX · TNX · URA · BDRY), pair-trade signals, residual stretches, and factor-vs-idiosyncratic attribution.

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

> The **↻ refresh** button in the top-bar re-runs the consolidator and reloads. Use it any time you've nudged YAML constituents, re-pulled prices, or re-run an upstream phase — no manual reload needed.

> Hard-refresh with **Cmd+Shift+R** after editing source files (atlas.jsx / app.jsx / dashboard.html). The JSON is cache-busted automatically; only the JS/CSS need a manual refresh.

If you already have `dashboard_data.json` (committed or shipped to you), you can skip steps 1–2 and go straight to `python3 server.py`.

---

## What you get

The dashboard renders an illustrated island. 23 sub-industry baskets are placed across 10 districts (Open Ocean · Port · Industrial · Oilfield · Equipment Yards · Quarry · Farmland · Power · Nuclear · Town).

Three view modes (toggle bottom-left):

| Mode | What edges represent |
|---|---|
| **Flow** | Commodity flow (taxonomy-driven) — molecule from upstream → downstream |
| **Correlation** *(default)* | Cross-basket weekly-return correlation. Color is a teal→neutral→gold gradient stretched across the live data's `[min ρ, max ρ]`. Width is bucketed (strong ≥ 0.7 / medium 0.5–0.7 / weak < 0.5). All pairs shown — no thresholding. |
| **Signal** | Active pair-trade signals where 60d residual `|z| > 0.5`. Gold = stretched, dashed grey = watch. |

Click any node to open the side drawer. Tabs:

- **Overview** — N constituents, 60d return, intra-basket correlation, R² to factors, IVR block (Realized / Factor-Implied / Gap)
- **Names** — sorted constituent table with 60d returns
- **Residuals** — sorted by `|z60d|`; `|z| > 1.5` rows highlighted gold (mean-reversion candidates)
- **Attribution** — 1m / 3m / YTD / 1y window selector + horizontal factor-contribution bars

The strip below the map shows top active signals (or, if no `|z| > 0.5` pairs, the top 8 strongest correlations as a fallback).

---

## File layout

| File | Role |
|---|---|
| `dashboard.html` | HTML shell · all CSS · SVG `<defs>` (filters, patterns, markers) |
| `app.jsx` | React app · map SVG · edges (D3) · drawer · screener · signals strip |
| `atlas.jsx` | Data hydrator · loads `dashboard_data.json` → `window.NODES` etc. · holds node `(x,y)` overlay |
| `districts.jsx` | Illustrated island components (terrain, motifs) — **don't modify** without illustrator's input |
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
  "nodes":  [ /* 23 NodeObj — see ARCHITECTURE.md for full schema */ ],
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

## Troubleshooting

| Symptom | Fix |
|---|---|
| "ATLAS HYDRATION FAILED" red banner | You opened `dashboard.html` via `file://`. Always serve via `python3 -m http.server`. |
| Stale data after pipeline re-run | Hard refresh; if still wrong, check that `consolidate_data.py` printed `[done] wrote dashboard_data.json`. |
| Empty signals strip | No pairs at `|z| > 0.5` right now. The strip falls back to top correlations. Re-run `phase3_analysis.py` to recompute. |
| Node missing from map | `[atlas] nodes in dashboard_data.json not on map (no POSITIONS entry):` warning in console — add the id to `POSITIONS` in `atlas.jsx`. |
