#!/usr/bin/env python
"""Databento futures loader. Quotes by default; spends only with --buy.

    plan                      show requests, no key or network needed
    quote                     price every request (free, needs DATABENTO_API_KEY)
    quote --buy credit|cash   buy what fits the caps
    build                     settlements/curve parquet from downloaded files
Caps live in signal_engine/data/databento_futures.py. Manual; not in the nightly job.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from signal_engine.config import REPO_ROOT
from signal_engine.data import databento_futures as F

ROOT = REPO_ROOT / "data_store" / "databento"


def client() -> Any:
    if not os.environ.get("DATABENTO_API_KEY"):
        raise SystemExit("Set DATABENTO_API_KEY in your shell (never commit it).")
    import databento as db

    return db.Historical()


def load(files: list[Path], cols: list[str]) -> pl.DataFrame:
    import databento as db

    frames = []
    for f in files:
        df = db.DBNStore.from_file(f).to_df(map_symbols=False).reset_index()
        if len(df):
            p = pl.from_pandas(df[cols].astype({c: str for c in ("instrument_class",) if c in cols}))
            frames.append(p.with_columns(pl.col(c).dt.cast_time_unit("ns")
                                         for c, t in p.schema.items() if t == pl.Datetime))
    if not frames:
        raise SystemExit("no records")
    return pl.concat(frames, how="vertical_relaxed")


def build(store: F.Store) -> None:
    done = {e["job"] for e in store.events() if e["event"] == "done"}
    frames = []
    for job in F.FUTURES:
        if job.schema != "statistics" or job.name not in done:
            continue
        dname = job.name.replace("statistics", "definition")
        if dname not in done:
            print(f"{job.name}: definitions not downloaded, skipped")
            continue

        def files(name: str) -> list[Path]:
            return sorted(p for p in (store.root / name).glob("*.dbn.zst") if ".part." not in p.name)

        stats = load(files(job.name), ["instrument_id", "ts_recv", "ts_ref", "price", "quantity", "stat_type",
                                       "stat_flags", "update_action"])
        defs = load(files(dname), ["instrument_id", "ts_recv", "instrument_class", "asset", "raw_symbol", "expiration"])
        frames.append(F.curve(stats, defs))
    if not frames:
        raise SystemExit("nothing downloaded yet")
    out = pl.concat(frames)
    out.write_parquet(store.root / "curves.parquet")
    print(out.group_by("root").agg(pl.len(), pl.col("date").min().alias("first"), pl.col("date").max().alias("last"),
                                   pl.col("final").mean().alias("final_share")).sort("root"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "quote", "build"])
    ap.add_argument("--buy", choices=["credit", "cash"])
    ap.add_argument("--options", action="store_true", help="include option roots")
    ap.add_argument("--only", help="job name prefix, e.g. CL_")
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--years", type=int, default=5)
    a = ap.parse_args()
    if a.buy and a.cmd != "quote":
        ap.error("--buy only works with quote")
    store = F.Store(ROOT)
    if a.cmd == "build":
        return build(store)
    jobs = [j for j in F.FUTURES + (F.OPTIONS if a.options else []) if j.name.startswith(a.only or "")]
    start = a.start or F.default_start(a.years)
    if a.cmd == "plan":
        for r in F.plan(jobs, store, start, datetime.now(UTC).date()):
            print(f"{r.job.name:<20}{r.start} .. {r.end}  {r.job.symbols[0]} x{len(r.job.symbols)}")
        return
    cl = client()
    ends = {s: F.available_end(cl, s) for s in {j.schema for j in jobs}}
    total = bought = 0.0
    with store.lock():
        for j in jobs:
            for r in F.plan([j], store, start, ends[j.schema]):
                tag = f"{r.job.name:<20}{r.start} .. {r.end}"
                try:
                    if a.buy:
                        usd = F.buy(cl, store, r, a.buy)
                        bought += usd
                        print(f"{tag}  ${usd:.4f} bought", flush=True)
                    else:
                        usd = F.quote(cl, r)
                        print(f"{tag}  ${usd:.4f}{'  OVER REQUEST CAP' if usd > F.MAX_REQUEST else ''}", flush=True)
                    total += usd
                except F.BudgetError as e:
                    print(f"{tag}  REFUSED: {e}")
                    break  # later ranges would leave a gap in this job's coverage
    month = datetime.now(UTC).strftime("%Y-%m")
    print(f"\ntotal ${total:.2f}, bought ${bought:.2f} | credit used ${store.spent('credit'):.2f}/{F.CREDIT_CAP:.0f}"
          f" | cash this month ${store.spent('cash', month):.2f}/{F.MONTHLY_CAP:.0f}")


if __name__ == "__main__":
    main()
