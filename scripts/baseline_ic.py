#!/usr/bin/env python3
"""Baseline forward-IC scorecard on real data - roadmap Phase 0.4.

Runs every implemented factor against the live DuckDB store and emits the
IC scorecard (Spearman, horizons 1/5/21/63) per factor, plus a markdown
report at docs/baselines.md. This is the "does anything predict anything"
checkpoint that Phase 1/2 work calibrates against.

Panels:
  * momentum  - daily, full price history (compute_momentum on the panel)
  * value     - month-end grid (fcf_yield; ev_ebitda sign-flipped)
  * quality   - month-end grid (roic; accruals sign-flipped; margin_stability)
  * pead      - SUE events broadcast forward over a 63-trading-day hold

Phase 1 upgrades folded in: Newey-West t (lag = horizon) reported next to
the iid t, momentum is total-return, SUE includes derived Q4 events.
Remaining caveats stamped into the report: no costs, no neutralization,
energy-taxonomy survivorship bias.

Usage:
  .venv/bin/python scripts/baseline_ic.py                 # full run (~10-15 min)
  .venv/bin/python scripts/baseline_ic.py --quick         # smoke: 8 grid dates
  .venv/bin/python scripts/baseline_ic.py --factors momentum,pead
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from signal_engine.atlas.clusters import energy_universe_tickers
from signal_engine.config import Config
from signal_engine.data.store import as_of_prices, connect
from signal_engine.factors.momentum import compute_momentum
from signal_engine.factors.pead import compute_sue
from signal_engine.factors.quality import compute_quality
from signal_engine.factors.value import compute_value
from signal_engine.validation.ic import IcSummary, ic_scorecard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("baseline_ic")

HOLD_WINDOW_DAYS = 63  # PEAD broadcast horizon (trading days)


def universe_ticker_to_cik(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Energy taxonomy tickers resolved through the latest ticker_cik_map.

    Energy-only by design. The taxonomy is a fixed curated list applied to
    all history, so it is survivorship-biased; flagged in the report.
    """
    energy = pl.DataFrame({"ticker": energy_universe_tickers()},
                          schema={"ticker": pl.Utf8})
    cmap = con.execute("""
        SELECT ticker, cik FROM (
            SELECT ticker, cik,
                   row_number() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS _rn
            FROM ticker_cik_map
        ) WHERE _rn = 1
    """).pl()
    joined = energy.join(cmap, on="ticker", how="left")
    missing = joined.filter(pl.col("cik").is_null())
    if missing.height:
        log.warning("[universe] %d energy tickers have no CIK (sample: %s)",
                    missing.height, missing["ticker"].to_list()[:8])
    resolved = joined.drop_nulls("cik")
    return dict(zip(resolved["ticker"].to_list(), resolved["cik"].to_list(), strict=True))


def month_end_grid(trading_dates: list[date], start: date) -> list[date]:
    """Last trading date of each month, from `start` onward."""
    df = pl.DataFrame({"date": trading_dates}).filter(pl.col("date") >= start)
    return (
        df.group_by([pl.col("date").dt.year().alias("_y"), pl.col("date").dt.month().alias("_m")])
        .agg(pl.col("date").max().alias("month_end"))
        .sort(["_y", "_m"])
        ["month_end"].to_list()
    )


def broadcast_events(events: pl.DataFrame, trading_dates: list[date],
                     value_col: str, hold_days: int) -> pl.DataFrame:
    """Broadcast event rows (ticker, signal_date, value) onto the next
    `hold_days` trading days starting at the first trading date >= signal_date.
    Overlapping events for a ticker resolve to the most recent signal_date."""
    dates_df = pl.DataFrame({"date": trading_dates}).sort("date").with_row_index("_idx")
    ev = (
        events.rename({value_col: "value"})
        .sort("signal_date")
        .join_asof(dates_df.rename({"date": "signal_date", "_idx": "_start_idx"}),
                   on="signal_date", strategy="forward")
        .drop_nulls("_start_idx")
    )
    if ev.height == 0:
        return pl.DataFrame(schema={"ticker": pl.Utf8, "date": pl.Date, "value": pl.Float64})
    expanded = (
        ev.with_columns(
            pl.int_ranges(pl.col("_start_idx"), pl.col("_start_idx") + hold_days).alias("_idx")
        )
        .explode("_idx")
        .with_columns(pl.col("_idx").cast(pl.UInt32))
        .join(dates_df, on="_idx", how="inner")
        # Most recent event wins when holds overlap for the same ticker.
        .sort(["ticker", "date", "signal_date"])
        .group_by(["ticker", "date"], maintain_order=True)
        .agg(pl.col("value").last())
    )
    return expanded.select(["ticker", "date", "value"])


