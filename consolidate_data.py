"""
IMA Energy Dashboard - Data Consolidator
==========================================

Reads all analysis outputs and produces a single dashboard_data.json that
the UI consumes. Run after each analysis update; the UI is stateless and
reads this single file at page load.

INPUTS (all should be in current directory):
  - energy_taxonomy_v0.4.yaml              (node graph + constituents)
  - basket_results/intra_basket_correlation.csv
  - basket_results/cross_basket_correlation.csv
  - basket_results/representative_tickers.csv  (from driver_analysis_v5)
  - drivers_results/basket_loadings.csv
  - drivers_results/name_loadings.csv
  - phase3_results/pairs_table.csv
  - phase3_results/name_residuals.csv
  - phase3_results/regime_change_alerts.csv
  - price_cache.parquet (for return histories)

OUTPUT:
  - dashboard_data.json   - single file, ~2-5MB, consumed by index.html

JSON STRUCTURE:
{
  "meta": {generated_at, version, n_nodes, n_constituents},
  "drivers": [list of driver names],
  "nodes": [
    {
      "id": "upstream_oil_eandp",
      "display_name": "...",
      "layer": "onshore_subsurface",
      "representative_ticker": "FANG",
      "thesis": "...",
      "feeds_into": [...],
      "served_by_revenue_from": [...],
      "constituents": [
        {"ticker": "FANG", "name": "...", "mc_bucket": "large", "index": "500",
         "residual_z60d": -0.4, "residual_cum60d": -0.05,
         "name_loadings": {"CL": 0.85, "NG": 0.02, ...},
         "return_60d": -0.08}
      ],
      "intra_corr": {"mean": 0.57, "min": -0.31, "max": 0.87},
      "basket_loadings": {"CL": 0.97, "NG": 0.01, ...},
      "var_shares": {"CL": 0.45, "SPX": 0.10, ...},
      "r2": 0.59,
      "rolling_betas": {  // last 60 obs sampled
        "CL": [{"date": "...", "beta": 0.85}, ...],
        "NG": [...]
      },
      "cumulative_returns": [{"date": "...", "value": 0.0}, ...]  // 5y
    }
  ],
  "pairs": [
    {"long": "...", "short": "...", "thesis": "...", "z_60d": ..., "ret_3m": ...}
  ],
  "regime_alerts": [...]
}

USAGE
-----
python consolidate_data.py
"""

import yaml
import pandas as pd
import numpy as np
import json
import math
from pathlib import Path
from datetime import datetime

# ---------- paths ----------
YAML_PATH = 'energy_taxonomy.yaml'
PRICE_CACHE = 'price_cache.parquet'
OUTPUT = 'dashboard_data.json'

PHASE_PATHS = {
    'intra': 'basket_results/intra_basket_correlation.csv',
    'cross': 'basket_results/cross_basket_correlation.csv',
    'reps': 'basket_results/representative_tickers.csv',
    'basket_loadings': 'drivers_results/basket_loadings.csv',
    'name_loadings': 'drivers_results/name_loadings.csv',
    'pairs': 'phase3_results/pairs_table.csv',
    'residuals': 'phase3_results/name_residuals.csv',
    'regime': 'phase3_results/regime_change_alerts.csv',
}

# Also try flat layout (if user keeps everything in one folder)
def find_csv(name, paths):
    for p in [paths.get(name), name + '.csv', f'./{name}.csv']:
        if p and Path(p).exists():
            return p
    return None

def safe_read(name):
    """Read CSV from any of the expected locations."""
    p = find_csv(name, PHASE_PATHS)
    if p is None:
        # Try the phase folders explicitly
        for folder in ['basket_results', 'drivers_results', 'phase3_results']:
            cand = Path(folder) / f'{name}.csv'
            if cand.exists():
                return pd.read_csv(cand)
        # Last resort: flat
        flat = Path(f'{name}.csv')
        if flat.exists():
            return pd.read_csv(flat)
        return None
    return pd.read_csv(p)

# ---------- helpers ----------
def safe_float(x):
    try:
        v = float(x)
        if np.isnan(v) or np.isinf(v):
            return None
        return round(v, 4)
    except (ValueError, TypeError):
        return None

def df_to_records(df):
    """Convert df to list of dicts with NaN → None."""
    if df is None or df.empty:
        return []
    return df.replace({np.nan: None}).to_dict(orient='records')

