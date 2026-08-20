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
from datetime import date

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
    period_returns: pl.DataFrame   # date, long/short_ret, ls_gross, turnover, cost,
                                   # ls_net, n_long, n_short, n_ranked
    quantile_means: pl.DataFrame   # quantile, mean_period_ret, n_obs (monotonicity view)
    summary: dict[str, float]

    def __str__(self) -> str:
        s = self.summary
        return (
            f"periods={s['n_periods']:.0f}  ann_ret_net={s['ann_ret_net']:+.2%}  "
            f"ann_vol={s['ann_vol']:.2%}  sharpe_net={s['sharpe_net']:+.2f}  "
            f"(gross {s['sharpe_gross']:+.2f}, nw_t {s['nw_t_net']:+.2f})  "
            f"maxdd={s['max_drawdown']:.2%}  turnover={s['avg_turnover']:.2f}  "
            f"books={s['median_n_long']:.0f}L/{s['median_n_short']:.0f}S "
            f"(min {s['min_n_long']:.0f}/{s['min_n_short']:.0f}, "
            f"skipped={s['n_periods_skipped']:.0f})"
        )


def _period_map(dates: list, rebalance_every: int) -> list:
    """Every Nth trading date is a rebalance date."""
    return dates[::rebalance_every]


def prices_asof(px: pl.DataFrame, target: date, max_stale_days: int) -> pl.DataFrame:
    """Last close at or before `target`, per ticker, within a staleness bound.

    Strictly backward-looking, so it is PIT-valid: a name that did not print
    on `target` (holiday, halt, thin tape) uses its most recent prior close
    rather than being silently dropped by an equality join.

    The staleness bound is the honest part. A name whose last print is more
    than `max_stale_days` calendar days old is almost certainly delisted or
    suspended; carrying its stale close forward would fabricate a 0% return
    for a position that in reality was liquidated or went to zero. Those
    names are dropped and COUNTED (`n_stale_dropped`) rather than silently
    vanishing, which is what the old inner join did.

    Returns: ticker, _px, _px_date.
    """
    sub = px.filter(pl.col("date") <= target)
    if sub.height == 0:
        return pl.DataFrame(schema={"ticker": pl.Utf8, "_px": pl.Float64, "_px_date": pl.Date})
    return (
        sub.sort("date")
        .group_by("ticker")
        .agg([pl.col("close").last().alias("_px"), pl.col("date").last().alias("_px_date")])
        .filter(
            (pl.lit(target) - pl.col("_px_date")).dt.total_days() <= max_stale_days
        )
        .drop_nulls("_px")
        .filter(pl.col("_px") > 0)
    )


