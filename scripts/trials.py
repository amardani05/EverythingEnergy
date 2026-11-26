#!/usr/bin/env python
"""Show the trial ledger. `--backfill` records the runs that predate it (idempotent)."""

from __future__ import annotations

import argparse
import json
from datetime import date

from signal_engine.config import REPO_ROOT
from signal_engine.validation.ledger import LEDGER_PATH, backfill, read_events, trials

DATASET = "energy205_free"
GRID = [date(2019, 1, 31), date(2019, 2, 28), date(2019, 3, 29), date(2019, 4, 30)]  # start + spacing only
MONTHLY = {"rebalance_dates": GRID, "n_quantiles": 3, "cost_bps": 60.0}


def run_backfill() -> int:
    n = 0
    old = "Void: predates the 2026-08-20 data fixes. Exact parameters partly reconstructed from the archive."
    n += backfill(signal="composite", dataset="pre_fix_free", hypothesis="composite ranks stocks", at="2026-07-21",
                  summary={"ann_ret_net": 0.0125, "sharpe_net": 0.11, "sharpe_gross": 0.44}, note=old,
                  rebalance_dates=GRID, n_quantiles=5)
    n += backfill(signal="momentum_12_1_tr", dataset="pre_fix_free", hypothesis="momentum ranks stocks weekly",
                  at="2026-07-20", summary={"ann_ret_net": -0.109, "sharpe_net": -0.45}, note=old)
    bt = json.loads((REPO_ROOT / "backtests.json").read_text())
    at = bt["meta"]["generated_at"][:19]
    n += backfill(signal="composite", dataset=DATASET, hypothesis="composite ranks energy stocks", at=at,
                  summary=bt["results"][0]["summary"], note="backtests.json", min_names=45, **MONTHLY)
    n += backfill(signal="momentum_12_1_tr", dataset=DATASET, hypothesis="momentum ranks energy stocks weekly",
                  at=at, summary=bt["results"][1]["summary"], note="backtests.json",
                  rebalance_every=5, n_quantiles=3, min_names=45)
    dx = json.loads((REPO_ROOT / "docs" / "composite_diagnosis.json").read_text())
    for sig, block in dx["signals"].items():
        n += backfill(signal=sig, dataset=DATASET, hypothesis="which family carries the inversion",
                      at=dx["generated_at"], summary=block["summary"], note="composite_diagnosis.json",
                      min_names=45 if sig == "composite" else 30, **MONTHLY)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true")
    if ap.parse_args().backfill:
        print(f"backfilled {run_backfill()} runs")
    rows = trials()
    f = lambda v: "     -" if v is None else f"{v:+6.2f}"  # noqa: E731
    print(f"{'signal':<18}{'dataset':<16}{'runs':>4} {'first':<11}{'gross':>6} {'net':>6} {'perm p':>6}  status")
    for t in rows:
        s = t.get("summary", {})
        p = t.get("perm_pvalue")
        print(f"{t['signal']:<18}{t['dataset']:<16}{t['runs']:>4} {t['first'][:10]:<11}"
              f"{f(s.get('sharpe_gross'))} {f(s.get('sharpe_net'))} {'-' if p is None else f'{p:.3f}':>6}  {t['status']}")
    runs = sum(e["event"] == "start" for e in read_events())
    print(f"\n{len(rows)} distinct trials, {runs} runs ({LEDGER_PATH}). IC studies are not counted: a lower bound.")


if __name__ == "__main__":
    main()
