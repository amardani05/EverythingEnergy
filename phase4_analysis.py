"""
IMA Energy Dashboard — Phase 4: Performance Attribution & Implied vs Realized
=============================================================================

Builds two analyses on top of Phase 2's loadings:

1. PERFORMANCE ATTRIBUTION
   For each basket and each name, decompose recent (1m, 3m, YTD) returns into:
   - Factor return:    sum over drivers of (basket_beta_d × driver_return_d)
   - Idiosyncratic:    actual - factor return
   This is *the* number a sector PM looks at daily. "Is XOM up because oil is up,
   or because XOM is doing something independent of oil?"

2. IMPLIED VS REALIZED
   Given the factor moves over a recent window, compute the "model-implied" basket
   return and compare to actual. A persistent gap (positive or negative) means
   positioning has shifted in a way the factors don't explain. Useful for:
   - Detecting AI rerating (IPPs realized > implied for months)
   - Catching rotation (sector rotates out, realized < implied)
   - Identifying mispricing (basket cheap/rich vs its own factor model)

OUTPUTS:
  - phase4_results/attribution_baskets.csv   (per basket)
  - phase4_results/attribution_names.csv     (per name)
  - phase4_results/attribution_chart.png     (factor vs idio bars)
  - phase4_results/implied_vs_realized.csv   (per basket, multiple windows)
  - phase4_results/implied_vs_realized_chart.png
  - phase4_results/report.html

DESIGN NOTES (overfitting defenses)
-----------------------------------
- Uses BETAS FROM PHASE 2 (already estimated on weekly data, full 5y window)
- Applies them to a FORWARD-LOOKING window (last N weeks) — strict out-of-sample
  in the temporal sense (the betas were fit on data that includes this period
  but the *prediction* uses driver returns realized in the period)
- We're not refitting per window, so no parameter snooping
- Results are descriptive, not predictive: "this is how much of recent
  performance is consistent with the basket's historical driver sensitivity"
- Idiosyncratic returns ≠ alpha (in finance sense) — they could be unpriced
  factors, news, mispricing, or noise. The screen flags candidates for analysis.
"""

import yaml
import pandas as pd
import numpy as np
import warnings
import matplotlib.pyplot as plt
from pathlib import Path
from html import escape

warnings.filterwarnings('ignore')

YAML_PATH = 'energy_taxonomy.yaml'
PRICE_CACHE = 'price_cache.parquet'
DRIVER_CACHE = 'driver_cache.parquet'
BASKET_LOADINGS = 'basket_loadings.csv'
NAME_LOADINGS = 'name_loadings.csv'
OUTPUT_DIR = Path('phase4_results')

# Windows to attribute over (in weeks)
WINDOWS = {
    '1m': 4,
    '3m': 13,
    'ytd': None,  # special-cased to use start-of-year
    '1y': 52,
}

