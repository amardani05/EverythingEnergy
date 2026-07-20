"""
IMA Energy Dashboard - Phase 1: Basket Correlation Sanity Check (v2)
======================================================================

Tests whether the taxonomy clusters correctly. Outputs go to ./basket_results/

CHANGES vs v1
-------------
- LEAVE-ONE-OUT BASKETS: when correlating a name against its home basket,
  we exclude that name from the basket. v1 had self-correlation bug that
  produced NaN home_corr for several gas E&P names.
- Outputs to ./basket_results/ folder
- PNG charts: intra-basket bar, cross-basket heatmap, basket scatter
- HTML report combining everything (open in browser)
- Better terminal summary with red/yellow/green calibration

USAGE
-----
1. Place alongside energy_taxonomy_v0.3.yaml
2. pip install yfinance pandas numpy pyyaml pyarrow matplotlib seaborn
3. python basket_check.py
4. Open basket_results/report.html
"""

import yaml
import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from html import escape

warnings.filterwarnings('ignore', category=RuntimeWarning)
warnings.filterwarnings('ignore', category=FutureWarning)

# -----------------------------------------------------------------
YAML_PATH = 'energy_taxonomy.yaml'
LOOKBACK_YEARS = 5
CACHE_PATH = 'price_cache.parquet'
OUTPUT_DIR = Path('basket_results')

# Calibration thresholds for intra-basket cohesion
TIER_GOOD = 0.55
TIER_OK = 0.40

# -----------------------------------------------------------------
def load_taxonomy(path=YAML_PATH):
    with open(path) as f:
        return yaml.safe_load(f)

def get_all_constituents(tax):
    rows = []
    for node_key, node in tax.items():
        if not isinstance(node, dict) or 'constituents' not in node:
            continue
        for c in node['constituents']:
            rows.append({
                'ticker': c['ticker'],
                'node': node_key,
                'mc_bucket': c.get('mc', 'unknown'),
                'name': c.get('name', ''),
                'index': c.get('index', 'unknown'),
                'tax_form': c.get('tax_form', '1099'),
            })
    return pd.DataFrame(rows)

# -----------------------------------------------------------------
def fetch_prices(tickers, years=LOOKBACK_YEARS, force_refresh=False):
    if not force_refresh and Path(CACHE_PATH).exists():
        cached = pd.read_parquet(CACHE_PATH)
        missing = set(tickers) - set(cached.columns)
        if not missing:
            print(f"[cache] hit - {len(tickers)} tickers")
            return cached[tickers]
        print(f"[cache] missing {len(missing)} tickers, refetching all")

    print(f"[yf] fetching {years}y daily prices for {len(tickers)} tickers...")
    data = yf.download(
        tickers, period=f"{years}y", interval='1d',
        auto_adjust=True, progress=True, group_by='ticker', threads=True,
    )
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
    closes_df.to_parquet(CACHE_PATH)
    print(f"[yf] got {len(closes_df.columns)} of {len(tickers)} tickers")
    return closes_df

# -----------------------------------------------------------------
def compute_returns(prices):
    return np.log(prices / prices.shift(1)).dropna(how='all')

def basket_returns_ew(returns, constituents_df, exclude_ticker=None):
    """Equal-weighted basket returns. Optionally exclude a ticker (for LOO)."""
    baskets = {}
    for node, group in constituents_df.groupby('node'):
        tickers = [t for t in group['ticker'].tolist() if t in returns.columns]
        if exclude_ticker is not None:
            tickers = [t for t in tickers if t != exclude_ticker]
        if len(tickers) < 2:
            continue
        baskets[node] = returns[tickers].mean(axis=1)
    return pd.DataFrame(baskets)

