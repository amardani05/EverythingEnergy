"""
IMA Energy Dashboard - Phase 3: Pairs, Residuals, Rolling Betas
=================================================================

Three analyses that turn the dashboard from a tracker into a screener:

1. PAIRS FRAMEWORK (cross_basket_pairs)
   Taxonomy-driven long/short pairs based on commodity flow logic.
   No optimization - the pairs are pre-defined by the energy value chain.
   For each pair, computes:
     - 5y rolling correlation (regime stability)
     - Current pair return z-score (mean-reversion signal)
     - Realized vol of the spread

2. RESIDUAL SCREEN (name_residuals)
   For each name, regresses returns on its home basket → residual.
   Two outputs:
     - Recent z-score of residual (mean-reversion candidates)
     - Cumulative residual over last 60d (momentum / re-rating candidates)
   Names that have decoupled from their peers are the trade ideas.

3. ROLLING BETAS (rolling_basket_betas)
   60-day rolling beta of each basket to CL, NG, SPX, TNX.
   Flags regime changes when beta moves >2 std from its long-run mean.
   Useful for "is this basket behaving differently than usual" questions.

OUTPUTS to phase3_results/:
  - pairs_table.csv             (current pair stats)
  - pairs_zscores.png           (which pairs are stretched right now)
  - residuals_top20.csv         (most-deviated names)
  - residual_screen.png         (mean-reversion + momentum candidates)
  - rolling_betas_<driver>.png  (one chart per driver)
  - regime_change_alerts.csv    (baskets whose betas have shifted recently)
  - report.html                 (combined view)

USAGE
-----
1. Place alongside energy_taxonomy_v0.4.yaml, price_cache.parquet, driver_cache.parquet
   (these are produced by basket_check.py and driver_analysis_v4.py)
2. python phase3_analysis.py
3. Open phase3_results/report.html

DESIGN NOTES (overfitting defenses)
-----------------------------------
- Pairs are NOT optimized: long-X / short-Y is determined by taxonomy
  (refiners vs upstream, IPPs vs utilities, etc.) - pre-specified before seeing data.
- Residual signals report BOTH directions (mean-revert AND momentum) so the
  analyst chooses based on context. Single-direction would be data-mined.
- Rolling betas use a fixed 60-day window - not selected to maximize anything.
- Regime change threshold is 2σ on the rolling beta z-score, fixed.
"""

import yaml
import pandas as pd
import numpy as np
import warnings
import matplotlib.pyplot as plt
import seaborn as sns
import statsmodels.api as sm
from pathlib import Path
from html import escape

warnings.filterwarnings('ignore')

YAML_PATH = 'energy_taxonomy.yaml'
PRICE_CACHE = 'price_cache.parquet'
DRIVER_CACHE = 'driver_cache.parquet'
OUTPUT_DIR = Path('phase3_results')
LOOKBACK_YEARS = 5
ROLLING_WINDOW = 60  # trading days for rolling beta

# ============================================================
# Pre-specified pairs (NOT data-mined - derived from taxonomy)
# ============================================================
# Format: (long_basket, short_basket, thesis)
TAXONOMY_PAIRS = [
    # Refining margin trade: long refiners, short crude E&P → captures crack spread
    ('downstream_refiners', 'upstream_oil_eandp', 'Long refining margin (crack spread)'),
    # Service intensity divergence: OFS catches up/down vs E&P capex
    ('ofs_onshore', 'upstream_oil_eandp', 'Service intensity vs E&P capex'),
    # Offshore vs onshore upstream: deepwater renaissance trade
    ('upstream_offshore_drillers', 'upstream_oil_eandp', 'Offshore renaissance vs onshore'),
    # Gas vs oil E&P: ratio of NG to CL prices in equity
    ('upstream_gas_eandp', 'upstream_oil_eandp', 'Gas vs oil commodity exposure'),
    # LNG corridor: terminal C-corps benefit when gas-only E&P does poorly
    ('lng_terminals', 'upstream_gas_eandp', 'LNG terminal margin vs gas producer'),
    # Midstream vs upstream: fee businesses vs commodity producers
    ('midstream_pipelines', 'upstream_oil_eandp', 'Fee-based vs commodity exposure'),
    # GPT vs pipelines: gathering volume vs long-haul throughput
    ('midstream_gpt', 'midstream_pipelines', 'Wellhead activity vs long-haul'),
    # Power: AI-rerated IPPs vs sleepy regulated
    ('power_ipps_merchant', 'power_utilities_regulated_gas', 'Merchant power AI thesis'),
    # Coal vs gas substitution: power burn switching trade
    ('coal', 'upstream_gas_eandp', 'Coal-gas substitution in power'),
    # Royalty vs operating: TPL/VNOM-style minerals vs operating E&P
    ('minerals_royalty', 'upstream_oil_eandp', 'Royalty (no opex) vs operating E&P'),
    # Tankers vs refiners: ton-mile demand vs domestic refining
    ('tanker_shipping', 'downstream_refiners', 'Crude/product shipping vs refining'),
    # Petrochem feedstock advantage: long petchem, short NGL producers
    ('petrochem', 'midstream_gpt', 'Petchem demand vs NGL feedstock supply'),
    # Uranium fuel vs nuclear-heavy IPPs: front-end vs back-end of nuclear
    ('uranium_nuclear_fuel', 'power_ipps_merchant', 'Nuclear fuel vs nuclear power'),
]