# Driver list — same as Phase 2
DRIVER_SET = ['CL', 'NG', 'CRACK_321', 'WTI_BRENT', 'SPX', 'TNX', 'URA', 'BDRY']
ALL_DRIVER_KINDS = {
    'CL': 'price', 'NG': 'price', 'BRENT': 'price', 'RB': 'price', 'HO': 'price',
    'SPX': 'price', 'XLE': 'price', 'XLU': 'price', 'URA': 'price', 'BDRY': 'price',
    'XOP': 'price', 'CORN': 'price', 'DXY': 'price',
    'CRACK_321': 'spread', 'WTI_BRENT': 'spread', 'TNX': 'rate',
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

def winsorize(s, n_std=3.0):
    if s.isna().all() or s.std() == 0: return s
    mu, sd = s.mean(), s.std()
    return s.clip(lower=mu - n_std*sd, upper=mu + n_std*sd)

def to_returns(prices_df, freq='W-FRI'):
    """Weekly returns. Spreads/rates use diffs (z-scored), prices use log returns."""
    weekly = prices_df.resample(freq).last()
    out = pd.DataFrame(index=weekly.index)
    diff_cols = []
    for c in weekly.columns:
        kind = ALL_DRIVER_KINDS.get(c, 'price')
        if kind in ('spread', 'rate'):
            out[c] = weekly[c].diff()
            diff_cols.append(c)
        else:
            out[c] = np.log(weekly[c] / weekly[c].shift(1))
    out = out.replace([np.inf, -np.inf], np.nan)
    # z-score the diff cols (matching Phase 2's preprocessing)
    for c in diff_cols:
        if out[c].std() > 0:
            out[c] = (out[c] - out[c].mean()) / out[c].std()
    return out.dropna(how='all')

def to_returns_names(prices_df, freq='W-FRI'):
    """Name returns: weekly log returns of weekly-last prices."""
    weekly = prices_df.resample(freq).last()
    return np.log(weekly / weekly.shift(1)).replace([np.inf, -np.inf], np.nan).dropna(how='all')


# -----------------------------------------------------------------
# Performance Attribution
# -----------------------------------------------------------------
def compute_basket_attribution(basket_returns_w, drivers_w, basket_loadings_df, windows=WINDOWS):
    """For each basket × window, return: actual, factor (implied), idio (residual).
    
    Factor return = sum over d of (β_d × driver_return_d)
    Idio = actual - factor
    """
    rows = []
    for _, brow in basket_loadings_df.iterrows():
        node = brow['node']
        if node not in basket_returns_w.columns:
            continue
        # Pull betas for this basket
        betas = {d: brow.get(f'beta_{d}', 0) for d in DRIVER_SET}
        betas = {d: 0 if (b is None or pd.isna(b) or np.isinf(b)) else b for d, b in betas.items()}
        
        for win_name, n_weeks in windows.items():
            # Determine the window
            basket_series = basket_returns_w[node].dropna()
            if len(basket_series) == 0:
                continue
            
            if win_name == 'ytd':
                # Year-to-date: from first week of current year
                latest = basket_series.index[-1]
                year_start = pd.Timestamp(year=latest.year, month=1, day=1)
                window_idx = basket_series.index >= year_start
                win_returns = basket_series[window_idx]
            else:
                win_returns = basket_series.iloc[-n_weeks:] if len(basket_series) >= n_weeks else basket_series
            
            if len(win_returns) == 0:
                continue
            
            # Compute factor return: cumulative β·x over the window
            factor_total = 0
            factor_breakdown = {}
            for d in DRIVER_SET:
                if d in drivers_w.columns:
                    # Align driver returns to the same window
                    d_win = drivers_w[d].reindex(win_returns.index).fillna(0)
                    contrib = betas[d] * d_win.sum()  # β × cumulative driver return
                    factor_breakdown[d] = contrib
                    factor_total += contrib
            
            actual = win_returns.sum()
            idio = actual - factor_total
            
            row = {
                'node': node,
                'window': win_name,
                'n_weeks': len(win_returns),
                'actual': actual,
                'factor': factor_total,
                'idio': idio,
                'idio_share': idio / actual if abs(actual) > 1e-6 else np.nan,
            }
            for d, c in factor_breakdown.items():
                row[f'contrib_{d}'] = c
            rows.append(row)
    return pd.DataFrame(rows)


def compute_name_attribution(name_returns_w, drivers_w, name_loadings_df, windows=WINDOWS):
    """Per-name attribution. Same logic as basket but using name-level betas."""
    rows = []
    for _, nrow in name_loadings_df.iterrows():
        ticker = nrow['ticker']
        if ticker not in name_returns_w.columns:
            continue
        betas = {d: nrow.get(f'beta_{d}', 0) for d in DRIVER_SET}
        betas = {d: 0 if (b is None or pd.isna(b) or np.isinf(b)) else b for d, b in betas.items()}
        
        for win_name, n_weeks in windows.items():
            name_series = name_returns_w[ticker].dropna()
            if len(name_series) == 0:
                continue
            if win_name == 'ytd':
                latest = name_series.index[-1]
                year_start = pd.Timestamp(year=latest.year, month=1, day=1)
                win_returns = name_series[name_series.index >= year_start]
            else:
                win_returns = name_series.iloc[-n_weeks:] if len(name_series) >= n_weeks else name_series
            if len(win_returns) == 0:
                continue
            
            factor_total = 0
            for d in DRIVER_SET:
                if d in drivers_w.columns:
                    d_win = drivers_w[d].reindex(win_returns.index).fillna(0)
                    factor_total += betas[d] * d_win.sum()
            
            actual = win_returns.sum()
            idio = actual - factor_total
            
            rows.append({
                'ticker': ticker,
                'name': nrow.get('name', ''),
                'node': nrow.get('node', ''),
                'window': win_name,
                'n_weeks': len(win_returns),
                'actual': actual,
                'factor': factor_total,
                'idio': idio,
                'idio_share': idio / actual if abs(actual) > 1e-6 else np.nan,
            })
    return pd.DataFrame(rows)


# -----------------------------------------------------------------
# Implied vs Realized
# -----------------------------------------------------------------
def compute_implied_vs_realized(basket_returns_w, drivers_w, basket_loadings_df, n_windows=12):
    """Rolling 4-week implied vs realized for each basket. Returns time-series
    of (date, implied, realized, gap) per node."""
    out = {}
    for _, brow in basket_loadings_df.iterrows():
        node = brow['node']
        if node not in basket_returns_w.columns:
            continue
        betas = {d: brow.get(f'beta_{d}', 0) for d in DRIVER_SET}
        betas = {d: 0 if (b is None or pd.isna(b) or np.isinf(b)) else b for d, b in betas.items()}
        
        actual = basket_returns_w[node]
        # Implied = sum of beta * driver each week
        implied = pd.Series(0.0, index=actual.index)
        for d in DRIVER_SET:
            if d in drivers_w.columns:
                implied = implied + betas[d] * drivers_w[d].reindex(actual.index).fillna(0)
        
        # Cumulative gap (4-week rolling)
        gap = actual - implied
        rolling_actual = actual.rolling(4).sum()
        rolling_implied = implied.rolling(4).sum()
        rolling_gap = gap.rolling(4).sum()
        
        out[node] = pd.DataFrame({
            'actual_4w': rolling_actual,
            'implied_4w': rolling_implied,
            'gap_4w': rolling_gap,
        }).dropna()
    return out


# -----------------------------------------------------------------
# Charts
# -----------------------------------------------------------------
def chart_attribution(attr_df, output_dir, window='3m'):
    """Stacked bar: per basket, factor vs idio over the chosen window."""
    df = attr_df[attr_df['window'] == window].copy()
    if len(df) == 0:
        return None
    df = df.sort_values('actual', ascending=True)
    
    fig, ax = plt.subplots(figsize=(13, max(7, 0.4 * len(df))))
    
    # Two-bar layout: factor and idio side-by-side
    y_pos = np.arange(len(df))
    factor_color = ['#5db075' if v >= 0 else '#c4544a' for v in df['factor']]
    idio_color = ['#d4af37' if v >= 0 else '#a06030' for v in df['idio']]
    
    h = 0.4
    ax.barh(y_pos - h/2, df['factor'].values, h, color=factor_color, edgecolor='black', linewidth=0.4, label='Factor (driver-explained)')
    ax.barh(y_pos + h/2, df['idio'].values, h, color=idio_color, edgecolor='black', linewidth=0.4, label='Idiosyncratic (residual)')
    
    # Annotations
    for i, (_, row) in enumerate(df.iterrows()):
        # Total return annotation at right
        actual_pct = row['actual'] * 100
        annotation_x = max(row['factor'], row['idio'], 0) + 0.005
        ax.text(annotation_x, i, f"  Actual: {actual_pct:+.1f}%",
                va='center', fontsize=9, color='#333')
    
    ax.set_yticks(y_pos)
    ax.set_yticklabels(df['node'])
    ax.axvline(0, color='black', linewidth=0.6)
    ax.set_xlabel(f'Cumulative return over last {window}')
    ax.set_title(f'Performance attribution by basket — {window}\n'
                 f'GREEN/RED = factor return (sum of β × driver moves);  GOLD = idiosyncratic (residual)',
                 fontsize=11)
    ax.legend(loc='lower right')
    ax.grid(axis='x', alpha=0.3)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x*100:.0f}%'))
    plt.tight_layout()
    path = output_dir / f'attribution_chart_{window}.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path


