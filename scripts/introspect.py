#!/usr/bin/env python3
"""One-shot introspection — verifies live schemas before bulk ingest.

Hits, once each:
  1. EDGAR ticker -> CIK map (one HTTP call)
  2. EDGAR submissions for AAON (one call; small-cap industrial)
  3. EDGAR companyfacts for AAON (one call; samples concept availability)
  4. iShares IJR holdings CSV (one call; shows header + table schema)
  5. Stooq bulk zip header check (HEAD request; we DON'T download the full zip here)
  6. FRED ping for DGS10 (one call; only if FRED_API_KEY is set)

Prints column listings so we can confirm field names match what the
ingestion code assumes. No data is written to DuckDB; this is read-only
recon.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date

import requests

from signal_engine.config import Config
from signal_engine.data import edgar, prices, universe
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

    # 1. ticker -> CIK map
    hr("EDGAR ticker->CIK map")
    tmap = client.ticker_cik_map()
    sample_keys = list(tmap.keys())[:3]
    print(f"total companies: {len(tmap)}")
    print("sample entries:")
    for k in sample_keys:
        print(f"  {k}: {tmap[k]}")
    cik = edgar.cik_for_ticker(tmap, "AAON")
    print(f"\nAAON -> CIK {cik}")
    assert cik is not None, "expected AAON in current ticker map"

    # 2. submissions
    hr("EDGAR submissions(AAON)")
    sub = client.submissions(cik)
    keep = {k: sub[k] for k in (
        "cik", "name", "sic", "sicDescription", "fiscalYearEnd",
        "tickers", "exchanges", "category", "stateOfIncorporation",
    ) if k in sub}
    print(json.dumps(keep, indent=2, default=str))
    print(f"\nrecent filings columns: {list(sub.get('filings', {}).get('recent', {}).keys())}")

    # 3. companyfacts
    hr("EDGAR companyfacts(AAON)")
    facts = client.company_facts(cik)
    taxonomies = list(facts.get("facts", {}).keys())
    print(f"taxonomies: {taxonomies}")
    for tax in taxonomies:
        tags = list(facts["facts"][tax].keys())
        print(f"  {tax}: {len(tags)} tags (first 10: {tags[:10]})")

    # Confirm our config'd concept fallback chains hit *something* per concept
    print("\nconcept-chain coverage on AAON:")
    chains = cfg.edgar_concepts
    chosen: dict[str, str | None] = {}
    for canonical, spec in chains.items():
        tax = spec["taxonomy"]
        block = facts.get("facts", {}).get(tax, {})
        for tag in spec["tags"]:
            if tag in block:
                chosen[canonical] = tag
                break
        else:
            chosen[canonical] = None
    for k, v in chosen.items():
        mark = "OK" if v else "MISS"
        print(f"  [{mark:>4}] {k:<32} -> {v}")

    # Look at one quarterly EPS fact to see exact shape
    eps_block = facts.get("facts", {}).get("us-gaap", {}).get("EarningsPerShareDiluted", {})
    if eps_block:
        units = list(eps_block.get("units", {}).keys())
        print(f"\nEPS_diluted units: {units}")
        sample_unit = units[0]
        sample_facts = eps_block["units"][sample_unit][:3]
        print(f"EPS_diluted sample facts ({sample_unit}, first 3):")
        for f in sample_facts:
            print(f"  {f}")

    # 4. extract & demo bitemporal rows for AAON
    hr("extract_facts() demo (AAON, first 5 rows)")
    rows = edgar.EdgarClient.extract_facts(facts, cik, chains)
    print(f"extracted {len(rows)} fact rows")
    for r in rows[:5]:
        print(f"  {r}")

    # 5. iShares IJR
    hr("iShares IJR holdings CSV")
    ijr = universe.IJRDownloader(holdings_url=cfg.raw["ijr"]["holdings_url"])
    try:
        text = ijr.fetch_raw()
        as_of, df = universe.parse_holdings_csv(text)
        print(f"header excerpt:\n{text.splitlines()[:5]}")
        print(f"\nparsed as_of: {as_of}")
        print(f"parsed rows: {df.height}; columns: {df.columns}")
        print("first 5 holdings:")
        print(df.head(5))
        # Sector cross-check vs our SIC mapping
        if "sector" in df.columns:
            print("\niShares-reported sector distribution:")
            print(df.group_by("sector").len().sort("len", descending=True))
    except Exception as e:  # noqa: BLE001
        print(f"IJR fetch failed: {e}")
        print("(URL may have rotated; check ishares.com IJR page for current AJAX endpoint)")

    # 6. Stooq HEAD only - no bulk download here
    hr("Stooq bulk endpoint HEAD")
    try:
        r = requests.head(prices.STOOQ_BULK_URL, allow_redirects=True, timeout=15,
                          headers={"User-Agent": "EverythingEnergy/SignalEngine (amard2@illinois.edu)"})
        print(f"status: {r.status_code}")
        print(f"final url: {r.url}")
        print(f"content-type: {r.headers.get('Content-Type')}")
        print(f"content-length: {r.headers.get('Content-Length')}")
    except Exception as e:  # noqa: BLE001
        print(f"Stooq HEAD failed: {e}")

    # 7. FRED ping (optional)
    hr("FRED ping (DGS10)")
    if not os.environ.get("FRED_API_KEY"):
        print("FRED_API_KEY not set in env; skipping. Get a key at https://fred.stlouisfed.org/docs/api/api_key.html")
    else:
        try:
            fred = FredClient()
            obs = fred.observations("DGS10")
            print(f"FRED keys: {list(obs.keys())}")
            print(f"first obs: {obs.get('observations', [{}])[0]}")
            print(f"obs count: {obs.get('count')}")
        except Exception as e:  # noqa: BLE001
            print(f"FRED ping failed: {e}")

    hr("done")
    print(f"introspection as of {date.today().isoformat()}.")
    print("If anything above looks off, fix before bulk ingest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
