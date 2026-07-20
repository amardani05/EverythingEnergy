"""
IMA Energy Dashboard - Phase 2 Driver Analysis (v5)
====================================================

Fixes from v1:
1. CHART BUG: basket_loadings heatmap was rendering blank because the annotation
   DataFrame was numeric and pandas silently NaN'd the strings. Switched to a
   pre-built object-dtype DataFrame.
2. NaN BASKET BUG: upstream_gas_eandp returned NaN because a recent IPO (BKV) had
   gaps that propagated through the equal-weight basket. Now we drop NaN rows
   from each basket before regression, and report what was dropped.
3. PRICE CACHE STALE: v1 reused the Phase 1 price cache (166 tickers). v0.4
   taxonomy adds ~40 utilities. Now detects mismatch and refetches if missing
   tickers are >5% of universe.
4. SPECIALTY DRIVERS: added more granular drivers where freely available:
   - HH_FORWARD via NG=F (already there)
   - JKM_HH_PROXY: would need FactSet - flagged
   - URANIUM proxy via URA ETF
   - REFINERY via XOP (E&P ETF for residualization)
   - COAL via KOL ETF (thermal coal proxy)
   - SHIPPING via BDRY ETF (shipping rates proxy)
5. REPRESENTATIVE_TICKER: now computed as the highest-correlation-to-basket name
   (per Amar's guidance). Stored in a separate output for downstream use.

USAGE
-----
1. Place alongside energy_taxonomy_v0.4.yaml
2. pip install yfinance pandas-datareader pandas numpy pyyaml pyarrow matplotlib seaborn statsmodels
3. python driver_analysis_v2.py
4. Open drivers_results/report.html

The first run will fetch prices for any tickers missing from cache.
To force a complete refetch: delete price_cache.parquet
"""

import yaml
import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import matplotlib.pyplot as plt
import seaborn as sns
import statsmodels.api as sm
from pathlib import Path
from html import escape

try:
    import pandas_datareader.data as pdr
    PDR_AVAILABLE = True
except ImportError:
    PDR_AVAILABLE = False
    print("[warn] pandas-datareader not installed - install for FRED access")

warnings.filterwarnings('ignore')

# -----------------------------------------------------------------
YAML_PATH = 'energy_taxonomy.yaml'
PRICE_CACHE = 'price_cache.parquet'
DRIVER_CACHE = 'driver_cache.parquet'
OUTPUT_DIR = Path('drivers_results')
LOOKBACK_YEARS = 5
REGRESSION_FREQ = 'W-FRI'

# Driver universe - symbol → (source, fred_or_yf_ticker, kind)
# kind: 'price' (use log returns) | 'spread' (use diffs) | 'rate' (use diffs)
DRIVERS = {
    'CL':       ('fred', 'DCOILWTICO', 'price'),
    'BRENT':    ('fred', 'DCOILBRENTEU', 'price'),
    'NG':       ('fred', 'DHHNGSP', 'price'),
    'RB':       ('yf',   'RB=F', 'price'),
    'HO':       ('yf',   'HO=F', 'price'),
    'CORN':     ('yf',   'ZC=F', 'price'),
    'SPX':      ('yf',   '^GSPC', 'price'),
    'XLE':      ('yf',   'XLE', 'price'),
    'XLU':      ('yf',   'XLU', 'price'),
    'TNX':      ('yf',   '^TNX', 'rate'),
    'DXY':      ('yf',   'DX-Y.NYB', 'price'),
    # Specialty proxies (v2 additions) - ETFs and macro proxies
    'URA':      ('yf',   'URA', 'price'),       # uranium miners ETF - proxy for U3O8
    # 'KOL' delisted Dec 2020 - no clean US thermal coal ETF replacement.
    # Coal basket attribution requires FactSet API2/Newcastle data.
    'BDRY':     ('yf',   'BDRY', 'price'),      # dry bulk shipping ETF
    'XOP':      ('yf',   'XOP', 'price'),       # E&P ETF - useful for residualizing
}

DERIVED_DRIVERS = ['CRACK_321', 'WTI_BRENT', 'NG_PROXY_LNG_SPREAD']
ALL_DRIVER_KINDS = {**{k: v[2] for k, v in DRIVERS.items()},
                    'CRACK_321': 'spread', 'WTI_BRENT': 'spread',
                    'NG_PROXY_LNG_SPREAD': 'spread'}

