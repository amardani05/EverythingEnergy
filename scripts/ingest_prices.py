#!/usr/bin/env python3
"""Bulk yfinance price ingest for the v1 universes.

By default ingests:
  * the energy taxonomy (signal_engine/atlas/clusters.py)         — ~205 names
  * the latest IJR snapshot in DuckDB                              — ~603 names
  * deduped union                                                  — ~750 names

You run this LOCALLY in your VSCode terminal, not from Claude. The pull
takes ~10-15 min at the default 0.1s throttle.

Usage:
  .venv/bin/python scripts/ingest_prices.py                  # both universes, full history
  .venv/bin/python scripts/ingest_prices.py --universe energy --start 2020-01-01
  .venv/bin/python scripts/ingest_prices.py --universe ijr --start 2015-01-01

Re-running is safe: writes use ON CONFLICT DO NOTHING on (ticker, date, source).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

import polars as pl

from signal_engine.atlas.clusters import energy_universe_tickers
from signal_engine.config import Config
from signal_engine.data import store
from signal_engine.data.prices import yfinance_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("ingest_prices")


def ijr_universe_from_db(db_path) -> list[str]:
    """Tickers from the most recent IJR snapshot."""
    with store.connect(db_path, read_only=True) as con:
        row = con.execute(
            "SELECT max(snapshot_date) FROM ijr_holdings"
        ).fetchone()
        latest = row[0] if row else None
        if latest is None:
            log.warning("[ijr] no IJR snapshots in db; run scripts/snapshot_ijr.py first")
            return []
        df = con.execute(
            "SELECT DISTINCT ticker FROM ijr_holdings WHERE snapshot_date = ?",
            [latest],
        ).pl()
        return sorted(df["ticker"].to_list())


def write_prices(db_path, df: pl.DataFrame) -> int:
    """Upsert prices, returning row count actually inserted (new rows only)."""
    if df.height == 0:
        return 0
    with store.connect(db_path) as con:
        before = con.execute("SELECT count(*) FROM prices").fetchone()[0]
        con.register("incoming", df)
        con.execute("""
            INSERT INTO prices
              (ticker, date, open, high, low, close, volume, source, div_adjusted, split_adjusted)
            SELECT ticker, date, open, high, low, close, volume, source, div_adjusted, split_adjusted
            FROM incoming
            ON CONFLICT DO NOTHING;
        """)
        after = con.execute("SELECT count(*) FROM prices").fetchone()[0]
        return int(after - before)


def write_actions(db_path, df: pl.DataFrame) -> int:
    """Upsert corporate actions (dividends/splits), returning new-row count."""
    if df.height == 0:
        return 0
    with store.connect(db_path) as con:
        before = con.execute("SELECT count(*) FROM corporate_actions").fetchone()[0]
        con.register("incoming_actions", df)
        con.execute("""
            INSERT INTO corporate_actions (ticker, date, kind, value, source)
            SELECT ticker, date, kind, value, source
            FROM incoming_actions
            ON CONFLICT DO NOTHING;
        """)
        after = con.execute("SELECT count(*) FROM corporate_actions").fetchone()[0]
        return int(after - before)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--universe",
        choices=["both", "energy", "ijr"],
        default="both",
    )
    parser.add_argument("--start", default="2018-01-01", help="yfinance start date (YYYY-MM-DD)")
    parser.add_argument("--throttle", type=float, default=0.1)
    parser.add_argument("--limit", type=int, default=None, help="cap ticker count (for debugging)")
    args = parser.parse_args()

    cfg = Config.load()
    store.init_db(cfg.duckdb_path)

    tickers: list[str] = []
    if args.universe in ("both", "energy"):
        et = energy_universe_tickers()
        log.info("[universe] energy taxonomy: %d tickers", len(et))
        tickers.extend(et)
    if args.universe in ("both", "ijr"):
        it = ijr_universe_from_db(cfg.duckdb_path)
        log.info("[universe] IJR latest snapshot: %d tickers", len(it))
        tickers.extend(it)

    # Dedupe, preserve sort
    tickers = sorted(set(tickers))
    if args.limit:
        tickers = tickers[: args.limit]
    if not tickers:
        log.error("no tickers to ingest")
        return 2

    log.info("[ingest] %d unique tickers from start=%s (yfinance, throttle=%.2fs)",
             len(tickers), args.start, args.throttle)
    log.info("[ingest] estimated wall-clock: %.1f min", len(tickers) * (args.throttle + 0.4) / 60)

    prices_df, actions_df = yfinance_batch(tickers, start=args.start, throttle_sec=args.throttle)
    log.info("[ingest] pulled %d price rows, %d action rows", prices_df.height, actions_df.height)

    new_rows = write_prices(cfg.duckdb_path, prices_df)
    log.info("[ingest] inserted %d new rows into prices (rest were duplicates)", new_rows)
    new_actions = write_actions(cfg.duckdb_path, actions_df)
    log.info("[ingest] inserted %d new rows into corporate_actions", new_actions)

    # Coverage summary
    with store.connect(cfg.duckdb_path, read_only=True) as con:
        cov = con.execute("""
            SELECT
              count(DISTINCT ticker) AS tickers,
              min(date)              AS first_date,
              max(date)              AS last_date,
              count(*)               AS rows
            FROM prices
            WHERE source = 'yfinance'
        """).fetchone()
        log.info("[coverage] yfinance: %d tickers, %s -> %s, %d rows",
                 cov[0], cov[1], cov[2], cov[3])
        acov = con.execute("""
            SELECT kind, count(DISTINCT ticker), count(*)
            FROM corporate_actions GROUP BY kind ORDER BY kind
        """).fetchall()
        for kind, n_tickers, n_rows in acov:
            log.info("[coverage] corporate_actions/%s: %d tickers, %d rows", kind, n_tickers, n_rows)

    log.info("[ingest] done at %s", date.today())
    return 0


if __name__ == "__main__":
    sys.exit(main())
