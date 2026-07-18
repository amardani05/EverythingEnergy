"""Prices ingestion — yfinance primary in v1, Stooq deferred for cross-check.

Per turn 8 decision: Stooq's free bulk endpoint now returns Unauthorized and
the per-ticker URL requires a captcha-acquired apikey, so v1 ships on
yfinance with explicit "fragile/unofficial" flagging. Every backtest output
that reads from `prices` carries the source label so we can audit what
fraction of any signal is built on yfinance bytes.

When a real second source lands (Stooq apikey, Tiingo, Polygon, etc.) the
existing schema already supports it — `source` is in the prices PK and the
`as_of_prices` view prefers stooq over yfinance — no migration needed.
"""

from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import requests

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)


PRICES_SCHEMA: dict[str, Any] = {
    "ticker": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "source": pl.Utf8,
    "div_adjusted": pl.Boolean,
    "split_adjusted": pl.Boolean,
}

# Mirrors the corporate_actions table in store.py.
ACTIONS_SCHEMA: dict[str, Any] = {
    "ticker": pl.Utf8,
    "date": pl.Date,
    "kind": pl.Utf8,        # 'dividend' | 'split'
    "value": pl.Float64,    # cash per share | new/old ratio
    "source": pl.Utf8,
}


def _empty_prices_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=PRICES_SCHEMA)


def _empty_actions_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=ACTIONS_SCHEMA)


# ---------- yfinance: PRIMARY in v1 ----------

