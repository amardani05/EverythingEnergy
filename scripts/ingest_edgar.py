#!/usr/bin/env python3
"""Bulk EDGAR XBRL ingest for the v1 universe.

Pulls:
  * ticker_cik_map (one HTTP call, ~10k entries)
  * companyfacts for every CIK in the universe (~750 calls; ~30MB total)
  * submissions for the same CIKs (~750 calls; provides SIC for sector mapping)

You run this LOCALLY. At 8 req/s the universe pull is ~5 minutes wall-clock
(750 CIKs × 2 endpoints / 8 req/s ≈ 190s, plus ~10s/call decode for the
largest filers). Idempotent: re-runs upsert by accession, no duplicates.

Caches raw payloads under data_store/raw/edgar/ so a re-parse without a
re-pull is cheap.

Usage:
  .venv/bin/python scripts/ingest_edgar.py                # both universes
  .venv/bin/python scripts/ingest_edgar.py --universe energy
  .venv/bin/python scripts/ingest_edgar.py --universe ijr --limit 10  # debugging
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from signal_engine.atlas.clusters import energy_universe_tickers
from signal_engine.config import Config
from signal_engine.data import edgar, store
from signal_engine.data.prices import yfinance_batch  # noqa: F401 — kept for parity with prices script

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("ingest_edgar")


def ijr_universe_from_db(db_path: Path) -> list[str]:
    with store.connect(db_path, read_only=True) as con:
        row = con.execute("SELECT max(snapshot_date) FROM ijr_holdings").fetchone()
        latest = row[0] if row else None
        if latest is None:
            log.warning("[ijr] no snapshots; run scripts/snapshot_ijr.py first")
            return []
        df = con.execute(
            "SELECT DISTINCT ticker FROM ijr_holdings WHERE snapshot_date = ?", [latest]
        ).pl()
        return sorted(df["ticker"].to_list())


def upsert_ticker_cik(client: edgar.EdgarClient, db_path: Path) -> dict[str, dict]:
    """Snapshot the current ticker -> CIK map into ticker_cik_map. Return
    the raw payload so callers can resolve CIKs without re-fetching."""
    payload = client.ticker_cik_map()
    today = date.today()
    rows = [
        {"snapshot_date": today, "ticker": ticker, "cik": cik, "title": title}
        for cik, ticker, title in edgar.iter_cik_ticker_pairs(payload)
    ]
    import polars as pl
    df = pl.DataFrame(rows)
    with store.connect(db_path) as con:
        con.register("incoming", df)
        con.execute("""
            INSERT INTO ticker_cik_map (snapshot_date, ticker, cik, title)
            SELECT snapshot_date, ticker, cik, title FROM incoming
            ON CONFLICT DO NOTHING;
        """)
    log.info("[ticker_cik] snapshot for %s -> %d rows", today, len(rows))
    return payload


def upsert_submissions(client: edgar.EdgarClient, ciks: list[int], db_path: Path,
                       raw_dir: Path) -> int:
    """Snapshot submissions metadata for each CIK. SIC drives sector mapping."""
    import polars as pl
    today = date.today()
    rows: list[dict] = []
    raw_dir.mkdir(parents=True, exist_ok=True)
    for i, cik in enumerate(ciks, start=1):
        try:
            sub = client.submissions(cik)
        except Exception as e:  # noqa: BLE001
            log.warning("[submissions] CIK %d failed: %s", cik, e)
            continue
        edgar.cache_raw(raw_dir / f"submissions_{cik:010d}.json", sub)
        rows.append(edgar.EdgarClient.submissions_row(sub, today.isoformat()))
        if i % 50 == 0:
            log.info("[submissions] %d/%d", i, len(ciks))
    if not rows:
        return 0
    df = pl.DataFrame(rows)
    with store.connect(db_path) as con:
        con.register("incoming", df)
        con.execute("""
            INSERT INTO edgar_submissions
              (cik, snapshot_date, name, tickers, exchanges, sic, sic_description, fiscal_year_end)
            SELECT cik, snapshot_date, name, tickers, exchanges, sic, sic_description, fiscal_year_end
            FROM incoming
            ON CONFLICT DO NOTHING;
        """)
    return len(rows)


def upsert_companyfacts(client: edgar.EdgarClient, ciks: list[int], db_path: Path,
                        raw_dir: Path, concept_chains: dict) -> int:
    """Pull companyfacts per CIK, extract via config'd chains, upsert."""
    import polars as pl
    raw_dir.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    for i, cik in enumerate(ciks, start=1):
        try:
            facts = client.company_facts(cik)
        except Exception as e:  # noqa: BLE001
            log.warning("[companyfacts] CIK %d failed: %s", cik, e)
            continue
        edgar.cache_raw(raw_dir / f"companyfacts_{cik:010d}.json", facts)
        rows = edgar.EdgarClient.extract_facts(facts, cik, concept_chains)
        if not rows:
            continue
        df = pl.DataFrame(rows)
        with store.connect(db_path) as con:
            con.register("incoming", df)
            con.execute("""
                INSERT INTO edgar_facts
                  (cik, taxonomy, concept, concept_used, unit, period_start, period_end,
                   fy, fp, form, is_amendment, accession, filed, value)
                SELECT cik, taxonomy, concept, concept_used, unit, period_start, period_end,
                       fy, fp, form, is_amendment, accession, filed, value
                FROM incoming
                ON CONFLICT DO NOTHING;
            """)
        total_rows += len(rows)
        if i % 25 == 0:
            log.info("[companyfacts] %d/%d (rows so far: %d)", i, len(ciks), total_rows)
    return total_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=["both", "energy", "ijr"], default="both")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-submissions", action="store_true")
    parser.add_argument("--skip-companyfacts", action="store_true")
    args = parser.parse_args()

    cfg = Config.load()
    store.init_db(cfg.duckdb_path)

    # 1. Build the ticker list
    tickers: list[str] = []
    if args.universe in ("both", "energy"):
        tickers.extend(energy_universe_tickers())
    if args.universe in ("both", "ijr"):
        tickers.extend(ijr_universe_from_db(cfg.duckdb_path))
    tickers = sorted(set(tickers))
    if args.limit:
        tickers = tickers[: args.limit]
    log.info("[universe] %d unique tickers", len(tickers))

    # 2. Snapshot the ticker->CIK map (also gives us CIKs for the universe)
    client = edgar.EdgarClient(user_agent=cfg.edgar_user_agent,
                               rate_limit_per_sec=cfg.edgar_rate_limit_per_sec)
    tmap = upsert_ticker_cik(client, cfg.duckdb_path)

    # 3. Resolve tickers -> CIKs (warn on misses; usually new spinoffs)
    universe_ciks: list[int] = []
    missing: list[str] = []
    for t in tickers:
        c = edgar.cik_for_ticker(tmap, t)
        if c is None:
            missing.append(t)
        else:
            universe_ciks.append(c)
    universe_ciks = sorted(set(universe_ciks))
    if missing:
        log.warning("[universe] %d tickers had no CIK match (sample: %s)",
                    len(missing), missing[:10])
    log.info("[universe] %d CIKs resolved", len(universe_ciks))

    raw_dir = cfg.raw_dir / "edgar"

    # 4. Submissions
    if not args.skip_submissions:
        n = upsert_submissions(client, universe_ciks, cfg.duckdb_path, raw_dir)
        log.info("[submissions] upserted %d rows", n)

    # 5. Companyfacts (the big one)
    if not args.skip_companyfacts:
        n = upsert_companyfacts(client, universe_ciks, cfg.duckdb_path, raw_dir,
                                cfg.edgar_concepts)
        log.info("[companyfacts] upserted %d fact rows", n)

    # 6. Coverage summary
    with store.connect(cfg.duckdb_path, read_only=True) as con:
        nf = con.execute("SELECT count(*) FROM edgar_facts").fetchone()[0]
        nc = con.execute("SELECT count(DISTINCT cik) FROM edgar_facts").fetchone()[0]
        log.info("[edgar_facts] %d rows across %d CIKs", nf, nc)
        ns = con.execute("SELECT count(*) FROM edgar_submissions").fetchone()[0]
        log.info("[edgar_submissions] %d rows", ns)
        # Concept-chain coverage report
        log.info("[coverage] facts per canonical concept:")
        cov = con.execute("""
            SELECT concept, count(DISTINCT cik) AS n_ciks, count(*) AS n_rows
            FROM edgar_facts
            GROUP BY concept
            ORDER BY n_ciks DESC
        """).fetchall()
        for c, n_ciks, n_rows in cov:
            log.info("  %-32s %d CIKs, %d rows", c, n_ciks, n_rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