# ============================================================
# Loading & utilities
# ============================================================
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

def load_caches():
    if not Path(PRICE_CACHE).exists():
        raise FileNotFoundError(f"Missing {PRICE_CACHE}. Run basket_check.py first.")
    if not Path(DRIVER_CACHE).exists():
        raise FileNotFoundError(f"Missing {DRIVER_CACHE}. Run driver_analysis_v4.py first.")
    prices = pd.read_parquet(PRICE_CACHE)
    drivers = pd.read_parquet(DRIVER_CACHE)
    return prices, drivers

def daily_log_returns(prices):
    return np.log(prices / prices.shift(1))

def basket_returns(returns_df, constituents_df, winsor=True):
    """Equal-weighted, optionally winsorized."""
    baskets = {}
    for node, group in constituents_df.groupby('node'):
        ts = [t for t in group['ticker'] if t in returns_df.columns]
        if len(ts) < 2: continue
        sub = returns_df[ts]
        if winsor:
            sub = sub.apply(winsorize, axis=0)
        b = sub.mean(axis=1, skipna=True).dropna()
        if len(b) < 100: continue
        baskets[node] = b
    return pd.DataFrame(baskets)

# ============================================================
# 1. PAIRS FRAMEWORK
# ============================================================
def compute_pair_stats(baskets_d):
    """For each pre-specified pair, compute current z-score, correlation, vol."""
    rows = []
    for long_b, short_b, thesis in TAXONOMY_PAIRS:
        if long_b not in baskets_d.columns or short_b not in baskets_d.columns:
            rows.append({'long': long_b, 'short': short_b, 'thesis': thesis,
                         'note': 'missing basket', 'n_obs': 0})
            continue
        # Pair daily return = long - short
        pair = (baskets_d[long_b] - baskets_d[short_b]).dropna()
        if len(pair) < 250:
            rows.append({'long': long_b, 'short': short_b, 'thesis': thesis,
                         'note': 'too few obs', 'n_obs': len(pair)})
            continue
        # Cumulative log-pair (the pair "level" in log space)
        cum = pair.cumsum()
        # Z-score: how many std-deviations from 60d mean is the current cum level
        recent_window = 60
        if len(cum) < recent_window: continue
        recent_mean = cum.rolling(recent_window).mean().iloc[-1]
        recent_std = cum.rolling(recent_window).std().iloc[-1]
        cur = cum.iloc[-1]
        z_60d = (cur - recent_mean) / recent_std if recent_std > 0 else 0
        
        # 1y rolling correlation (between long and short basket returns) - pair stability
        if len(baskets_d) >= 252:
            rolling_corr = baskets_d[long_b].rolling(252).corr(baskets_d[short_b]).dropna()
            corr_now = rolling_corr.iloc[-1] if len(rolling_corr) else np.nan
            corr_min = rolling_corr.min() if len(rolling_corr) else np.nan
            corr_max = rolling_corr.max() if len(rolling_corr) else np.nan
        else:
            corr_now = corr_min = corr_max = np.nan
        
        # Pair volatility (annualized)
        pair_vol = pair.std() * np.sqrt(252)
        # Sharpe of always-long-this-pair (sanity check, NOT a recommendation)
        pair_sharpe = (pair.mean() * 252) / pair_vol if pair_vol > 0 else 0
        
        # Recent 1m return on the pair (annualized for comparability)
        recent_1m = pair.iloc[-21:].sum() if len(pair) >= 21 else np.nan
        recent_3m = pair.iloc[-63:].sum() if len(pair) >= 63 else np.nan
        
        rows.append({
            'long': long_b, 'short': short_b, 'thesis': thesis,
            'z_60d': z_60d, 'corr_now': corr_now, 'corr_min': corr_min, 'corr_max': corr_max,
            'pair_vol': pair_vol, 'pair_sharpe': pair_sharpe,
            'ret_1m': recent_1m, 'ret_3m': recent_3m, 'n_obs': len(pair), 'note': 'ok',
        })
    return pd.DataFrame(rows)

