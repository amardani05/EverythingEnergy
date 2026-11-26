"""Archive SHFE weekly warehouse stocks and emit metals_stocks.json.

Each weekly report is saved once to data_store/shfe/<date>.html and never
rewritten. The SHFE site serves reports from 2025-10-31 onward, so the
archive starts there; 5-year norms fill in as it grows.

  .venv/bin/python scripts/shfe_stocks.py            # fetch missing weeks, emit
  .venv/bin/python scripts/shfe_stocks.py --offline  # rebuild from the archive
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

from signal_engine.config import REPO_ROOT
from signal_engine.macro.shfe import build, parse_weekly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("shfe")

URL = "https://www.shfe.com.cn/data/tradedata/future/stockdata/weeklystock_{d}/EN/all.html"
ARCHIVE = REPO_ROOT / "data_store" / "shfe"
FIRST = date(2025, 10, 31)
UA = {"User-Agent": "Mozilla/5.0 (signal-engine research)"}


def fridays(start: date, end: date) -> list[date]:
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def fetch_week(fri: date, session: requests.Session) -> Path | None:
    """Try Friday back to Monday (holiday weeks report early)."""
    for k in range(5):
        d = fri - timedelta(days=k)
        r = session.get(URL.format(d=d.strftime("%Y%m%d")), headers=UA, timeout=30)
        time.sleep(1.0)
        if r.status_code == 200 and "DATE:" in r.text:
            parse_weekly(r.text)  # refuse to archive a report that does not reconcile
            path = ARCHIVE / f"{d.isoformat()}.html"
            path.write_text(r.text, encoding="utf-8")
            return path
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "metals_stocks.json")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    if not args.offline:
        have = {date.fromisoformat(p.stem) for p in ARCHIVE.glob("*.html")}
        today = datetime.now(UTC).date()
        skip = set((ARCHIVE / "no_report.txt").read_text().split()) if (ARCHIVE / "no_report.txt").exists() else set()
        todo = [f for f in fridays(FIRST, today)
                if not any(0 <= (f - h).days < 5 for h in have) and f.isoformat() not in skip]
        s, new, gaps = requests.Session(), 0, []
        for fri in todo:
            try:
                got = fetch_week(fri, s)
            except (requests.RequestException, ValueError) as e:
                log.warning("[fetch] week of %s: %s", fri, e)
                got = None
            if got:
                new += 1
            elif (today - fri).days > 3:  # this week's report may not be out yet
                gaps.append(fri.isoformat())
        old = [g for g in gaps if (today - date.fromisoformat(g)).days > 21]  # holiday weeks: stop retrying
        if old:
            (ARCHIVE / "no_report.txt").write_text("\n".join(sorted(skip | set(old))) + "\n")
        log.info("[fetch] %d new weeks; %d missing%s", new, len(gaps), f": {gaps[:6]}" if gaps else "")

    reports = []
    for p in sorted(ARCHIVE.glob("*.html")):
        try:
            reports.append(parse_weekly(p.read_text(encoding="utf-8")))
        except ValueError as e:
            log.warning("[parse] %s: %s", p.name, e)
    if not reports:
        log.error("no SHFE reports archived")
        return 1
    out = build(reports)
    out["meta"] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "as_of": reports[-1]["date"], "first": reports[0]["date"], "weeks": len(reports),
        "units": "tonnes", "source": {"name": "Shanghai Futures Exchange weekly warehouse stocks", "url": URL.format(d="YYYYMMDD")},
    }
    args.out.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    for m, v in out["summary"].items():
        log.info("[shfe] %s %s: %.0f t (%+.0f wk), warrant %.0f", m, v["date"], v["deliverable"], v["chg_1w"] or 0, v["warrant"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
