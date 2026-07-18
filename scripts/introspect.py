#!/usr/bin/env python3
"""One-shot introspection — verifies live schemas before bulk ingest.

v2 (post turn-8 pivot): yfinance primary, IJR manual-drop, Stooq future.

Hits, once each:
  1. EDGAR ticker -> CIK map
  2. EDGAR submissions for AAON
  3. EDGAR companyfacts for AAON
  4. yfinance daily history for AAON (last 30 days)
  5. IJR raw-drop directory check (just reports what's there)
  6. FRED ping for DGS10 (only if FRED_API_KEY is set)

Nothing is written to DuckDB. This is read-only recon.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date

from signal_engine.config import Config
from signal_engine.data import edgar, prices
from signal_engine.data.supplements.fred import FredClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("introspect")


def hr(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def main() -> int:
    cfg = Config.load()
    ua = cfg.edgar_user_agent
    log.info("using EDGAR User-Agent: %s", ua)
    client = edgar.EdgarClient(user_agent=ua, rate_limit_per_sec=cfg.edgar_rate_limit_per_sec)

    # 1. ticker -> CIK
    hr("EDGAR ticker->CIK map")
    tmap = client.ticker_cik_map()
    print(f"total companies: {len(tmap)}")
    cik = edgar.cik_for_ticker(tmap, "AAON")
    print(f"AAON -> CIK {cik}")
    assert cik is not None

    # 2. submissions
    hr("EDGAR submissions(AAON)")
    sub = client.submissions(cik)
    keep = {k: sub[k] for k in ("cik", "name", "sic", "sicDescription",
                                "fiscalYearEnd", "tickers", "exchanges") if k in sub}
    print(json.dumps(keep, indent=2, default=str))

    # 3. companyfacts + concept-chain audit
    hr("EDGAR companyfacts(AAON) concept-chain coverage")
    facts = client.company_facts(cik)
    chains = cfg.edgar_concepts
    for canonical, spec in chains.items():
        tax_block = facts.get("facts", {}).get(spec["taxonomy"], {})
        chosen = next((t for t in spec["tags"] if t in tax_block), None)
        mark = "OK" if chosen else "MISS"
        print(f"  [{mark:>4}] {canonical:<32} -> {chosen}")

    rows = edgar.EdgarClient.extract_facts(facts, cik, chains)
    print(f"\nextract_facts produced {len(rows)} rows for AAON")

    # 4. yfinance — primary v1 path
    hr("yfinance(AAON) recent history")
    yf, yf_actions = prices.yfinance_history("AAON", start="2025-01-01")
    print(f"rows: {yf.height}; columns: {yf.columns}")
    print(f"corporate actions rows: {yf_actions.height}")
    if yf.height > 0:
        print("first 3 rows:")
        print(yf.head(3))
        print("last 3 rows:")
        print(yf.tail(3))

    # 5. IJR raw drop directory
    hr("IJR raw-drop directory")
    ijr_dir = cfg.raw_dir / "ijr"
    ijr_dir.mkdir(parents=True, exist_ok=True)
    csvs = sorted(ijr_dir.glob("*.csv"))
    if not csvs:
        print(f"{ijr_dir} is empty.")
        print(f"  To populate: open {cfg.raw['ijr']['download_page']} and click 'Download holdings (CSV)'.")
        print(f"  Save the file into {ijr_dir}/ and run scripts/snapshot_ijr.py")
    else:
        print(f"{ijr_dir} contains:")
        for p in csvs:
            print(f"  {p.name} ({p.stat().st_size} bytes)")

    # 6. FRED
    hr("FRED ping (DGS10)")
    if not os.environ.get("FRED_API_KEY"):
        print("FRED_API_KEY not set. Sign up at https://fred.stlouisfed.org/docs/api/api_key.html")
    else:
        try:
            obs = FredClient().observations("DGS10")
            print(f"keys: {list(obs.keys())}")
            print(f"count: {obs.get('count')}")
            print(f"first obs: {obs.get('observations', [{}])[0]}")
            print(f"last  obs: {obs.get('observations', [{}])[-1]}")
        except Exception as e:
            print(f"FRED ping failed: {e}")

    hr("done")
    print(f"introspection as of {date.today().isoformat()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