def compute_derived(prices_df):
    df = prices_df.copy()
    if all(c in df.columns for c in ['RB', 'HO', 'CL']):
        df['CRACK_321'] = 2*df['RB'] + df['HO'] - 3*df['CL']
    if all(c in df.columns for c in ['CL', 'BRENT']):
        df['WTI_BRENT'] = df['CL'] - df['BRENT']
    # No JKM proxy from free data - placeholder for FactSet integration
    # If user has JKM via another path, drop it into df['JKM'] and the framework picks it up.
    return df


# -----------------------------------------------------------------
# v4 helpers
# -----------------------------------------------------------------
def winsorize(s, n_std=3.0):
    """Cap a return series at +/- n_std standard deviations."""
    if s.isna().all() or s.std() == 0:
        return s
    mu, sd = s.mean(), s.std()
    return s.clip(lower=mu - n_std*sd, upper=mu + n_std*sd)

def zscore_columns(df, cols):
    """Standardize specific columns (zero mean, unit std)."""
    df = df.copy()
    for c in cols:
        if c in df.columns and df[c].std() > 0:
            df[c] = (df[c] - df[c].mean()) / df[c].std()
    return df

# Per-basket driver exclusions to avoid circular references.
# URA contains UEC, which is in uranium_nuclear_fuel basket → exclude URA from that node.
DRIVER_EXCLUSIONS = {
    'uranium_nuclear_fuel': ['URA'],
}

# -----------------------------------------------------------------
def load_taxonomy():
    with open(YAML_PATH) as f:
        return yaml.safe_load(f)

def get_constituents(tax):
    rows = []
    for node, n in tax.items():
        if isinstance(n, dict) and 'constituents' in n:
            for c in n['constituents']:
                rows.append({
                    'ticker': c['ticker'], 'node': node, 'name': c.get('name', ''),
                    'mc_bucket': c.get('mc', '?'), 'layer': n.get('layer', '?'),
                })
    return pd.DataFrame(rows)

# -----------------------------------------------------------------
def fetch_prices(tickers, years=LOOKBACK_YEARS, force=False):
    """Smart cache: only refetch if cache is missing >5% of needed tickers."""
    if not force and Path(PRICE_CACHE).exists():
        cached = pd.read_parquet(PRICE_CACHE)
        missing = set(tickers) - set(cached.columns)
        miss_pct = len(missing) / max(len(tickers), 1)
        if miss_pct < 0.05:
            print(f"[cache] price cache hit ({len(missing)} missing, {miss_pct:.1%}) - using cache")
            return cached.reindex(columns=[t for t in tickers if t in cached.columns])
        print(f"[cache] missing {len(missing)} tickers ({miss_pct:.1%}); refetching all")

    print(f"[yf] fetching {years}y daily prices for {len(tickers)} tickers...")
    data = yf.download(tickers, period=f"{years}y", interval='1d',
                       auto_adjust=True, progress=True, group_by='ticker', threads=True)
    closes = {}
    for t in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if (t, 'Close') in data.columns:
                    closes[t] = data[(t, 'Close')]
            else:
                closes[t] = data['Close']
        except Exception as e:
            print(f"  ! {t}: {e}")
    closes_df = pd.DataFrame(closes).dropna(axis=1, how='all')
    closes_df.to_parquet(PRICE_CACHE)
    print(f"[yf] got {len(closes_df.columns)} of {len(tickers)} tickers")
    return closes_df

def fetch_drivers(years=LOOKBACK_YEARS, force=False):
    if not force and Path(DRIVER_CACHE).exists():
        cached = pd.read_parquet(DRIVER_CACHE)
        # Check for new drivers added in v2
        all_keys = list(DRIVERS.keys())
        missing = [k for k in all_keys if k not in cached.columns]
        if not missing:
            print(f"[cache] driver cache hit - {cached.shape[1]} series, {cached.shape[0]} obs")
            return cached
        print(f"[cache] driver cache missing {missing} - refetching")
    
    end = pd.Timestamp.today()
    start = end - pd.DateOffset(years=years)
    series = {}
    
    fred_drivers = {k: v[1] for k, v in DRIVERS.items() if v[0] == 'fred'}
    yf_drivers = {k: v[1] for k, v in DRIVERS.items() if v[0] == 'yf'}
    
    if PDR_AVAILABLE and fred_drivers:
        print(f"[fred] pulling {len(fred_drivers)} series...")
        for k, sym in fred_drivers.items():
            try:
                s = pdr.DataReader(sym, 'fred', start, end)
                series[k] = s.iloc[:, 0]
                print(f"  ✓ {k} ({sym}): {len(s)} obs")
            except Exception as e:
                print(f"  ✗ {k}: {e}")
    
    if yf_drivers:
        print(f"[yf] pulling {len(yf_drivers)} series...")
        for k, sym in yf_drivers.items():
            try:
                d = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
                if 'Close' in d.columns:
                    series[k] = d['Close']
                else:
                    series[k] = d.xs('Close', level=0, axis=1).iloc[:, 0]
                print(f"  ✓ {k} ({sym}): {len(series[k])} obs")
            except Exception as e:
                print(f"  ✗ {k}: {e}")
    
    df = pd.concat(series, axis=1)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.ffill().dropna(how='all')
    # FIX: drop driver columns that are mostly NaN (delisted/failed fetches).
    # If <50% of obs are populated, the column is unusable and would kill regressions.
    bad_cols = [c for c in df.columns if df[c].notna().mean() < 0.5]
    if bad_cols:
        print(f"[drivers] dropping unusable columns (<50% obs): {bad_cols}")
        df = df.drop(columns=bad_cols)
    df = compute_derived(df)
    df.to_parquet(DRIVER_CACHE)
    print(f"[fetched] {df.shape[1]} drivers, {df.shape[0]} daily obs")
    return df