# ---------- main ----------
def main():
    print("[consolidate] reading taxonomy...")
    with open(YAML_PATH) as f:
        tax = yaml.safe_load(f)
    
    # Driver list - from basket_loadings columns
    print("[consolidate] reading analysis CSVs...")
    
    # Resolve a CSV by trying cwd first then the natural phase output dir.
    def _read(*candidates):
        for p in candidates:
            if Path(p).exists():
                return pd.read_csv(p)
        return pd.DataFrame()

    intra_df = safe_read('intra_basket_correlation')
    if intra_df is None:
        intra_df = _read('intra_basket_correlation.csv', 'basket_results/intra_basket_correlation.csv')

    basket_loadings_df = _read('basket_loadings.csv', 'drivers_results/basket_loadings.csv')
    name_loadings_df   = _read('name_loadings.csv',   'drivers_results/name_loadings.csv')
    pairs_df           = _read('pairs_table.csv',     'phase3_results/pairs_table.csv')
    residuals_df       = _read('name_residuals.csv',  'phase3_results/name_residuals.csv')
    regime_df          = _read('regime_change_alerts.csv', 'phase3_results/regime_change_alerts.csv')
    
    # v2 addition: load cross-basket correlation matrix for the map view
    cross_corr = None
    cross_path = None
    for p in ['cross_basket_correlation.csv', 'basket_results/cross_basket_correlation.csv']:
        if Path(p).exists():
            cross_path = p
            break
    if cross_path:
        cdf = pd.read_csv(cross_path)
        # First column is row label
        first_col = cdf.columns[0]
        cdf = cdf.set_index(first_col)
        cross_corr = {row: {col: safe_float(cdf.loc[row, col]) for col in cdf.columns}
                      for row in cdf.index}
        print(f"[consolidate] cross-basket correlation loaded: {len(cross_corr)} rows")
    
    # v3 addition: attribution data (factor vs idio per basket and per name)
    attr_baskets_df = pd.DataFrame()
    attr_names_df = pd.DataFrame()
    ivr_df = pd.DataFrame()
    for p in ['attribution_baskets.csv', 'phase4_results/attribution_baskets.csv']:
        if Path(p).exists():
            attr_baskets_df = pd.read_csv(p)
            print(f"[consolidate] attribution_baskets loaded: {len(attr_baskets_df)} rows")
            break
    for p in ['attribution_names.csv', 'phase4_results/attribution_names.csv']:
        if Path(p).exists():
            attr_names_df = pd.read_csv(p)
            print(f"[consolidate] attribution_names loaded: {len(attr_names_df)} rows")
            break
    for p in ['ivr_snapshot.csv', 'phase4_results/ivr_snapshot.csv']:
        if Path(p).exists():
            ivr_df = pd.read_csv(p)
            print(f"[consolidate] ivr_snapshot loaded: {len(ivr_df)} rows")
            break
    
    # Driver list - extract from beta_ columns
    drivers = []
    if not basket_loadings_df.empty:
        drivers = [c.replace('beta_', '') for c in basket_loadings_df.columns if c.startswith('beta_')]
    print(f"[consolidate] drivers found: {drivers}")
    
    # Reps
    reps_map = {}
    if Path('representative_tickers.csv').exists():
        reps_df = pd.read_csv('representative_tickers.csv')
        reps_map = dict(zip(reps_df['node'], reps_df['representative']))
    
    # Prices for return histories
    prices = None
    if Path(PRICE_CACHE).exists():
        prices = pd.read_parquet(PRICE_CACHE)
        print(f"[consolidate] prices: {prices.shape}")
    
    # ---------- build node objects ----------
    nodes = []
    for node_id, node_data in tax.items():
        if not isinstance(node_data, dict) or 'constituents' not in node_data:
            continue
        if node_id.startswith('external_'):
            continue
        
        # Constituents with loadings + residuals merged in
        constituents = []
        for c in node_data['constituents']:
            tk = c['ticker']
            con = {
                'ticker': tk,
                'name': c.get('name', ''),
                'mc_bucket': c.get('mc', '?'),
                'index': str(c.get('index', '?')),
                'tax_form': c.get('tax_form', '1099'),
                'sub_tag': c.get('basin') or c.get('niche') or c.get('fuel') or '',
                'confidence': c.get('confidence', 'high'),
            }
            
            # Merge in residuals
            if not residuals_df.empty:
                rrow = residuals_df[residuals_df['ticker'] == tk]
                if len(rrow):
                    con['residual_z60d'] = safe_float(rrow['z_resid_60d'].iloc[0])
                    con['residual_cum60d'] = safe_float(rrow['cum_resid_60d'].iloc[0])
                    con['beta_to_basket'] = safe_float(rrow['beta_to_basket'].iloc[0])
                    con['r2_to_basket'] = safe_float(rrow['r2'].iloc[0])
            
            # Merge in name loadings
            if not name_loadings_df.empty:
                nrow = name_loadings_df[name_loadings_df['ticker'] == tk]
                if len(nrow):
                    loadings = {}
                    var_shares = {}
                    for d in drivers:
                        bcol = f'beta_{d}'
                        tcol = f't_{d}'
                        vcol = f'varshare_{d}'
                        if bcol in nrow.columns:
                            loadings[d] = {
                                'beta': safe_float(nrow[bcol].iloc[0]),
                                't': safe_float(nrow[tcol].iloc[0]) if tcol in nrow.columns else None,
                            }
                        if vcol in nrow.columns:
                            var_shares[d] = safe_float(nrow[vcol].iloc[0])
                    con['driver_loadings'] = loadings
                    con['driver_var_shares'] = var_shares
                    con['name_r2'] = safe_float(nrow['r2_adj'].iloc[0])
                    con['name_dominant'] = nrow['dominant'].iloc[0] if 'dominant' in nrow.columns else None
            
            # Recent return
            if prices is not None and tk in prices.columns:
                px = prices[tk].dropna()
                if len(px) > 60:
                    con['return_60d'] = safe_float(np.log(px.iloc[-1] / px.iloc[-60]))
                    con['return_ytd'] = safe_float(np.log(px.iloc[-1] / px[px.index.year >= 2026].iloc[0])) if (px.index.year >= 2026).any() else None
                con['last_price'] = safe_float(px.iloc[-1]) if len(px) else None
            
            constituents.append(con)
        
        # Intra-basket correlation
        intra_info = {}
        if not intra_df.empty:
            irow = intra_df[intra_df['node'] == node_id]
            if len(irow):
                intra_info = {
                    'mean': safe_float(irow['mean_pair_corr'].iloc[0]),
                    'min': safe_float(irow['min_corr'].iloc[0]),
                    'max': safe_float(irow['max_corr'].iloc[0]),
                    'min_pair': irow['min_pair'].iloc[0] if 'min_pair' in irow.columns else None,
                    'max_pair': irow['max_pair'].iloc[0] if 'max_pair' in irow.columns else None,
                }
        
        # Basket loadings
        basket_loadings = {}
        var_shares = {}
        r2 = None
        dominant = None
        if not basket_loadings_df.empty:
            brow = basket_loadings_df[basket_loadings_df['node'] == node_id]
            if len(brow):
                r2 = safe_float(brow['r2_adj'].iloc[0])
                dominant = brow['dominant'].iloc[0] if 'dominant' in brow.columns else None
                for d in drivers:
                    bcol = f'beta_{d}'
                    tcol = f't_{d}'
                    vcol = f'varshare_{d}'
                    if bcol in brow.columns:
                        basket_loadings[d] = {
                            'beta': safe_float(brow[bcol].iloc[0]),
                            't': safe_float(brow[tcol].iloc[0]) if tcol in brow.columns else None,
                        }
                    if vcol in brow.columns:
                        var_shares[d] = safe_float(brow[vcol].iloc[0])
        
        # Cumulative returns (basket EW) - sample at weekly freq for chart.
        # Single-constituent baskets (e.g. nuclear_smr_developers / OKLO) used to
        # fall through and produce an empty series, which broke the backtester
        # with "not enough history". For len==1 we use that ticker's series
        # directly; otherwise EW-mean across constituents.
        cum_returns = []
        if prices is not None:
            tk_in_prices = [c['ticker'] for c in node_data['constituents'] if c['ticker'] in prices.columns]
            if len(tk_in_prices) >= 1:
                sub = prices[tk_in_prices]
                # Weekly returns
                weekly = sub.resample('W-FRI').last()
                weekly_ret = np.log(weekly / weekly.shift(1)).replace([np.inf, -np.inf], np.nan)
                basket_w = weekly_ret.mean(axis=1, skipna=True).dropna()
                cum = (1 + basket_w).cumprod() - 1
                # Sample to ~150 points
                step = max(1, len(cum) // 150)
                cum_sampled = cum.iloc[::step]
                cum_returns = [
                    {'date': d.strftime('%Y-%m-%d'), 'value': safe_float(v)}
                    for d, v in cum_sampled.items() if safe_float(v) is not None
                ]
        
        # Rolling betas - last 130 weekly obs (~2.5y) for visualization
        # We don't have these stored; could compute on-the-fly but expensive.
        # For UI we'll skip this for now and fetch from rolling_betas charts as images.
        # Future: include here.
        
        nodes.append({
            'id': node_id,
            'display_name': node_data.get('display_name', node_id),
            'description': node_data.get('description', ''),
            'layer': node_data.get('layer', 'unknown'),
            'representative_ticker': reps_map.get(node_id) or node_data.get('representative_ticker', ''),
            'gics': node_data.get('gics', ''),
            'commodity_exposure': node_data.get('commodity_exposure', []),
            'key_drivers': node_data.get('key_drivers', []),
            'feeds_into': node_data.get('feeds_into', []),
            'served_by_revenue_from': node_data.get('served_by_revenue_from', []),
            'constituents': constituents,
            'n_constituents': len(constituents),
            'intra_corr': intra_info,
            'basket_loadings': basket_loadings,
            'var_shares': var_shares,
            'r2': r2,
            'dominant_driver': dominant,
            'cumulative_returns': cum_returns,
        })
    
    # ---------- pairs ----------
    pairs_list = []
    if not pairs_df.empty:
        for _, p in pairs_df.iterrows():
            if p.get('note') != 'ok':
                continue
            pairs_list.append({
                'long': p['long'], 'short': p['short'],
                'thesis': p.get('thesis', ''),
                'z_60d': safe_float(p.get('z_60d')),
                'corr_now': safe_float(p.get('corr_now')),
                'corr_min': safe_float(p.get('corr_min')),
                'corr_max': safe_float(p.get('corr_max')),
                'pair_vol': safe_float(p.get('pair_vol')),
                'ret_1m': safe_float(p.get('ret_1m')),
                'ret_3m': safe_float(p.get('ret_3m')),
            })
    
    # ---------- regime alerts ----------
    regime_list = df_to_records(regime_df) if not regime_df.empty else []
    
    # ---------- attribution data per node (from phase4) ----------
    attribution_by_node = {}
    if not attr_baskets_df.empty:
        for node_id, grp in attr_baskets_df.groupby('node'):
            windows = {}
            for _, row in grp.iterrows():
                w = row['window']
                contribs = {}
                for col in row.index:
                    if col.startswith('contrib_'):
                        v = safe_float(row[col])
                        if v is not None and abs(v) > 0.0001:
                            contribs[col.replace('contrib_', '')] = v
                windows[w] = {
                    'n_weeks': int(row['n_weeks']),
                    'actual': safe_float(row['actual']),
                    'factor': safe_float(row['factor']),
                    'idio': safe_float(row['idio']),
                    'idio_share': safe_float(row['idio_share']),
                    'contribs': contribs,
                }
            attribution_by_node[node_id] = windows

    # ---------- name-level attribution (from phase4) ----------
    # Group attribution_names rows by ticker -> { window: {actual, factor, idio, idio_share, contribs} }.
    # Stock-level drawer reads c.attribution to render the same factor-vs-idio
    # bars at the constituent grain. No contribs in this CSV (yet); the bar
    # chart degrades gracefully to actual/factor/idio only.
    attribution_by_ticker = {}
    if not attr_names_df.empty:
        for tk, grp in attr_names_df.groupby('ticker'):
            windows = {}
            for _, row in grp.iterrows():
                w = row['window']
                # Some columns may exist as contrib_* in future revs; pick them up
                # opportunistically so we don't have to re-edit when they land.
                contribs = {}
                for col in row.index:
                    if col.startswith('contrib_'):
                        v = safe_float(row[col])
                        if v is not None and abs(v) > 0.0001:
                            contribs[col.replace('contrib_', '')] = v
                windows[w] = {
                    'n_weeks': int(row['n_weeks']),
                    'actual': safe_float(row['actual']),
                    'factor': safe_float(row['factor']),
                    'idio': safe_float(row['idio']),
                    'idio_share': safe_float(row['idio_share']),
                    'contribs': contribs,
                }
            attribution_by_ticker[tk] = windows
        print(f"[consolidate] name-level attribution: {len(attribution_by_ticker)} tickers")
    
    # ---------- IVR snapshot per node ----------
    ivr_by_node = {}
    if not ivr_df.empty:
        for _, row in ivr_df.iterrows():
            ivr_by_node[row['node']] = {
                'actual_4w': safe_float(row['actual_4w']),
                'implied_4w': safe_float(row['implied_4w']),
                'gap_4w': safe_float(row['gap_4w']),
            }
    
    # ---------- attach to nodes ----------
    for n in nodes:
        n['attribution'] = attribution_by_node.get(n['id'], {})
        n['ivr'] = ivr_by_node.get(n['id'], {})
        # Attach name-level attribution to each constituent by ticker.
        for c in n['constituents']:
            c['attribution'] = attribution_by_ticker.get(c['ticker'], {})
    
    # ---------- per-ticker weekly cumulative returns ----------
    # Lab needs single-name backtests + pair legs that can be tickers.
    # We emit a shared `weekly_index` (one row of dates) and a dict
    # `weekly_returns_by_ticker` where each value is an aligned array of
    # cumulative returns (null for weeks before the ticker's first close).
    weekly_index = []
    weekly_by_ticker = {}
    if prices is not None:
        weekly = prices.resample('W-FRI').last()
        weekly_ret = np.log(weekly / weekly.shift(1)).replace([np.inf, -np.inf], np.nan)
        # Per-ticker cumulative log return → simple cum return; keep NaN for
        # bars before the ticker started so the JS side knows where to begin.
        weekly_index = [d.strftime('%Y-%m-%d') for d in weekly.index]
        for tk in weekly.columns:
            ser = weekly_ret[tk]
            # Valid range = first non-NaN onward
            first_valid = ser.first_valid_index()
            if first_valid is None:
                continue
            ret_clean = ser.copy()
            ret_clean.loc[ret_clean.index < first_valid] = None  # mark pre-start
            ret_clean.loc[ret_clean.index >= first_valid] = ret_clean.loc[ret_clean.index >= first_valid].fillna(0)
            cum = (1 + ret_clean.fillna(0)).cumprod() - 1
            # Re-mask the pre-start area as null
            cum.loc[cum.index < first_valid] = None
            arr = []
            for v in cum.values:
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    arr.append(None)
                else:
                    fv = safe_float(v)
                    arr.append(fv if fv is not None else None)
            weekly_by_ticker[tk] = arr
        print(f"[consolidate] weekly_returns_by_ticker: {len(weekly_by_ticker)} tickers · {len(weekly_index)} weeks")

    # ---------- build payload ----------
    payload = {
        'meta': {
            'generated_at': datetime.now().isoformat(),
            'taxonomy_version': tax.get('meta', {}).get('version', '0.4'),
            'n_nodes': len(nodes),
            'n_constituents': sum(n['n_constituents'] for n in nodes),
            'lookback_years': 5,
        },
        'drivers': drivers,
        'nodes': nodes,
        'pairs': pairs_list,
        'regime_alerts': regime_list,
        'cross_basket_correlation': cross_corr,
        'weekly_index': weekly_index,
        'weekly_returns_by_ticker': weekly_by_ticker,
    }
    
    def _sanitize(obj):
        """NaN/inf -> None recursively. Python's json.dump happily emits bare
        NaN, which is NOT valid JSON: browsers throw on parse and the whole
        dashboard fails to hydrate. Caught live 2026-07-19 ('in_pair': NaN)."""
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return obj

    with open(OUTPUT, 'w') as f:
        json.dump(_sanitize(payload), f, indent=None, default=str)  # indent=None for smallest size
    
    sz = Path(OUTPUT).stat().st_size
    print(f"\n[done] wrote {OUTPUT}: {sz/1024:.1f} KB")
    print(f"  {len(nodes)} nodes, {sum(n['n_constituents'] for n in nodes)} constituents")
    print(f"  {len(pairs_list)} pairs, {len(regime_list)} regime alerts")

if __name__ == '__main__':
    main()