# -----------------------------------------------------------------
def intra_basket_correlation(returns, constituents_df, min_obs=200):
    rows = []
    for node, group in constituents_df.groupby('node'):
        tickers = [t for t in group['ticker'].tolist() if t in returns.columns]
        if len(tickers) < 2:
            rows.append({'node': node, 'n_names': len(tickers),
                         'mean_pair_corr': np.nan, 'min_corr': np.nan, 'max_corr': np.nan,
                         'min_pair': '', 'max_pair': ''})
            continue
        sub = returns[tickers].dropna(how='all')
        if len(sub) < min_obs:
            rows.append({'node': node, 'n_names': len(tickers),
                         'mean_pair_corr': np.nan, 'min_corr': np.nan, 'max_corr': np.nan,
                         'min_pair': '', 'max_pair': ''})
            continue
        corr_mat = sub.corr()
        n = len(corr_mat)
        # Off-diagonal
        mask = ~np.eye(n, dtype=bool)
        vals = corr_mat.values[mask]
        # Find min and max pairs
        flat = []
        for i in range(n):
            for j in range(i+1, n):
                flat.append((corr_mat.index[i], corr_mat.columns[j], corr_mat.iloc[i,j]))
        flat_sorted = sorted(flat, key=lambda x: x[2])
        min_pair = f"{flat_sorted[0][0]}-{flat_sorted[0][1]}" if flat_sorted else ''
        max_pair = f"{flat_sorted[-1][0]}-{flat_sorted[-1][1]}" if flat_sorted else ''
        rows.append({
            'node': node, 'n_names': len(tickers),
            'mean_pair_corr': np.nanmean(vals),
            'min_corr': np.nanmin(vals),
            'max_corr': np.nanmax(vals),
            'min_pair': min_pair,
            'max_pair': max_pair,
        })
    return pd.DataFrame(rows).sort_values('mean_pair_corr', ascending=False)

def name_vs_baskets_loo(returns, constituents_df):
    """LEAVE-ONE-OUT: for each name, build baskets WITHOUT it, then correlate."""
    rows = []
    print(f"[diag] computing leave-one-out for {len(constituents_df)} names...")
    for i, (_, c) in enumerate(constituents_df.iterrows()):
        if i % 25 == 0 and i > 0:
            print(f"  ... {i}/{len(constituents_df)}")
        t = c['ticker']
        if t not in returns.columns:
            continue
        ret_series = returns[t].dropna()
        if len(ret_series) < 100:
            continue
        # Build LOO baskets
        loo_baskets = basket_returns_ew(returns, constituents_df, exclude_ticker=t)
        if c['node'] not in loo_baskets.columns:
            # Singleton home basket - skip
            continue
        common = ret_series.index.intersection(loo_baskets.index)
        if len(common) < 100:
            continue
        ret_aligned = ret_series.loc[common]
        bask_aligned = loo_baskets.loc[common]
        corrs = bask_aligned.corrwith(ret_aligned)
        corrs = corrs.dropna()
        if len(corrs) == 0:
            continue
        home_corr = corrs.get(c['node'], np.nan)
        top3 = corrs.nlargest(3)
        is_home_top = (corrs.idxmax() == c['node'])
        rows.append({
            'ticker': t, 'name': c['name'], 'assigned_node': c['node'],
            'home_corr': home_corr,
            'top1_node': top3.index[0] if len(top3) > 0 else None,
            'top1_corr': top3.iloc[0] if len(top3) > 0 else np.nan,
            'top2_node': top3.index[1] if len(top3) > 1 else None,
            'top2_corr': top3.iloc[1] if len(top3) > 1 else np.nan,
            'is_home_top': is_home_top,
        })
    return pd.DataFrame(rows)

# -----------------------------------------------------------------
def tier(c):
    if pd.isna(c): return 'na'
    if c >= TIER_GOOD: return 'good'
    if c >= TIER_OK: return 'ok'
    return 'weak'

# -----------------------------------------------------------------
# CHARTS
# -----------------------------------------------------------------
def chart_intra_basket(intra_df, output_dir):
    df = intra_df.dropna(subset=['mean_pair_corr']).copy()
    df = df.sort_values('mean_pair_corr', ascending=True)
    
    colors = ['#2ecc71' if c >= TIER_GOOD else '#f39c12' if c >= TIER_OK else '#e74c3c' 
              for c in df['mean_pair_corr']]
    
    fig, ax = plt.subplots(figsize=(11, 9))
    bars = ax.barh(df['node'], df['mean_pair_corr'], color=colors, edgecolor='black', linewidth=0.5)
    
    # Annotate with n_names and value
    for bar, (_, row) in zip(bars, df.iterrows()):
        w = bar.get_width()
        ax.text(w + 0.01, bar.get_y() + bar.get_height()/2,
                f"{w:.2f}  (n={int(row['n_names'])})",
                va='center', fontsize=9)
    
    ax.axvline(TIER_GOOD, color='#27ae60', linestyle='--', alpha=0.5, label=f'Good (≥{TIER_GOOD})')
    ax.axvline(TIER_OK, color='#e67e22', linestyle='--', alpha=0.5, label=f'OK (≥{TIER_OK})')
    ax.set_xlabel('Mean pairwise correlation (off-diagonal)')
    ax.set_title('Intra-basket cohesion - taxonomy v0.3\nhigher = tighter bucket', fontsize=13)
    ax.set_xlim(0, max(df['mean_pair_corr'].max() * 1.15, 1.0))
    ax.legend(loc='lower right')
    ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()
    
    path = output_dir / 'intra_basket.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