# -----------------------------------------------------------------
def to_returns(df, freq=REGRESSION_FREQ):
    """Resample to weekly, log returns or diffs based on series kind.
    
    v4 fix: z-score the diffs (spreads and rates) so coefficients are on
    the same scale as log-return columns. Otherwise CRACK_321 betas come
    out at ~0.001 because the spread is in $/bbl and returns are in %."""
    weekly = df.resample(freq).last()
    out = pd.DataFrame(index=weekly.index)
    diff_cols = []
    for c in weekly.columns:
        kind = ALL_DRIVER_KINDS.get(c, 'price')
        if kind in ('spread', 'rate'):
            out[c] = weekly[c].diff()
            diff_cols.append(c)
        else:
            out[c] = np.log(weekly[c] / weekly[c].shift(1))
    out = out.dropna(how='all')
    # z-score the spread/rate columns so betas are comparable
    out = zscore_columns(out, diff_cols)
    return out

def to_returns_names(df, freq=REGRESSION_FREQ):
    """Name returns are always log returns."""
    weekly = df.resample(freq).last()
    return np.log(weekly / weekly.shift(1)).dropna(how='all')

# -----------------------------------------------------------------
def basket_returns(returns_df, constituents_df):
    """v5 fix: replace +/-inf with NaN before winsorize/aggregation.
    
    Caused by Yahoo data quality issues (e.g. DEC has $0 prices in first 130 weeks
    from cross-listing migration → log(0/X) = -inf). The inf propagates into
    every basket coefficient. Defensive replacement at multiple layers."""
    baskets = {}
    diagnostics = []
    for node, group in constituents_df.groupby('node'):
        ts = [t for t in group['ticker'] if t in returns_df.columns]
        if len(ts) < 2: 
            diagnostics.append({'node': node, 'n_in_yaml': len(group), 'n_priced': len(ts), 'n_obs': 0, 'note': 'singleton or no prices'})
            continue
        sub = returns_df[ts].replace([np.inf, -np.inf], np.nan)
        sub = sub.apply(winsorize, axis=0)
        b = sub.mean(axis=1, skipna=True).replace([np.inf, -np.inf], np.nan)
        b_clean = b.dropna()
        if len(b_clean) < 100:
            diagnostics.append({'node': node, 'n_in_yaml': len(group), 'n_priced': len(ts), 'n_obs': len(b_clean), 'note': 'too few clean obs'})
            continue
        baskets[node] = b_clean
        diagnostics.append({'node': node, 'n_in_yaml': len(group), 'n_priced': len(ts), 'n_obs': len(b_clean), 'note': 'ok'})
    diag_df = pd.DataFrame(diagnostics)
    return pd.DataFrame(baskets), diag_df

# -----------------------------------------------------------------
def regress(y, X, min_obs=80):
    # v5: defensive against inf in y or X
    y = y.replace([np.inf, -np.inf], np.nan)
    X = X.replace([np.inf, -np.inf], np.nan)
    # FIX: drop X columns that are entirely NaN or have <50% obs BEFORE the dropna().
    X = X.loc[:, X.notna().mean() >= 0.5]
    if X.shape[1] == 0:
        return None
    df = pd.concat([y.rename('y'), X], axis=1).dropna()
    if len(df) < min_obs: 
        return None
    y_, X_ = df['y'], df.drop(columns=['y'])
    # Sanity: drop constant or near-zero-variance columns
    X_ = X_.loc[:, X_.std() > 1e-9]
    if X_.shape[1] == 0:
        return None
    X_ = sm.add_constant(X_)
    try:
        model = sm.OLS(y_, X_).fit(cov_type='HAC', cov_kwds={'maxlags': 4})
    except Exception as e:
        print(f"  [regress error] {e}")
        return None
    return model

