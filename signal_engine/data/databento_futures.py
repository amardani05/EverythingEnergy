"""CME futures settlements and open interest from Databento, behind a hard budget.

Nothing is bought unless a fresh quote fits every cap. Spend is written to an
append-only ledger BEFORE the download, so a failed download still counts against
the caps: the guard can overstate spend, never understate it. Vendor data is
licensed and stays in gitignored data_store/databento/.
"""

from __future__ import annotations

import fcntl
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

DATASET = "GLBX.MDP3"
MAX_REQUEST = 2.0
MONTHLY_CAP = 10.0  # cash bucket, per UTC calendar month
CREDIT_CAP = 110.0  # lifetime cap on the $125 sign-up credit, with headroom
SETTLE, OPEN_INTEREST = 3, 9
FINAL, ACTUAL, INTRADAY = 1, 2, 8  # CME stat_flags bits
DELETE = 2  # StatUpdateAction


class BudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    """One root and schema. `rank` c = calendar order, n = open-interest order."""

    name: str
    symbols: tuple[str, ...]
    stype: str
    schema: str


def _ranked(root: str, rank: str, n: int) -> tuple[str, ...]:
    return tuple(f"{root}.{rank}.{i}" for i in range(n))


# Continuous symbology excludes calendar spreads, which the .FUT parent would bill.
_FUTURES = {
    "CL": ("c", 12), "NG": ("c", 12), "RB": ("c", 6), "HO": ("c", 6),
    "GC": ("n", 4), "SI": ("n", 4), "HG": ("n", 4),
}
# Roots from CME product codes; not yet checked against the live symbology.
_OPTIONS = {"CL": "LO", "NG": "LN", "GC": "OG", "HG": "HXE"}

FUTURES = [
    Job(f"{r}_{s}", _ranked(r, *spec), "continuous", s)
    for r, spec in _FUTURES.items() for s in ("definition", "statistics")
]
OPTIONS = [
    Job(f"{r}_opt_{s}", (f"{o}.OPT",), "parent", s)
    for r, o in _OPTIONS.items() for s in ("definition", "statistics")
]


@dataclass(frozen=True)
class Request:
    job: Job
    start: date  # inclusive, UTC
    end: date  # exclusive, UTC

    def api(self) -> dict[str, Any]:
        return {"dataset": DATASET, "symbols": list(self.job.symbols), "stype_in": self.job.stype,
                "schema": self.job.schema, "start": self.start.isoformat(), "end": self.end.isoformat()}

    def file(self, root: Path) -> Path:
        return root / self.job.name / f"{self.start}_{self.end}.dbn.zst"


