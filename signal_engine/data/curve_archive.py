"""Append-only archive of futures contract-month closes.

Yahoo drops a contract when it expires, so what is not saved is lost. Each run writes
one immutable file holding only new or changed closes, stamped with the capture time;
nothing is ever overwritten, so revisions stay visible and point-in-time reads work.

Rows captured long after their date are backfill: valid history for that one contract,
but the contracts that were nearer at the time are missing. `curve` therefore refuses
them by default, because a rebuilt "front month" from backfill would be wrong.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

EXCHANGE = {"CL": "NYM", "BZ": "NYM", "NG": "NYM", "RB": "NYM", "HO": "NYM",
            "GC": "CMX", "SI": "CMX", "HG": "CMX", "ALI": "CMX", "AUP": "CMX"}
MONTH_CODES = "FGHJKMNQUVXZ"
MONTHS_AHEAD = 49
MIN_CONTRACTS = {"CL": 24, "BZ": 12, "NG": 24, "RB": 12, "HO": 12, "GC": 10, "SI": 8, "HG": 10, "ALI": 4, "AUP": 8}
SETTLED_HOUR = 17  # local (Central) hour after which today's session is closed
LIVE_LAG_DAYS = 7  # captured within this many days of its date = live, else backfill
TOL = 1e-6

SCHEMA: dict[str, Any] = {"symbol": pl.Utf8, "root": pl.Utf8, "delivery": pl.Utf8, "date": pl.Date, "close": pl.Float64,
          "volume": pl.Float64, "captured_at": pl.Datetime("us")}


def contracts(today: date, months: int = MONTHS_AHEAD) -> list[tuple[str, str, str]]:
    """(symbol, root, YYYY-MM) for every delivery month from this one on."""
    out = []
    for root, ex in EXCHANGE.items():
        for k in range(months):
            y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12
            out.append((f"{root}{MONTH_CODES[m]}{y % 100:02d}.{ex}", root, f"{y}-{m + 1:02d}"))
    return out


def to_request(now: datetime, known: pl.DataFrame) -> list[tuple[str, str, str]]:
    """Contracts already in the archive and unexpired; every possible month on Mondays
    (new listings) or for a root that is empty. Asking for hundreds of unlisted symbols
    nightly gets the job throttled."""
    every = contracts(now.date())
    seen, roots = set(known["symbol"].to_list()), set(known["root"].to_list())
    return [c for c in every if now.weekday() == 0 or c[0] in seen or c[1] not in roots]


def last_settled(now: datetime) -> date:
    """Newest session that has closed; later bars are still moving."""
    return now.date() if now.hour >= SETTLED_HOUR else now.date() - timedelta(days=1)


def clean(obs: pl.DataFrame, now: datetime) -> pl.DataFrame:
    """Drop unsettled and unusable closes. Negative prices are real (CL, April 2020)."""
    return (
        obs.filter(pl.col("close").is_not_null() & pl.col("close").is_finite() & (pl.col("close") != 0)
                   & (pl.col("date") <= last_settled(now)))
        .with_columns(pl.lit(now).dt.cast_time_unit("us").alias("captured_at"))
        .select(pl.col(c).cast(t) for c, t in SCHEMA.items())
        .unique(["symbol", "date"], keep="last", maintain_order=True)
    )


def read(root: Path, asof: datetime | None = None) -> pl.DataFrame:
    """Every observation captured up to `asof`."""
    files = sorted(root.glob("*.parquet"))
    if not files:
        return pl.DataFrame(schema=SCHEMA)
    df = pl.concat([pl.read_parquet(f) for f in files]).sort("captured_at")
    return df if asof is None else df.filter(pl.col("captured_at") <= asof)


def latest(df: pl.DataFrame) -> pl.DataFrame:
    """Newest capture per contract and date, with when it was first seen."""
    return df.sort("captured_at").group_by("symbol", "date", maintain_order=True).agg(
        pl.col("root", "delivery", "close", "volume", "captured_at").last(),
        pl.col("captured_at").first().alias("first_seen"),
        (pl.len() > 1).alias("revised"),
    )


def delta(obs: pl.DataFrame, known: pl.DataFrame) -> pl.DataFrame:
    """Rows that are new or whose close changed."""
    prev = latest(known).select("symbol", "date", pl.col("close").alias("_prev"))
    return (
        obs.join(prev, on=["symbol", "date"], how="left")
        .filter(pl.col("_prev").is_null() | ((pl.col("close") - pl.col("_prev")).abs() > TOL))
        .select(list(SCHEMA))
    )


def write(root: Path, rows: pl.DataFrame, now: datetime) -> Path | None:
    if rows.is_empty():
        return None
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{now:%Y%m%dT%H%M%S%f}.parquet"
    if dest.exists():
        raise FileExistsError(dest)
    tmp = dest.with_suffix(".tmp")
    rows.write_parquet(tmp)
    tmp.rename(dest)
    return dest


def coverage(obs: pl.DataFrame, now: datetime) -> dict[str, int]:
    """Contracts per root with a close in the last week."""
    recent = obs.filter(pl.col("date") >= now.date() - timedelta(days=7))
    got = dict(recent.group_by("root").agg(pl.col("symbol").n_unique()).iter_rows())
    return {r: int(got.get(r, 0)) for r in EXCHANGE}


def curve(df: pl.DataFrame, *, allow_backfill: bool = False) -> pl.DataFrame:
    """root, date, m (1 = nearest listed), delivery, close. Live rows only by default."""
    x = latest(df)
    if not allow_backfill:
        x = x.filter((pl.col("first_seen").dt.date() - pl.col("date")).dt.total_days() <= LIVE_LAG_DAYS)
    return x.with_columns(
        pl.col("delivery").rank("ordinal").over("root", "date").cast(pl.Int32).alias("m")
    ).select("root", "date", "m", "delivery", "symbol", "close", "volume", "revised").sort("root", "date", "m")
