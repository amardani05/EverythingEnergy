"""US refined products balance from the EIA Weekly Petroleum Status Report.

For gasoline, distillate and jet fuel, each week:

* ``build`` is the reported stock change in mb/d.
* ``modelled`` is production + imports - exports - product supplied.
* ``adjustment`` is what is left. EIA derives product supplied from the same
  identity, so for distillate and jet it is near zero. Gasoline runs about
  -0.4 mb/d because finished-gasoline production leaves out blending
  components and ethanol; watch changes in it, not its level.
* ``cover`` is days of cover: stocks over 4-week average product supplied.

Seasonal norms use the same ISO week over the prior five years, current year
excluded, so nothing looks ahead. Pure functions; fetching is in
``scripts/oil_balance.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from statistics import fmean

from signal_engine.macro.oil_balance import _iso_minus_days, _iso_year_week, _round

Series = dict[str, float]

# product -> field -> dnav weekly series id (kb for stocks, kb/d for flows)
PRODUCTS: dict[str, dict[str, str]] = {
    "gasoline": {"stocks": "WGTSTUS1", "production": "WGFRPUS2", "imports": "WGTIMUS2",
                 "exports": "W_EPM0F_EEX_NUS-Z00_MBBLD", "supplied": "WGFUPUS2"},
    "distillate": {"stocks": "WDISTUS1", "production": "WDIRPUS2", "imports": "WDIIMUS2",
                   "exports": "WDIEXUS2", "supplied": "WDIUPUS2"},
    "jet": {"stocks": "WKJSTUS1", "production": "WKJRPUS2", "imports": "WKJIMUS2",
            "exports": "WKJEXUS2", "supplied": "WKJUPUS2"},
}
LABELS = {"gasoline": "Gasoline", "distillate": "Distillate", "jet": "Jet fuel"}
UTILIZATION = "WPULEUS3"  # percent of operable capacity
FLOWS = ("production", "imports", "exports", "supplied")
SIGN = {"production": 1.0, "imports": 1.0, "exports": -1.0, "supplied": -1.0}
FIELDS = ("stocks", *FLOWS, "demand_4w", "cover", "build", "modelled", "adjustment")


def series_ids() -> list[str]:
    return sorted({s for p in PRODUCTS.values() for s in p.values()} | {UTILIZATION})


def _rows(raw: Mapping[str, Series]) -> list[dict]:
    stocks = raw["stocks"]
    dates = sorted(d for d in stocks if all(d in raw[k] for k in FLOWS))
    rows: list[dict] = []
    for i, d in enumerate(dates):
        prev = _iso_minus_days(d, 7)
        flows = {k: raw[k][d] / 1000.0 for k in FLOWS}
        last4 = [raw["supplied"][z] for z in dates[max(0, i - 3): i + 1]]
        dem4 = fmean(last4) / 1000.0 if len(last4) == 4 else None
        build = (stocks[d] - stocks[prev]) / 7000.0 if prev in stocks else None
        modelled = sum(SIGN[k] * v for k, v in flows.items())
        rows.append({
            "date": d, "stocks": stocks[d] / 1000.0, **flows, "demand_4w": dem4,
            "cover": stocks[d] / 1000.0 / dem4 if dem4 else None,
            "build": build, "modelled": modelled,
            "adjustment": build - modelled if build is not None else None,
        })
    return rows


def _norms(rows: list[dict], kept: list[dict], key: str, years: int) -> tuple[list, list, list]:
    by_week = {}
    for r in rows:
        y, w = _iso_year_week(r["date"])
        by_week[(y, min(w, 52))] = r[key]
    avg, lo, hi = [], [], []
    for r in kept:
        y, w = _iso_year_week(r["date"])
        hist = [v for j in range(1, years + 1) if (v := by_week.get((y - j, min(w, 52)))) is not None]
        ok = len(hist) >= 3
        avg.append(_round(fmean(hist)) if ok else None)
        lo.append(_round(min(hist)) if ok else None)
        hi.append(_round(max(hist)) if ok else None)
    return avg, lo, hi


def build_product(raw: Mapping[str, Series], start: str = "2015-01-01", years: int = 5) -> dict:
    """One product's weekly balance plus 5-year seasonal avg / min / max."""
    rows = _rows(raw)
    kept = [r for r in rows if r["date"] >= start]
    out: dict = {"dates": [r["date"] for r in kept]}
    for k in FIELDS:
        out[k] = [_round(r[k]) for r in kept]
    for k in ("stocks", "supplied", "cover"):
        out[f"{k}_avg"], out[f"{k}_min"], out[f"{k}_max"] = _norms(rows, kept, k, years)
    return out


def summary(p: dict) -> dict:
    """Latest week versus its seasonal norm, for the overview table."""
    i = len(p["dates"]) - 1

    def dev(k: str) -> float | None:
        v, a = p[k][i], p[f"{k}_avg"][i]
        return _round(v - a) if v is not None and a is not None else None

    def pos(k: str) -> float | None:  # 0 = 5y low, 1 = 5y high
        v, lo, hi = p[k][i], p[f"{k}_min"][i], p[f"{k}_max"][i]
        if None in (v, lo, hi) or hi == lo:
            return None
        return _round((v - lo) / (hi - lo))

    b4 = [b for b in p["build"][-4:] if b is not None]
    return {
        "date": p["dates"][i], "stocks": p["stocks"][i], "stocks_vs_5y": dev("stocks"),
        "stocks_pct_vs_5y": _round(p["stocks"][i] / p["stocks_avg"][i] - 1) if p["stocks_avg"][i] else None,
        "stocks_in_range": pos("stocks"), "build": p["build"][i], "build_4w": _round(fmean(b4)) if b4 else None,
        "supplied": p["supplied"][i], "supplied_vs_5y": dev("supplied"), "demand_4w": p["demand_4w"][i],
        "cover": p["cover"][i], "cover_vs_5y": dev("cover"), "production": p["production"][i],
        "net_exports": _round(p["exports"][i] - p["imports"][i]),
    }


def build_products(raw: Mapping[str, Series], start: str = "2020-01-01") -> dict:
    """``raw`` maps dnav series id -> {iso_date: value} in EIA units."""
    out: dict = {"labels": LABELS, "products": {}, "summary": {}}
    for name, ids in PRODUCTS.items():
        p = build_product({f: raw[s] for f, s in ids.items()}, start=start)
        out["products"][name] = p
        out["summary"][name] = summary(p)
    util = raw.get(UTILIZATION, {})
    dates = sorted(d for d in util if d >= start)
    out["utilization"] = {"dates": dates, "value": [_round(util[d]) for d in dates]}
    return out
