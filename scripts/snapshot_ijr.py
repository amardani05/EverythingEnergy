#!/usr/bin/env python3
"""IJR holdings ingester — disk-based (manual daily drop).

Per turn 8 decision: iShares' AJAX endpoint is Akamai-walled, so v1 ingests
from a directory of CSVs you save manually:

  1. Open https://www.ishares.com/us/products/239774/ishares-core-sp-small-cap-etf
  2. Click "Download holdings (CSV)" near the top right.
  3. Move the downloaded file into data_store/raw/ijr/  (filename doesn't
     matter — we parse the as-of date from the CSV header).

Run this script (idempotent, PK-protected) to ingest any new snapshots:

  .venv/bin/python scripts/snapshot_ijr.py

Re-runs are no-ops for already-ingested (snapshot_date, ticker) keys, so
running it nightly via launchd is safe even when you haven't dropped a new
file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from signal_engine.config import Config
from signal_engine.data import store
from signal_engine.data.universe import parse_holdings_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("snapshot_ijr")


def ingest_directory(raw_dir: Path, db_path: Path) -> int:
    """Process every *.csv in raw_dir; return the count of NEW (snapshot_date)
    keys that were inserted."""
    csvs = sorted(raw_dir.glob("*.csv"))
    if not csvs:
        log.info("[ijr] no CSVs in %s — nothing to ingest. Drop the iShares "
                 "holdings file there and re-run.", raw_dir)
        return 0

    store.init_db(db_path)
    new_snapshots = 0
    with store.connect(db_path) as con:
        existing = {
            row[0] for row in con.execute(
                "SELECT DISTINCT snapshot_date FROM ijr_holdings"
            ).fetchall()
        }
        for path in csvs:
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                text = path.read_bytes().decode("latin-1")
            try:
                as_of, df = parse_holdings_csv(text)
            except ValueError as e:
                log.warning("[ijr] skipping %s: %s", path.name, e)
                continue
            if as_of is None:
                log.warning("[ijr] skipping %s: could not parse as-of date", path.name)
                continue
            if as_of in existing:
                log.info("[ijr] %s -> snapshot %s already ingested; skipping", path.name, as_of)
                continue
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
            log.info("[ijr] ingested %s -> snapshot_date=%s, %d holdings",
                     path.name, as_of, n[0] if n else 0)
            existing.add(as_of)
            new_snapshots += 1
    return new_snapshots


def main() -> int:
    cfg = Config.load()
    raw_dir = cfg.raw_dir / "ijr"
    raw_dir.mkdir(parents=True, exist_ok=True)
    n = ingest_directory(raw_dir, cfg.duckdb_path)
    log.info("[ijr] done; %d new snapshot(s) ingested", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
