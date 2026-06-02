"""Prices ingestion — Stooq primary, yfinance fallback.

Stooq delivers the entire US daily EOD universe as a single zip:
    https://stooq.com/db/d/?b=d_us_txt
US tickers carry a ".us" suffix inside the archive (e.g. `aapl.us.txt`).
Stooq does not have an authenticated/per-ticker API; the canonical workflow
is: download the zip nightly, extract, parse the small per-ticker CSVs.

Stooq's `Close` is split-adjusted but dividend handling is NOT clearly
documented and varies by ticker. We verify against a known dividend-payer
on first connect (see scripts/introspect.py) and set `div_adjusted` on
each row based on that finding.

yfinance is used as a per-ticker fallback when Stooq is missing a name
(typical for very recent IPOs / spinoffs). yfinance is flagged unofficial
and is never the sole source for a production signal.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import polars as pl
import requests

log = logging.getLogger(__name__)


# ---------- Stooq ----------

STOOQ_BULK_URL = "https://stooq.com/db/d/?b=d_us_txt"


@dataclass
class StooqDownloader:
    bulk_url: str = STOOQ_BULK_URL
    user_agent: str = "EverythingEnergy/SignalEngine (amard2@illinois.edu)"

    def download_bulk_zip(self, out_path: Path) -> Path:
        """Download the daily US bulk zip. ~50-100MB; do this once per day,
        never per-ticker. Caller is responsible for not hitting Stooq more
        than necessary."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("[stooq] downloading bulk US zip -> %s", out_path)
        # Stooq sometimes 403s without a real UA; mirror that of a browser-ish client.
        with requests.get(self.bulk_url, headers={"User-Agent": self.user_agent}, stream=True, timeout=300) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
        return out_path

    @staticmethod
    def parse_zip_for_tickers(zip_path: Path, tickers: Iterable[str]) -> pl.DataFrame:
        """Extract just the per-ticker CSVs we care about and concatenate.

        Stooq archives lowercase the filename and suffix with `.us.txt`. They
        also nest by exchange (`data/daily/us/nasdaq stocks/`, etc.) — we walk
        the whole archive once and pick matches.
        """
        wanted = {t.lower() + ".us.txt" for t in tickers}
        frames: list[pl.DataFrame] = []
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename.split("/")[-1].lower()
                if name not in wanted:
                    continue
                ticker = name.removesuffix(".us.txt").upper()
                with zf.open(info) as fh:
                    df = StooqDownloader._parse_one(fh.read().decode("utf-8"), ticker)
                if df is not None and df.height > 0:
                    frames.append(df)
        if not frames:
            return pl.DataFrame(schema={
                "ticker": pl.Utf8, "date": pl.Date,
                "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
                "close": pl.Float64, "volume": pl.Float64,
                "source": pl.Utf8, "div_adjusted": pl.Boolean, "split_adjusted": pl.Boolean,
            })
        return pl.concat(frames)

    @staticmethod
    def _parse_one(text: str, ticker: str) -> pl.DataFrame | None:
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for r in reader:
            try:
                rows.append({
                    "ticker": ticker,
                    "date": r["Date"],          # YYYY-MM-DD
                    "open":   float(r["Open"]),
                    "high":   float(r["High"]),
                    "low":    float(r["Low"]),
                    "close":  float(r["Close"]),
                    "volume": float(r.get("Volume") or 0),
                    "source": "stooq",
                    # Conservative defaults until we verify on first connect:
                    "div_adjusted": False,
                    "split_adjusted": True,
                })
            except (KeyError, ValueError):
                continue
        if not rows:
            return None
        return pl.DataFrame(rows).with_columns(pl.col("date").str.to_date("%Y-%m-%d"))


# ---------- yfinance (fallback only) ----------

def yfinance_history(ticker: str, *, start: str | None = None) -> pl.DataFrame:
    """Fallback per-ticker pull. Returns the same schema as Stooq output but
    with source='yfinance'. Always treat the result as suspect — flag rows
    in price-vs-price reconciliation downstream.
    """
    import yfinance as yf

    yf_df = yf.Ticker(ticker).history(start=start or "1990-01-01", auto_adjust=False, actions=True)
    if yf_df is None or yf_df.empty:
        return pl.DataFrame(schema={
            "ticker": pl.Utf8, "date": pl.Date,
            "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
            "close": pl.Float64, "volume": pl.Float64,
            "source": pl.Utf8, "div_adjusted": pl.Boolean, "split_adjusted": pl.Boolean,
        })
    yf_df = yf_df.reset_index()
    return pl.from_pandas(yf_df.rename(columns={
        "Date": "date", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })[["date", "open", "high", "low", "close", "volume"]]).with_columns([
        pl.col("date").cast(pl.Date),
        pl.lit(ticker.upper()).alias("ticker"),
        pl.lit("yfinance").alias("source"),
        pl.lit(False).alias("div_adjusted"),   # auto_adjust=False above
        pl.lit(True).alias("split_adjusted"),  # yfinance always adjusts splits
    ]).select(["ticker", "date", "open", "high", "low", "close", "volume",
               "source", "div_adjusted", "split_adjusted"])
