#!/usr/bin/env python3
"""Composite walk-forward backtest (Phase 2): the number that matters.

Builds the month-end composite cross-section over the full grid, runs the
quantile long-short harness on the composite's own dates (monthly
rebalance), and writes backtests.json: the committed artifact the Signals
page renders. Momentum's weekly result is included for contrast; it is the
cautionary tale about horizon mismatch.

Slow path warning: compute_value/compute_quality issue per-ticker queries,
so the grid build takes ~15-20 min. Fine as an occasional background run;
the vectorized fundamentals read remains a Phase 2 roadmap item.

Usage:
  .venv/bin/python scripts/backtest_composite.py                # full grid
  .venv/bin/python scripts/backtest_composite.py --quick        # last 12 month-ends
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import polars as pl

from signal_engine.config import Config
from signal_engine.data.store import as_of_corporate_actions, as_of_prices, connect
from signal_engine.factors.momentum import compute_momentum
from signal_engine.scoring import build_composite
from signal_engine.validation.backtest import BacktestResult, walk_forward_ls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("backtest_composite")


def month_end_grid(trading_dates: list[date], start: date) -> list[date]:
    df = pl.DataFrame({"date": trading_dates}).filter(pl.col("date") >= start)
    return (
        df.group_by([pl.col("date").dt.year().alias("_y"), pl.col("date").dt.month().alias("_m")])
        .agg(pl.col("date").max().alias("month_end"))
        .sort(["_y", "_m"])
        ["month_end"].to_list()
    )


def result_block(name: str, res: BacktestResult, config_note: str) -> dict:
    return {
        "name": name,
        "config": config_note,
        "summary": res.summary,
        "quantile_means": res.quantile_means.to_dicts(),
        "recent_periods": res.period_returns.tail(24).to_dicts(),
    }


def clean(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 6)
    return obj


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="last 12 month-ends only")
    parser.add_argument("--grid-start", default="2019-01-01")
    parser.add_argument("--out", default="backtests.json")
    args = parser.parse_args()

    cfg = Config.load()
    with connect(cfg.duckdb_path, read_only=True) as con:
        today = date.today()
        ijr = con.execute("""
            SELECT DISTINCT ticker FROM ijr_holdings
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM ijr_holdings)
        """).pl()["ticker"].to_list()
        panel = as_of_prices(con, as_of=today).filter(
            pl.col("ticker").is_in(ijr)).select(["ticker", "date", "close"])
        divs = as_of_corporate_actions(con, as_of=today, kind="dividend").filter(
            pl.col("ticker").is_in(ijr))

        trading_dates = sorted(panel["date"].unique().to_list())
        grid = month_end_grid(trading_dates, date.fromisoformat(args.grid_start))
        if args.quick:
            grid = grid[-12:]
        log.info("[grid] %d month-end dates (%s -> %s)", len(grid), grid[0], grid[-1])

        # ---- composite panel over the grid (slow path; see module docstring) ----
        frames: list[pl.DataFrame] = []
        t0 = time.monotonic()
        for i, d in enumerate(grid, start=1):
            comp = build_composite(con, d, cfg)
            frames.append(
                comp.filter(pl.col("composite").is_not_null())
                .select(["ticker", pl.col("as_of").alias("date"),
                         pl.col("composite").alias("value")])
            )
            if i % 10 == 0:
                log.info("[composite] %d/%d grid dates (%.0fs)", i, len(grid),
                         time.monotonic() - t0)
        comp_panel = pl.concat(frames)
        log.info("[composite] %d signal rows in %.0fs", comp_panel.height,
                 time.monotonic() - t0)

    results: list[dict] = []

    comp_res = walk_forward_ls(
        comp_panel, panel, rebalance_dates=grid,
        n_quantiles=5, cost_bps=60.0, min_names=100,
    )
    log.info("[composite monthly] %s", comp_res)
    results.append(result_block(
        "composite (momentum + EV/EBITDA + PEAD)", comp_res,
        "monthly rebalance on the signal grid, quintiles, 60bps per replaced position",
    ))

    mom = compute_momentum(panel, dividends=divs).rename({"momentum": "value"})
    mom_res = walk_forward_ls(mom, panel, rebalance_every=5, n_quantiles=5,
                              cost_bps=60.0, min_names=100)
    log.info("[momentum weekly] %s", mom_res)
    results.append(result_block(
        "momentum 12-1 TR alone (cautionary)", mom_res,
        "weekly rebalance, quintiles, 60bps; the horizon-mismatch failure case",
    ))

    git_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=False).stdout.strip()
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "git_sha": git_sha,
            "universe": "S&P 600 (ijr_current; survivorship_clean = false)",
            "grid": {"start": str(grid[0]), "end": str(grid[-1]), "n_dates": len(grid)},
            "caveats": "survivorship-biased universe; flat 60bps cost model; "
                       "no hysteresis; single configuration per signal (no tuning)",
        },
        "results": results,
    }
    Path(args.out).write_text(json.dumps(clean(payload), default=str))
    log.info("[report] wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
