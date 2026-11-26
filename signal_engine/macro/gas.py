"""US natural gas balance: weekly storage and the monthly STEO balance.

* **Weekly storage (Bcf)** from the EIA Weekly Natural Gas Storage Report,
  Lower 48 and five regions. Each week is compared with the same ISO week over
  the prior five years (current year excluded). ``end_of_season`` projects
  Lower 48 stocks to the end of the current season (end of March or end of
  October) by adding the 5-year average change for each remaining week.

* **Monthly balance (Bcf/d)** from the EIA Short-Term Energy Outlook, history
  and forecast. ``implied_change`` is supply minus demand; ``balancing`` is
  what is left against the reported change in working gas. EIA's balancing
  item covers measurement error and losses, so watch its trend, not its level.

LNG appears only monthly: weekly feedgas is not available from a free source.
Pure functions; fetching is in ``scripts/oil_balance.py``.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date, timedelta
from statistics import fmean

from signal_engine.macro.oil_balance import _iso_minus_days, _iso_year_week, _round
from signal_engine.macro.products import _norms

Series = dict[str, float]

REGIONS: dict[str, str] = {
    "l48": "NW2_EPG0_SWO_R48_BCF",
    "east": "NW2_EPG0_SWO_R31_BCF",
    "midwest": "NW2_EPG0_SWO_R32_BCF",
    "south_central": "NW2_EPG0_SWO_R33_BCF",
    "mountain": "NW2_EPG0_SWO_R34_BCF",
    "pacific": "NW2_EPG0_SWO_R35_BCF",
}
REGION_LABELS = {"l48": "Lower 48", "east": "East", "midwest": "Midwest", "south_central": "South Central",
                 "mountain": "Mountain", "pacific": "Pacific"}

# STEO monthly ids, Bcf/d (storage in Bcf, price in $/MMBtu)
STEO_GAS = {
    "production": "NGPRPUS", "supplemental": "NGSFPUS", "imports_pipe": "NGIMPUS_PIPE", "imports_lng": "NGIMPUS_LNG",
    "consumption": "NGTCPUS", "power": "NGEPCON", "industrial": "NGINX_US",
    "exports_lng": "NGEXPUS_LNG", "exports_pipe": "NGEXPUS_PIPE", "storage": "NGWGPUS", "henry_hub": "NGHHUUS",
}
SUPPLY = ("production", "supplemental", "imports_pipe", "imports_lng")
DEMAND = ("consumption", "exports_lng", "exports_pipe")


def weekly_ids() -> list[str]:
    return sorted(REGIONS.values())


def steo_ids() -> list[str]:
    return sorted(STEO_GAS.values())


def build_region(stocks: Series, start: str, years: int = 5) -> dict:
    rows: list[dict] = []
    for d in sorted(stocks):
        prev = _iso_minus_days(d, 7)
        rows.append({"date": d, "stocks": stocks[d], "change": stocks[d] - stocks[prev] if prev in stocks else None})
    kept = [r for r in rows if r["date"] >= start]
    out: dict = {"dates": [r["date"] for r in kept], "stocks": [_round(r["stocks"], 1) for r in kept],
                 "change": [_round(r["change"], 1) for r in kept]}
    for k in ("stocks", "change"):
        out[f"{k}_avg"], out[f"{k}_min"], out[f"{k}_max"] = _norms(rows, kept, k, years)
    by_date = {r["date"]: r["stocks"] for r in rows}
    out["stocks_1y"] = [_round(by_date.get(_iso_minus_days(d, 364)), 1) for d in out["dates"]]
    return out


def season_end(d: str) -> str:
    """Last Friday on or before 31 March (withdrawal) or 31 October (injection)."""
    x = date.fromisoformat(d)
    end = date(x.year, 10, 31) if 4 <= x.month <= 10 else date(x.year + (x.month >= 11), 3, 31)
    return (end - timedelta(days=(end.weekday() - 4) % 7)).isoformat()


def end_of_season(r: dict, years: int = 5) -> dict:
    """Project stocks to season end with the 5-year average weekly change,
    and compare with the 5-year average level for that week."""
    changes: dict[tuple[int, int], float] = {}
    levels: dict[tuple[int, int], float] = {}
    for d, c, lv in zip(r["dates"], r["change"], r["stocks"], strict=True):
        y, w = _iso_year_week(d)
        levels[(y, min(w, 52))] = lv
        if c is not None:
            changes[(y, min(w, 52))] = c
    last, proj = r["dates"][-1], r["stocks"][-1]
    target, d = season_end(last), last
    while d < target:
        d = (date.fromisoformat(d) + timedelta(days=7)).isoformat()
        y, w = _iso_year_week(d)
        hist = [v for j in range(1, years + 1) if (v := changes.get((y - j, min(w, 52)))) is not None]
        proj += fmean(hist) if hist else 0.0
    y, w = _iso_year_week(target)
    lv = [v for j in range(1, years + 1) if (v := levels.get((y - j, min(w, 52)))) is not None]
    avg = fmean(lv) if len(lv) >= 3 else None
    return {"date": target, "projected": _round(proj, 0),
            "weeks": (date.fromisoformat(target) - date.fromisoformat(last)).days // 7,
            "avg_5y": _round(avg, 0), "vs_5y": _round(proj - avg, 0) if avg is not None else None}


def summary(r: dict) -> dict:
    i = len(r["dates"]) - 1
    s, a, lo, hi, ly = r["stocks"][i], r["stocks_avg"][i], r["stocks_min"][i], r["stocks_max"][i], r["stocks_1y"][i]
    return {
        "date": r["dates"][i], "stocks": s, "change": r["change"][i], "change_avg": r["change_avg"][i],
        "vs_5y": _round(s - a, 0) if a is not None else None,
        "vs_5y_pct": _round(s / a - 1) if a else None,
        "vs_1y": _round(s - ly, 0) if ly is not None else None,
        "in_range": _round((s - lo) / (hi - lo)) if None not in (lo, hi) and hi != lo else None,
    }


def build_storage(raw: Mapping[str, Series], start: str = "2015-01-01", keep: str = "2020-01-01") -> dict:
    """``start`` is how much history feeds the norms; ``keep`` is what is emitted."""
    full = {k: build_region(raw[sid], start) for k, sid in REGIONS.items()}
    eos = end_of_season(full["l48"])
    regions = {k: {f: v[f][-n:] for f in v} for k, v in full.items()
               for n in [sum(d >= keep for d in v["dates"])]}
    return {"labels": REGION_LABELS, "regions": regions,
            "summary": {k: summary(v) for k, v in regions.items()}, "end_of_season": eos}


def build_monthly(series: Mapping[str, Series], last_hist: Mapping[str, str], start: str = "202001") -> dict:
    """STEO gas balance in Bcf/d with a history/forecast flag per month."""
    s = {k: series[sid] for k, sid in STEO_GAS.items()}
    periods = sorted(p for p in s["production"] if p >= start and all(p in s[k] for k in (*SUPPLY, *DEMAND)))
    hist_end = last_hist.get(STEO_GAS["consumption"], "")
    out: dict = {"periods": periods, "status": ["hist" if p <= hist_end else "fcst" for p in periods], "hist_end": hist_end}
    for k in STEO_GAS:
        out[k] = [_round(s[k].get(p)) for p in periods]
    out["other_demand"] = [_round(c - pw - ind) if None not in (c, pw, ind) else None
                           for c, pw, ind in zip(out["consumption"], out["power"], out["industrial"], strict=True)]
    out["supply"] = [_round(sum(s[k][p] for k in SUPPLY)) for p in periods]
    out["demand"] = [_round(sum(s[k][p] for k in DEMAND)) for p in periods]
    out["implied_change"] = [_round(a - b) for a, b in zip(out["supply"], out["demand"], strict=True)]
    rep, bal = [], []
    for p, imp in zip(periods, out["implied_change"], strict=True):
        y, m = int(p[:4]), int(p[4:])
        prev = f"{y - (m == 1)}{12 if m == 1 else m - 1:02d}"
        st = s["storage"]
        if p in st and prev in st:
            r = (st[p] - st[prev]) / calendar.monthrange(y, m)[1]
            rep.append(_round(r))
            bal.append(_round(r - imp) if imp is not None else None)
        else:
            rep.append(None)
            bal.append(None)
    out["reported_change"], out["balancing"] = rep, bal
    return out
