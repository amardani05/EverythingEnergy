#!/usr/bin/env python3
"""Bulk EDGAR XBRL ingest for the v1 universe.

Pulls:
  * ticker_cik_map (one HTTP call, ~10k entries)
  * companyfacts for every CIK in the universe (~750 calls; ~30MB total)
  * submissions for the same CIKs (~750 calls; provides SIC for sector mapping)

You run this LOCALLY. At 8 req/s the universe pull is ~5 minutes wall-clock
(750 CIKs x 2 endpoints / 8 req/s ≈ 190s, plus ~10s/call decode for the
largest filers). Idempotent: re-runs upsert by accession, no duplicates.

Robustness contract (post-mortem of the 2026-06-23 crash at ~650/766 CIKs):
  * One bad filer can never kill the run — fetch + cache + parse are all
    inside the per-CIK try.
  * Rows are flushed to DuckDB incrementally (every FLUSH_EVERY CIKs), so a
    crash loses at most one flush window, not the whole run.
  * Raw payloads are cached under data_store/raw/edgar/ as they arrive, and
    `--from-cache` re-parses them into DuckDB without any HTTP — a re-parse
    after a code fix costs seconds, not a re-pull.

Usage:
  .venv/bin/python scripts/ingest_edgar.py                # both universes
  .venv/bin/python scripts/ingest_edgar.py --universe energy
  .venv/bin/python scripts/ingest_edgar.py --universe ijr --limit 10  # debugging
  .venv/bin/python scripts/ingest_edgar.py --from-cache   # no network, re-parse raw/
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from signal_engine.atlas.clusters import energy_universe_tickers
from signal_engine.config import Config
from signal_engine.data import edgar, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("ingest_edgar")

FLUSH_EVERY = 50  # submissions rows buffered between DuckDB flushes


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


def flush_submissions(db_path: Path, rows: list[dict[str, Any]]) -> int:
    """Insert a batch of edgar_submissions rows. Small batches, called often."""
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


def flush_facts(db_path: Path, rows: list[dict[str, Any]]) -> int:
    """Insert one CIK's edgar_facts rows."""
    if not rows:
        return 0
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
    return len(rows)


def upsert_submissions(client: edgar.EdgarClient, ciks: list[int], db_path: Path,
                       raw_dir: Path) -> int:
    """Snapshot submissions metadata for each CIK. SIC drives sector mapping.

    Fetch, cache, AND parse live inside the try: the 6/23 crash was a parse
    error (`None` in the exchanges array) escaping a try that only covered
    the HTTP call, killing the batch insert that used to sit after the loop.
    """
    today = date.today()
    raw_dir.mkdir(parents=True, exist_ok=True)
    buffered: list[dict[str, Any]] = []
    total = 0
    failed: list[int] = []
    for i, cik in enumerate(ciks, start=1):
        try:
            sub = client.submissions(cik)
            edgar.cache_raw(raw_dir / f"submissions_{cik:010d}.json", sub)
            buffered.append(edgar.EdgarClient.submissions_row(sub, today.isoformat()))
        except Exception as e:
            log.warning("[submissions] CIK %d failed: %s", cik, e)
            failed.append(cik)
            continue
        if len(buffered) >= FLUSH_EVERY:
            total += flush_submissions(db_path, buffered)
            buffered = []
        if i % 50 == 0:
            log.info("[submissions] %d/%d (flushed: %d, failed: %d)",
                     i, len(ciks), total, len(failed))
    total += flush_submissions(db_path, buffered)
    if failed:
        log.warning("[submissions] %d CIKs failed: %s", len(failed), failed[:10])
    return total


