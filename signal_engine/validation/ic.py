"""Forward Information Coefficient (IC) — Spearman rank correlation
between a signal at date t and realized forward returns from t+1.

Convention (constraint: no formation-day return):
  * Signal known at close t                  -> value(t)
  * Forward H-day return measured t+1..t+H+1 -> ret_fwd(t, H) = close[t+H+1] / close[t+1] - 1
  * IC(t, H) = spearmanr(value(t), ret_fwd(t, H))  cross-sectionally
  * IC summary = mean / std / t-stat over all t in the panel.

The harness operates on a tidy "long" panel:
  signals_df: ticker, date, value
  prices_df:  ticker, date, close
Both must already be filtered to the universe + as-of-date you care about
— this module does no PIT enforcement itself. Use signal_engine.data.store
as_of_* helpers to construct the inputs.

Walk-forward driver lives in signal_engine.validation.backtest (later step).
This file is the IC primitive that the driver calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import polars as pl
from scipy.stats import spearmanr

DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 21, 63)


@dataclass(frozen=True)
class IcSummary:
    horizon: int
    n_dates: int           # number of date cross-sections that had valid IC
    mean: float
    std: float
    t_stat: float          # mean / (std / sqrt(n))
    hit_rate: float        # fraction of dates with IC > 0
    sample_size_mean: float  # average names per cross-section

    def __str__(self) -> str:
        return (
            f"H={self.horizon:>3}d  n={self.n_dates:>4}  "
            f"IC={self.mean:+.4f}  std={self.std:.4f}  "
            f"t={self.t_stat:+.2f}  hit={self.hit_rate:.2f}  "
            f"avg_xs_size={self.sample_size_mean:.0f}"
        )


def forward_returns(prices: pl.DataFrame, horizon: int) -> pl.DataFrame:
    """Per-ticker forward H-day return from t+1 to t+H+1.

    Signal known at close t => entry at close t+1 => exit at close t+H+1.
    This shifts by -(horizon+1) and -1 against the t row so the row at
    date t carries the forward return that an entry-at-t+1 trader would
    realize.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    return (
        prices.sort(["ticker", "date"])
        .with_columns([
            pl.col("close").shift(-1).over("ticker").alias("_entry"),
            pl.col("close").shift(-(horizon + 1)).over("ticker").alias("_exit"),
        ])
        .with_columns(
            (pl.col("_exit") / pl.col("_entry") - 1.0).alias(f"fwd_{horizon}d")
        )
        .select(["ticker", "date", f"fwd_{horizon}d"])
        .drop_nulls(f"fwd_{horizon}d")
    )


def daily_ic(
    signals: pl.DataFrame,
    prices: pl.DataFrame,
    horizon: int,
    *,
    value_col: str = "value",
    min_cross_section: int = 20,
) -> pl.DataFrame:
    """Cross-sectional Spearman IC per date for one horizon.

    signals: ticker, date, <value_col>
    prices : ticker, date, close

    Returns: date, ic, n
    """
    fwd_col = f"fwd_{horizon}d"
    fwd = forward_returns(prices, horizon)
    joined = signals.join(fwd, on=["ticker", "date"], how="inner").select(
        ["ticker", "date", value_col, fwd_col]
    )
    if joined.height == 0:
        return pl.DataFrame(schema={"date": pl.Date, "ic": pl.Float64, "n": pl.Int64})

    out_dates: list = []
    ics: list[float] = []
    ns: list[int] = []
    # group_by then iterate — small panels per date so the python loop is fine.
    for (d,), sub in joined.group_by(["date"], maintain_order=True):
        if sub.height < min_cross_section:
            continue
        v = sub[value_col].to_numpy()
        f = sub[fwd_col].to_numpy()
        # Drop pairs with NaN/inf — spearmanr otherwise returns NaN silently.
        mask = np.isfinite(v) & np.isfinite(f)
        if mask.sum() < min_cross_section:
            continue
        rho, _ = spearmanr(v[mask], f[mask])
        if not np.isfinite(rho):
            continue
        out_dates.append(d)
        ics.append(float(rho))
        ns.append(int(mask.sum()))

    return pl.DataFrame({"date": out_dates, "ic": ics, "n": ns})


def summarize(ic_daily: pl.DataFrame, horizon: int) -> IcSummary:
    """Time-series stats on a per-date IC series."""
    if ic_daily.height == 0:
        return IcSummary(horizon, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    arr = ic_daily["ic"].to_numpy()
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    t = mean / (std / np.sqrt(arr.size)) if std > 0 else 0.0
    n_mean_raw = ic_daily["n"].mean()
    n_mean = float(n_mean_raw) if isinstance(n_mean_raw, (int, float)) else 0.0
    return IcSummary(
        horizon=horizon,
        n_dates=int(ic_daily.height),
        mean=mean,
        std=std,
        t_stat=float(t),
        hit_rate=float((arr > 0).mean()),
        sample_size_mean=n_mean,
    )


def ic_scorecard(
    signals: pl.DataFrame,
    prices: pl.DataFrame,
    *,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    value_col: str = "value",
    min_cross_section: int = 20,
) -> list[IcSummary]:
    """Convenience: run daily_ic + summarize across multiple horizons."""
    return [
        summarize(
            daily_ic(signals, prices, h, value_col=value_col, min_cross_section=min_cross_section),
            horizon=h,
        )
        for h in horizons
    ]
