"""Universe construction — iShares IJR ETF holdings as the S&P 600 proxy.

iShares posts the full holdings file daily as a CSV. We snapshot it on
ingest with the snapshot_date being the file's reporting date (parsed from
the CSV header, NOT the download wall-clock — the file may publish on T+1).

Three membership modes downstream (config: universe.membership_mode):

  * ijr_current  - today's IJR snapshot applied to all historical dates.
                   BIASED toward survivors; every output row carrying a
                   `survivorship_clean=False` flag.
  * ijr_pit      - point-in-time membership from snapshot history. Only
                   meaningful from the first snapshot we recorded onward.
                   Clean, but small until we accumulate months of snapshots.
  * none         - no membership filter; diagnostic. Shows how much of the
                   measured effect comes from the universe definition.

Every backtest emits results for `ijr_current` AND `none` so the membership
contribution is always visible — per turn 7 decision.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import requests

log = logging.getLogger(__name__)


IJR_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239774/ishares-core-sp-small-cap-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IJR_holdings&dataType=fund"
)


@dataclass
class IJRDownloader:
    holdings_url: str = IJR_HOLDINGS_URL
    user_agent: str = "EverythingEnergy/SignalEngine (amard2@illinois.edu)"

    def fetch_raw(self) -> str:
        log.info("[ijr] fetching holdings CSV")
        r = requests.get(self.holdings_url, headers={"User-Agent": self.user_agent}, timeout=60)
        r.raise_for_status()
        return r.text

    def save_raw(self, out_dir: Path) -> Path:
        """Save the unparsed CSV, named by the report date found in its header
        (or today's date if not found)."""
        text = self.fetch_raw()
        as_of = parse_as_of_date(text) or date.today()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"ijr_holdings_{as_of.isoformat()}.csv"
        out_path.write_text(text)
        return out_path


# ---------- parsing ----------

# iShares CSVs prepend ~9 header rows of fund metadata, then the table.
# Layout as observed 2026-06: line 2 is `Fund Holdings as of,"Jun 12, 2026"`
# (date is a quoted CSV cell containing an internal comma). The regex allows
# the optional surrounding quote.

_AS_OF_RE = re.compile(
    r'Fund Holdings as of[,\s]+"?([A-Za-z]+ \d{1,2},?\s*\d{4})"?', re.I,
)
_AS_OF_RE_NUM = re.compile(
    r'Fund Holdings as of[,\s]+"?(\d{1,2}/\d{1,2}/\d{2,4})"?', re.I,
)


def parse_as_of_date(text: str) -> date | None:
    """Pull the 'as of' date from the iShares header. Returns None if not found."""
    m = _AS_OF_RE.search(text) or _AS_OF_RE_NUM.search(text)
    if not m:
        return None
    from datetime import datetime
    s = m.group(1).strip().rstrip(",")
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_holdings_csv(text: str) -> tuple[date | None, pl.DataFrame]:
    """Parse the iShares holdings CSV into (as_of, df)."""
    # Akamai/CloudFront bot mitigation on the iShares CDN sometimes returns
    # the product PAGE (HTML) with a fake `text/csv` content-type. Detect that
    # and fail loudly — the calling code should fall back to a manual download.
    stripped = text.lstrip()
    if stripped.startswith("<") or "<!DOCTYPE" in stripped[:200].upper():
        raise ValueError(
            "iShares returned HTML instead of CSV (Akamai bot mitigation). "
            "Workaround: manually download IJR_holdings.csv from the iShares "
            "product page once per day and place it in data_store/raw/ijr/."
        )

    as_of = parse_as_of_date(text)
    lines = text.splitlines()

    # Find the header line of the holdings table.
    header_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("ticker,"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("could not find 'Ticker,' header row in IJR holdings CSV")

    table_lines = []
    for line in lines[header_idx:]:
        # The holdings table ends when an empty line or footer text appears.
        if not line.strip():
            break
        table_lines.append(line)

    reader = csv.DictReader(io.StringIO("\n".join(table_lines)))
    rows: list[dict[str, Any]] = []
    for r in reader:
        # Schema as of 2026-06: Ticker, Name, Type, Sector, Asset Class,
        # Market Value, Notional Value, Quantity, Price, Location, Exchange,
        # Currency, FX Rate, Market Currency, Accrual Date, Market Weight,
        # Notional Weight. Older snapshots used `Weight (%)` / `Shares`.
        rows.append({
            "snapshot_date": as_of,
            "ticker": (r.get("Ticker") or "").strip().upper() or None,
            "name": (r.get("Name") or "").strip() or None,
            "weight": _to_float(
                r.get("Market Weight")
                or r.get("Weight (%)")
                or r.get("Weight")
            ),
            "shares": _to_float(r.get("Quantity") or r.get("Shares")),
            "market_value": _to_float(r.get("Market Value") or r.get("Market Value ($)")),
            "asset_class": (r.get("Asset Class") or "").strip() or None,
            "sector": (r.get("Sector") or "").strip() or None,
        })
    # Filter to equity holdings only. iShares includes cash sweeps and
    # derivatives (e.g. XTSLA = BlackRock Cash Fund). We're building the
    # equity universe for cross-sectional scoring.
    equity_rows = [
        r for r in rows
        if r["ticker"] and (r["asset_class"] or "").lower() == "equity"
    ]
    df = pl.DataFrame(equity_rows)
    return as_of, df


def _to_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", "").replace("$", "").rstrip("%")
    if not s or s in {"-", "--"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None
