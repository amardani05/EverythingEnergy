"""Walk-forward quantile long-short backtester (the module ic.py promised).

Converts a signal panel into portfolio returns under honest mechanics:

  * Rebalance every `rebalance_every` trading dates. At each rebalance the
    cross-section is ranked; top quantile is held long, bottom short,
    equal weight within each book.
  * The signal is known at close t; entry happens at close t+1 and the
    position is held to the NEXT rebalance's entry close (same no
    formation-day-return convention as the IC harness).
  * Costs: every name that enters or leaves a book pays `cost_bps` twice
    per replacement (exit one name + enter its replacement), charged
    against that period's gross. The first period pays full entry.
  * Reported both gross and net; Sharpe carries an iid and a Newey-West
    variant (period returns barely overlap, so they should be close;
    a large gap is itself a red flag).

This module does NO point-in-time enforcement: feed it signal panels built
through the as_of_* readers. The poisoned-signal test in
tests/test_backtest.py proves the harness would light up on a leak.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from signal_engine.validation.ic import newey_west_t

TRADING_DAYS_PER_YEAR = 252


def _f(x: object) -> float:
    """Narrow polars aggregate results (wide union types) to float."""
    if x is None:
        return 0.0
    return float(x)  # type: ignore[arg-type]


@dataclass(frozen=True)
class BacktestResult:
    period_returns: pl.DataFrame   # date, long_ret, short_ret, ls_gross, turnover, cost, ls_net
    quantile_means: pl.DataFrame   # quantile, mean_period_ret, n_obs (monotonicity view)
    summary: dict[str, float]

    def __str__(self) -> str:
        s = self.summary
        return (
            f"periods={s['n_periods']:.0f}  ann_ret_net={s['ann_ret_net']:+.2%}  "
            f"ann_vol={s['ann_vol']:.2%}  sharpe_net={s['sharpe_net']:+.2f}  "
            f"(gross {s['sharpe_gross']:+.2f}, nw_t {s['nw_t_net']:+.2f})  "
            f"maxdd={s['max_drawdown']:.2%}  turnover={s['avg_turnover']:.2f}"
        )


def _period_map(dates: list, rebalance_every: int) -> list:
    """Every Nth trading date is a rebalance date."""
    return dates[::rebalance_every]


def walk_forward_ls(
    signals: pl.DataFrame,
    prices: pl.DataFrame,
    *,
    rebalance_every: int = 5,
    n_quantiles: int = 5,
    cost_bps: float = 60.0,
    min_names: int = 30,
    value_col: str = "value",
) -> BacktestResult:
    """Quantile long-short walk-forward on a (ticker, date, value) panel.

    prices: (ticker, date, close). Periods with fewer than `min_names`
    ranked names are skipped (no partial books).
    """
    px = prices.select(["ticker", "date", "close"]).sort(["ticker", "date"])
    dates = sorted(px["date"].unique().to_list())
    if len(dates) < 2 * rebalance_every + 2:
        raise ValueError("not enough trading dates for even one holding period")
    rebs = _period_map(dates, rebalance_every)

    # Entry price = close at t+1 (next trading date after signal date).
    next_date = {d: dates[i + 1] for i, d in enumerate(dates[:-1])}
    close_lookup = px.rename({"date": "_d", "close": "_px"})

    sig = signals.select(["ticker", "date", value_col]).drop_nulls(value_col)

    rows: list[dict[str, float | object]] = []
    qrows: list[dict[str, float | int]] = []
    prev_long: set[str] = set()
    prev_short: set[str] = set()

    for i in range(len(rebs) - 1):
        t, t_next = rebs[i], rebs[i + 1]
        if t not in next_date or t_next not in next_date:
            continue
        entry_d, exit_d = next_date[t], next_date[t_next]

        cross = sig.filter(pl.col("date") == t)
        if cross.height < min_names:
            continue
        # Period return per name: entry close -> exit close.
        entry_px = close_lookup.filter(pl.col("_d") == entry_d).select(
            ["ticker", pl.col("_px").alias("_entry")])
        exit_px = close_lookup.filter(pl.col("_d") == exit_d).select(
            ["ticker", pl.col("_px").alias("_exit")])
        merged = (
            cross.join(entry_px, on="ticker", how="inner")
            .join(exit_px, on="ticker", how="inner")
            .with_columns((pl.col("_exit") / pl.col("_entry") - 1.0).alias("_ret"))
            .drop_nulls("_ret")
        )
        if merged.height < min_names:
            continue

        ranked = merged.with_columns(
            # qcut by rank: quantile 1 = lowest signal ... n = highest.
            (pl.col(value_col).rank(method="ordinal") * n_quantiles / (merged.height + 1))
            .floor().cast(pl.Int32).clip(0, n_quantiles - 1).alias("_q")
        )
        for q in range(n_quantiles):
            sub = ranked.filter(pl.col("_q") == q)
            if sub.height:
                qrows.append({"quantile": q + 1,
                              "mean_period_ret": _f(sub["_ret"].mean()),
                              "n_obs": sub.height})

        long_book = set(ranked.filter(pl.col("_q") == n_quantiles - 1)["ticker"].to_list())
        short_book = set(ranked.filter(pl.col("_q") == 0)["ticker"].to_list())
        long_ret = _f(ranked.filter(pl.col("_q") == n_quantiles - 1)["_ret"].mean())
        short_ret = _f(ranked.filter(pl.col("_q") == 0)["_ret"].mean())
        gross = long_ret - short_ret

        def _turnover(new: set[str], old: set[str]) -> float:
            if not new:
                return 0.0
            return len(new - old) / len(new)

        tov = 0.5 * (_turnover(long_book, prev_long) + _turnover(short_book, prev_short))
        # Each replaced slot trades twice (exit old + enter new).
        cost = tov * 2.0 * (cost_bps / 1e4)
        rows.append({
            "date": t, "long_ret": long_ret, "short_ret": short_ret,
            "ls_gross": gross, "turnover": tov, "cost": cost,
            "ls_net": gross - cost,
        })
        prev_long, prev_short = long_book, short_book

    if not rows:
        raise ValueError("no valid rebalance periods (breadth below min_names throughout?)")

    period = pl.DataFrame(rows)
    qdf = (
        pl.DataFrame(qrows).group_by("quantile")
        .agg([
            (pl.col("mean_period_ret") * pl.col("n_obs")).sum().alias("_w"),
            pl.col("n_obs").sum(),
        ])
        .with_columns((pl.col("_w") / pl.col("n_obs")).alias("mean_period_ret"))
        .select(["quantile", "mean_period_ret", "n_obs"])
        .sort("quantile")
    )

    net = period["ls_net"].to_numpy()
    gross_arr = period["ls_gross"].to_numpy()
    periods_per_year = TRADING_DAYS_PER_YEAR / rebalance_every
    ann = float(net.mean()) * periods_per_year
    vol = float(net.std(ddof=1)) * np.sqrt(periods_per_year) if net.size > 1 else 0.0
    cum = np.cumprod(1.0 + net)
    dd = float(np.min(cum / np.maximum.accumulate(cum) - 1.0)) if cum.size else 0.0

    def _sharpe(x: np.ndarray) -> float:
        sd = x.std(ddof=1)
        return float(x.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0

    summary = {
        "n_periods": float(period.height),
        "ann_ret_net": ann,
        "ann_ret_gross": float(gross_arr.mean()) * periods_per_year,
        "ann_vol": vol,
        "sharpe_net": _sharpe(net),
        "sharpe_gross": _sharpe(gross_arr),
        "nw_t_net": newey_west_t(net, n_lags=1),
        "max_drawdown": dd,
        "avg_turnover": _f(period["turnover"].mean()),
        "avg_cost_per_period": _f(period["cost"].mean()),
    }
    return BacktestResult(period_returns=period, quantile_means=qdf, summary=summary)