def chart_pairs(pairs_df, output_dir):
    df = pairs_df[pairs_df['note']=='ok'].copy()
    if len(df) == 0: return None
    df['pair_label'] = df['long'].str.replace('_','-') + '\nvs ' + df['short'].str.replace('_','-')
    df = df.sort_values('z_60d')
    
    fig, ax = plt.subplots(figsize=(13, max(6, 0.6 * len(df))))
    colors = ['#e74c3c' if z < -1.5 else '#2ecc71' if z > 1.5 else '#95a5a6' for z in df['z_60d']]
    bars = ax.barh(df['pair_label'], df['z_60d'], color=colors, edgecolor='black', linewidth=0.4)
    for bar, (_, row) in zip(bars, df.iterrows()):
        w = bar.get_width()
        ha = 'left' if w >= 0 else 'right'
        offset = 0.05 if w >= 0 else -0.05
        ax.text(w + offset, bar.get_y() + bar.get_height()/2,
                f"  z={w:+.2f}  (3m={row['ret_3m']*100:+.1f}%)",
                va='center', ha=ha, fontsize=9)
    ax.axvline(-1.5, color='#c0392b', linestyle='--', alpha=0.5, label='Stretched (|z|>1.5)')
    ax.axvline(1.5, color='#c0392b', linestyle='--', alpha=0.5)
    ax.axvline(0, color='black', linewidth=0.5)
    ax.set_xlabel('Pair z-score (current cum vs 60d mean)')
    ax.set_title('Cross-basket pair z-scores - taxonomy-driven pairs only\nNegative z = pair stretched DOWN (long side underperformed); Positive z = pair stretched UP', fontsize=11)
    ax.legend(loc='lower right'); ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()
    path = output_dir / 'pairs_zscores.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

# ============================================================
# 2. RESIDUAL SCREEN
# ============================================================
def compute_name_residuals(returns_df, baskets_d, constituents_df):
    """For each name, regress returns on its home basket (LOO) → residuals.
    Report recent z-score (mean-reversion) AND recent cumulative (momentum)."""
    rows = []
    for _, c in constituents_df.iterrows():
        t = c['ticker']; node = c['node']
        if t not in returns_df.columns: continue
        if node not in baskets_d.columns: continue
        
        # Build LOO basket (exclude this name)
        same_node = [x for x in constituents_df[constituents_df['node']==node]['ticker']
                     if x in returns_df.columns and x != t]
        if len(same_node) < 2: continue
        loo_basket = returns_df[same_node].apply(winsorize, axis=0).mean(axis=1, skipna=True)
        
        y = returns_df[t].dropna()
        x = loo_basket.dropna()
        common = y.index.intersection(x.index)
        if len(common) < 252: continue  # need 1y minimum
        y_, x_ = y.loc[common], x.loc[common]
        
        # Single-factor regression: ticker = alpha + beta * loo_basket + residual
        X = sm.add_constant(x_.rename('basket'))
        try:
            m = sm.OLS(y_, X).fit()
        except Exception:
            continue
        residuals = m.resid
        beta = float(m.params.get('basket', np.nan))
        alpha = float(m.params.get('const', np.nan))
        r2 = m.rsquared
        
        # Recent residual stats
        last_60 = residuals.iloc[-60:] if len(residuals) >= 60 else residuals
        last_20 = residuals.iloc[-20:] if len(residuals) >= 20 else residuals
        full_std = residuals.std()
        if full_std == 0: continue
        
        cum_60d = last_60.sum()           # how much residual has accumulated
        cum_20d = last_20.sum()
        z_60d = cum_60d / (full_std * np.sqrt(60))    # mean-reversion signal
        z_20d = cum_20d / (full_std * np.sqrt(20))    # short-term move
        
        rows.append({
            'ticker': t, 'name': c['name'], 'node': node,
            'beta_to_basket': beta, 'alpha_annual': alpha * 252,
            'r2': r2, 'resid_std_daily': full_std,
            'cum_resid_60d': cum_60d, 'cum_resid_20d': cum_20d,
            'z_resid_60d': z_60d, 'z_resid_20d': z_20d,
            'n_obs': len(common),
        })
    return pd.DataFrame(rows)

