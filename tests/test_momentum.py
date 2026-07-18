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
