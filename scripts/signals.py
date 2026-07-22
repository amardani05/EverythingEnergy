#!/usr/bin/env python3
"""Daily signal run - the Phase 1 acceptance command.

One invocation:
  1. builds the ranked, sector-neutral composite cross-section at --as-of
     (default: latest trading date in the store),
  2. writes `signals_<as_of>.parquet` + `diff_<as_of>.md` into
     config output.snapshot_dir,
  3. prints the top decile, the family Spearman-correlation matrix, and
     coverage stats.

Usage:
  .venv/bin/python scripts/signals.py                 # latest date
  .venv/bin/python scripts/signals.py --as-of 2026-07-15
  .venv/bin/python scripts/signals.py --top 25
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import polars as pl

from signal_engine.config import REPO_ROOT, Config
from signal_engine.data.store import connect
from signal_engine.output import diff_snapshots, latest_prior_snapshot, write_diff, write_snapshot
from signal_engine.scoring import build_composite, family_correlation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("signals")


def emit_json(df: pl.DataFrame, as_of: date, diff: object, corr: pl.DataFrame,
              out_path: Path, *, top_n: int = 50) -> None:
    """Write the web artifact signals.html consumes. Committed to the repo
    and deployed with the site, same contract as dashboard_data.json:
    NaN-free, small, self-describing, stamped with its vintage."""
    import json
    import math
    from datetime import datetime

    def clean(obj: object) -> object:
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return round(obj, 5)
        return obj

    ranked = df.filter(pl.col("composite").is_not_null())
    keep_cols = [c for c in (
        "rank", "ticker", "sector", "composite", "score_momentum",
        "score_value", "score_pead", "score_quality", "n_families", "pctl",
    ) if c in df.columns]
    coverage = {
        c.removeprefix("score_"): [df[c].drop_nulls().len(), df.height]
        for c in df.columns if c.startswith("score_")
    }
    diff_block = None
    if diff is not None and getattr(diff, "prev_as_of", None) is not None:
        diff_block = {
            "prev_as_of": str(diff.prev_as_of),
            "new_entrants": diff.new_entrants.to_dicts(),
            "exits": diff.exits.to_dicts(),
            "rank_jumps": diff.rank_jumps.to_dicts(),
        }
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "as_of": str(as_of),
            "universe": "S&P 600 (ijr_current; survivorship_clean = false)",
            "n_names": df.height,
            "n_ranked": ranked.height,
        },
        "coverage": coverage,
        "family_correlation": corr.to_dicts(),
        "top": ranked.head(top_n).select(keep_cols).to_dicts(),
        "bottom": ranked.tail(10).select(keep_cols).to_dicts(),
        "diff": diff_block,
    }
    out_path.write_text(json.dumps(clean(payload), default=str))
    log.info("[emit-json] wrote %s", out_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD; default = latest price date")
    parser.add_argument("--top", type=int, default=15, help="rows to print")
    parser.add_argument("--no-write", action="store_true", help="skip snapshot/diff files")
    parser.add_argument("--emit-json", default=None, metavar="PATH",
                        help="also write the web artifact (e.g. signals_latest.json)")
    args = parser.parse_args()

    cfg = Config.load()
    snapshot_dir = REPO_ROOT / cfg.raw["output"]["snapshot_dir"]
    diff_cfg = cfg.raw["output"].get("diff", {})

    with connect(cfg.duckdb_path, read_only=True) as con:
        if args.as_of:
            as_of = date.fromisoformat(args.as_of)
        else:
            as_of = con.execute("SELECT max(date) FROM prices").fetchone()[0]
            if as_of is None:
                log.error("prices table is empty - run scripts/ingest_prices.py first")
                return 2

        df = build_composite(con, as_of, cfg)

    ranked = df.filter(pl.col("composite").is_not_null())
    log.info("[signals] %s: %d names, %d ranked (>=2 families)", as_of, df.height, ranked.height)

    # ---- console report ----
    top = ranked.head(args.top)
    with pl.Config(tbl_rows=args.top, tbl_cols=-1, float_precision=3):
        print(f"\n=== Top {args.top} - {as_of} (pctl 0% = best; survivorship_clean=False) ===")
        print(top.select([c for c in (
            "rank", "ticker", "sector", "composite",
            "score_momentum", "score_value", "score_pead", "score_quality",
            "n_families", "pctl") if c in top.columns]))
        print("\n=== Family Spearman correlation (ranked names) ===")
        print(family_correlation(df))

    n_total = df.height
    for col in [c for c in df.columns if c.startswith("score_")]:
        cov = df[col].drop_nulls().len()
        print(f"coverage {col.removeprefix('score_'):>9}: {cov}/{n_total}")

    # ---- snapshot + diff ----
    if not args.no_write:
        prior = latest_prior_snapshot(snapshot_dir, before=as_of)
        write_snapshot(df, as_of, snapshot_dir)
        if prior is None:
            from signal_engine.output import DiffReport
            diff = DiffReport(as_of=as_of, prev_as_of=None)
        else:
            prev_date, prev_df = prior
            diff = diff_snapshots(
                prev_df, df, as_of=as_of, prev_as_of=prev_date,
                new_entrant_decile=float(diff_cfg.get("new_entrant_decile", 0.10)),
                rank_jump_threshold=float(diff_cfg.get("rank_jump_threshold", 50)),
            )
            print(f"\n=== Diff vs {prev_date} ===")
            print(f"new top-decile entrants: {diff.new_entrants.height}  "
                  f"exits: {diff.exits.height}  large moves: {diff.rank_jumps.height}")
        path = write_diff(diff, snapshot_dir)
        print(f"\nsnapshot: {Path(snapshot_dir) / f'signals_{as_of}.parquet'}")
        print(f"diff:     {path}")

    if args.emit_json:
        diff_obj = diff if not args.no_write else None
        emit_json(df, as_of, diff_obj, family_correlation(df), Path(args.emit_json))

    return 0


if __name__ == "__main__":
    sys.exit(main())