def chart_implied_vs_realized(ivr_data, output_dir, top_n=10):
    """Show baskets with the largest current 4-week gap (implied vs realized)."""
    # Compute current gap per basket
    rows = []
    for node, df in ivr_data.items():
        if len(df) == 0:
            continue
        latest = df.iloc[-1]
        rows.append({
            'node': node,
            'actual_4w': latest['actual_4w'],
            'implied_4w': latest['implied_4w'],
            'gap_4w': latest['gap_4w'],
        })
    snap_df = pd.DataFrame(rows).sort_values('gap_4w', key=abs, ascending=False)
    
    if len(snap_df) == 0:
        return None, None
    
    fig, ax = plt.subplots(figsize=(13, max(7, 0.4 * len(snap_df))))
    df = snap_df.sort_values('gap_4w')
    y_pos = np.arange(len(df))
    h = 0.4
    ax.barh(y_pos - h/2, df['implied_4w'].values, h,
            color='#5a8fb8', edgecolor='black', linewidth=0.4, label='Implied (factor model)')
    ax.barh(y_pos + h/2, df['actual_4w'].values, h,
            color='#d4af37', edgecolor='black', linewidth=0.4, label='Realized (actual)')
    
    for i, (_, row) in enumerate(df.iterrows()):
        gap = row['gap_4w']
        gap_color = '#5db075' if gap > 0 else '#c4544a'
        ax.text(max(row['actual_4w'], row['implied_4w'], 0) + 0.005, i,
                f"  Gap: {gap*100:+.1f}%",
                va='center', fontsize=9, color=gap_color, fontweight='bold')
    
    ax.set_yticks(y_pos)
    ax.set_yticklabels(df['node'])
    ax.axvline(0, color='black', linewidth=0.6)
    ax.set_xlabel('Cumulative return — last 4 weeks')
    ax.set_title('Implied vs realized — last 4 weeks\n'
                 'Positive gap (green) = realized > implied (basket outperforming model). Negative = under.',
                 fontsize=11)
    ax.legend(loc='lower right')
    ax.grid(axis='x', alpha=0.3)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x*100:.0f}%'))
    plt.tight_layout()
    path = output_dir / 'implied_vs_realized_snap.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    
    return snap_df, path


