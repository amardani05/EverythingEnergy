#!/usr/bin/env python
"""Archive every listed contract month (Yahoo). Nightly; safe to rerun.

    curve_archive.py            save new closes (first run backfills two years)
    curve_archive.py --emit     rebuild curves.json only
    curve_archive.py --show CL  print the latest archived curve
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime

import polars as pl

from signal_engine.config import REPO_ROOT
from signal_engine.data import curve_archive as A
from signal_engine.data import curve_view as V

log = logging.getLogger("curve_archive")
ROOT = REPO_ROOT / "data_store" / "curves"
OUT = REPO_ROOT / "curves.json"


RETRIES, PAUSE = 2, 45  # seconds; Yahoo throttles in bursts


def pull(todo: list[tuple[str, str, str]], period: str) -> list[pl.DataFrame]:
    import yfinance as yf

    frames = []
    for k in range(0, len(todo), 40):
        batch = {s: (r, d) for s, r, d in todo[k : k + 40]}
        data = yf.download(list(batch), period=period, auto_adjust=False, progress=False, group_by="ticker", threads=8)
        have = set(data.columns.get_level_values(0))
        for s, (r, dl) in batch.items():
            x = data[s][["Close", "Volume"]].dropna(subset=["Close"]) if s in have else []
            if len(x):
                frames.append(pl.DataFrame({"date": [d.date() for d in x.index], "close": x["Close"].to_numpy(),
                                            "volume": x["Volume"].astype(float).to_numpy()}).with_columns(
                    pl.lit(s).alias("symbol"), pl.lit(r).alias("root"), pl.lit(dl).alias("delivery")))
    return frames


def fetch(now: datetime, known: pl.DataFrame) -> pl.DataFrame:
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # unlisted months are expected
    todo = A.to_request(now, known)
    seen = set(known["symbol"].to_list())
    period = "2y" if known.is_empty() else "1mo"
    frames = pull(todo, period)
    for _ in range(RETRIES):
        got = {f["symbol"][0] for f in frames}
        missing = [c for c in todo if c[0] in seen and c[0] not in got]
        if not missing:
            break
        log.info("[retry] %d contracts", len(missing))
        time.sleep(PAUSE)
        frames += pull(missing, period)
    return pl.concat(frames) if frames else pl.DataFrame(schema=A.SCHEMA).drop("captured_at")


def emit() -> None:
    """curves.json for the terminal, rebuilt from the archive."""
    view = V.rounded(V.build(A.latest(A.read(ROOT))))
    view["meta"] = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "source": "Yahoo contract-month closes"}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(view, allow_nan=False, separators=(",", ":")))
    tmp.rename(OUT)
    log.info("[emit] %s (%d roots, %.0f KB)", OUT.name, len(view["roots"]), OUT.stat().st_size / 1024)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", choices=list(A.EXCHANGE))
    ap.add_argument("--emit", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s | %(message)s")
    known = A.read(ROOT)
    if a.show:
        c = A.curve(known).filter(pl.col("root") == a.show)
        print(c.filter(pl.col("date") == c["date"].max()) if c.height else "no live curve yet")
        return 0
    if a.emit:
        emit()
        return 0
    now = datetime.now()
    obs = A.clean(fetch(now, known), now)
    new = A.delta(obs, known)
    f = A.write(ROOT, new, now)  # save whatever came back, then judge coverage
    emit()
    changed = new.join(known.select("symbol", "date").unique(), on=["symbol", "date"], how="semi").height
    log.info("[archive] %d new, %d revised closes -> %s", new.height - changed, changed, f.name if f else "nothing to save")
    cov = A.coverage(obs, now)
    thin = {r: n for r, n in cov.items() if n < A.MIN_CONTRACTS[r]}
    log.info("[coverage] %s", " ".join(f"{r}:{n}" for r, n in cov.items()))
    if thin:
        log.error("[coverage] below floor: %s", thin)
    return 1 if thin else 0


if __name__ == "__main__":
    sys.exit(main())
