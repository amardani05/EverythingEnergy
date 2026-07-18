# Baseline IC scorecard

*Generated 2026-07-17 on `02124ca`. Spearman rank IC; forward return t+1 -> t+H+1 (no formation-day return). Universe: latest IJR snapshot (602 names, `ijr_current` — survivorship-biased by construction).*

*Data watermarks: prices through 2026-07-17, edgar_facts = 1,325,958 rows.*

**Caveats (roadmap Phase 1 fixes):** t-stats assume iid daily ICs — overlapping 21/63d horizons overstate |t| until Newey-West lands; momentum uses non-div-adjusted closes; SUE currently skips Q4 events; no costs, no neutralization — raw single-factor IC only.

## Takeaways

Ranked by strength of the raw, un-neutralized signal. "Does anything predict anything?" — yes, three things do, and four don't.

1. **PEAD/SUE — strong, and textbook-shaped.** IC rises monotonically with horizon (+0.022 → +0.123 from 1d to 63d), hit rate up to 0.76 at 21d. This is the classic post-earnings-announcement-drift fingerprint. The iid t≈9 is *overstated* (the 63d broadcast makes daily ICs heavily autocorrelated — Newey-West will cut it), but the magnitude and monotonic shape are real. Caveat: low breadth (29–42 names/day, only on earnings events) and Q4 events are still missing.
2. **Value/EV-EBITDA — real at longer horizons.** Cheapness builds with horizon to +0.040 IC / hit 0.67 at 63d (t≈2.5). The signal is in EV/EBITDA, **not** FCF yield — worth weighting accordingly in the composite.
3. **Momentum 12-1 — modest but consistent.** +0.008 to +0.014 IC, positive and significant at every horizon, hit rate 0.55–0.57. The expected small-cap momentum premium; should firm up once returns are dividend-adjusted.

**Noise in this universe/window** (leave out of the composite until neutralization/interaction terms are tried): FCF yield (~0), ROIC (~0), accruals (weak, decays negative), margin stability (slightly negative). Their weakness may be small-cap-specific or masked by sector tilts — revisit after sector-neutralization lands in Phase 1.

**Design implication for Phase 1:** the composite should lean on PEAD + EV/EBITDA + momentum; don't equal-weight all six raw factors as `config.yaml` currently implies. Re-run this scorecard after sector-neutralization and dividend-adjustment to see which of the "noise" factors were being masked.

### momentum (12-1, daily)

| horizon | n dates | mean IC | std | t (iid) | hit rate | avg breadth |
|---|---|---|---|---|---|---|
| 1d | 1892 | +0.0125 | 0.1939 | +2.80 | 0.55 | 562 |
| 5d | 1888 | +0.0114 | 0.1841 | +2.69 | 0.55 | 562 |
| 21d | 1872 | +0.0082 | 0.1657 | +2.13 | 0.57 | 562 |
| 63d | 1830 | +0.0138 | 0.1420 | +4.16 | 0.57 | 561 |

### value/fcf_yield (monthly)

| horizon | n dates | mean IC | std | t (iid) | hit rate | avg breadth |
|---|---|---|---|---|---|---|
| 1d | 90 | -0.0050 | 0.1215 | -0.39 | 0.41 | 378 |
| 5d | 90 | +0.0056 | 0.1296 | +0.41 | 0.51 | 378 |
| 21d | 89 | +0.0019 | 0.1080 | +0.17 | 0.49 | 377 |
| 63d | 87 | -0.0029 | 0.1047 | -0.26 | 0.43 | 376 |

### value/ev_ebitda_flipped (monthly)

| horizon | n dates | mean IC | std | t (iid) | hit rate | avg breadth |
|---|---|---|---|---|---|---|
| 1d | 90 | +0.0061 | 0.1602 | +0.36 | 0.46 | 284 |
| 5d | 90 | +0.0257 | 0.1716 | +1.42 | 0.52 | 284 |
| 21d | 89 | +0.0249 | 0.1495 | +1.57 | 0.53 | 283 |
| 63d | 87 | +0.0395 | 0.1494 | +2.47 | 0.67 | 281 |

### quality/roic (monthly)

| horizon | n dates | mean IC | std | t (iid) | hit rate | avg breadth |
|---|---|---|---|---|---|---|
| 1d | 90 | -0.0133 | 0.0948 | -1.33 | 0.43 | 394 |
| 5d | 90 | -0.0046 | 0.0970 | -0.45 | 0.51 | 394 |
| 21d | 89 | +0.0042 | 0.0929 | +0.42 | 0.48 | 394 |
| 63d | 87 | +0.0008 | 0.0881 | +0.08 | 0.48 | 392 |

### quality/accruals_flipped (monthly)

| horizon | n dates | mean IC | std | t (iid) | hit rate | avg breadth |
|---|---|---|---|---|---|---|
| 1d | 90 | +0.0131 | 0.0826 | +1.50 | 0.52 | 448 |
| 5d | 90 | +0.0049 | 0.0781 | +0.59 | 0.53 | 448 |
| 21d | 89 | -0.0011 | 0.0771 | -0.13 | 0.44 | 447 |
| 63d | 87 | -0.0046 | 0.0692 | -0.62 | 0.45 | 446 |

### quality/margin_stability (monthly)

| horizon | n dates | mean IC | std | t (iid) | hit rate | avg breadth |
|---|---|---|---|---|---|---|
| 1d | 90 | -0.0083 | 0.1092 | -0.72 | 0.48 | 377 |
| 5d | 90 | -0.0128 | 0.1039 | -1.17 | 0.37 | 377 |
| 21d | 89 | -0.0015 | 0.1014 | -0.14 | 0.45 | 376 |
| 63d | 87 | -0.0033 | 0.0987 | -0.31 | 0.48 | 375 |

### pead/sue (events broadcast 63d)

| horizon | n dates | mean IC | std | t (iid) | hit rate | avg breadth |
|---|---|---|---|---|---|---|
| 1d | 230 | +0.0222 | 0.1909 | +1.76 | 0.59 | 42 |
| 5d | 226 | +0.0502 | 0.1762 | +4.28 | 0.65 | 41 |
| 21d | 210 | +0.1039 | 0.1737 | +8.67 | 0.76 | 37 |
| 63d | 178 | +0.1227 | 0.1745 | +9.38 | 0.67 | 29 |