def chart_residual_screen(resid_df, output_dir, top_n=20):
    if len(resid_df) == 0: return None
    df = resid_df.dropna(subset=['z_resid_60d']).copy()
    
    # Top N positive residuals (outperforming peers - momentum / catch-up rerating)
    momentum = df.sort_values('z_resid_60d', ascending=False).head(top_n)
    # Top N negative residuals (underperforming peers - mean-reversion candidates)
    mean_rev = df.sort_values('z_resid_60d', ascending=True).head(top_n)
    
    fig, axes = plt.subplots(1, 2, figsize=(16, max(7, 0.4*top_n)))
    
    # Mean-reversion (left): underperformers
    mr = mean_rev.sort_values('z_resid_60d', ascending=True)
    labels_mr = [f"{r['ticker']} ({r['node'].replace('_',' ')[:20]})" for _, r in mr.iterrows()]
    bars_mr = axes[0].barh(labels_mr, mr['z_resid_60d'], color='#3498db', edgecolor='black', linewidth=0.4)
    for bar, (_, row) in zip(bars_mr, mr.iterrows()):
        w = bar.get_width()
        axes[0].text(w - 0.05, bar.get_y() + bar.get_height()/2,
                     f" {w:.2f} ({row['cum_resid_60d']*100:+.1f}%)",
                     va='center', ha='right', fontsize=8)
    axes[0].axvline(0, color='black', linewidth=0.5)
    axes[0].set_title(f'Top {top_n} UNDER-performers vs peers\n(mean-reversion candidates: short basket / long stock if thesis intact)', fontsize=11)
    axes[0].set_xlabel('60d residual z-score (negative = underperformed peers)')
    axes[0].grid(axis='x', alpha=0.3)
    
    # Momentum (right): outperformers
    mo = momentum.sort_values('z_resid_60d', ascending=True)
    labels_mo = [f"{r['ticker']} ({r['node'].replace('_',' ')[:20]})" for _, r in mo.iterrows()]
    bars_mo = axes[1].barh(labels_mo, mo['z_resid_60d'], color='#e74c3c', edgecolor='black', linewidth=0.4)
    for bar, (_, row) in zip(bars_mo, mo.iterrows()):
        w = bar.get_width()
        axes[1].text(w + 0.05, bar.get_y() + bar.get_height()/2,
                     f" {w:.2f} ({row['cum_resid_60d']*100:+.1f}%)",
                     va='center', ha='left', fontsize=8)
    axes[1].axvline(0, color='black', linewidth=0.5)
    axes[1].set_title(f'Top {top_n} OUT-performers vs peers\n(momentum / re-rating candidates: investigate the news)', fontsize=11)
    axes[1].set_xlabel('60d residual z-score (positive = outperformed peers)')
    axes[1].grid(axis='x', alpha=0.3)
    
    plt.tight_layout()
    path = output_dir / 'residual_screen.png'
    plt.savefig(path, dpi=130, bbox_inches='tight')
    plt.close()
    return path

