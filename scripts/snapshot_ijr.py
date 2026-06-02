#!/usr/bin/env python3
"""Snapshot IJR holdings and load into DuckDB. Designed to run nightly.

Idempotent: re-running on the same `snapshot_date` is a no-op (the PK on
ijr_holdings prevents duplicates).

Wire into launchd with the plist in scripts/launchd/com.signalengine.ijr.plist.
"""

from __future__ import annotations

import logging
import sys

import polars as pl

from signal_engine.config import Config
from signal_engine.data import store
from signal_engine.data.universe import IJRDownloader, parse_holdings_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("snapshot_ijr")


def main() -> int:
    cfg = Config.load()
    raw_dir = cfg.raw_dir / "ijr"
    raw_dir.mkdir(parents=True, exist_ok=True)

    downloader = IJRDownloader(holdings_url=cfg.raw["ijr"]["holdings_url"])
    text = downloader.fetch_raw()
    as_of, df = parse_holdings_csv(text)

    if as_of is None:
        log.error("could not parse as-of date from IJR CSV header; aborting")
        return 2

    raw_path = raw_dir / f"ijr_holdings_{as_of.isoformat()}.csv"
    raw_path.write_text(text)
    log.info("[ijr] saved raw to %s (%d holdings)", raw_path, df.height)

    store.init_db(cfg.duckdb_path)
    with store.connect(cfg.duckdb_path) as con:
        # INSERT OR IGNORE so re-runs on the same snapshot_date are no-ops.
        con.register("incoming", df)
        con.execute("""
            INSERT INTO ijr_holdings
              (snapshot_date, ticker, name, weight, shares, market_value, asset_class, sector)
            SELECT snapshot_date, ticker, name, weight, shares, market_value, asset_class, sector
            FROM incoming
            ON CONFLICT DO NOTHING;
        """)
        n = con.execute(
            "SELECT count(*) FROM ijr_holdings WHERE snapshot_date = ?", [as_of]
        ).fetchone()
        log.info("[ijr] db now has %s rows for snapshot_date=%s", n[0] if n else "?", as_of)
    return 0


if __name__ == "__main__":
    sys.exit(main())
