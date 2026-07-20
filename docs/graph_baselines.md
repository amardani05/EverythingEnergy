# Graph-propagation baseline (roadmap 4.8a)

*Generated 2026-07-19 on `36bb174`. Universe: energy taxonomy (205 names with prices, 23 baskets, 25 directed flow edges). Signal lookback 21d. Spearman IC, forward return t+1 -> t+H+1. Read t (NW).*

The control row matters: neighbor signals must beat own-basket momentum to prove the GRAPH is adding information beyond sector co-movement.

### graph/neigh_up (suppliers' trailing ret)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 2123 | -0.0081 | 0.2828 | -1.32 | -1.33 | 0.49 | 119 |
| 5d | 2119 | -0.0044 | 0.2829 | -0.71 | -0.40 | 0.49 | 119 |
| 21d | 2103 | -0.0007 | 0.2885 | -0.12 | -0.04 | 0.52 | 119 |
| 63d | 2061 | -0.0070 | 0.2853 | -1.12 | -0.34 | 0.51 | 119 |

### graph/neigh_down (customers' trailing ret)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 2123 | +0.0031 | 0.2333 | +0.61 | +0.63 | 0.51 | 82 |
| 5d | 2119 | +0.0014 | 0.2355 | +0.27 | +0.15 | 0.51 | 82 |
| 21d | 2103 | +0.0159 | 0.2262 | +3.22 | +1.03 | 0.54 | 82 |
| 63d | 2061 | +0.0073 | 0.2307 | +1.44 | +0.36 | 0.52 | 82 |

### control/own_basket_mom (same lookback)

| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |
|---|---|---|---|---|---|---|---|
| 1d | 2123 | +0.0020 | 0.2881 | +0.32 | +0.32 | 0.51 | 188 |
| 5d | 2119 | +0.0033 | 0.2877 | +0.53 | +0.29 | 0.51 | 188 |
| 21d | 2103 | +0.0069 | 0.2884 | +1.09 | +0.36 | 0.50 | 188 |
| 63d | 2061 | -0.0251 | 0.2813 | -4.05 | -1.07 | 0.48 | 188 |

