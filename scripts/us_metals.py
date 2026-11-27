#!/usr/bin/env python
"""US metals snapshot -> us_metals.json (imports by origin, COMEX stocks, Midwest premium).

  imports  UN Comtrade public preview, key-free; completed months cached locally
  premium  Yahoo futures on the Platts Midwest premium (AUP): settlement proxy,
           not the Platts assessment itself
  stocks   CME blocks automated downloads, so save Copper_Stocks.xls and
           Aluminum_Stocks.xls from cmegroup.com into data_store/drops/comex/
           (any file name; keep old ones, they build the history)
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests

from signal_engine.config import REPO_ROOT
from signal_engine.macro import us_metals as U

log = logging.getLogger("us_metals")
CACHE = REPO_ROOT / "data_store" / "comtrade"
DROPS = REPO_ROOT / "data_store" / "drops" / "comex"
OUT = REPO_ROOT / "us_metals.json"
API = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
AREAS = "https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json"
MONTHS, REFRESH = 25, 4  # two years of history; recent months are revised


def get(url: str, **params: Any) -> Any:
    for wait in (5, 20, None):
        r = requests.get(url, params=params, timeout=60)
        if r.status_code == 429 and wait:
            time.sleep(wait)
            continue
        r.raise_for_status()
        return json.loads(r.content.decode("utf-8-sig"))


def imports(today: date) -> list[dict[str, Any]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    names: dict[int, str] = {}
    out = []
    for k in range(MONTHS, 0, -1):
        idx = today.year * 12 + today.month - 1 - k
        period = f"{idx // 12}{idx % 12 + 1:02d}"
        f = CACHE / f"{period}.json"
        if f.exists() and k > REFRESH + 2:
            rec = json.loads(f.read_text())
            out.append({**rec, **{m: U.fill_total(rec[m]) for m in U.HS if m in rec}})
            continue
        if not names:
            names = {int(a["PartnerCode"]): a["PartnerDesc"] for a in get(AREAS)["results"]}
        raw = get(API, reporterCode=842, period=period, cmdCode=",".join(U.HS.values()), flowCode="M",
                  partner2Code=0, customsCode="C00", motCode=0)
        time.sleep(1.5)
        got = U.parse_imports(raw, names, period)
        if got is None:
            log.info("[imports] %s not published", period)
            continue
        rec = {"month": f"{period[:4]}-{period[4:]}", **got}
        f.write_text(json.dumps(rec))
        out.append(rec)
    if not out:
        raise RuntimeError("no import months")
    return out


def premium(today: date) -> dict[str, Any]:
    import yfinance as yf

    def closes(sym: str, period: str) -> dict[str, float]:
        h = yf.Ticker(sym).history(period=period, auto_adjust=False)["Close"].dropna()
        return {d.strftime("%Y-%m-%d"): round(float(v), 5) for d, v in h.items() if v > 0}

    mwp, ali = closes("AUP=F", "2y"), closes("ALI=F", "2y")
    days = sorted(set(mwp) & set(ali))
    if len(days) < 100:
        raise RuntimeError(f"only {len(days)} common premium days")
    curve = []
    for sym, month in U.contract_months("AUP", today, 13):
        c = closes(sym, "1mo")
        if c:
            d = max(c)
            curve.append({"month": month, "symbol": sym, "date": d, "usd_lb": c[d]})
    return {"dates": days, "usd_lb": [mwp[d] for d in days], "ali_usd_t": [ali[d] for d in days],
            "all_in_usd_t": [round(U.all_in(ali[d], mwp[d]), 2) for d in days], "curve": curve}


def stocks() -> dict[str, list[dict[str, Any]]]:
    import xlrd

    DROPS.mkdir(parents=True, exist_ok=True)
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    for f in sorted(DROPS.glob("*.xls"), key=lambda p: p.stat().st_mtime):
        try:
            sh = xlrd.open_workbook(str(f)).sheet_by_index(0)
            r = U.parse_comex([sh.row_values(i) for i in range(sh.nrows)])
            recs[(r["metal"], r["date"])] = r
        except Exception as e:
            log.error("[stocks] %s rejected: %s", f.name, e)
    return {m: [r for k, r in sorted(recs.items()) if k[0] == m] for m in U.HS}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s | %(message)s")
    today = date.today()
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    out: dict[str, Any] = {"meta": {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": "Imports: UN Comtrade (US Census data). Premium: CME AUP futures via Yahoo. Stocks: CME depository reports."}}
    failed = []
    for name, fn in (("imports", lambda: imports(today)), ("premium", lambda: premium(today)), ("stocks", stocks)):
        try:
            out[name] = fn()
        except Exception as e:
            failed.append(name)
            log.error("[%s] failed, keeping the previous data: %s", name, e)
            out[name] = old.get(name)
    tmp = Path(str(OUT) + ".tmp")
    tmp.write_text(json.dumps(out, allow_nan=False))
    tmp.rename(OUT)
    log.info("[emit] %s%s", OUT.name, f" (stale: {', '.join(failed)})" if failed else "")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
