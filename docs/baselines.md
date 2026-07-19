# Baseline IC scorecard

*Generated 2026-07-19 on `143536d`. Spearman rank IC; forward return t+1 -> t+H+1 (no formation-day return). Universe: latest IJR snapshot (602 names, `ijr_current` — survivorship-biased by construction).*

*Data watermarks: prices through 2026-07-17, edgar_facts = 1,325,958 rows.*

**Read `t (NW)`, not `t (iid)`:** overlapping 21/63d horizons make daily ICs autocorrelated; the Newey-West column (lag = horizon) is the honest significance. Momentum is total-return (dividends folded in); SUE includes derived Q4 events (FY - sum(Q1..3), filed at the 10-K). Remaining caveats: no costs, no neutralization — raw single-factor IC only; `ijr_current` survivorship bias.

## Takeaways (revised 2026-07-19 with NW t-stats, TR momentum, Q4 SUE)

The honest-significance pass changes the ranking from the 2026-07-17 baseline:

1. **PEAD/SUE — still the strongest signal, now honestly significant.** The iid t of ~9 was inflated as suspected, but NW keeps it at 2.5–3.0 across 5–63d with the same textbook monotone IC (+0.02 → +0.12) and 0.76 hit rate at 21d. Q4-derived events are now included. Low breadth remains (29–42 names/day).
2. **Value/EV-EBITDA — the most robust slow signal.** +0.0395 IC at 63d with NW t 2.5 (barely different from iid because the grid is monthly — little overlap). Hit rate 0.67. FCF yield stays dead; the config's `components: [ev_ebitda]` choice stands.
3. **Momentum 12-1 — downgraded: a short-horizon signal only.** Total-return momentum keeps 1d significance (NW t 2.7) but the 21/63d "consistency" in the previous baseline was an overlapping-window artifact: NW t collapses to 0.4–0.5. Keep it in the composite for its diversification (corr with pead +0.23, value −0.12) and short-horizon edge, but don't lean on it for slow rebalances.

**Still noise** (NW confirms): FCF yield, ROIC, accruals, margin stability. Revisit after sector-neutralized IC (the composite now z-scores within sector; re-running this scorecard on neutralized components is the natural Phase 2 diagnostic).

**Composite implication:** the enabled set (momentum + ev_ebitda + pead, equal weights) remains defensible on diversification grounds, but a Phase 2 backtest at weekly/monthly rebalance should expect its edge to come mostly from PEAD and EV/EBITDA.

### momentum (12-1 total-return, daily)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 1892 | +0.0122 | 0.1942 | +2.73 | +2.70 | 0.55 | 562 |
| 5d | 1888 | +0.0104 | 0.1844 | +2.46 | +1.36 | 0.55 | 562 |
| 21d | 1872 | +0.0059 | 0.1663 | +1.54 | +0.43 | 0.56 | 562 |
| 63d | 1830 | +0.0103 | 0.1426 | +3.10 | +0.52 | 0.56 | 561 |

### value/fcf_yield (monthly)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 90 | -0.0050 | 0.1215 | -0.39 | -0.41 | 0.41 | 378 |
| 5d | 90 | +0.0056 | 0.1296 | +0.41 | +0.42 | 0.51 | 378 |
| 21d | 89 | +0.0019 | 0.1080 | +0.17 | +0.17 | 0.49 | 377 |
| 63d | 87 | -0.0029 | 0.1047 | -0.26 | -0.30 | 0.43 | 376 |

### value/ev_ebitda_flipped (monthly)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 90 | +0.0061 | 0.1602 | +0.36 | +0.37 | 0.46 | 284 |
| 5d | 90 | +0.0257 | 0.1716 | +1.42 | +1.43 | 0.52 | 284 |
| 21d | 89 | +0.0249 | 0.1495 | +1.57 | +1.82 | 0.53 | 283 |
| 63d | 87 | +0.0395 | 0.1494 | +2.47 | +2.51 | 0.67 | 281 |

### quality/roic (monthly)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 90 | -0.0133 | 0.0948 | -1.33 | -1.40 | 0.43 | 394 |
| 5d | 90 | -0.0046 | 0.0970 | -0.45 | -0.49 | 0.51 | 394 |
| 21d | 89 | +0.0042 | 0.0929 | +0.42 | +0.49 | 0.48 | 394 |
| 63d | 87 | +0.0008 | 0.0881 | +0.08 | +0.06 | 0.48 | 392 |

### quality/accruals_flipped (monthly)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 90 | +0.0131 | 0.0826 | +1.50 | +1.52 | 0.52 | 448 |
| 5d | 90 | +0.0049 | 0.0781 | +0.59 | +0.68 | 0.53 | 448 |
| 21d | 89 | -0.0011 | 0.0771 | -0.13 | -0.20 | 0.44 | 447 |
| 63d | 87 | -0.0046 | 0.0692 | -0.62 | -0.44 | 0.45 | 446 |

### quality/margin_stability (monthly)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 90 | -0.0083 | 0.1092 | -0.72 | -0.70 | 0.48 | 377 |
| 5d | 90 | -0.0128 | 0.1039 | -1.17 | -1.76 | 0.37 | 377 |
| 21d | 89 | -0.0015 | 0.1014 | -0.14 | -0.19 | 0.45 | 376 |
| 63d | 87 | -0.0033 | 0.0987 | -0.31 | -0.75 | 0.48 | 375 |

### pead/sue (events broadcast 63d)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 230 | +0.0222 | 0.1909 | +1.76 | +1.78 | 0.59 | 42 |
| 5d | 226 | +0.0502 | 0.1762 | +4.28 | +2.45 | 0.65 | 41 |
| 21d | 210 | +0.1039 | 0.1737 | +8.67 | +2.67 | 0.76 | 37 |
| 63d | 178 | +0.1227 | 0.1745 | +9.38 | +2.97 | 0.67 | 29 |

