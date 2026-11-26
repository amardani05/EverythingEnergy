#!/usr/bin/env python
"""Composite rerun on the survivorship-free panel (spec: data/wrds_signals.py).

Every run is a logged trial. Output stays local: WRDS-derived, do not publish.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from signal_engine.data.wrds_signals import score, total_return_index
from signal_engine.validation.backtest import subsample_report
from signal_engine.validation.ledger import run_trial

ROOT = Path(__file__).resolve().parents[1] / "data_store" / "wrds"


def main() -> None:
    src = max(p.parent for p in ROOT.glob("*/panel.parquet"))
    panel, daily = pl.read_parquet(src / "panel.parquet"), pl.read_parquet(src / "daily.parquet")
    scores = score(panel)
    grid = sorted(scores["date"].unique().to_list())
    out = {}
    for stress in (False, True):
        prices = total_return_index(daily, grid, stress=stress)
        ds = "crsp_energy_1usd" + ("_stress" if stress else "")
        for sig in ("composite", "score_momentum", "score_value", "score_pead"):
            s = scores.select("ticker", "date", pl.col(sig).alias("value")).drop_nulls()
            r = run_trial(s, prices, signal=sig, version="crsp1", dataset=ds,
                          hypothesis="the signal ranks energy stocks without survivorship bias",
                          rebalance_dates=grid, n_quantiles=3, cost_bps=60.0,
                          min_names=45 if sig == "composite" else 30, max_abs_period_return=None)
            rep = subsample_report(r.period_returns)
            out[f"{ds}:{sig}"] = {"summary": r.summary, "quantiles": r.quantile_means.to_dicts(), "subsample": rep}
            print(ds, sig, r)
            print("   quantiles", [round(100 * q, 2) for q in r.quantile_means["mean_period_ret"]],
                  "stale", r.summary["n_stale_dropped"], "thirds",
                  [round(w["sharpe"], 2) for w in rep["windows"][:3]], "stable", rep["stability"], flush=True)
            if not stress:
                r.period_returns.write_parquet(src / f"rerun_{sig}.parquet")
    (src / "rerun.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
