#!/usr/bin/env python3
"""Market snapshot for the terminal: futures history + listed option chains.

Writes two web artifacts:

  market_data.json   daily closes (2y) for energy and metals futures plus the
                     option underlyings, with realized vol at 10/20/60/120d
  options_data.json  option chains for commodity ETFs and bellwether equities
                     (bid/ask/OI/volume per strike), spot, carry inputs

Yahoo has no chains for the futures themselves (CL=F etc return none), so
the option desk prices ETF proxies: USO for WTI, UNG for gas, GLD for gold
and so on. Chains are American-style; the terminal's Black-Scholes fair
value is the European approximation and says so.

A failed or thin download never replaces a good file: each artifact is
written only if at least `--min-cover` of its symbols came back.

Usage:
  .venv/bin/python scripts/market_snapshot.py
  .venv/bin/python scripts/market_snapshot.py --skip-options
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("market_snapshot")

# symbol -> (label, group, unit)
FUTURES = {
    "CL=F": ("WTI crude", "energy", "$/bbl"),
    "BZ=F": ("Brent crude", "energy", "$/bbl"),
    "NG=F": ("Henry Hub gas", "energy", "$/MMBtu"),
    "RB=F": ("RBOB gasoline", "energy", "$/gal"),
    "HO=F": ("ULSD heating oil", "energy", "$/gal"),
    "TTF=F": ("Dutch TTF gas", "energy", "EUR/MWh"),
    "MTF=F": ("Newcastle coal", "energy", "$/t"),
    "HG=F": ("Copper", "base metals", "$/lb"),
    "ALI=F": ("Aluminum", "base metals", "$/t"),
    "GC=F": ("Gold", "precious metals", "$/oz"),
    "SI=F": ("Silver", "precious metals", "$/oz"),
    "PL=F": ("Platinum", "precious metals", "$/oz"),
    "PA=F": ("Palladium", "precious metals", "$/oz"),
    "DX-Y.NYB": ("US dollar index", "macro", "index"),
    "^TNX": ("US 10y yield", "macro", "%"),
    "^VIX": ("VIX", "macro", "vol pts"),
    "^OVX": ("Crude oil VIX (OVX)", "macro", "vol pts"),
    "^GVZ": ("Gold VIX (GVZ)", "macro", "vol pts"),
}

# symbol -> (label, what it proxies)
OPTION_UNDERLYINGS = {
    "USO": ("US Oil Fund", "WTI crude"),
    "BNO": ("US Brent Oil Fund", "Brent crude"),
    "UNG": ("US Natural Gas Fund", "Henry Hub gas"),
    "XLE": ("Energy Select SPDR", "energy equities"),
    "XOP": ("SPDR Oil & Gas E&P", "E&P equities"),
    "OIH": ("VanEck Oil Services", "oilfield services"),
    "XOM": ("Exxon Mobil", "integrated oil"),
    "GLD": ("SPDR Gold Shares", "gold"),
    "SLV": ("iShares Silver Trust", "silver"),
    "GDX": ("VanEck Gold Miners", "gold miners"),
    "PPLT": ("abrdn Platinum", "platinum"),
    "PALL": ("abrdn Palladium", "palladium"),
    "CPER": ("US Copper Index Fund", "copper"),
    "COPX": ("Global X Copper Miners", "copper miners"),
    "FCX": ("Freeport-McMoRan", "copper"),
    "URA": ("Global X Uranium", "uranium"),
    "CCJ": ("Cameco", "uranium"),
    "LIT": ("Global X Lithium & Battery", "lithium"),
    "ALB": ("Albemarle", "lithium"),
    "REMX": ("VanEck Rare Earth", "rare earths"),
}

TARGET_DTE = [7, 14, 30, 45, 60, 90, 180, 270, 365, 540]
RV_WINDOWS = [10, 20, 60, 120]


def clean(o: object) -> object:
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if (math.isnan(o) or math.isinf(o)) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    return o


def realized_vol(closes: pd.Series) -> dict[str, float | None]:
    r = np.log(closes / closes.shift(1)).dropna()
    return {str(w): (float(r.tail(w).std(ddof=1) * math.sqrt(252)) if len(r) >= w else None) for w in RV_WINDOWS}


def history_block(symbols: list[str]) -> dict[str, dict]:
    data = yf.download(symbols, period="2y", interval="1d", auto_adjust=True, progress=False,
                       group_by="ticker", threads=True)
    out: dict[str, dict] = {}
    for s in symbols:
        try:
            c = data[(s, "Close")].dropna() if isinstance(data.columns, pd.MultiIndex) else data["Close"].dropna()
        except KeyError:
            continue
        if len(c) < 30:
            continue
        out[s] = {
            "dates": [d.strftime("%Y-%m-%d") for d in c.index],
            "close": [round(float(v), 4) for v in c.values],
            "last": float(c.iloc[-1]),
            "chg_1d": float(c.iloc[-1] / c.iloc[-2] - 1),
            "chg_1m": float(c.iloc[-1] / c.iloc[-22] - 1) if len(c) > 22 else None,
            "chg_1y": float(c.iloc[-1] / c.iloc[-253] - 1) if len(c) > 253 else None,
            "hi_52w": float(c.tail(252).max()),
            "lo_52w": float(c.tail(252).min()),
            "rv": realized_vol(c),
        }
    return out


def pick_expiries(expiries: tuple[str, ...], today: date) -> list[tuple[str, int]]:
    dte = [(e, (date.fromisoformat(e) - today).days) for e in expiries]
    dte = [(e, d) for e, d in dte if d >= 2]
    chosen: dict[str, int] = {}
    for target in TARGET_DTE:
        if not dte:
            break
        e, d = min(dte, key=lambda x: abs(x[1] - target))
        if abs(d - target) <= max(5, target * 0.45):
            chosen[e] = d
    return sorted(chosen.items(), key=lambda x: x[1])


def chain_block(symbol: str, today: date) -> dict | None:
    t = yf.Ticker(symbol)
    px = t.history(period="1y", auto_adjust=False)
    if px.empty:
        return None
    spot = float(px["Close"].iloc[-1])
    divs = t.dividends
    q = 0.0
    if len(divs):
        cutoff = divs.index.max() - pd.Timedelta(days=365)
        q = float(divs[divs.index > cutoff].sum()) / spot
    expiries = []
    for e, d in pick_expiries(t.options, today):
        try:
            ch = t.option_chain(e)
        except Exception as ex:
            log.warning("[%s] %s chain failed: %s", symbol, e, ex)
            continue
        rows: dict[float, dict] = {}
        for side, frame in (("c", ch.calls), ("p", ch.puts)):
            f = frame[(frame["strike"] >= 0.6 * spot) & (frame["strike"] <= 1.5 * spot)]
            for r in f.itertuples(index=False):
                row = rows.setdefault(float(r.strike), {"k": float(r.strike)})
                row[side + "b"] = float(r.bid) if r.bid == r.bid else None
                row[side + "a"] = float(r.ask) if r.ask == r.ask else None
                row[side + "l"] = float(r.lastPrice) if r.lastPrice == r.lastPrice else None
                row[side + "oi"] = int(r.openInterest) if r.openInterest == r.openInterest else 0
                row[side + "v"] = int(r.volume) if r.volume == r.volume else 0
        if len(rows) >= 5:
            expiries.append({"date": e, "dte": d, "rows": [rows[k] for k in sorted(rows)]})
    if not expiries:
        return None
    label, proxy = OPTION_UNDERLYINGS[symbol]
    return {"symbol": symbol, "name": label, "proxy_for": proxy, "spot": spot, "div_yield": q,
            "rv": realized_vol(px["Close"]), "expiries": expiries}


def write_if_healthy(path: Path, payload: dict, got: int, want: int, min_cover: float) -> bool:
    if want and got / want < min_cover:
        log.warning("[emit] %s NOT written: only %d of %d symbols came back", path.name, got, want)
        return False
    text = json.dumps(clean(payload), separators=(",", ":"), allow_nan=False)
    path.write_text(text)
    log.info("[emit] %s (%d of %d symbols, %.0f KB)", path.name, got, want, len(text) / 1024)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-options", action="store_true")
    ap.add_argument("--min-cover", type=float, default=0.7)
    args = ap.parse_args()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    today = date.today()

    symbols = list(FUTURES) + list(OPTION_UNDERLYINGS) + ["^IRX"]
    hist = history_block(symbols)
    rate = hist.get("^IRX", {}).get("last")
    series = []
    for s, (label, group, unit) in FUTURES.items():
        if s in hist:
            series.append({"symbol": s, "name": label, "group": group, "unit": unit, **hist[s]})
    for s, (label, proxy) in OPTION_UNDERLYINGS.items():
        if s in hist:
            series.append({"symbol": s, "name": label, "group": "equities + ETFs", "unit": "$", "proxy_for": proxy, **hist[s]})
    write_if_healthy(REPO_ROOT / "market_data.json",
                     {"meta": {"generated_at": now, "source": "Yahoo Finance via yfinance", "rate_3m": rate}, "series": series},
                     len(series), len(FUTURES) + len(OPTION_UNDERLYINGS), args.min_cover)

    if args.skip_options:
        return 0
    chains = []
    for s in OPTION_UNDERLYINGS:
        try:
            block = chain_block(s, today)
        except Exception as ex:
            log.warning("[%s] failed: %s", s, ex)
            block = None
        if block:
            chains.append(block)
            log.info("[%s] spot %.2f, %d expiries, %d rows", s, block["spot"], len(block["expiries"]),
                     sum(len(e["rows"]) for e in block["expiries"]))
    write_if_healthy(REPO_ROOT / "options_data.json",
                     {"meta": {"generated_at": now, "as_of": today.isoformat(), "source": "Yahoo Finance via yfinance",
                               "rate": (rate or 4.0) / 100.0,
                               "note": "American-style listed options on ETF and equity proxies; quotes are delayed snapshots."},
                      "underlyings": chains},
                     len(chains), len(OPTION_UNDERLYINGS), args.min_cover)
    return 0


if __name__ == "__main__":
    sys.exit(main())
