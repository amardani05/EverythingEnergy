#!/usr/bin/env python3
"""Oil supply/demand balance - fetch, model, publish.

Pulls two key-free EIA feeds, runs `signal_engine.macro.oil_balance`, and
writes the web artifact `oil_balance.json` that `balances.html` renders:

  * STEO bulk file  -> global liquids balance (monthly, with EIA's forecast)
  * WPSR xls series -> US crude balance (weekly, real refinery inputs)

Each STEO release is also archived into `macro_series` keyed by its release
date (the store's `knowledge_date`), so forecast-vs-actual error on the
build accumulates one vintage per month from here on.

Usage:
  .venv/bin/python scripts/oil_balance.py
  .venv/bin/python scripts/oil_balance.py --offline      # reuse cached raw files
  .venv/bin/python scripts/oil_balance.py --no-store     # skip the vintage archive
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import requests

from signal_engine.config import REPO_ROOT, Config
from signal_engine.macro.oil_balance import (
    US_WEEKLY_SERIES,
    build_global_balance,
    build_us_weekly,
    days_in_month,
    fit_stock_model,
    parse_steo_bulk,
    project_oecd_stocks,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("oil_balance")

STEO_URL = "https://www.eia.gov/opendata/bulk/STEO.zip"
WPSR_URL = "https://www.eia.gov/dnav/pet/hist_xls/{sid}w.xls"
UA = {"User-Agent": "signal-engine/0.1 (energy research dashboard)"}


def fetch(url: str, dest: Path, *, offline: bool) -> Path:
    if offline:
        if not dest.exists():
            raise FileNotFoundError(f"--offline but no cached file at {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    log.info("[fetch] %s -> %s (%.1f KB)", url, dest.name, len(r.content) / 1024)
    return dest


def read_dnav_xls(path: Path) -> dict[str, float]:
    """EIA dnav history workbook -> {iso_date: value}. Sheet 2, rows from 4."""
    import xlrd

    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(1)
    out: dict[str, float] = {}
    for i in range(3, sheet.nrows):
        d, v = sheet.cell_value(i, 0), sheet.cell_value(i, 1)
        if d == "" or v == "":
            continue
        out[xlrd.xldate_as_datetime(d, book.datemode).date().isoformat()] = float(v)
    return out


def archive_vintage(cfg: Config, bundle, balance: dict) -> int:
    """One row per (series, period) stamped with the STEO release date."""
    from signal_engine.data.store import connect, init_db

    init_db(cfg.duckdb_path)
    released = date.fromisoformat(bundle.released)
    rows = []
    for key in ("supply_total", "demand_total", "implied_build", "oecd_stocks"):
        for p, v in zip(balance["periods"], balance[key], strict=True):
            if v is None:
                continue
            period_end = date(int(p[:4]), int(p[4:6]), days_in_month(p))
            rows.append(("EIA", f"STEO_BAL.{key}", period_end, released, float(v)))
    with connect(cfg.duckdb_path) as con:
        before = con.execute("SELECT count(*) FROM macro_series").fetchone()[0]
        con.executemany(
            "INSERT INTO macro_series (source, series_id, period, knowledge_date, value) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            rows,
        )
        after = con.execute("SELECT count(*) FROM macro_series").fetchone()[0]
    return after - before


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "oil_balance.json")
    ap.add_argument("--offline", action="store_true", help="reuse cached raw files, no HTTP")
    ap.add_argument("--no-store", action="store_true", help="do not archive the STEO vintage to DuckDB")
    ap.add_argument("--start", default="201501", help="first month of the global balance (YYYYMM)")
    args = ap.parse_args()

    cfg = Config.load()
    raw_dir = cfg.raw_dir / "eia"

    # --- global (STEO) ---
    bundle = parse_steo_bulk(fetch(STEO_URL, raw_dir / "STEO.zip", offline=args.offline))
    balance = build_global_balance(bundle, start=args.start)
    stk_hist = balance["last_hist"]["oecd_stocks"]
    fit_idx = [i for i, p in enumerate(balance["periods"]) if p <= stk_hist]
    model = fit_stock_model(
        [balance["implied_build"][i] for i in fit_idx],
        [balance["oecd_build"][i] for i in fit_idx],
    )
    projection = project_oecd_stocks(balance, model)
    log.info(
        "[global] %d months (%s..%s), STEO released %s | OECD build = %.2f + %.2f x implied, "
        "sigma %.2f mb/d, R2 %.2f, n=%d",
        len(balance["periods"]), balance["periods"][0], balance["periods"][-1], bundle.released,
        model.alpha, model.beta, model.sigma, model.r2, model.n,
    )

    # --- US weekly (WPSR) ---
    raw = {
        key: read_dnav_xls(fetch(WPSR_URL.format(sid=sid), raw_dir / f"{sid}w.xls", offline=args.offline))
        for key, sid in US_WEEKLY_SERIES.items()
    }
    us = build_us_weekly(raw)
    log.info(
        "[us] %d weeks through %s | adjustment mean %+.2f, sigma %.2f mb/d over %d weeks",
        len(us["dates"]), us["dates"][-1], us["adjustment_stats"]["mean"],
        us["adjustment_stats"]["sigma"], us["adjustment_stats"]["window_weeks"],
    )

    payload = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "steo_released": bundle.released,
            "us_week_ending": us["dates"][-1],
            "units": "million barrels per day unless noted; stocks in million barrels",
            "sources": [
                {"name": "EIA Short-Term Energy Outlook (bulk file)", "url": STEO_URL},
                {"name": "EIA Weekly Petroleum Status Report (dnav history)", "url": "https://www.eia.gov/dnav/pet/pet_sum_sndw_dcus_nus_w.htm"},
            ],
        },
        "global": balance,
        "stock_model": model.as_dict(),
        "projection": projection,
        "us_weekly": us,
    }
    text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    args.out.write_text(text)
    log.info("[emit] %s (%.0f KB)", args.out.name, len(text) / 1024)

    if not args.no_store:
        try:
            n = archive_vintage(cfg, bundle, balance)
            log.info("[archive] STEO vintage %s: %d new rows in macro_series", bundle.released, n)
        except Exception as e:
            log.warning("[archive] skipped: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