def upsert_companyfacts(client: edgar.EdgarClient, ciks: list[int], db_path: Path,
                        raw_dir: Path, concept_chains: dict) -> int:
    """Pull companyfacts per CIK, extract via config'd chains, upsert per CIK."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    failed: list[int] = []
    for i, cik in enumerate(ciks, start=1):
        try:
            facts = client.company_facts(cik)
            edgar.cache_raw(raw_dir / f"companyfacts_{cik:010d}.json", facts)
            rows = edgar.EdgarClient.extract_facts(facts, cik, concept_chains)
        except Exception as e:
            log.warning("[companyfacts] CIK %d failed: %s", cik, e)
            failed.append(cik)
            continue
        total_rows += flush_facts(db_path, rows)
        if i % 25 == 0:
            log.info("[companyfacts] %d/%d (rows so far: %d, failed: %d)",
                     i, len(ciks), total_rows, len(failed))
    if failed:
        log.warning("[companyfacts] %d CIKs failed: %s", len(failed), failed[:10])
    return total_rows


def reparse_from_cache(db_path: Path, raw_dir: Path, concept_chains: dict) -> None:
    """Rebuild edgar_submissions + edgar_facts from cached raw JSON — no HTTP.

    snapshot_date for cached submissions = the cache file's mtime date (when
    the knowledge was actually fetched), NOT today: a re-parse must not
    fabricate fresher knowledge than we have.
    """
    n_files = 0
    n_rows = 0
    buffered: list[dict[str, Any]] = []
    for cik, path, payload in edgar.iter_cached_payloads(raw_dir, "submissions"):
        snap = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        try:
            buffered.append(edgar.EdgarClient.submissions_row(payload, snap))
        except Exception as e:
            log.warning("[cache/submissions] CIK %d failed: %s", cik, e)
            continue
        n_files += 1
        if len(buffered) >= FLUSH_EVERY:
            n_rows += flush_submissions(db_path, buffered)
            buffered = []
    n_rows += flush_submissions(db_path, buffered)
    log.info("[cache/submissions] parsed %d files -> %d rows upserted", n_files, n_rows)

    f_files = 0
    f_rows = 0
    for cik, _path, payload in edgar.iter_cached_payloads(raw_dir, "companyfacts"):
        try:
            rows = edgar.EdgarClient.extract_facts(payload, cik, concept_chains)
        except Exception as e:
            log.warning("[cache/companyfacts] CIK %d failed: %s", cik, e)
            continue
        f_files += 1
        f_rows += flush_facts(db_path, rows)
        if f_files % 100 == 0:
            log.info("[cache/companyfacts] %d files (rows so far: %d)", f_files, f_rows)
    log.info("[cache/companyfacts] parsed %d files -> %d rows upserted", f_files, f_rows)


def coverage_summary(db_path: Path) -> None:
    with store.connect(db_path, read_only=True) as con:
        nf = con.execute("SELECT count(*) FROM edgar_facts").fetchone()[0]
        nc = con.execute("SELECT count(DISTINCT cik) FROM edgar_facts").fetchone()[0]
        log.info("[edgar_facts] %d rows across %d CIKs", nf, nc)
        ns = con.execute("SELECT count(*) FROM edgar_submissions").fetchone()[0]
        log.info("[edgar_submissions] %d rows", ns)
        log.info("[coverage] facts per canonical concept:")
        cov = con.execute("""
            SELECT concept, count(DISTINCT cik) AS n_ciks, count(*) AS n_rows
            FROM edgar_facts
            GROUP BY concept
            ORDER BY n_ciks DESC
        """).fetchall()
        for c, n_ciks, n_rows in cov:
            log.info("  %-32s %d CIKs, %d rows", c, n_ciks, n_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=["both", "energy", "ijr"], default="both")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-submissions", action="store_true")
    parser.add_argument("--skip-companyfacts", action="store_true")
    parser.add_argument("--from-cache", action="store_true",
                        help="re-parse cached raw JSON into DuckDB; no network at all")
    args = parser.parse_args()

    cfg = Config.load()
    store.init_db(cfg.duckdb_path)
    raw_dir = cfg.raw_dir / "edgar"

    if args.from_cache:
        reparse_from_cache(cfg.duckdb_path, raw_dir, cfg.edgar_concepts)
        coverage_summary(cfg.duckdb_path)
        return 0

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
    coverage_summary(cfg.duckdb_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
