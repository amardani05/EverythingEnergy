#!/usr/bin/env python3
"""Why is the composite inverted? Subsample diagnosis (ROADMAP Tier 0.1).

Builds the monthly composite panel once (cached to data_store/), then runs
the composite and each family score on its own through the same tercile
long-short harness, and splits every result into thirds and halves. The
question it answers: is the inversion one regime (the 2020 crash and
rebound) or the whole sample, and which family carries it.

Usage:
  .venv/bin/python scripts/diagnose_composite.py            # reuse cached panel
  .venv/bin/python scripts/diagnose_composite.py --rebuild
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_composite import clean, energy_universe_tickers, month_end_grid

from signal_engine.config import REPO_ROOT, Config
from signal_engine.data.store import as_of_prices, connect
from signal_engine.scoring import build_composite
from signal_engine.validation.backtest import subsample_report, walk_forward_ls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("diagnose")

SIGNALS = ["composite", "score_momentum", "score_value", "score_pead"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--grid-start", default="2019-01-01")
    ap.add_argument("--out", default=str(REPO_ROOT / "docs" / "composite_diagnosis.json"))
    args = ap.parse_args()

    cfg = Config.load()
    cache = REPO_ROOT / "data_store" / "composite_panel.parquet"
    with connect(cfg.duckdb_path, read_only=True) as con:
        universe = energy_universe_tickers()
        prices = as_of_prices(con, as_of=date.today()).filter(
            pl.col("ticker").is_in(universe)).select(["ticker", "date", "close"])
        grid = month_end_grid(sorted(prices["date"].unique().to_list()), date.fromisoformat(args.grid_start))
        if cache.exists() and not args.rebuild:
            panel = pl.read_parquet(cache)
            log.info("[panel] reused %s (%d rows)", cache.name, panel.height)
        else:
            frames, t0 = [], time.monotonic()
            for i, d in enumerate(grid, start=1):
                frames.append(build_composite(con, d, cfg).select(["ticker", pl.col("as_of").alias("date"), *SIGNALS]))
                if i % 10 == 0:
                    log.info("[panel] %d/%d dates (%.0fs)", i, len(grid), time.monotonic() - t0)
            panel = pl.concat(frames)
            panel.write_parquet(cache)

    out: dict = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "grid": {"start": str(grid[0]), "end": str(grid[-1]), "n": len(grid)}, "signals": {}}
    for sig in SIGNALS:
        sp = panel.filter(pl.col(sig).is_not_null()).select(["ticker", "date", pl.col(sig).alias("value")])
        # single families cover fewer names than the composite: lower the floor, keep terciles
        res = walk_forward_ls(sp, prices, rebalance_dates=grid, n_quantiles=3, cost_bps=60.0,
                              min_names=45 if sig == "composite" else 30)
        rep = subsample_report(res.period_returns, col="ls_gross", periods_per_year=12.0)
        out["signals"][sig] = {"summary": res.summary, "quantile_means": res.quantile_means.to_dicts(), "subsample": rep}
        log.info("[%s] gross sharpe %+.2f, n=%d, stability=%s, worst window %+.2f",
                 sig, res.summary["sharpe_gross"], res.summary["n_periods"], rep["stability"], rep["worst_subsample_sharpe"])
        for w in rep["windows"]:
            log.info("    %-10s %s..%s n=%2d mean %+.3f%% sharpe %+.2f hit %.0f%%",
                     w["window"], w["start"], w["end"], w["n"], 100 * w["mean"], w["sharpe"], 100 * w["hit_rate"])
    Path(args.out).write_text(json.dumps(clean(out), default=str, indent=1))
    log.info("[report] wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