def regression_summary(model):
    """v5: also compute share of variance explained per driver (β² · var(x) / var(y)).
    This is the analyst-friendly metric: "what fraction of basket return variance
    is attributable to this driver?". Sums to ≤R² across drivers (orthogonal sum)."""
    if model is None: return None
    params = model.params.drop('const', errors='ignore')
    tvals = model.tvalues.drop('const', errors='ignore')
    if len(params) == 0:
        return None
    # Variance shares: cov-based decomposition for orthogonal-ish drivers
    # Var(β·X) / Var(y) per driver, computed from the model
    y_var = float(model.model.endog.var())
    var_shares = {}
    if y_var > 0 and hasattr(model.model, 'exog'):
        exog_df = pd.DataFrame(model.model.exog, columns=model.model.exog_names)
        for d in params.index:
            if d in exog_df.columns:
                contrib_var = (params[d] ** 2) * exog_df[d].var()
                var_shares[d] = float(contrib_var / y_var) if y_var > 0 else 0
    
    return {
        'r2_adj': model.rsquared_adj,
        'r2': model.rsquared,
        'n_obs': int(model.nobs),
        'betas': params.to_dict(),
        'tvals': tvals.to_dict(),
        'var_shares': var_shares,  # NEW v5
        'dominant': max(var_shares, key=var_shares.get) if var_shares else params.abs().idxmax(),
        'dominant_t': float(tvals[max(var_shares, key=var_shares.get) if var_shares else params.abs().idxmax()]),
    }

# Driver set for regression
# CL, NG → core commodities; CRACK_321 → refining; WTI_BRENT → regional;
# SPX → market; TNX → rates; URA → uranium proxy; KOL → coal proxy; BDRY → shipping
DRIVER_SET = ['CL', 'NG', 'CRACK_321', 'WTI_BRENT', 'SPX', 'TNX', 'URA', 'BDRY']

def run_basket_regressions(baskets_ret, drivers_ret):
    """v4 fix: apply DRIVER_EXCLUSIONS per node (e.g. drop URA from uranium basket)."""
    rows = []
    available = [d for d in DRIVER_SET if d in drivers_ret.columns]
    if len(available) < len(DRIVER_SET):
        print(f"[reg] using available drivers: {available} (missing: {set(DRIVER_SET)-set(available)})")
    for node in baskets_ret.columns:
        excluded = DRIVER_EXCLUSIONS.get(node, [])
        node_drivers = [d for d in available if d not in excluded]
        if excluded:
            print(f"[reg] {node}: excluding {excluded} (circularity)")
        X = drivers_ret[node_drivers]
        y = baskets_ret[node]
        m = regress(y, X)
        s = regression_summary(m)
        row = {'node': node, 'r2_adj': np.nan, 'r2': np.nan, 'n_obs': 0,
               'dominant': None, 'dominant_t': np.nan}
        if s is not None:
            row.update({'r2_adj': s['r2_adj'], 'r2': s['r2'], 'n_obs': s['n_obs'],
                        'dominant': s['dominant'], 'dominant_t': s['dominant_t']})
            for d in available:
                row[f'beta_{d}'] = s['betas'].get(d, np.nan)
                row[f't_{d}'] = s['tvals'].get(d, np.nan)
                row[f'varshare_{d}'] = s.get('var_shares', {}).get(d, np.nan)
        rows.append(row)
    return pd.DataFrame(rows).sort_values('r2_adj', ascending=False, na_position='last')

def run_name_regressions(returns_df, drivers_ret, constituents_df):
    rows = []
    available = [d for d in DRIVER_SET if d in drivers_ret.columns]
    X = drivers_ret[available]
    for _, c in constituents_df.iterrows():
        t = c['ticker']
        if t not in returns_df.columns: continue
        y = returns_df[t]
        m = regress(y, X)
        s = regression_summary(m)
        if s is None: continue
        row = {'ticker': t, 'name': c['name'], 'node': c['node'],
               'r2_adj': s['r2_adj'], 'n_obs': s['n_obs'],
               'dominant': s['dominant'], 'dominant_t': s['dominant_t']}
        for d in available:
            row[f'beta_{d}'] = s['betas'].get(d, np.nan)
            row[f't_{d}'] = s['tvals'].get(d, np.nan)
            row[f'varshare_{d}'] = s.get('var_shares', {}).get(d, np.nan)
        rows.append(row)
    return pd.DataFrame(rows)