def chart_ivr_timeseries(ivr_data, output_dir, top_n=6):
    """For top N baskets by current absolute gap, plot the gap over time."""
    rows = []
    for node, df in ivr_data.items():
        if len(df) == 0: continue
        rows.append((node, abs(df['gap_4w'].iloc[-1])))
    rows.sort(key=lambda x: x[1], reverse=True)
    top_nodes = [r[0] for r in rows[:top_n]]
    
    if not top_nodes:
        return None
    
    fig, ax = plt.subplots(figsize=(14, 7))
    cmap = plt.colormaps['tab10']
    for i, node in enumerate(top_nodes):
        df = ivr_data[node]
        ax.plot(df.index, df['gap_4w'] * 100, label=node,
                color=cmap(i / max(top_n - 1, 1)), linewidth=1.4, alpha=0.85)
    ax.axhline(0, color='black', linewidth=0.6)
    ax.set_ylabel('Realized minus implied — 4w cumulative (%)')
    ax.set_title(f'Implied vs realized gap over time — top {top_n} baskets by current |gap|\n'
                 'Persistent positive (negative) gap = sustained outperformance (under) of factor model',
                 fontsize=11)
    ax.legend(loc='center left', bbox_to_anchor=(1, 0.5), fontsize=9)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    path = output_dir / 'ivr_timeseries.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path