class Store:
    """Spend ledger and download coverage under one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ledger = root / "spend.jsonl"

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise BudgetError("another loader is running") from e
            yield

    def events(self) -> list[dict[str, Any]]:
        if not self.ledger.exists():
            return []
        try:
            return [json.loads(x) for x in self.ledger.read_text().splitlines()]
        except json.JSONDecodeError as e:
            raise BudgetError(f"{self.ledger} is corrupt; refusing to spend") from e

    def record(self, **event: Any) -> None:
        with self.ledger.open("a") as f:
            f.write(json.dumps(event, allow_nan=False) + "\n")
            f.flush()

    def spent(self, bucket: str, month: str | None = None) -> float:
        return sum(e["usd"] for e in self.events()
                   if e["event"] == "reserve" and e["bucket"] == bucket
                   and (month is None or e["at"].startswith(month)))

    def covered_to(self, job: Job) -> date | None:
        """Coverage is tied to the exact symbols, so editing a job re-requests it."""
        ends = [date.fromisoformat(e["end"]) for e in self.events()
                if e["event"] == "done" and e["job"] == job.name and e["symbols"] == list(job.symbols)]
        return max(ends, default=None)


def plan(jobs: list[Job], store: Store, start: date, end: date) -> list[Request]:
    """Uncovered ranges, split at year ends. Never re-requests a covered day."""
    out = []
    for job in jobs:
        lo = max(start, store.covered_to(job) or start)
        while lo < end:
            hi = min(end, date(lo.year + 1, 1, 1))
            out.append(Request(job, lo, hi))
            lo = hi
    return out


def available_end(client: Any, schema: str) -> date:
    """Last complete UTC day available, as an exclusive end."""
    rng = client.metadata.get_dataset_range(dataset=DATASET)
    end = rng.get("schema", {}).get(schema, rng)["end"]
    return min(date.fromisoformat(str(end)[:10]), datetime.now(UTC).date())


def quote(client: Any, req: Request) -> float:
    usd = float(client.metadata.get_cost(**req.api()))
    if not math.isfinite(usd) or usd < 0:
        raise BudgetError(f"unusable quote {usd!r} for {req.job.name}")
    return usd


def check_budget(store: Store, usd: float, bucket: str, now: datetime) -> None:
    if usd > MAX_REQUEST:
        raise BudgetError(f"${usd:.2f} exceeds the ${MAX_REQUEST:.2f} request cap")
    if bucket == "cash":
        used, cap = store.spent("cash", now.strftime("%Y-%m")), MONTHLY_CAP
    elif bucket == "credit":
        used, cap = store.spent("credit"), CREDIT_CAP
    else:
        raise BudgetError(f"unknown bucket {bucket!r}")
    if used + usd > cap + 1e-9:
        raise BudgetError(f"${used:.2f} used + ${usd:.2f} exceeds the ${cap:.2f} {bucket} cap")


def buy(client: Any, store: Store, req: Request, bucket: str, now: datetime | None = None) -> float:
    """Quote, check, reserve, download, record. Caller holds store.lock()."""
    now = now or datetime.now(UTC)
    usd = quote(client, req)
    check_budget(store, usd, bucket, now)
    base = {"job": req.job.name, "symbols": list(req.job.symbols), "start": str(req.start), "end": str(req.end),
            "bucket": bucket, "at": now.isoformat(timespec="seconds")}
    store.record(event="reserve", usd=usd, **base)
    dest = req.file(store.root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name.replace(".dbn.zst", ".part.dbn.zst"))
    part.unlink(missing_ok=True)
    client.timeseries.get_range(**req.api(), path=str(part))
    if not part.exists() or part.stat().st_size == 0:
        raise RuntimeError(f"empty download for {req.job.name}; charge stays counted")
    part.rename(dest)
    store.record(event="done", **base)
    return usd


def settlements(stats: pl.DataFrame) -> pl.DataFrame:
    """One settlement and open interest per instrument and CME session date.

    Expects instrument_id, ts_recv, ts_ref, price, quantity, stat_type, stat_flags,
    update_action. Final beats preliminary, actual beats theoretical, then latest wins.
    """
    s = stats.filter(pl.col("update_action") != DELETE).with_columns(
        pl.col("ts_ref").dt.date().alias("date")  # a session date, never localized
    ).filter(pl.col("date").is_not_null())
    key = ["instrument_id", "date"]
    flags = pl.col("stat_flags").cast(pl.Int64)
    px = (
        s.filter((pl.col("stat_type") == SETTLE) & (flags & INTRADAY == 0)
                 & pl.col("price").is_not_null() & pl.col("price").is_finite())
        .with_columns((flags & FINAL > 0).alias("final"), (flags & ACTUAL > 0).alias("_actual"))
        .sort("final", "_actual", "ts_recv").group_by(key, maintain_order=True)
        .agg(pl.col("price").last().alias("settle"), pl.col("final").last(),
             pl.col("ts_recv").last().alias("known_at"))
    )
    oi = (
        s.filter((pl.col("stat_type") == OPEN_INTEREST) & pl.col("quantity").is_between(0, 10**9))
        .sort("ts_recv").group_by(key, maintain_order=True)
        .agg(pl.col("quantity").last().alias("open_interest"))
    )
    return px.join(oi, on=key, how="left")


def curve(stats: pl.DataFrame, defs: pl.DataFrame) -> pl.DataFrame:
    """Outright futures by root and session, with month rank m (1 = nearest expiry)."""
    d = defs.filter(pl.col("instrument_class") == "F").select(
        "instrument_id", pl.col("ts_recv").alias("known_at"), pl.col("asset").alias("root"),
        "raw_symbol", "expiration",
    ).sort("known_at")
    out = settlements(stats).sort("known_at").join_asof(
        # CME reuses instrument ids, so only a definition close in time may match.
        d, on="known_at", by="instrument_id", strategy="nearest", tolerance="10d", check_sortedness=False
    )
    unmatched = out["raw_symbol"].null_count()
    if unmatched:
        raise ValueError(f"{unmatched} settlements have no definition")
    return (
        out.filter(pl.col("expiration").dt.date() >= pl.col("date"))
        .with_columns(pl.col("expiration").rank("ordinal").over("root", "date").cast(pl.Int32).alias("m"))
        .select("date", "root", "raw_symbol", "expiration", "m", "settle", "final", "open_interest", "known_at")
        .sort("root", "date", "m")
    )


def default_start(years: int) -> date:
    return datetime.now(UTC).date() - timedelta(days=round(365.25 * years))