def chart_cross_basket_heatmap(bask_corr, output_dir):
    fig, ax = plt.subplots(figsize=(13, 11))
    
    # Mask upper triangle for clarity
    mask = np.triu(np.ones_like(bask_corr, dtype=bool), k=1)
    
    sns.heatmap(
        bask_corr, mask=mask, cmap='RdYlBu_r', center=0.5,
        vmin=0, vmax=1, annot=True, fmt='.2f', square=True,
        annot_kws={'size': 7}, cbar_kws={'shrink': 0.7, 'label': 'correlation'},
        ax=ax, linewidths=0.5, linecolor='white'
    )
    ax.set_title('Cross-basket correlation matrix (lower triangle)\nlight blocks of high correlation = baskets that move together', fontsize=12)
    plt.xticks(rotation=45, ha='right', fontsize=9)
    plt.yticks(rotation=0, fontsize=9)
    plt.tight_layout()
    
    path = output_dir / 'cross_basket_heatmap.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

def chart_misclassification(misclass_df, output_dir, top_n=20):
    if len(misclass_df) == 0:
        return None
    df = misclass_df.head(top_n).copy()
    df = df.sort_values('delta', ascending=True)
    
    fig, ax = plt.subplots(figsize=(11, max(5, 0.35 * len(df))))
    
    y_pos = np.arange(len(df))
    ax.barh(y_pos - 0.2, df['home_corr'], 0.4, label='Home basket corr', color='#3498db')
    ax.barh(y_pos + 0.2, df['top1_corr'], 0.4, label='Top1 basket corr', color='#e74c3c')
    
    labels = [f"{row['ticker']:6} → wants {row['top1_node']}" for _, row in df.iterrows()]
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel('Correlation')
    ax.set_title(f'Top {top_n} misclassifications - names whose top correlation is not their home basket\n(LOO baskets, large delta = stronger evidence to move)', fontsize=12)
    ax.legend(loc='lower right')
    ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()
    
    path = output_dir / 'misclassifications.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

def chart_basket_returns(baskets, output_dir):
    """Cumulative return of each basket over the lookback."""
    cum = (1 + baskets).cumprod() - 1
    
    fig, ax = plt.subplots(figsize=(13, 8))
    
    # Sort by final return
    final_rets = cum.iloc[-1].sort_values(ascending=False)
    
    cmap = plt.colormaps['tab20']
    for i, node in enumerate(final_rets.index):
        color = cmap(i / len(final_rets))
        ax.plot(cum.index, cum[node], label=f"{node} ({final_rets[node]*100:+.0f}%)",
                color=color, linewidth=1.4, alpha=0.85)
    
    ax.set_title(f'Cumulative basket returns (equal-weighted, last {LOOKBACK_YEARS}y)', fontsize=13)
    ax.set_ylabel('Cumulative return')
    ax.legend(loc='center left', bbox_to_anchor=(1, 0.5), fontsize=8, ncol=1)
    ax.grid(alpha=0.3)
    ax.axhline(0, color='black', linewidth=0.5)
    plt.tight_layout()
    
    path = output_dir / 'basket_returns.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

