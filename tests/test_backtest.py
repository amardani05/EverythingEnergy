"""Backtester mechanics + the portfolio-level poisoned-signal canary."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from signal_engine.validation.backtest import walk_forward_ls


def _world(n_days: int = 260, n_names: int = 40, seed: int = 5,
           alpha_frac: float = 0.0) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Synthetic prices + a signal. With alpha_frac > 0, the signal is a
    noisy peek at each name's NEXT-period drift, so higher signal = higher
    forward return by construction."""
    rng = np.random.default_rng(seed)
    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(n_days)]
    tickers = [f"T{i:03d}" for i in range(n_names)]
    drift = rng.normal(0.0, 0.002, size=(n_names, n_days))
    px = 100.0 * np.cumprod(1.0 + drift, axis=1)

    price_rows = [
        {"ticker": tickers[i], "date": days[d], "close": float(px[i, d])}
        for i in range(n_names) for d in range(n_days)
    ]
    sig_rows = []
    horizon = 6  # matches rebalance_every=5 plus entry shift
    for i in range(n_names):
        for d in range(n_days - horizon - 1):
            fwd = px[i, d + horizon] / px[i, d + 1] - 1.0
            noise = rng.normal(0.0, 0.01)
            sig_rows.append({
                "ticker": tickers[i], "date": days[d],
                "value": alpha_frac * fwd + (1.0 - alpha_frac) * noise,
            })
    return pl.DataFrame(sig_rows), pl.DataFrame(price_rows)


def test_poisoned_signal_produces_absurd_sharpe() -> None:
    """THE canary: a signal that peeks at forward returns must light the
    harness up. If a leak ever reaches the backtester, this is what it
    looks like; anything resembling this Sharpe in real life is a bug."""
    sig, px = _world(alpha_frac=1.0)
    res = walk_forward_ls(sig, px, rebalance_every=5, n_quantiles=5,
                          cost_bps=60.0, min_names=20)
    assert res.summary["sharpe_net"] > 3.0
    # Monotone quantiles: top bucket must beat bottom by construction.
    q = res.quantile_means
    top = q.filter(pl.col("quantile") == 5)["mean_period_ret"][0]
    bot = q.filter(pl.col("quantile") == 1)["mean_period_ret"][0]
    assert top > bot


def test_noise_signal_is_flat_after_costs() -> None:
    sig, px = _world(alpha_frac=0.0)
    res = walk_forward_ls(sig, px, rebalance_every=5, n_quantiles=5,
                          cost_bps=60.0, min_names=20)
    assert abs(res.summary["sharpe_gross"]) < 2.0
    # Random ranking implies heavy churn, so costs must bite.
    assert res.summary["sharpe_net"] < res.summary["sharpe_gross"]
    assert res.summary["avg_turnover"] > 0.5


def test_cost_drag_scales_with_cost_bps() -> None:
    sig, px = _world(alpha_frac=1.0)
    cheap = walk_forward_ls(sig, px, cost_bps=0.0, min_names=20)
    dear = walk_forward_ls(sig, px, cost_bps=200.0, min_names=20)
    assert cheap.summary["ann_ret_net"] > dear.summary["ann_ret_net"]
    assert cheap.summary["ann_ret_gross"] == pytest.approx(
        dear.summary["ann_ret_gross"], rel=1e-12)


def test_static_signal_has_near_zero_turnover_after_entry() -> None:
    """A constant ranking should trade once and then sit still."""
    _, px = _world()
    tickers = sorted(px["ticker"].unique().to_list())
    dates = sorted(px["date"].unique().to_list())
    sig = pl.DataFrame([
        {"ticker": t, "date": d, "value": float(i)}
        for i, t in enumerate(tickers) for d in dates
    ])
    res = walk_forward_ls(sig, px, rebalance_every=5, cost_bps=60.0, min_names=20)
    period = res.period_returns
    assert period["turnover"][0] == pytest.approx(1.0)      # full entry
    assert float(period["turnover"][1:].max()) == pytest.approx(0.0)


def test_insufficient_breadth_raises() -> None:
    sig, px = _world(n_names=5)
    with pytest.raises(ValueError):
        walk_forward_ls(sig, px, min_names=30)
