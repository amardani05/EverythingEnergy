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


# ---------- book integrity: the degenerate-join guard ----------

def _drop_prices(px: pl.DataFrame, tickers: list[str], on_date: date) -> pl.DataFrame:
    """Remove specific names' prints on one date (simulates a thin tape)."""
    mask = ~(pl.col("ticker").is_in(tickers) & (pl.col("date") == on_date))
    return px.filter(mask)


def test_missing_prints_are_filled_not_dropped() -> None:
    """A name that simply did not print on the entry date must still be
    priced from its most recent prior close, not silently dropped."""
    sig, px = _world(alpha_frac=1.0)
    dates = sorted(px["date"].unique().to_list())
    entry = dates[6]
    victims = sorted(px["ticker"].unique().to_list())[:15]
    thin = _drop_prices(px, victims, entry)

    full = walk_forward_ls(sig, px, rebalance_every=5, n_quantiles=5,
                           cost_bps=60.0, min_names=20)
    holed = walk_forward_ls(sig, thin, rebalance_every=5, n_quantiles=5,
                            cost_bps=60.0, min_names=20)
    # Same number of periods survive: the hole is bridged, not fatal.
    assert holed.summary["n_periods"] == full.summary["n_periods"]
    assert holed.summary["n_stale_dropped"] == 0


def test_delisted_name_is_dropped_and_counted() -> None:
    """A name whose tape stops entirely (delisting) must leave the book and
    be counted, never carried forward at a stale price as a 0% return."""
    sig, px = _world(alpha_frac=1.0)
    dates = sorted(px["date"].unique().to_list())
    dead = sorted(px["ticker"].unique().to_list())[0]
    cutoff = dates[40]
    px_dead = px.filter(~((pl.col("ticker") == dead) & (pl.col("date") > cutoff)))

    res = walk_forward_ls(sig, px_dead, rebalance_every=5, n_quantiles=5,
                          cost_bps=60.0, min_names=20, max_stale_days=7)
    assert res.summary["n_stale_dropped"] > 0
    # The dead name cannot appear in any traded book after its tape ends.
    assert res.summary["median_n_ranked"] < 40


def test_thin_leg_period_is_skipped_not_reported() -> None:
    """THE regression: if a leg collapses below min_book, the period must be
    skipped rather than reported as a 2-name 'book' return."""
    sig, px = _world(alpha_frac=1.0, n_names=40)
    # min_book of 8 with quintiles on 40 names (8/leg) is satisfiable; raising
    # min_book above the achievable leg size must skip every period.
    ok = walk_forward_ls(sig, px, rebalance_every=5, n_quantiles=5,
                         cost_bps=60.0, min_names=20, min_book=5)
    assert ok.summary["n_periods"] > 0
    assert ok.summary["min_n_long"] >= 5
    assert ok.summary["min_n_short"] >= 5

    with pytest.raises(ValueError):
        walk_forward_ls(sig, px, rebalance_every=5, n_quantiles=5,
                        cost_bps=60.0, min_names=20, min_book=40)


def test_every_emitted_period_respects_min_book() -> None:
    """Invariant across the whole run: no emitted period may hold a leg
    thinner than min_book, and the summary must expose the leg sizes."""
    sig, px = _world(alpha_frac=0.0, n_names=60)
    res = walk_forward_ls(sig, px, rebalance_every=5, n_quantiles=5,
                          cost_bps=60.0, min_names=25, min_book=10)
    p = res.period_returns
    assert float(p["n_long"].min()) >= 10
    assert float(p["n_short"].min()) >= 10
    assert res.summary["min_book"] == 10
    # quantile_means describes the same traded sample as period_returns
    assert res.quantile_means["n_obs"].sum() > 0


def test_thin_breadth_periods_are_skipped_and_counted() -> None:
    """With breadth that varies by date, fat periods trade and thin ones are
    skipped and counted. This is the mixed case the guard exists for: the old
    code would have emitted the thin periods as tiny-book returns."""
    sig, px = _world(alpha_frac=1.0, n_names=60)
    all_dates = sorted(sig["date"].unique().to_list())
    thin_dates = set(all_dates[::2])          # every other signal date starves
    keep = sorted(sig["ticker"].unique().to_list())[:12]
    sig_mixed = sig.filter(
        ~pl.col("date").is_in(list(thin_dates)) | pl.col("ticker").is_in(keep)
    )

    res = walk_forward_ls(sig_mixed, px, rebalance_every=5, n_quantiles=5,
                          cost_bps=60.0, min_names=12, min_book=8)
    assert res.summary["n_periods"] > 0, "fat periods must still trade"
    assert res.summary["n_periods_skipped"] > 0, "thin periods must be skipped"
    # And nothing that survived is a degenerate book.
    assert float(res.period_returns["n_long"].min()) >= 8
    assert float(res.period_returns["n_short"].min()) >= 8


def test_corporate_action_artifact_is_dropped_and_counted() -> None:
    """Regression for the CHRD blowup: a post-reorganization price
    discontinuity (a +280x 'return') must never reach a book. Shorted, it
    produced a -500% period and a -228% max drawdown on the real panel."""
    _, px = _world(alpha_frac=0.0, n_names=40)
    victim = sorted(px["ticker"].unique().to_list())[0]
    dates = sorted(px["date"].unique().to_list())
    # Re-denominate the victim 280x from the midpoint on: exactly the shape
    # of a bankruptcy-emergence relisting in a free-data price series.
    blown = px.with_columns(
        pl.when((pl.col("ticker") == victim) & (pl.col("date") >= dates[100]))
        .then(pl.col("close") * 280.0)
        .otherwise(pl.col("close")).alias("close")
    )

    guarded = walk_forward_ls(blown, blown, rebalance_every=5, n_quantiles=5,
                              cost_bps=60.0, min_names=20, value_col="close",
                              max_abs_period_return=3.0)
    unguarded = walk_forward_ls(blown, blown, rebalance_every=5, n_quantiles=5,
                                cost_bps=60.0, min_names=20, value_col="close",
                                max_abs_period_return=None)

    assert guarded.summary["n_extreme_dropped"] > 0, "artifact must be caught"
    assert unguarded.summary["n_extreme_dropped"] == 0
    # A drawdown worse than -100% is arithmetically impossible for a real
    # unlevered book; the guard must restore sanity.
    assert guarded.summary["max_drawdown"] > -1.0
    assert guarded.summary["ann_vol"] < unguarded.summary["ann_vol"]


def test_guard_leaves_plausible_moves_alone() -> None:
    """The bound must not quietly eat real volatility: a name that doubles
    inside a period is a real energy move and must survive."""
    _, px = _world(alpha_frac=0.0, n_names=40)
    victim = sorted(px["ticker"].unique().to_list())[0]
    dates = sorted(px["date"].unique().to_list())
    doubled = px.with_columns(
        pl.when((pl.col("ticker") == victim) & (pl.col("date") >= dates[100]))
        .then(pl.col("close") * 2.0)
        .otherwise(pl.col("close")).alias("close")
    )
    res = walk_forward_ls(doubled, doubled, rebalance_every=5, n_quantiles=5,
                          cost_bps=60.0, min_names=20, value_col="close",
                          max_abs_period_return=3.0)
    assert res.summary["n_extreme_dropped"] == 0