# -----------------------------------------------------------------
def compute_representative_tickers(returns_df, baskets_ret, constituents_df):
    """For each basket, find the constituent name with the HIGHEST correlation
    to a leave-one-out basket of the same node. Per Amar's request."""
    reps = []
    for node, group in constituents_df.groupby('node'):
        ts = [t for t in group['ticker'] if t in returns_df.columns]
        if len(ts) < 2: 
            if len(ts) == 1:
                reps.append({'node': node, 'representative': ts[0], 'corr': np.nan, 'note': 'singleton'})
            continue
        best_t, best_corr = None, -np.inf
        for t in ts:
            others = [x for x in ts if x != t]
            loo_basket = returns_df[others].mean(axis=1, skipna=True)
            common = returns_df[t].dropna().index.intersection(loo_basket.dropna().index)
            if len(common) < 100: continue
            corr = returns_df[t].loc[common].corr(loo_basket.loc[common])
            if corr > best_corr:
                best_corr = corr; best_t = t
        reps.append({'node': node, 'representative': best_t, 'corr': best_corr, 'note': 'ok'})
    return pd.DataFrame(reps)

# -----------------------------------------------------------------
# CHARTS
# -----------------------------------------------------------------
def chart_basket_r2(basket_reg, output_dir):
    df = basket_reg.dropna(subset=['r2_adj']).sort_values('r2_adj', ascending=True).copy()
    if len(df) == 0:
        # FIX: emit a placeholder so downstream HTML doesn't break
        fig, ax = plt.subplots(figsize=(11, 4))
        ax.text(0.5, 0.5, 'No basket regressions succeeded.\nCheck driver_cache.parquet for bad columns.',
                ha='center', va='center', fontsize=14, color='#c0392b')
        ax.set_axis_off()
        path = output_dir / 'basket_r2.png'
        plt.savefig(path, dpi=130, bbox_inches='tight')
        plt.close()
        return path
    colors = ['#2ecc71' if r >= 0.5 else '#f39c12' if r >= 0.3 else '#e74c3c' for r in df['r2_adj']]
    fig, ax = plt.subplots(figsize=(11, 9))
    bars = ax.barh(df['node'], df['r2_adj'], color=colors, edgecolor='black', linewidth=0.5)
    for bar, (_, row) in zip(bars, df.iterrows()):
        w = bar.get_width()
        dom = row['dominant'] or '?'
        ax.text(w + 0.005, bar.get_y() + bar.get_height()/2, f"  {w:.2f}  ({dom})", va='center', fontsize=9)
    ax.axvline(0.5, color='#27ae60', linestyle='--', alpha=0.5, label='Well-explained (≥0.5)')
    ax.axvline(0.3, color='#e67e22', linestyle='--', alpha=0.5, label='Moderate (≥0.3)')
    ax.set_xlabel('Adjusted R²')
    ax.set_title(f'How much of each basket\'s variance is explained by drivers?\n(weekly returns, {LOOKBACK_YEARS}y)', fontsize=11)
    # FIX: defensive xlim - handle case where max is NaN/Inf
    xmax = df['r2_adj'].max()
    if pd.isna(xmax) or not np.isfinite(xmax):
        xmax = 0.7
    ax.set_xlim(0, max(xmax * 1.25, 0.7))
    ax.legend(loc='lower right'); ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()
    path = output_dir / 'basket_r2.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