def run_grid_factor(
    con: duckdb.DuckDBPyConnection,
    grid: list[date],
    t2c: dict[str, int],
    which: str,
) -> dict[str, pl.DataFrame]:
    """Run compute_value/compute_quality over the date grid; return one tidy
    signal panel per raw column (sign-flipped where lower-is-better)."""
    frames: list[pl.DataFrame] = []
    fn = compute_value if which == "value" else compute_quality
    t0 = time.monotonic()
    for i, d in enumerate(grid, start=1):
        frames.append(fn(con, d, t2c).with_columns(pl.lit(d).alias("date")))
        if i % 10 == 0:
            log.info("[%s] %d/%d grid dates (%.0fs)", which, i, len(grid), time.monotonic() - t0)
    # vertical_relaxed: a grid date where a column is all-None infers Null
    # dtype for that frame; relaxed concat resolves it to the supertype.
    panel = pl.concat(frames, how="vertical_relaxed")
    if which == "value":
        cols = {"fcf_yield": 1.0, "ev_ebitda": -1.0}     # cheaper (lower multiple) = better
    else:
        cols = {"roic": 1.0, "accruals": -1.0, "margin_stability": 1.0}
    out: dict[str, pl.DataFrame] = {}
    for col, sign in cols.items():
        name = col if sign > 0 else f"{col}_flipped"
        out[name] = (
            panel.select(["ticker", "date", col])
            .drop_nulls(col)
            .with_columns((pl.col(col) * sign).alias("value"))
            .select(["ticker", "date", "value"])
        )
    return out


def scorecard_lines(name: str, summaries: list[IcSummary]) -> list[str]:
    lines = [f"### {name}", "",
             "| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |",
             "|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.horizon}d | {s.n_dates} | {s.mean:+.4f} | {s.std:.4f} "
            f"| {s.t_stat:+.2f} | {s.t_stat_nw:+.2f} | {s.hit_rate:.2f} "
            f"| {s.sample_size_mean:.0f} |"
        )
    lines.append("")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="smoke run: last 8 grid dates only")
    parser.add_argument("--factors", default="momentum,value,quality,pead",
                        help="comma list from: momentum,value,quality,pead")
    parser.add_argument("--grid-start", default="2019-01-01")
    parser.add_argument("--out", default="docs/baselines.md")
    args = parser.parse_args()
    wanted = {f.strip() for f in args.factors.split(",")}

    cfg = Config.load()
    report: list[str] = []
    with connect(cfg.duckdb_path, read_only=True) as con:
        today = date.today()
        t2c = universe_ticker_to_cik(con)
        log.info("[universe] %d energy tickers resolved to CIKs", len(t2c))

        panel = as_of_prices(con, as_of=today).filter(
            pl.col("ticker").is_in(list(t2c.keys()))
        ).select(["ticker", "date", "close"])
        trading_dates = sorted(panel["date"].unique().to_list())
        log.info("[prices] %d rows, %d trading dates, last=%s",
                 panel.height, len(trading_dates), trading_dates[-1])

        grid = month_end_grid(trading_dates, date.fromisoformat(args.grid_start))
        if args.quick:
            grid = grid[-8:]
        log.info("[grid] %d month-end dates (%s -> %s)", len(grid), grid[0], grid[-1])

        results: dict[str, list[IcSummary]] = {}

        if "momentum" in wanted:
            t0 = time.monotonic()
            from signal_engine.data.store import as_of_corporate_actions
            divs = as_of_corporate_actions(con, as_of=today, kind="dividend").filter(
                pl.col("ticker").is_in(list(t2c.keys()))
            )
            mom = compute_momentum(panel, dividends=divs).rename({"momentum": "value"})
            results["momentum (12-1 total-return, daily)"] = ic_scorecard(mom, panel)
            log.info("[momentum] %d signal rows (%d div rows) in %.0fs",
                     mom.height, divs.height, time.monotonic() - t0)

        if "value" in wanted:
            for name, sig in run_grid_factor(con, grid, t2c, "value").items():
                results[f"value/{name} (monthly)"] = ic_scorecard(sig, panel)

        if "quality" in wanted:
            for name, sig in run_grid_factor(con, grid, t2c, "quality").items():
                results[f"quality/{name} (monthly)"] = ic_scorecard(sig, panel)

        if "pead" in wanted:
            t0 = time.monotonic()
            events = compute_sue(con, today, t2c)
            log.info("[pead] %d SUE events in %.0fs", events.height, time.monotonic() - t0)
            sue_daily = broadcast_events(
                events.select(["ticker", "signal_date", "sue"]),
                trading_dates, "sue", HOLD_WINDOW_DAYS,
            )
            results[f"pead/sue (events broadcast {HOLD_WINDOW_DAYS}d)"] = ic_scorecard(sue_daily, panel)

        # ---- console + markdown ----
        git_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, check=False).stdout.strip()
        n_facts = con.execute("SELECT count(*) FROM edgar_facts").fetchone()[0]
        report += [
            "# Baseline IC scorecard",
            "",
            f"*Generated {today} on `{git_sha}`. Spearman rank IC; forward return "
            "t+1 -> t+H+1 (no formation-day return). Universe: energy taxonomy "
            f"({len(t2c)} names, survivorship-biased curated list).*",
            "",
            f"*Data watermarks: prices through {trading_dates[-1]}, "
            f"edgar_facts = {n_facts:,} rows.*",
            "",
            "**Read `t (NW)`, not `t (iid)`:** overlapping 21/63d horizons make "
            "daily ICs autocorrelated; the Newey-West column (lag = horizon) is "
            "the honest significance. Momentum is total-return (dividends folded "
            "in); SUE includes derived Q4 events (FY - sum(Q1..3), filed at the 10-K). "
            "Remaining caveats: no costs, no neutralization - raw single-factor "
            "IC only; energy-taxonomy survivorship bias.",
            "",
        ]
        for name, summaries in results.items():
            for s in summaries:
                log.info("[%s] %s", name, s)
            report += scorecard_lines(name, summaries)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report) + "\n")
    log.info("[report] wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