# ============================================================
# 3. ROLLING BETAS
# ============================================================
def compute_rolling_betas(baskets_d, drivers_d, drivers_to_track, window=ROLLING_WINDOW):
    """Per-basket rolling beta to each driver. Returns dict of dicts."""
    out = {}
    for driver in drivers_to_track:
        if driver not in drivers_d.columns: continue
        x = drivers_d[driver].dropna()
        beta_frame = {}
        for basket in baskets_d.columns:
            y = baskets_d[basket].dropna()
            common = y.index.intersection(x.index)
            if len(common) < window + 30: continue
            y_aligned = y.loc[common]
            x_aligned = x.loc[common]
            # Rolling cov / var
            cov = y_aligned.rolling(window).cov(x_aligned)
            var = x_aligned.rolling(window).var()
            beta = (cov / var).dropna()
            beta_frame[basket] = beta
        if beta_frame:
            out[driver] = pd.DataFrame(beta_frame)
    return out

def detect_regime_changes(rolling_betas, threshold=2.0):
    """Flag baskets whose CURRENT rolling beta is >threshold std from long-run mean."""
    rows = []
    for driver, df in rolling_betas.items():
        for basket in df.columns:
            beta_series = df[basket].dropna()
            if len(beta_series) < 100: continue
            mu = beta_series.mean()
            sd = beta_series.std()
            if sd == 0: continue
            current = beta_series.iloc[-1]
            z = (current - mu) / sd
            if abs(z) > threshold:
                rows.append({
                    'driver': driver, 'basket': basket,
                    'current_beta': current, 'long_run_mean': mu, 'long_run_std': sd,
                    'z_score': z, 'direction': 'increased' if z > 0 else 'decreased',
                })
    return pd.DataFrame(rows).sort_values('z_score', key=abs, ascending=False) if rows else pd.DataFrame()

def chart_rolling_betas(rolling_betas, output_dir, baskets_to_show=None):
    """One chart per driver showing rolling betas of selected baskets."""
    paths = []
    for driver, df in rolling_betas.items():
        if df.empty: continue
        # Show top 10 most-volatile baskets (in beta space) for readability
        beta_vol = df.std()
        top_baskets = baskets_to_show if baskets_to_show else beta_vol.nlargest(10).index.tolist()
        plot_df = df[[c for c in top_baskets if c in df.columns]]
        
        fig, ax = plt.subplots(figsize=(13, 7))
        cmap = plt.colormaps['tab20']
        for i, col in enumerate(plot_df.columns):
            ax.plot(plot_df.index, plot_df[col], label=col,
                    color=cmap(i / max(len(plot_df.columns), 1)), linewidth=1.2, alpha=0.85)
        ax.axhline(0, color='black', linewidth=0.5)
        ax.set_title(f'{ROLLING_WINDOW}-day rolling beta to {driver}\n(top 10 most-variable baskets)', fontsize=12)
        ax.set_ylabel(f'beta to {driver}')
        ax.legend(loc='center left', bbox_to_anchor=(1, 0.5), fontsize=8)
        ax.grid(alpha=0.3)
        plt.tight_layout()
        path = output_dir / f'rolling_betas_{driver}.png'
        plt.savefig(path, dpi=130, bbox_inches='tight')
        plt.close()
        paths.append(path)
    return paths