# -----------------------------------------------------------------
# HTML report
# -----------------------------------------------------------------
def build_report(attr_df, name_attr_df, ivr_snap, output_dir):
    # Top idio movers (3m, both directions)
    df_3m = attr_df[attr_df['window'] == '3m'].copy() if len(attr_df) else pd.DataFrame()
    
    pos_idio = df_3m.nlargest(8, 'idio') if len(df_3m) else pd.DataFrame()
    neg_idio = df_3m.nsmallest(8, 'idio') if len(df_3m) else pd.DataFrame()
    
    def attr_row(r):
        return (
            f"<tr><td><b>{escape(r['node'])}</b></td>"
            f"<td class='num'>{r['actual']*100:+.1f}%</td>"
            f"<td class='num'>{r['factor']*100:+.1f}%</td>"
            f"<td class='num' style='color:{'#5db075' if r['idio']>0 else '#c4544a'}'>{r['idio']*100:+.1f}%</td>"
            f"</tr>"
        )
    
    pos_rows = ''.join(attr_row(r) for _, r in pos_idio.iterrows())
    neg_rows = ''.join(attr_row(r) for _, r in neg_idio.iterrows())
    
    # IVR table
    ivr_rows = ''
    if ivr_snap is not None and len(ivr_snap):
        for _, r in ivr_snap.iterrows():
            gap = r['gap_4w']
            color = '#5db075' if gap > 0 else '#c4544a'
            ivr_rows += (
                f"<tr><td><b>{escape(r['node'])}</b></td>"
                f"<td class='num'>{r['actual_4w']*100:+.1f}%</td>"
                f"<td class='num'>{r['implied_4w']*100:+.1f}%</td>"
                f"<td class='num' style='color:{color}; font-weight:bold'>{gap*100:+.1f}%</td>"
                f"</tr>"
            )
    
    # Top idio names (3m)
    name_3m = name_attr_df[name_attr_df['window'] == '3m'].copy() if len(name_attr_df) else pd.DataFrame()
    name_top = name_3m.nlargest(15, 'idio').to_dict('records') if len(name_3m) else []
    name_bot = name_3m.nsmallest(15, 'idio').to_dict('records') if len(name_3m) else []
    
    def name_row(r):
        return (
            f"<tr><td class='mono'>{escape(r['ticker'])}</td>"
            f"<td>{escape(str(r.get('name','')))}</td>"
            f"<td>{escape(r.get('node',''))}</td>"
            f"<td class='num'>{r['actual']*100:+.1f}%</td>"
            f"<td class='num'>{r['factor']*100:+.1f}%</td>"
            f"<td class='num' style='color:{'#5db075' if r['idio']>0 else '#c4544a'}; font-weight:bold'>{r['idio']*100:+.1f}%</td>"
            f"</tr>"
        )
    
    name_top_rows = ''.join(name_row(r) for r in name_top)
    name_bot_rows = ''.join(name_row(r) for r in name_bot)
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Phase 4 — Attribution & IVR</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1400px; margin: 30px auto; padding: 20px; color: #222; }}
h1 {{ border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }}
h2 {{ margin-top: 35px; color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
h3 {{ color: #555; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12px; }}
th {{ background: #2c3e50; color: white; padding: 7px; text-align: left; }}
td {{ padding: 5px 7px; border-bottom: 1px solid #eee; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.mono {{ font-family: ui-monospace, 'SF Mono', monospace; }}
img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; margin: 10px 0; }}
.note {{ background: #fffbe6; padding: 10px; border-left: 4px solid #f1c40f; margin: 12px 0; font-size: 13px; }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
</style></head><body>

<h1>Phase 4 — Performance Attribution &amp; Implied vs Realized</h1>
<p style="color:#666;">Decomposes recent returns into factor (driver-explained) and idiosyncratic (residual).</p>

<div class="note">
<b>How to read attribution:</b> for each basket, ACTUAL return = FACTOR return + IDIOSYNCRATIC return.
Factor return is what the basket's historical betas would predict given the actual driver moves over the window.
Idiosyncratic is everything else — could be unmodeled factors, news, mispricing, or noise.
A large positive idio means the basket outperformed what its driver model predicts; large negative means underperformed.
This is the lens for "what's actually moving here that I should investigate."
</div>

<h2>1. Basket attribution — 3 month</h2>
<img src="attribution_chart_3m.png" alt="Attribution 3m">

<div class="cols">
<div>
<h3>Top idio outperformers (3m)</h3>
<table>
<tr><th>Basket</th><th>Actual</th><th>Factor</th><th>Idio</th></tr>
{pos_rows or '<tr><td colspan=4>no data</td></tr>'}
</table>
</div>
<div>
<h3>Top idio underperformers (3m)</h3>
<table>
<tr><th>Basket</th><th>Actual</th><th>Factor</th><th>Idio</th></tr>
{neg_rows or '<tr><td colspan=4>no data</td></tr>'}
</table>
</div>
</div>

<h2>2. Implied vs realized — last 4 weeks</h2>
<img src="implied_vs_realized_snap.png" alt="IVR snap">
<table>
<tr><th>Basket</th><th>Realized 4w</th><th>Implied 4w</th><th>Gap</th></tr>
{ivr_rows or '<tr><td colspan=4>no data</td></tr>'}
</table>

<h3>Gap over time — top 6 baskets by current |gap|</h3>
<img src="ivr_timeseries.png" alt="IVR timeseries">

<h2>3. Top idiosyncratic name movers (3m)</h2>

<div class="cols">
<div>
<h3>↑ Outperformed factor model</h3>
<table>
<tr><th>Ticker</th><th>Name</th><th>Node</th><th>Actual</th><th>Factor</th><th>Idio</th></tr>
{name_top_rows or '<tr><td colspan=6>no data</td></tr>'}
</table>
</div>
<div>
<h3>↓ Underperformed factor model</h3>
<table>
<tr><th>Ticker</th><th>Name</th><th>Node</th><th>Actual</th><th>Factor</th><th>Idio</th></tr>
{name_bot_rows or '<tr><td colspan=6>no data</td></tr>'}
</table>
</div>
</div>

</body></html>
"""
    
    path = output_dir / 'report.html'
    open(path, 'w').write(html)
    return path


# -----------------------------------------------------------------
def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    # Load taxonomy + caches
    tax = load_taxonomy()
    consts = get_constituents(tax)
    print(f"[taxonomy] {len(consts)} constituents in {consts['node'].nunique()} nodes")
    
    if not Path(PRICE_CACHE).exists():
        raise FileNotFoundError(f"missing {PRICE_CACHE}; run driver_analysis_v5 first")
    if not Path(DRIVER_CACHE).exists():
        raise FileNotFoundError(f"missing {DRIVER_CACHE}; run driver_analysis_v5 first")
    if not Path(BASKET_LOADINGS).exists():
        # try drivers_results subfolder
        alt = Path('drivers_results') / 'basket_loadings.csv'
        if alt.exists():
            globals()['BASKET_LOADINGS'] = str(alt)
        else:
            raise FileNotFoundError(f"missing {BASKET_LOADINGS}; run driver_analysis_v5 first")
    
    prices = pd.read_parquet(PRICE_CACHE)
    drivers = pd.read_parquet(DRIVER_CACHE)
    basket_loadings_df = pd.read_csv(BASKET_LOADINGS)
    
    name_loadings_df = pd.DataFrame()
    nl_path = Path(NAME_LOADINGS) if Path(NAME_LOADINGS).exists() else Path('drivers_results') / 'name_loadings.csv'
    if nl_path.exists():
        name_loadings_df = pd.read_csv(nl_path)
    
    print(f"[caches] prices {prices.shape}, drivers {drivers.shape}")
    print(f"[loadings] basket {basket_loadings_df.shape}, name {name_loadings_df.shape}")
    
    # Compute weekly returns
    name_returns_w = to_returns_names(prices)
    driver_returns_w = to_returns(drivers)
    
    common_idx = name_returns_w.index.intersection(driver_returns_w.index)
    name_returns_w = name_returns_w.loc[common_idx]
    driver_returns_w = driver_returns_w.loc[common_idx]
    print(f"[align] {len(common_idx)} weekly obs")
    
    # Build basket weekly returns (winsorize then EW)
    baskets_w = {}
    for node, group in consts.groupby('node'):
        ts = [t for t in group['ticker'] if t in name_returns_w.columns]
        if len(ts) < 2: continue
        sub = name_returns_w[ts].replace([np.inf, -np.inf], np.nan).apply(winsorize, axis=0)
        b = sub.mean(axis=1, skipna=True).replace([np.inf, -np.inf], np.nan).dropna()
        if len(b) >= 100:
            baskets_w[node] = b
    baskets_w_df = pd.DataFrame(baskets_w)
    print(f"[baskets] {baskets_w_df.shape}")
    
    # ---- Phase 4.1: Performance Attribution ----
    print("\n[1/2] Performance attribution...")
    basket_attr = compute_basket_attribution(baskets_w_df, driver_returns_w, basket_loadings_df)
    basket_attr.to_csv(OUTPUT_DIR / 'attribution_baskets.csv', index=False)
    print(f"  {basket_attr['window'].nunique()} windows × {basket_attr['node'].nunique()} baskets = {len(basket_attr)} rows")
    
    if not name_loadings_df.empty:
        name_attr = compute_name_attribution(name_returns_w, driver_returns_w, name_loadings_df)
        name_attr.to_csv(OUTPUT_DIR / 'attribution_names.csv', index=False)
        print(f"  per-name: {len(name_attr)} rows")
    else:
        name_attr = pd.DataFrame()
    
    # ---- Phase 4.2: Implied vs Realized ----
    print("\n[2/2] Implied vs realized...")
    ivr_data = compute_implied_vs_realized(baskets_w_df, driver_returns_w, basket_loadings_df)
    print(f"  {len(ivr_data)} baskets")
    
    # Charts
    print("\n[charts]")
    for w in ['1m', '3m', 'ytd']:
        chart_attribution(basket_attr, OUTPUT_DIR, window=w)
    snap_df, snap_path = chart_implied_vs_realized(ivr_data, OUTPUT_DIR)
    chart_ivr_timeseries(ivr_data, OUTPUT_DIR)
    
    # IVR snapshot to CSV
    if snap_df is not None:
        snap_df.to_csv(OUTPUT_DIR / 'ivr_snapshot.csv', index=False)
    
    # Per-basket IVR timeseries to one CSV (for UI consumption)
    ivr_long = []
    for node, df in ivr_data.items():
        for date, row in df.iterrows():
            ivr_long.append({
                'node': node,
                'date': date.strftime('%Y-%m-%d'),
                'actual_4w': float(row['actual_4w']),
                'implied_4w': float(row['implied_4w']),
                'gap_4w': float(row['gap_4w']),
            })
    pd.DataFrame(ivr_long).to_csv(OUTPUT_DIR / 'ivr_timeseries.csv', index=False)
    
    # HTML
    print("[html]")
    build_report(basket_attr, name_attr, snap_df, OUTPUT_DIR)
    
    print(f"\n[done] outputs in: {OUTPUT_DIR.resolve()}")
    print(f"       open {OUTPUT_DIR / 'report.html'}")
    
    # Summary
    print("\n=== TOP IDIO MOVERS (3m) ===")
    if len(basket_attr):
        df_3m = basket_attr[basket_attr['window'] == '3m']
        print("Outperformers:")
        for _, r in df_3m.nlargest(5, 'idio').iterrows():
            print(f"  {r['node']:35} actual {r['actual']*100:+.1f}%  factor {r['factor']*100:+.1f}%  idio {r['idio']*100:+.1f}%")
        print("Underperformers:")
        for _, r in df_3m.nsmallest(5, 'idio').iterrows():
            print(f"  {r['node']:35} actual {r['actual']*100:+.1f}%  factor {r['factor']*100:+.1f}%  idio {r['idio']*100:+.1f}%")


if __name__ == '__main__':
    main()