def chart_basket_loadings(basket_reg, output_dir, available_drivers):
    """v4 rebuild: explicit fig sizing, dropna properly, robust annot construction."""
    beta_cols = [f'beta_{d}' for d in available_drivers if f'beta_{d}' in basket_reg.columns]
    drivers = [c.replace('beta_', '') for c in beta_cols]
    if not beta_cols:
        return None
    
    df = basket_reg.set_index('node')[beta_cols].copy()
    df.columns = drivers
    # Drop rows where ALL betas are NaN (failed regressions)
    df = df.dropna(how='all')
    if len(df) == 0:
        return None
    
    # Replace remaining NaN with 0 for color rendering, but track mask
    nan_mask = df.isna()
    df_filled = df.fillna(0)
    
    # Sort by CL beta if available, else by first column
    sort_col = 'CL' if 'CL' in df.columns else drivers[0]
    df_filled = df_filled.sort_values(by=sort_col, ascending=False)
    nan_mask = nan_mask.loc[df_filled.index]
    
    # t-stats for annotation
    t_cols = [f't_{d}' for d in drivers if f't_{d}' in basket_reg.columns]
    t_df = basket_reg.set_index('node')[t_cols].copy()
    t_df.columns = [c.replace('t_', '') for c in t_cols]
    t_df = t_df.reindex(df_filled.index)
    
    # Build annotation as a 2D list (most reliable for seaborn)
    annot_arr = np.empty(df_filled.shape, dtype=object)
    for i, r in enumerate(df_filled.index):
        for j, c in enumerate(df_filled.columns):
            v = df.loc[r, c] if r in df.index else np.nan  # original value, not filled
            t = t_df.loc[r, c] if c in t_df.columns else np.nan
            if pd.isna(v) or nan_mask.loc[r, c]:
                annot_arr[i, j] = ''
            else:
                marker = '**' if pd.notna(t) and abs(t) > 2.5 else '*' if pd.notna(t) and abs(t) > 1.96 else ''
                annot_arr[i, j] = f"{v:+.2f}{marker}"
    
    n_rows, n_cols = df_filled.shape
    # Per-cell sizing: each row 0.5", each col 1.2", min total 8x8
    fig_w = max(10, 1.2 * n_cols + 4)
    fig_h = max(8, 0.5 * n_rows + 2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    
    vmax = float(np.nanmax(np.abs(df.values))) if df.size else 1.0
    if vmax == 0 or not np.isfinite(vmax):
        vmax = 1.0
    
    sns.heatmap(df_filled.astype(float), annot=annot_arr, fmt='', cmap='RdBu_r', center=0,
                vmin=-vmax, vmax=vmax, mask=nan_mask,
                cbar_kws={'label': 'beta (z-scored where applicable)', 'shrink': 0.6},
                linewidths=0.5, linecolor='white',
                annot_kws={'size': 10, 'weight': 'normal'}, ax=ax)
    ax.set_title(f'Basket loadings on drivers\n** = |t|>2.5  * = |t|>1.96  (weekly, {LOOKBACK_YEARS}y)', fontsize=12)
    ax.set_xlabel(''); ax.set_ylabel('')
    plt.xticks(rotation=0, fontsize=10)
    plt.yticks(rotation=0, fontsize=10)
    plt.tight_layout()
    path = output_dir / 'basket_loadings.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

def chart_dominant_driver_distribution(name_reg, output_dir):
    if len(name_reg) == 0: return None
    pivot = name_reg.groupby(['node', 'dominant']).size().unstack(fill_value=0)
    pivot = pivot.div(pivot.sum(axis=1), axis=0)
    fig, ax = plt.subplots(figsize=(11, 9))
    pivot.plot(kind='barh', stacked=True, ax=ax, colormap='tab10', edgecolor='white', linewidth=0.5)
    ax.set_xlabel('Share of constituent names by dominant driver')
    ax.set_xlim(0, 1)
    ax.set_title('Dominant driver distribution within each basket', fontsize=11)
    ax.legend(loc='center left', bbox_to_anchor=(1, 0.5), title='dominant driver')
    ax.grid(axis='x', alpha=0.3); plt.tight_layout()
    path = output_dir / 'dominant_drivers.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

def chart_diagnostic(diag_df, output_dir):
    """v2 addition: shows which baskets had data issues."""
    if len(diag_df) == 0: return None
    fig, ax = plt.subplots(figsize=(11, max(5, 0.4*len(diag_df))))
    df = diag_df.sort_values('n_obs')
    colors = ['#e74c3c' if n < 100 else '#f39c12' if n < 200 else '#2ecc71' for n in df['n_obs']]
    bars = ax.barh(df['node'], df['n_obs'], color=colors, edgecolor='black', linewidth=0.5)
    for bar, (_, row) in zip(bars, df.iterrows()):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height()/2,
                f"  {int(row['n_obs'])}  ({row['n_priced']}/{row['n_in_yaml']} priced)",
                va='center', fontsize=9)
    ax.set_xlabel('Weekly observations available for regression')
    ax.set_title('Data availability diagnostic per basket', fontsize=11)
    ax.grid(axis='x', alpha=0.3); plt.tight_layout()
    path = output_dir / 'data_diagnostic.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