# -----------------------------------------------------------------
# HTML REPORT
# -----------------------------------------------------------------
def build_html_report(intra_df, bask_corr, misclass_df, missing_tickers, output_dir, n_obs):
    intra_clean = intra_df.dropna(subset=['mean_pair_corr']).copy()
    
    # Tier counts
    good = sum(intra_clean['mean_pair_corr'] >= TIER_GOOD)
    ok = sum((intra_clean['mean_pair_corr'] >= TIER_OK) & (intra_clean['mean_pair_corr'] < TIER_GOOD))
    weak = sum(intra_clean['mean_pair_corr'] < TIER_OK)
    
    def tier_class(c):
        if pd.isna(c): return 'na'
        if c >= TIER_GOOD: return 'good'
        if c >= TIER_OK: return 'ok'
        return 'weak'
    
    # Build intra table rows
    intra_rows = []
    for _, r in intra_df.iterrows():
        cls = tier_class(r['mean_pair_corr'])
        mc = f"{r['mean_pair_corr']:.3f}" if pd.notna(r['mean_pair_corr']) else 'n/a'
        mn = f"{r['min_corr']:.3f}" if pd.notna(r['min_corr']) else 'n/a'
        mx = f"{r['max_corr']:.3f}" if pd.notna(r['max_corr']) else 'n/a'
        intra_rows.append(
            f"<tr class='{cls}'>"
            f"<td>{escape(r['node'])}</td>"
            f"<td>{int(r['n_names'])}</td>"
            f"<td class='num'>{mc}</td>"
            f"<td class='num'>{mn}</td>"
            f"<td class='num'>{mx}</td>"
            f"<td>{escape(r['min_pair'])}</td>"
            f"<td>{escape(r['max_pair'])}</td>"
            f"</tr>"
        )
    
    # Misclass table
    misc_rows = []
    for _, r in misclass_df.head(40).iterrows():
        delta = r['top1_corr'] - r['home_corr'] if pd.notna(r['home_corr']) else None
        delta_s = f"{delta:+.3f}" if delta is not None else 'n/a'
        sev = ''
        if delta is not None:
            if delta > 0.10: sev = 'sev-high'
            elif delta > 0.03: sev = 'sev-med'
        hc = f"{r['home_corr']:.3f}" if pd.notna(r['home_corr']) else 'n/a'
        tc = f"{r['top1_corr']:.3f}" if pd.notna(r['top1_corr']) else 'n/a'
        misc_rows.append(
            f"<tr class='{sev}'>"
            f"<td><b>{escape(r['ticker'])}</b></td>"
            f"<td>{escape(str(r['name'] or ''))}</td>"
            f"<td>{escape(str(r['assigned_node']))}</td>"
            f"<td>{escape(str(r['top1_node'] or ''))}</td>"
            f"<td class='num'>{hc}</td>"
            f"<td class='num'>{tc}</td>"
            f"<td class='num'>{delta_s}</td>"
            f"</tr>"
        )
    
    missing_block = ''
    if missing_tickers:
        missing_block = f"<p class='warn'>⚠ Missing prices: {', '.join(sorted(missing_tickers))}</p>"
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Basket Check Report</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1200px; margin: 30px auto; padding: 20px; color: #222; }}
h1 {{ border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }}
h2 {{ margin-top: 35px; color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
.tile-row {{ display: flex; gap: 12px; margin: 18px 0; }}
.tile {{ flex: 1; padding: 14px; border-radius: 6px; text-align: center; }}
.tile.good {{ background: #d5f5e3; border: 1px solid #2ecc71; }}
.tile.ok   {{ background: #fdebd0; border: 1px solid #f39c12; }}
.tile.weak {{ background: #fadbd8; border: 1px solid #e74c3c; }}
.tile.info {{ background: #e8f4f8; border: 1px solid #3498db; }}
.tile .num {{ font-size: 28px; font-weight: bold; }}
.tile .lbl {{ font-size: 12px; color: #555; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
th {{ background: #2c3e50; color: white; padding: 8px; text-align: left; }}
td {{ padding: 6px 8px; border-bottom: 1px solid #eee; }}
tr.good {{ background: #eafaf1; }}
tr.ok   {{ background: #fef5e7; }}
tr.weak {{ background: #fdedec; }}
tr.sev-high {{ background: #fadbd8; }}
tr.sev-med  {{ background: #fef5e7; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; margin: 10px 0; }}
.warn {{ color: #c0392b; font-weight: 600; }}
.legend {{ font-size: 12px; color: #666; margin: 8px 0; }}
.note {{ background: #fffbe6; padding: 10px; border-left: 4px solid #f1c40f; margin: 12px 0; }}
</style></head><body>

<h1>IMA Energy Dashboard - Basket Check Report</h1>
<p style="color:#666;">Taxonomy v0.3 · {LOOKBACK_YEARS}y daily returns · {n_obs} observations · LOO baskets</p>
{missing_block}

<h2>Summary</h2>
<div class="tile-row">
    <div class="tile good"><div class="num">{good}</div><div class="lbl">Healthy baskets (≥{TIER_GOOD})</div></div>
    <div class="tile ok"><div class="num">{ok}</div><div class="lbl">Acceptable ({TIER_OK}–{TIER_GOOD})</div></div>
    <div class="tile weak"><div class="num">{weak}</div><div class="lbl">Weak baskets (&lt;{TIER_OK})</div></div>
    <div class="tile info"><div class="num">{len(misclass_df)}</div><div class="lbl">Misclassified names (LOO)</div></div>
</div>

<div class="note"><b>How to read this:</b> "Mean pair correlation" measures whether names within a basket move together. >0.55 is a tight bucket; 0.40–0.55 is acceptable; below 0.40 means the bucket is heterogeneous and may need to be split or rebuilt. The LOO column in the misclassification table excludes the name itself when computing its home basket, so home_corr ≠ NaN even for small baskets.</div>

<h2>Intra-basket cohesion</h2>
<img src="intra_basket.png" alt="Intra-basket bar chart">
<table>
<tr><th>Node</th><th>n</th><th>mean</th><th>min</th><th>max</th><th>weakest pair</th><th>tightest pair</th></tr>
{''.join(intra_rows)}
</table>

<h2>Cross-basket correlation</h2>
<p class="legend">Look for blocks of high correlation - these are nodes that move together (e.g. OFS-onshore/offshore/equipment cluster, or upstream and integrated).</p>
<img src="cross_basket_heatmap.png" alt="Cross-basket heatmap">

<h2>Cumulative basket returns ({LOOKBACK_YEARS}y)</h2>
<img src="basket_returns.png" alt="Basket returns">

<h2>Misclassifications (LOO)</h2>
<p class="legend">Names whose home basket is not their top-correlated basket. <span style='color:#c0392b'>Red rows</span> have delta &gt;0.10 (strong evidence to move); <span style='color:#e67e22'>amber rows</span> are 0.03–0.10 (investigate).</p>
<img src="misclassifications.png" alt="Misclassifications">
<table>
<tr><th>Ticker</th><th>Name</th><th>Assigned</th><th>Top1 corr basket</th><th>home corr</th><th>top1 corr</th><th>delta</th></tr>
{''.join(misc_rows)}
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
    consts = get_all_constituents(tax)
    print(f"[taxonomy] {len(consts)} constituents across {consts['node'].nunique()} nodes")

    tickers = consts['ticker'].unique().tolist()
    prices = fetch_prices(tickers)

    missing = sorted(set(tickers) - set(prices.columns))
    if missing:
        print(f"[warn] missing prices for: {missing}")

    returns = compute_returns(prices)
    print(f"[returns] {returns.shape[0]} obs, {returns.shape[1]} tickers")

    baskets = basket_returns_ew(returns, consts)
    print(f"[baskets] {baskets.shape[1]} baskets computed")

    # ----- diagnostics
    print("\n[diag] intra-basket correlation...")
    intra = intra_basket_correlation(returns, consts)
    intra.to_csv(OUTPUT_DIR / 'intra_basket_correlation.csv', index=False)

    print("[diag] cross-basket correlation...")
    bask_corr = baskets.corr()
    bask_corr.to_csv(OUTPUT_DIR / 'cross_basket_correlation.csv')

    print("[diag] leave-one-out misclassification check...")
    nvb = name_vs_baskets_loo(returns, consts)
    nvb.to_csv(OUTPUT_DIR / 'name_vs_basket.csv', index=False)

    misclass = nvb[~nvb['is_home_top']].copy()
    misclass['delta'] = misclass['top1_corr'] - misclass['home_corr']
    misclass = misclass.sort_values('delta', ascending=False, na_position='last')
    misclass.to_csv(OUTPUT_DIR / 'misclassifications.csv', index=False)

    # ----- charts
    print("\n[charts] generating...")
    chart_intra_basket(intra, OUTPUT_DIR)
    chart_cross_basket_heatmap(bask_corr, OUTPUT_DIR)
    chart_misclassification(misclass, OUTPUT_DIR)
    chart_basket_returns(baskets, OUTPUT_DIR)

    # ----- HTML
    print("[html] building report...")
    report_path = build_html_report(intra, bask_corr, misclass, missing, OUTPUT_DIR, returns.shape[0])

    # ----- terminal summary
    print("\n" + "="*78)
    print("INTRA-BASKET COHESION")
    print("="*78)
    print(intra.to_string(index=False))
    
    print("\n" + "="*78)
    print(f"MISCLASSIFICATIONS - top 20 by delta (LOO basket)")
    print("="*78)
    cols = ['ticker', 'name', 'assigned_node', 'top1_node', 'home_corr', 'top1_corr', 'delta']
    print(misclass.head(20)[cols].to_string(index=False))

    print(f"\n[done] outputs in: {OUTPUT_DIR.resolve()}")
    print(f"       open {report_path} in your browser")

if __name__ == '__main__':
    main()