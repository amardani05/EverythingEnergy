"""Momentum factor — 12-1 month price return, skipping the most recent month.

Standard small-cap momentum convention:

  mom_12_1(t) = price[t - 21] / price[t - 252] - 1

The 21-day skip avoids contamination from short-term reversal, which is
particularly strong in small caps. The 252-day window captures the
intermediate-term trend.

Caveat for v1: we use raw close-to-close return, not total return.
yfinance is stored with `auto_adjust=False`, so close is split-adjusted but
NOT dividend-adjusted. For 12-1 momentum the bias is small (one year of
dividend yield ≈ 1-3% across the cross-section, mostly orthogonal to the
signal), but it IS a bias. Plan: when we ingest the yfinance dividend
action stream, swap to dividend-adjusted return. The factor signature
won't change.

Returns a tidy `(ticker, date, momentum)` frame. Cross-sectional
neutralization happens in scoring/, not here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import polars as pl

log = logging.getLogger(__name__)


# Standard small-cap convention. The leading 21-day skip is the key thing
# that distinguishes "intermediate momentum" from "short-term reversal".
LOOKBACK_DAYS = 252       # ~12 months of trading days
SKIP_DAYS = 21            # ~1 month of trading days


@dataclass(frozen=True)
class MomentumConfig:
    lookback_days: int = LOOKBACK_DAYS
    skip_days: int = SKIP_DAYS

    def __post_init__(self) -> None:
        if self.skip_days >= self.lookback_days:
            raise ValueError("skip_days must be strictly less than lookback_days")


def compute_momentum(
    prices: pl.DataFrame,
    cfg: MomentumConfig = MomentumConfig(),
) -> pl.DataFrame:
    """Compute 12-1 month momentum from a long-form price panel.

    Input columns required: `ticker`, `date`, `close`.
    Output columns: `ticker`, `date`, `momentum`.

    Per ticker, momentum at row t = close[t - skip_days] / close[t - lookback_days] - 1.

    The "as-of-t" feature is the price L days back relative to S days back —
    so the value at t uses ONLY data known at t (no peek into the future).
    Rows where either lag is missing (start of series) are dropped.
    """
    required = {"ticker", "date", "close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"prices missing columns: {missing}")

    return (
        prices
        .sort(["ticker", "date"])
        .with_columns([
            pl.col("close").shift(cfg.skip_days).over("ticker").alias("_skip_anchor"),
            pl.col("close").shift(cfg.lookback_days).over("ticker").alias("_lookback_anchor"),
        ])
        .with_columns(
            (pl.col("_skip_anchor") / pl.col("_lookback_anchor") - 1.0).alias("momentum")
        )
        .select(["ticker", "date", "momentum"])
        .drop_nulls("momentum")
    )


def momentum_as_of(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    *,
    tickers: list[str] | None = None,
    cfg: MomentumConfig = MomentumConfig(),
    history_buffer_days: int = 50,
) -> pl.DataFrame:
    """Read prices through the bitemporal API, compute momentum, and return
    only the row for `as_of` (one momentum per ticker).

    The lookback panel is loaded for `as_of - lookback - buffer` through
    `as_of`. We over-pull by `history_buffer_days` to absorb holidays and
    early-listing gaps without losing signals.
    """
    from signal_engine.data.store import as_of_prices

    earliest_needed = as_of - timedelta(days=cfg.lookback_days + history_buffer_days)
    panel = (
        as_of_prices(con, as_of=as_of)
        .filter(pl.col("date") >= earliest_needed)
    )
    if tickers is not None:
        panel = panel.filter(pl.col("ticker").is_in(tickers))

    mom = compute_momentum(panel, cfg)
    return mom.filter(pl.col("date") == as_of)