# ============================================================
# HTML REPORT
# ============================================================
def build_report(pairs_df, resid_df, regime_df, rolling_paths, output_dir):
    # Pairs table
    pair_rows = []
    pdf = pairs_df[pairs_df['note']=='ok'].copy()
    pdf = pdf.sort_values('z_60d', key=abs, ascending=False)
    for _, r in pdf.iterrows():
        z = r['z_60d']
        cls = 'sev-high' if abs(z) > 2 else 'sev-med' if abs(z) > 1.5 else ''
        pair_rows.append(
            f"<tr class='{cls}'><td>{escape(r['long'])}</td><td>{escape(r['short'])}</td>"
            f"<td>{escape(r['thesis'])}</td>"
            f"<td class='num'>{z:+.2f}</td>"
            f"<td class='num'>{r['corr_now']:.2f}</td>"
            f"<td class='num'>{r['ret_1m']*100:+.1f}%</td>"
            f"<td class='num'>{r['ret_3m']*100:+.1f}%</td>"
            f"<td class='num'>{r['pair_vol']*100:.1f}%</td>"
            f"</tr>"
        )
    
    # Residuals: combine top 15 each direction
    resid_top = resid_df.dropna(subset=['z_resid_60d']).sort_values('z_resid_60d', ascending=True).head(15)
    resid_bot = resid_df.dropna(subset=['z_resid_60d']).sort_values('z_resid_60d', ascending=False).head(15)
    
    def resid_row_html(r, rank_label):
        z = r['z_resid_60d']
        cum = r['cum_resid_60d']
        return (f"<tr><td>{rank_label}</td><td><b>{escape(r['ticker'])}</b></td>"
                f"<td>{escape(str(r['name']))}</td><td>{escape(r['node'])}</td>"
                f"<td class='num'>{z:+.2f}</td>"
                f"<td class='num'>{cum*100:+.1f}%</td>"
                f"<td class='num'>{r['beta_to_basket']:.2f}</td>"
                f"<td class='num'>{r['r2']:.2f}</td>"
                f"</tr>")
    
    mean_rev_rows = ''.join(resid_row_html(r, '↓') for _, r in resid_top.iterrows())
    momentum_rows = ''.join(resid_row_html(r, '↑') for _, r in resid_bot.iterrows())
    
    # Regime changes
    regime_rows = []
    if len(regime_df):
        for _, r in regime_df.iterrows():
            cls = 'sev-high' if abs(r['z_score']) > 3 else 'sev-med'
            regime_rows.append(
                f"<tr class='{cls}'><td>{escape(r['driver'])}</td><td>{escape(r['basket'])}</td>"
                f"<td class='num'>{r['current_beta']:+.2f}</td>"
                f"<td class='num'>{r['long_run_mean']:+.2f}</td>"
                f"<td class='num'>{r['z_score']:+.2f}</td>"
                f"<td>{escape(r['direction'])}</td>"
                f"</tr>"
            )
    
    rolling_imgs = ''.join(f"<img src='{p.name}' alt='rolling beta {p.stem}'>" for p in rolling_paths)
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Phase 3 Analysis</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1400px; margin: 30px auto; padding: 20px; color: #222; }}
h1 {{ border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }}
h2 {{ margin-top: 35px; color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
h3 {{ color: #555; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12px; }}
th {{ background: #2c3e50; color: white; padding: 7px; text-align: left; }}
td {{ padding: 5px 7px; border-bottom: 1px solid #eee; }}
tr.sev-high {{ background: #fadbd8; }} tr.sev-med {{ background: #fef5e7; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; margin: 10px 0; }}
.note {{ background: #fffbe6; padding: 10px; border-left: 4px solid #f1c40f; margin: 12px 0; font-size: 13px; }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
.cols h3 {{ margin-top: 0; }}
</style></head><body>
<h1>IMA Energy Dashboard - Phase 3: Pairs, Residuals, Regimes</h1>
<p style="color:#666;">Daily returns, {LOOKBACK_YEARS}y, {ROLLING_WINDOW}d rolling windows where applicable</p>

<div class="note"><b>How to read this:</b> three idea-generation lenses on top of the basket framework.
<ul style="margin: 8px 0;">
<li><b>Pairs:</b> taxonomy-driven long/short combinations. Z-score = how stretched the pair is vs its 60d mean. |z|&gt;1.5 means pair is in unusual territory (red rows).</li>
<li><b>Residuals:</b> each name's recent return minus its predicted return given the basket's move. Negative residual = name underperformed peers (potential mean-reversion long); positive = outperformed (catalyst-driven, investigate).</li>
<li><b>Regimes:</b> baskets whose rolling beta to a driver has shifted &gt;2σ from its long-run mean. Flags structural change (e.g. IPP betas to NG dropping post-AI rerating).</li>
</ul>
None of these are signals to trade blindly - they're idea filters that surface things worth investigating.</div>

<h2>1. Cross-basket pair z-scores</h2>
<img src="pairs_zscores.png" alt="Pairs z-scores">
<table>
<tr><th>Long</th><th>Short</th><th>Thesis</th><th>z (60d)</th><th>1y corr</th><th>1m</th><th>3m</th><th>Vol (ann)</th></tr>
{''.join(pair_rows)}
</table>

<h2>2. Name vs basket residuals</h2>
<img src="residual_screen.png" alt="Residual screen">

<div class="cols">
<div>
<h3>Mean-reversion candidates (recent underperformers)</h3>
<table>
<tr><th></th><th>Ticker</th><th>Name</th><th>Node</th><th>z 60d</th><th>cum resid</th><th>β to basket</th><th>R²</th></tr>
{mean_rev_rows}
</table>
</div>
<div>
<h3>Momentum / re-rating candidates (recent outperformers)</h3>
<table>
<tr><th></th><th>Ticker</th><th>Name</th><th>Node</th><th>z 60d</th><th>cum resid</th><th>β to basket</th><th>R²</th></tr>
{momentum_rows}
</table>
</div>
</div>

<h2>3. Rolling beta regime changes</h2>
<p style="font-size:13px; color:#555;">Baskets whose current 60d rolling beta is more than 2σ from its long-run mean. May indicate structural shift in driver sensitivity.</p>
<table>
<tr><th>Driver</th><th>Basket</th><th>Current β</th><th>LR mean β</th><th>Z-score</th><th>Direction</th></tr>
{''.join(regime_rows) if regime_rows else "<tr><td colspan='6'>No baskets currently exceeding 2σ threshold.</td></tr>"}
</table>

<h3>Rolling betas over time</h3>
{rolling_imgs}

</body></html>"""
    
    path = output_dir / 'report.html'
    with open(path, 'w') as f:
        f.write(html)
    return path

# ============================================================
def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    tax = load_taxonomy()
    consts = get_constituents(tax)
    print(f"[taxonomy] {len(consts)} constituents in {consts['node'].nunique()} nodes")
    
    prices, drivers = load_caches()
    print(f"[cache] prices {prices.shape}, drivers {drivers.shape}")
    
    name_returns_d = daily_log_returns(prices)
    driver_returns_d = daily_log_returns(drivers[['CL','NG','SPX']]) if all(c in drivers.columns for c in ['CL','NG','SPX']) else None
    if 'TNX' in drivers.columns:
        driver_returns_d['TNX'] = drivers['TNX'].diff()
    
    baskets_d = basket_returns(name_returns_d, consts, winsor=True)
    print(f"[baskets] {baskets_d.shape}")
    
    # 1. Pairs
    print("\n[1/3] Computing pairs...")
    pairs_df = compute_pair_stats(baskets_d)
    pairs_df.to_csv(OUTPUT_DIR / 'pairs_table.csv', index=False)
    chart_pairs(pairs_df, OUTPUT_DIR)
    print(f"  {(pairs_df['note']=='ok').sum()} pairs computed")
    print(pairs_df[pairs_df['note']=='ok'][['long','short','z_60d','ret_3m','corr_now']].to_string(index=False))
    
    # 2. Residuals
    print("\n[2/3] Computing name residuals...")
    resid_df = compute_name_residuals(name_returns_d, baskets_d, consts)
    resid_df.to_csv(OUTPUT_DIR / 'name_residuals.csv', index=False)
    chart_residual_screen(resid_df, OUTPUT_DIR)
    print(f"  {len(resid_df)} names processed")
    
    # 3. Rolling betas
    print("\n[3/3] Computing rolling betas...")
    drivers_to_track = [d for d in ['CL', 'NG', 'SPX', 'TNX'] if d in driver_returns_d.columns]
    rolling_betas = compute_rolling_betas(baskets_d, driver_returns_d, drivers_to_track)
    print(f"  {len(rolling_betas)} drivers tracked")
    
    regime_df = detect_regime_changes(rolling_betas, threshold=2.0)
    if len(regime_df):
        regime_df.to_csv(OUTPUT_DIR / 'regime_change_alerts.csv', index=False)
        print(f"  {len(regime_df)} basket-driver pairs flagged for regime change")
        print(regime_df.to_string(index=False))
    else:
        print("  No regime changes flagged")
    
    rolling_paths = chart_rolling_betas(rolling_betas, OUTPUT_DIR)
    
    # Report
    print("\nBuilding HTML report...")
    report = build_report(pairs_df, resid_df, regime_df, rolling_paths, OUTPUT_DIR)
    
    print(f"\n[done] outputs in: {OUTPUT_DIR.resolve()}")
    print(f"       open {report}")

if __name__ == '__main__':
    main()
