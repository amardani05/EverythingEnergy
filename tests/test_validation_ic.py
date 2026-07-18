"""IC harness tests — algebra + leakage canary.

The leakage canary is THE most important test in this file: it injects a
synthetic feature whose value at t equals the realized forward H-day
return. An honest IC harness MUST flag that feature with IC near 1.0 at
horizon H. If it doesn't, the PIT plumbing in validation/ic.py has a hole.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from signal_engine.validation.ic import (
    daily_ic,
    forward_returns,
    ic_scorecard,
    summarize,
)
from tests.test_leakage import FutureLeakSynthetic, make_toy_prices


def test_forward_returns_no_formation_day() -> None:
    """forward_returns at row t must equal close[t+horizon+1]/close[t+1]-1,
    NEVER including the t->t+1 return."""
    rows = [{"ticker": "A", "date": date(2024, 1, i + 1), "close": float(100 + i)}
            for i in range(10)]
    prices = pl.DataFrame(rows)
    fwd = forward_returns(prices, horizon=3).sort("date")
    # At t=0 (close=100): entry close=101 (t+1), exit close=104 (t+4).
    # fwd_3d = 104/101 - 1 ≈ 0.02970
    row0 = fwd.row(0, named=True)
    assert row0["fwd_3d"] == pytest.approx(104 / 101 - 1)


def test_ic_harness_flags_future_leak_at_matching_horizon() -> None:
    """A feature whose value at t equals the realized 5-day return starting
    at t should produce an absurdly-high IC at horizon=5. The harness
    enforces "no formation-day return" (entry at t+1, exit at t+H+1), so
    the leak and the forward return are 1-day misaligned — they correlate
    near 0.78, not 1.00, on a 50-ticker noisy panel. That's still ~10x
    higher than any real signal achieves; the canary's job is to make
    sure IC blows up obviously, not to match a specific number.

    If this test ever produces IC < 0.4, the PIT plumbing has changed and
    the harness has gone blind to leakage."""
    prices = make_toy_prices(
        tickers=[f"T{i:02d}" for i in range(50)],
        start=date(2024, 1, 1),
        days=200,
        seed=7,
    )
    leak = FutureLeakSynthetic(horizon_days=5).build(prices).rename({"leak_value": "value"})

    ic5 = daily_ic(leak, prices, horizon=5, min_cross_section=10)
    summary5 = summarize(ic5, horizon=5)
    assert summary5.mean > 0.5, (
        f"leak synthetic must produce absurdly high IC at H=5 "
        f"(real signals run 0.01-0.05); got mean={summary5.mean:.4f} "
        f"(n_dates={summary5.n_dates})"
    )
    assert summary5.n_dates > 0


def test_ic_harness_not_fooled_by_random_feature() -> None:
    """A pure-noise feature must produce IC mean near zero."""
    np.random.seed(0)
    tickers = [f"T{i:02d}" for i in range(40)]
    prices = make_toy_prices(tickers, start=date(2024, 1, 1), days=200, seed=11)

    # Random signal independent of returns
    noise = (
        prices.select(["ticker", "date"])
        .with_columns(pl.Series("value", np.random.randn(prices.height)))
    )
    summaries = ic_scorecard(noise, prices, horizons=[1, 5, 21], min_cross_section=10)
    for s in summaries:
        # 95% CI for IC mean on noise is roughly ±2/sqrt(n_dates); for ~200
        # dates that's ±0.14. Use a generous threshold.
        assert abs(s.mean) < 0.05, f"noise IC mean too large at H={s.horizon}: {s.mean}"


def test_ic_summary_empty_panel() -> None:
    empty = pl.DataFrame(schema={"date": pl.Date, "ic": pl.Float64, "n": pl.Int64})
    s = summarize(empty, horizon=21)
    assert s.n_dates == 0
    assert s.mean == 0.0 and s.std == 0.0 and s.t_stat == 0.0