def walk_forward_ls(
    signals: pl.DataFrame,
    prices: pl.DataFrame,
    *,
    rebalance_every: int = 5,
    n_quantiles: int = 5,
    cost_bps: float = 60.0,
    min_names: int = 30,
    value_col: str = "value",
    rebalance_dates: list | None = None,
    min_book: int | None = None,
    max_stale_days: int = 7,
    max_abs_period_return: float | None = 3.0,
) -> BacktestResult:
    """Quantile long-short walk-forward on a (ticker, date, value) panel.

    prices: (ticker, date, close). Periods with fewer than `min_names`
    ranked names are skipped (no partial books).

    `rebalance_dates` overrides the every-Nth-trading-day calendar; pass the
    signal panel's own dates when the signal only exists on a sparse grid
    (e.g. a month-end composite). Annualization then uses the median gap
    between rebalances measured in trading days.

    Book integrity (the guard that makes a reported number trustworthy):
    prices are resolved with a backward as-of lookup bounded by
    `max_stale_days` (see `prices_asof`), and a period is emitted ONLY if
    both legs hold at least `min_book` names (default: `min_names //
    n_quantiles`, floor 5). Without this a "quintile mean" could silently
    collapse to a couple of names and still be reported as a book return.
    Skipped periods are counted in `summary.n_periods_skipped`; per-period
    leg sizes are in `period_returns.n_long` / `n_short`.

    `max_abs_period_return` (default 3.0 = a 300% move in one holding period)
    drops name-periods whose return is not credible as a return. On free data
    these are corporate-action artifacts, not tradable moves: the motivating
    case is CHRD, whose post-bankruptcy reorganization shows as a +283x
    five-day "return" and, when shorted, single-handedly produced a -500%
    period and a nonsensical -228% max drawdown. Calibrated against the real
    panel: at 3.0 only CHRD and DEC are caught (0.003% of weekly name-periods)
    while genuine 2020 crash-recovery doubles in AR/APA/BTU survive. Dropped
    rows are counted in `summary.n_extreme_dropped`. Set None to disable.
    """
    px = prices.select(["ticker", "date", "close"]).sort(["ticker", "date"])
    dates = sorted(px["date"].unique().to_list())
    if len(dates) < 2 * rebalance_every + 2:
        raise ValueError("not enough trading dates for even one holding period")
    date_idx = {d: i for i, d in enumerate(dates)}
    if rebalance_dates is not None:
        rebs = sorted(d for d in rebalance_dates if d in date_idx)
        if len(rebs) < 2:
            raise ValueError("rebalance_dates must contain >= 2 trading dates")
        gaps = np.diff([date_idx[d] for d in rebs])
        effective_period = float(np.median(gaps))
    else:
        rebs = _period_map(dates, rebalance_every)
        effective_period = float(rebalance_every)

    # Entry price = close at t+1 (next trading date after signal date).
    next_date = {d: dates[i + 1] for i, d in enumerate(dates[:-1])}

    sig = signals.select(["ticker", "date", value_col]).drop_nulls(value_col)

    if min_book is None:
        min_book = max(5, min_names // n_quantiles)

    rows: list[dict[str, float | object]] = []
    qrows: list[dict[str, float | int]] = []
    prev_long: set[str] = set()
    prev_short: set[str] = set()
    n_skipped = 0
    n_stale_dropped = 0
    n_extreme_dropped = 0

    for i in range(len(rebs) - 1):
        t, t_next = rebs[i], rebs[i + 1]
        if t not in next_date or t_next not in next_date:
            continue
        entry_d, exit_d = next_date[t], next_date[t_next]

        cross = sig.filter(pl.col("date") == t)
        if cross.height < min_names:
            n_skipped += 1
            continue
        # Period return per name: entry close -> exit close, both resolved
        # with a bounded backward as-of lookup (never an equality join, which
        # silently dropped any name that did not print on the exact date).
        entry_px = prices_asof(px, entry_d, max_stale_days).select(
            ["ticker", pl.col("_px").alias("_entry")])
        exit_px = prices_asof(px, exit_d, max_stale_days).select(
            ["ticker", pl.col("_px").alias("_exit")])
        merged = (
            cross.join(entry_px, on="ticker", how="inner")
            .join(exit_px, on="ticker", how="inner")
            .with_columns((pl.col("_exit") / pl.col("_entry") - 1.0).alias("_ret"))
            .drop_nulls("_ret")
        )
        # Names in the cross-section with no usable price pair: delisted,
        # halted, or too stale. Counted, not hidden.
        n_stale_dropped += cross.height - merged.height

        # Returns too large to be returns: corporate-action artifacts on free
        # data (see docstring). Dropped and counted, never traded.
        if max_abs_period_return is not None:
            before = merged.height
            merged = merged.filter(pl.col("_ret").abs() <= max_abs_period_return)
            n_extreme_dropped += before - merged.height

        if merged.height < min_names:
            n_skipped += 1
            continue

        ranked = merged.with_columns(
            # qcut by rank: quantile 1 = lowest signal ... n = highest.
            (pl.col(value_col).rank(method="ordinal") * n_quantiles / (merged.height + 1))
            .floor().cast(pl.Int32).clip(0, n_quantiles - 1).alias("_q")
        )
        long_leg = ranked.filter(pl.col("_q") == n_quantiles - 1)
        short_leg = ranked.filter(pl.col("_q") == 0)
        # THE guard: a leg thinner than min_book is not a book, it is an
        # anecdote. Skip the period entirely rather than emit its mean.
        # Positions simply stay held (prev_long/prev_short unchanged), so the
        # next traded period's turnover is measured against what we still own.
        if long_leg.height < min_book or short_leg.height < min_book:
            n_skipped += 1
            continue

        # Quantile diagnostic recorded only for periods that actually traded,
        # so quantile_means and period_returns describe the same sample.
        for q in range(n_quantiles):
            sub = ranked.filter(pl.col("_q") == q)
            if sub.height:
                qrows.append({"quantile": q + 1,
                              "mean_period_ret": _f(sub["_ret"].mean()),
                              "n_obs": sub.height})

        long_book = set(long_leg["ticker"].to_list())
        short_book = set(short_leg["ticker"].to_list())
        long_ret = _f(long_leg["_ret"].mean())
        short_ret = _f(short_leg["_ret"].mean())
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
            "n_long": long_leg.height, "n_short": short_leg.height,
            "n_ranked": merged.height,
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
    periods_per_year = TRADING_DAYS_PER_YEAR / effective_period
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
        # Book-integrity block: these say whether the numbers above describe
        # real books. median_n_long/short below min_book should be impossible
        # by construction; a large n_periods_skipped means the sample is thin.
        "n_periods_skipped": float(n_skipped),
        "min_book": float(min_book),
        "median_n_long": _f(period["n_long"].median()),
        "median_n_short": _f(period["n_short"].median()),
        "min_n_long": _f(period["n_long"].min()),
        "min_n_short": _f(period["n_short"].min()),
        "median_n_ranked": _f(period["n_ranked"].median()),
        "n_stale_dropped": float(n_stale_dropped),
        "n_extreme_dropped": float(n_extreme_dropped),
    }
    return BacktestResult(period_returns=period, quantile_means=qdf, summary=summary)