# -----------------------------------------------------------------
# HTML REPORT
# -----------------------------------------------------------------
def build_report(basket_reg, name_reg, reps_df, diag_df, available_drivers, output_dir, n_obs):
    drivers_html = ', '.join(f'<code>{d}</code>' for d in available_drivers)
    
    # Representative ticker table
    rep_rows = []
    for _, r in reps_df.sort_values('node').iterrows():
        c = f"{r['corr']:.3f}" if pd.notna(r['corr']) else 'n/a'
        rep_rows.append(f"<tr><td>{escape(r['node'])}</td><td><b>{escape(str(r['representative']) or ' - ')}</b></td><td class='num'>{c}</td><td>{escape(str(r['note']))}</td></tr>")
    
    # Basket regression table
    bask_rows = []
    for _, r in basket_reg.iterrows():
        r2 = r['r2_adj']
        cls = 'good' if pd.notna(r2) and r2 >= 0.5 else 'ok' if pd.notna(r2) and r2 >= 0.3 else 'weak'
        if pd.isna(r2): cls = 'na'
        beta_cells = []
        for d in available_drivers:
            b = r.get(f'beta_{d}', np.nan); t = r.get(f't_{d}', np.nan)
            if pd.isna(b):
                beta_cells.append("<td class='num'> - </td>")
            else:
                bold = ' style="font-weight:bold"' if pd.notna(t) and abs(t) > 1.96 else ''
                beta_cells.append(f"<td class='num'{bold}>{b:+.3f}</td>")
        r2s = f"{r2:.2f}" if pd.notna(r2) else ' - '
        bask_rows.append(
            f"<tr class='{cls}'><td><b>{escape(r['node'])}</b></td>"
            f"<td class='num'>{r2s}</td><td>{r['dominant'] or ' - '}</td>"
            + ''.join(beta_cells) + "</tr>"
        )
    
    # Top-30 names
    name_rows = []
    if len(name_reg) > 0:
        nr = name_reg.dropna(subset=['r2_adj']).sort_values('r2_adj', ascending=False).head(30)
        for _, r in nr.iterrows():
            beta_cells = []
            for d in available_drivers:
                b = r.get(f'beta_{d}', np.nan); t = r.get(f't_{d}', np.nan)
                if pd.isna(b):
                    beta_cells.append("<td class='num'> - </td>")
                else:
                    bold = ' style="font-weight:bold"' if pd.notna(t) and abs(t) > 1.96 else ''
                    beta_cells.append(f"<td class='num'{bold}>{b:+.3f}</td>")
            name_rows.append(
                f"<tr><td><b>{escape(r['ticker'])}</b></td><td>{escape(str(r['name']))}</td>"
                f"<td>{escape(r['node'])}</td><td class='num'>{r['r2_adj']:.2f}</td>"
                f"<td>{r['dominant']}</td>" + ''.join(beta_cells) + "</tr>"
            )
    
    # Diagnostic table
    diag_rows = []
    for _, r in diag_df.sort_values('n_obs').iterrows():
        cls = 'weak' if r['n_obs'] < 100 else 'ok' if r['n_obs'] < 200 else 'good'
        diag_rows.append(f"<tr class='{cls}'><td>{escape(r['node'])}</td><td class='num'>{int(r['n_in_yaml'])}</td><td class='num'>{int(r['n_priced'])}</td><td class='num'>{int(r['n_obs'])}</td><td>{escape(str(r['note']))}</td></tr>")
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Driver Analysis v2</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1400px; margin: 30px auto; padding: 20px; color: #222; }}
h1 {{ border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }}
h2 {{ margin-top: 35px; color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
h3 {{ color: #555; margin-top: 25px; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12px; }}
th {{ background: #2c3e50; color: white; padding: 7px; text-align: left; }}
td {{ padding: 5px 7px; border-bottom: 1px solid #eee; }}
tr.good {{ background: #eafaf1; }} tr.ok {{ background: #fef5e7; }} tr.weak {{ background: #fdedec; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; margin: 10px 0; }}
.note {{ background: #fffbe6; padding: 10px; border-left: 4px solid #f1c40f; margin: 12px 0; font-size: 13px; }}
.alert {{ background: #fee; padding: 10px; border-left: 4px solid #e74c3c; margin: 12px 0; font-size: 13px; }}
code {{ background: #f4f4f4; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}
</style></head><body>
<h1>IMA Energy Dashboard - Phase 2 Driver Decomposition (v2)</h1>
<p style="color:#666;">Weekly returns · {LOOKBACK_YEARS}y · ~{n_obs} obs · OLS w/ Newey-West HAC SE</p>
<p style="color:#666;">Available drivers: {drivers_html}</p>

<div class="note">
  <b>v2 fixes:</b> heatmap rendering bug (was rendering blank), upstream_gas_eandp NaN bug (recent IPOs propagating NaNs), price cache stale detection, added URA/KOL/BDRY ETF proxies for uranium/coal/shipping.
</div>

<h2>Data availability diagnostic</h2>
<p style="font-size:13px; color:#555;">Before reading the regressions, verify each basket has enough clean data. Recent IPOs and delistings reduce usable observations.</p>
<img src="data_diagnostic.png" alt="Data diagnostic">
<table>
<tr><th>Node</th><th>names in YAML</th><th>names priced</th><th>weekly obs</th><th>note</th></tr>
{''.join(diag_rows)}
</table>

<h2>Representative tickers (per basket)</h2>
<p style="font-size:13px; color:#555;">For each basket, the constituent name with the highest correlation to a leave-one-out basket of the same node. Use this as the "most aligned name" for hover preview on the dashboard.</p>
<table>
<tr><th>Node</th><th>Representative ticker</th><th>Corr to LOO basket</th><th>Note</th></tr>
{''.join(rep_rows)}
</table>

<h2>Basket-level driver decomposition</h2>
<img src="basket_r2.png" alt="Basket R²">
<img src="basket_loadings.png" alt="Basket loadings">

<div class="alert"><b>Driver gap notice:</b> baskets like LNG terminals, tanker shipping, and petrochem appear "SPX-dominant" because the right specialty drivers (JKM-HH spread, Worldscale rates, ethylene margin) aren't in this regression. The loadings on commodity factors here are real but incomplete. To capture those baskets properly will require FactSet specialty data.</div>

<h3>Full basket regression table</h3>
<table>
<tr><th>Basket</th><th>R² adj</th><th>Dominant</th>{''.join(f'<th>β {d}</th>' for d in available_drivers)}</tr>
{''.join(bask_rows)}
</table>

<h2>Per-name dominant driver distribution</h2>
<img src="dominant_drivers.png" alt="Dominant drivers per basket">

<h2>Top 30 most-explained names</h2>
<table>
<tr><th>Ticker</th><th>Name</th><th>Basket</th><th>R² adj</th><th>Dominant</th>{''.join(f'<th>β {d}</th>' for d in available_drivers)}</tr>
{''.join(name_rows)}
</table>

</body></html>"""
    
    path = output_dir / 'report.html'
    with open(path, 'w') as f:
        f.write(html)
    return path

# -----------------------------------------------------------------
def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    tax = load_taxonomy()
    consts = get_constituents(tax)
    print(f"[taxonomy] {len(consts)} constituents in {consts['node'].nunique()} nodes")
    
    tickers = consts['ticker'].unique().tolist()
    prices = fetch_prices(tickers)
    
    drivers = fetch_drivers()
    print(f"[drivers] available: {list(drivers.columns)}")
    
    name_returns_w = to_returns_names(prices)
    driver_returns_w = to_returns(drivers)
    
    common_idx = name_returns_w.index.intersection(driver_returns_w.index)
    name_returns_w = name_returns_w.loc[common_idx]
    driver_returns_w = driver_returns_w.loc[common_idx]
    print(f"[align] weekly obs: {len(common_idx)}")
    
    baskets_w, diag_df = basket_returns(name_returns_w, consts)
    print(f"[baskets] {baskets_w.shape[1]} baskets computed")
    if (diag_df['note'] != 'ok').any():
        print("[diag] basket issues:")
        print(diag_df[diag_df['note'] != 'ok'].to_string(index=False))
    diag_df.to_csv(OUTPUT_DIR / 'basket_diagnostic.csv', index=False)
    
    # Representative tickers
    print("\n[rep] computing representative tickers (highest LOO corr)...")
    reps_df = compute_representative_tickers(name_returns_w, baskets_w, consts)
    reps_df.to_csv(OUTPUT_DIR / 'representative_tickers.csv', index=False)
    print(reps_df.to_string(index=False))
    
    # Regressions
    print("\n[reg] basket-level...")
    basket_reg = run_basket_regressions(baskets_w, driver_returns_w)
    basket_reg.to_csv(OUTPUT_DIR / 'basket_loadings.csv', index=False)
    print(basket_reg[['node','r2_adj','n_obs','dominant']].to_string(index=False))
    
    print("\n[reg] per-name...")
    name_reg = run_name_regressions(name_returns_w, driver_returns_w, consts)
    name_reg.to_csv(OUTPUT_DIR / 'name_loadings.csv', index=False)
    print(f"[reg] {len(name_reg)} names regressed")
    
    available_drivers = [d for d in DRIVER_SET if d in driver_returns_w.columns]
    
    print("\n[charts] generating...")
    chart_basket_r2(basket_reg, OUTPUT_DIR)
    chart_basket_loadings(basket_reg, OUTPUT_DIR, available_drivers)
    chart_dominant_driver_distribution(name_reg, OUTPUT_DIR)
    chart_diagnostic(diag_df, OUTPUT_DIR)
    
    print("[html] building report...")
    report_path = build_report(basket_reg, name_reg, reps_df, diag_df, available_drivers, OUTPUT_DIR, len(common_idx))
    
    print(f"\n[done] outputs in: {OUTPUT_DIR.resolve()}")
    print(f"       open {report_path}")

if __name__ == '__main__':
    main()
