"""Momentum factor tests — the 12-1 algebra + no peek into the future.

Layered:
  1. Algebra:  hand-computed momentum matches the formula on a tiny panel.
  2. No-peek: momentum at date t only uses close[<=t-skip_days].
  3. Empty panel: degenerate cases don't raise.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from signal_engine.factors.momentum import MomentumConfig, compute_momentum


def _line_panel(ticker: str, start: date, days: int, *, growth_per_day: float) -> pl.DataFrame:
    """A deterministic price series: close[i] = 100 * (1+g)^i. With g=0.01,
    a 5-day return = (1.01)^5 - 1 ≈ 0.0510. Easy to assert."""
    rows = []
    for i in range(days):
        rows.append({
            "ticker": ticker,
            "date": start + timedelta(days=i),
            "close": 100.0 * (1.0 + growth_per_day) ** i,
        })
    return pl.DataFrame(rows)


def test_momentum_formula_on_geometric_series() -> None:
    """For close[i] = 100*(1+g)^i, momentum at t with lookback=L, skip=S
    must equal (1+g)^(L-S) - 1, exactly."""
    g = 0.01
    L, S = 10, 3
    panel = _line_panel("A", date(2024, 1, 1), days=30, growth_per_day=g)
    mom = compute_momentum(panel, MomentumConfig(lookback_days=L, skip_days=S))
    # First valid row is at index L (need close[L-S] and close[L-L]=close[0]).
    first = mom.row(0, named=True)
    expected = (1 + g) ** (L - S) - 1
    assert first["momentum"] == pytest.approx(expected, rel=1e-12)


def test_momentum_at_t_uses_only_past_prices() -> None:
    """Inject a synthetic 'future spike' into prices AFTER date t and confirm
    momentum at t is unchanged. This is the no-peek contract."""
    g = 0.005
    panel = _line_panel("A", date(2024, 1, 1), days=60, growth_per_day=g)
    cfg = MomentumConfig(lookback_days=20, skip_days=5)
    mom_a = compute_momentum(panel, cfg)

    # Overwrite every close AFTER 2024-02-01 with garbage. Momentum values
    # for dates <= 2024-02-01 must be identical.
    cutoff = date(2024, 2, 1)
    poisoned = panel.with_columns(
        pl.when(pl.col("date") > cutoff)
        .then(pl.lit(99999.0))
        .otherwise(pl.col("close"))
        .alias("close")
    )
    mom_b = compute_momentum(poisoned, cfg)

    # Compare on dates <= cutoff
    a_pre = mom_a.filter(pl.col("date") <= cutoff)
    b_pre = mom_b.filter(pl.col("date") <= cutoff)
    assert a_pre.equals(b_pre), "future prices must not affect past momentum"


def test_empty_panel_returns_empty_frame() -> None:
    empty = pl.DataFrame(schema={"ticker": pl.Utf8, "date": pl.Date, "close": pl.Float64})
    mom = compute_momentum(empty)
    assert mom.height == 0
    assert set(mom.columns) == {"ticker", "date", "momentum"}


def test_short_panel_below_lookback_returns_empty() -> None:
    """If the panel has fewer rows than lookback_days, no momentum can be
    computed."""
    panel = _line_panel("A", date(2024, 1, 1), days=10, growth_per_day=0.01)
    mom = compute_momentum(panel, MomentumConfig(lookback_days=20, skip_days=5))
    assert mom.height == 0


def test_rejects_missing_required_columns() -> None:
    bad = pl.DataFrame({"ticker": ["A"], "date": [date(2024, 1, 1)]})
    with pytest.raises(ValueError, match="missing columns"):
        compute_momentum(bad)


# ---------- total-return (dividend-adjusted) momentum ----------

def _flat_panel(ticker: str, start: date, days: int, close: float = 100.0) -> pl.DataFrame:
    return pl.DataFrame([
        {"ticker": ticker, "date": start + timedelta(days=i), "close": close}
        for i in range(days)
    ])


def test_total_return_counts_dividend_inside_window() -> None:
    """Flat price + one dividend inside the [t-L, t-S] window: price momentum
    is 0, total-return momentum is exactly div/close."""
    L, S = 20, 5
    start = date(2024, 1, 1)
    panel = _flat_panel("A", start, days=30)
    div = pl.DataFrame([{"ticker": "A", "date": start + timedelta(days=10), "value": 1.0}])

    raw = compute_momentum(panel, MomentumConfig(lookback_days=L, skip_days=S))
    tr = compute_momentum(panel, MomentumConfig(lookback_days=L, skip_days=S), dividends=div)

    last_raw = raw.row(raw.height - 1, named=True)
    last_tr = tr.row(tr.height - 1, named=True)
    assert last_raw["momentum"] == pytest.approx(0.0)
    # index[t-S] carries the 1% dividend bump; index[t-L] predates it.
    assert last_tr["momentum"] == pytest.approx(0.01)


def test_total_return_ignores_dividend_in_skip_window() -> None:
    """A dividend AFTER t-S (inside the skipped month) must not move 12-1
    momentum — the window ends at t-S."""
    L, S = 20, 5
    start = date(2024, 1, 1)
    panel = _flat_panel("A", start, days=30)
    # Last row is day 29; skip anchor is day 24. Dividend on day 27.
    div = pl.DataFrame([{"ticker": "A", "date": start + timedelta(days=27), "value": 1.0}])
    tr = compute_momentum(panel, MomentumConfig(lookback_days=L, skip_days=S), dividends=div)
    assert tr.row(tr.height - 1, named=True)["momentum"] == pytest.approx(0.0)


def test_total_return_equals_price_momentum_without_dividends() -> None:
    """dividends=None and an empty dividends frame both reproduce raw price
    momentum exactly."""
    panel = _line_panel("A", date(2024, 1, 1), days=30, growth_per_day=0.01)
    cfg = MomentumConfig(lookback_days=10, skip_days=3)
    base = compute_momentum(panel, cfg)
    with_none = compute_momentum(panel, cfg, dividends=None)
    with_empty = compute_momentum(
        panel, cfg,
        dividends=pl.DataFrame(schema={"ticker": pl.Utf8, "date": pl.Date, "value": pl.Float64}),
    )
    for variant in (with_none, with_empty):
        joined = base.join(variant, on=["ticker", "date"], suffix="_v")
        assert joined.height == base.height
        diffs = (joined["momentum"] - joined["momentum_v"]).abs()
        assert float(diffs.max()) < 1e-12


def test_momentum_as_of_survives_weekday_only_data(tmp_con) -> None:
    """Real markets trade ~5/7 of calendar days. momentum_as_of's panel
    window must still contain >= lookback_days TRADING rows, or the 252-row
    shift never fills and every name silently gets null momentum.
    Regression for the calendar-vs-trading-day window bug found on live data."""
    from signal_engine.factors.momentum import momentum_as_of
    from tests.factor_fixtures import insert_price

    as_of = date(2026, 7, 17)
    d = as_of - timedelta(days=560)  # ~400 weekdays of history
    n = 0
    while d <= as_of:
        if d.weekday() < 5:  # weekdays only, like a real exchange calendar
            insert_price(tmp_con, ticker="AAA", d=d, close=100.0 + n * 0.1)
            n += 1
        d += timedelta(days=1)
    assert n >= 300  # sanity: enough trading rows for the 252-day lookback

    mom = momentum_as_of(tmp_con, as_of, tickers=["AAA"], total_return=False)
    assert mom.height == 1, "momentum must exist at as_of on weekday-only data"
    assert mom.row(0, named=True)["momentum"] > 0  # rising series