def parse_yf_history(yf_df: pd.DataFrame | None, ticker: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Convert one yfinance `history()` frame (auto_adjust=False, actions=True)
    into `(prices, corporate_actions)` polars frames.

    Pure function — tests drive it with a synthetic pandas frame, no network.
    Dividend rows are days where `Dividends != 0` (cash per share on ex-date);
    split rows are days where `Stock Splits != 0` (new/old ratio, e.g. 2.0).
    """
    if yf_df is None or yf_df.empty:
        return _empty_prices_frame(), _empty_actions_frame()
    base = (
        pl.from_pandas(yf_df.reset_index().rename(columns={
            "Date": "date", "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
            "Dividends": "dividends", "Stock Splits": "splits",
        }))
        .with_columns(pl.col("date").cast(pl.Date))
    )
    prices = (
        base.select(["date", "open", "high", "low", "close", "volume"])
        .with_columns([
            pl.col("volume").cast(pl.Float64),
            pl.lit(ticker.upper()).alias("ticker"),
            pl.lit("yfinance").alias("source"),
            pl.lit(False).alias("div_adjusted"),    # auto_adjust=False
            pl.lit(True).alias("split_adjusted"),   # yfinance always splits
        ])
        .select(list(PRICES_SCHEMA.keys()))
    )
    action_frames: list[pl.DataFrame] = []
    for col, kind in (("dividends", "dividend"), ("splits", "split")):
        if col not in base.columns:
            continue
        f = base.filter(pl.col(col).fill_null(0.0) != 0.0).select([
            pl.lit(ticker.upper()).alias("ticker"),
            pl.col("date"),
            pl.lit(kind).alias("kind"),
            pl.col(col).cast(pl.Float64).alias("value"),
            pl.lit("yfinance").alias("source"),
        ])
        if f.height:
            action_frames.append(f)
    actions = pl.concat(action_frames) if action_frames else _empty_actions_frame()
    return prices, actions


def yfinance_history(
    ticker: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Pull full split-adjusted (NOT div-adjusted) daily history from yfinance.
    Returns `(prices, corporate_actions)`.

    Why auto_adjust=False: yfinance's "adjusted close" lumps splits AND
    dividends into one number, which silently changes when a future
    dividend is paid (forward-adjusted). We store unadjusted closes + the
    explicit dividend/split series so total-return calculations are explicit
    and auditable.
    """
    import yfinance as yf

    yf_df = yf.Ticker(ticker).history(
        start=start or "1990-01-01",
        end=end,
        auto_adjust=False,
        actions=True,
    )
    return parse_yf_history(yf_df, ticker)


def yfinance_batch(
    tickers: Iterable[str],
    *,
    start: str | None = None,
    end: str | None = None,
    throttle_sec: float = 0.1,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Per-ticker pull with a small inter-call throttle (yfinance has no
    official rate limit but does ban hammer at scale). Returns
    `(prices, corporate_actions)` concatenated across all tickers; misses are
    skipped silently but logged. Caller is responsible for writing to DuckDB.
    """
    price_frames: list[pl.DataFrame] = []
    action_frames: list[pl.DataFrame] = []
    skipped: list[str] = []
    for i, t in enumerate(tickers, start=1):
        try:
            prices, actions = yfinance_history(t, start=start, end=end)
            if prices.height == 0:
                skipped.append(t)
            else:
                price_frames.append(prices)
                if actions.height:
                    action_frames.append(actions)
        except Exception as e:
            log.warning("[yfinance] %s failed: %s", t, e)
            skipped.append(t)
        if i % 50 == 0:
            log.info("[yfinance] pulled %d (skipped=%d)", i, len(skipped))
        time.sleep(throttle_sec)
    if skipped:
        log.info("[yfinance] %d tickers returned no data: %s", len(skipped),
                 skipped[:20] + (["..."] if len(skipped) > 20 else []))
    prices_out = pl.concat(price_frames) if price_frames else _empty_prices_frame()
    actions_out = pl.concat(action_frames) if action_frames else _empty_actions_frame()
    return prices_out, actions_out


# ---------- Stooq: future cross-check, NOT used in v1 ----------

STOOQ_BULK_URL = "https://stooq.com/db/d/?b=d_us_txt"
STOOQ_PER_TICKER_URL = "https://stooq.com/q/d/l/?s={ticker}.us&i=d"
STOOQ_API_KEY_PAGE = "https://stooq.com/q/d/?s=aaon.us&get_apikey"  # captcha


@dataclass
class StooqDownloader:
    """Deferred. As of v1, Stooq bulk requires a paid account and per-ticker
    requires a captcha-acquired apikey (see STOOQ_API_KEY_PAGE). Keep this
    class in tree so the cross-check path is easy to enable later — just
    pass `apikey` (from `STOOQ_API_KEY` env var) and call `per_ticker()`.
    """
    bulk_url: str = STOOQ_BULK_URL
    apikey: str | None = None
    user_agent: str = "EverythingEnergy/SignalEngine (amard2@illinois.edu)"

    def per_ticker(self, ticker: str) -> pl.DataFrame:
        """Pull one Stooq daily CSV. Requires self.apikey set."""
        if not self.apikey:
            raise RuntimeError(
                "Stooq per-ticker requires an apikey. Obtain via captcha at "
                f"{STOOQ_API_KEY_PAGE} and set STOOQ_API_KEY in the env."
            )
        url = STOOQ_PER_TICKER_URL.format(ticker=ticker.lower()) + f"&apikey={self.apikey}"
        r = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=30)
        r.raise_for_status()
        return _parse_stooq_csv(r.text, ticker.upper())

    @staticmethod
    def parse_zip_for_tickers(zip_path: Path, tickers: Iterable[str]) -> pl.DataFrame:
        """Legacy bulk-zip parser. Retained for the day Stooq reopens bulk."""
        wanted = {t.lower() + ".us.txt" for t in tickers}
        frames: list[pl.DataFrame] = []
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename.split("/")[-1].lower()
                if name not in wanted:
                    continue
                ticker = name.removesuffix(".us.txt").upper()
                with zf.open(info) as fh:
                    df = _parse_stooq_csv(fh.read().decode("utf-8"), ticker)
                if df.height > 0:
                    frames.append(df)
        return pl.concat(frames) if frames else _empty_prices_frame()


def _parse_stooq_csv(text: str, ticker: str) -> pl.DataFrame:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, object]] = []
    for r in reader:
        try:
            rows.append({
                "ticker": ticker,
                "date": r["Date"],
                "open":   float(r["Open"]),
                "high":   float(r["High"]),
                "low":    float(r["Low"]),
                "close":  float(r["Close"]),
                "volume": float(r.get("Volume") or 0),
                "source": "stooq",
                # Conservative: Stooq's div handling per-ticker is undocumented.
                # When we re-enable Stooq, verify against a known dividend payer
                # on first connect and overwrite this column row-by-row.
                "div_adjusted": False,
                "split_adjusted": True,
            })
        except (KeyError, ValueError):
            continue
    if not rows:
        return _empty_prices_frame()
    return (
        pl.DataFrame(rows)
        .with_columns(pl.col("date").str.to_date("%Y-%m-%d"))
        .select(list(PRICES_SCHEMA.keys()))
    )
