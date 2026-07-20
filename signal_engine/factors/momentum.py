"""Momentum factor - 12-1 month price return, skipping the most recent month.

Standard small-cap momentum convention:

  mom_12_1(t) = price[t - 21] / price[t - 252] - 1

The 21-day skip avoids contamination from short-term reversal, which is
particularly strong in small caps. The 252-day window captures the
intermediate-term trend.

Total-return: when a `dividends` frame is supplied (ticker, date, value =
cash per share on ex-date), momentum is computed on a per-ticker
total-return index - daily gross return (close_t + div_t) / close_{t-1},
cumulated - so the ex-date price drop no longer reads as negative
momentum. Without dividends the TR index collapses to the close ratio and
the result is identical to raw price momentum. Closes and yfinance
dividend values are both split-adjusted, so the two series compose.

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
    *,
    dividends: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Compute 12-1 month momentum from a long-form price panel.

    Input columns required: `ticker`, `date`, `close`. Optional `dividends`:
    `ticker`, `date`, `value` (cash per share on ex-date).
    Output columns: `ticker`, `date`, `momentum`.

    Per ticker, momentum at row t = index[t - skip_days] / index[t - lookback_days] - 1,
    where `index` is the close itself (no dividends) or a total-return index
    cumulated from daily gross returns (close_t + div_t) / close_{t-1}.
    A dividend is knowable on its ex-date and affects only that date's
    return, so the value at t uses ONLY data known at t (no peek into the
    future). Rows where either lag is missing (start of series) are dropped.
    """
    required = {"ticker", "date", "close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"prices missing columns: {missing}")

    panel = prices.sort(["ticker", "date"])
    if dividends is not None and dividends.height > 0:
        div = (
            dividends.select(["ticker", "date", "value"])
            .rename({"value": "_div"})
            # One row per (ticker, ex-date) - sum in case of multiple entries.
            .group_by(["ticker", "date"]).agg(pl.col("_div").sum())
        )
        panel = (
            panel.join(div, on=["ticker", "date"], how="left")
            .with_columns(pl.col("_div").fill_null(0.0))
            .with_columns(
                # Daily gross total return; first row per ticker has no prev
                # close -> null -> seeded to 1.0 so cum_prod starts the index.
                ((pl.col("close") + pl.col("_div"))
                 / pl.col("close").shift(1).over("ticker")).alias("_gross")
            )
            .with_columns(
                pl.col("_gross").fill_null(1.0).cum_prod().over("ticker").alias("_index")
            )
        )
    else:
        panel = panel.with_columns(pl.col("close").alias("_index"))

    return (
        panel
        .with_columns([
            pl.col("_index").shift(cfg.skip_days).over("ticker").alias("_skip_anchor"),
            pl.col("_index").shift(cfg.lookback_days).over("ticker").alias("_lookback_anchor"),
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
    total_return: bool = True,
) -> pl.DataFrame:
    """Read prices through the bitemporal API, compute momentum, and return
    only the row for `as_of` (one momentum per ticker).

    The lookback panel is loaded for `as_of - lookback - buffer` through
    `as_of`. We over-pull by `history_buffer_days` to absorb holidays and
    early-listing gaps without losing signals.

    `total_return=True` (default) folds cash dividends from the
    corporate_actions table into the return index via the PIT accessor.
    """
    from signal_engine.data.store import as_of_corporate_actions, as_of_prices

    # lookback_days counts TRADING rows; the panel window is CALENDAR days.
    # ~252 trading days span ~365 calendar days (5/7 week + holidays), so
    # scale by 7/5 before adding the buffer - a plain lookback+buffer window
    # leaves the 252-row shift permanently unfilled on real data.
    calendar_span = int(cfg.lookback_days * 7 / 5) + history_buffer_days
    earliest_needed = as_of - timedelta(days=calendar_span)
    panel = (
        as_of_prices(con, as_of=as_of)
        .filter(pl.col("date") >= earliest_needed)
    )
    if tickers is not None:
        panel = panel.filter(pl.col("ticker").is_in(tickers))

    dividends: pl.DataFrame | None = None
    if total_return:
        dividends = (
            as_of_corporate_actions(con, as_of=as_of, kind="dividend")
            .filter(pl.col("date") >= earliest_needed)
        )
        if tickers is not None:
            dividends = dividends.filter(pl.col("ticker").is_in(tickers))

    mom = compute_momentum(panel, cfg, dividends=dividends)
    return mom.filter(pl.col("date") == as_of)
