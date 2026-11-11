"""Barebones oil supply/demand balance: bridges and build variances.

Two balances, both built from free EIA data and both reducing to the same
identity, ``stock build = supply - demand + unexplained``:

* **Global liquids (monthly, mb/d)** from the EIA Short-Term Energy Outlook.
  Supply and demand are split into a handful of blocs; ``implied_build`` is
  supply minus demand. The only stock series that is actually *observed*
  with any timeliness is OECD commercial inventory, so the gap between the
  implied global build and the observed OECD build is the classic "missing
  barrels" term (non-OECD stocks, oil on water, and plain measurement
  error). A one-variable OLS of observed-on-implied calibrates how much of
  an implied build historically shows up in OECD tanks and how wide the
  miss is; that residual sigma is the variance estimate carried forward
  over the forecast months.

* **US crude (weekly, mb/d)** from the Weekly Petroleum Status Report.
  Here demand is real refinery intake: ``production + imports - exports -
  refinery inputs`` is the modelled build, and whatever is left against the
  reported stock change is EIA's "adjustment" (unaccounted-for crude).

Everything here is a pure function over plain dicts / lists so it can be
unit-tested without the network; fetching lives in ``scripts/oil_balance.py``.
"""

from __future__ import annotations

import calendar
import json
import math
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, pstdev

Series = dict[str, float]  # period key ("YYYYMM" or ISO date) -> value

# --- STEO series map -------------------------------------------------------
STEO_TOTALS = {
    "supply": "PAPR_WORLD",
    "demand": "PATC_WORLD",
    "oecd_stocks": "PASC_OECD_T3",  # million barrels, end of period
    "brent": "BREPUUS",
    "wti": "WTIPUUS",
}

# (key, label, added ids, subtracted ids): value = sum(added) - sum(subtracted).
# Residual blocs net named countries out of a published aggregate, so the
# blocs always sum exactly to the world total.
Bloc = tuple[str, str, tuple[str, ...], tuple[str, ...]]

SUPPLY_BLOCS: list[Bloc] = [
    ("opec_crude", "OPEC crude", ("COPR_OPEC",), ()),
    ("opec_other", "OPEC NGLs + other", ("OPEC_NC",), ()),
    ("us", "United States", ("PAPR_US",), ()),
    ("canada", "Canada", ("PAPR_CA",), ()),
    ("russia", "Russia", ("PAPR_RS",), ()),
    ("brazil_guyana", "Brazil + Guyana", ("PAPR_BR", "PAPR_GY"), ()),
    ("other_non_opec", "Other non-OPEC", ("PAPR_NONOPEC",), ("PAPR_US", "PAPR_CA", "PAPR_RS", "PAPR_BR", "PAPR_GY")),
]

DEMAND_BLOCS: list[Bloc] = [
    ("us", "United States", ("PATC_US",), ()),
    ("oecd_europe", "OECD Europe", ("PATC_OECD_EUROPE",), ()),
    ("japan", "Japan", ("PATC_JA",), ()),
    ("other_oecd", "Other OECD", ("PATC_OECD",), ("PATC_US", "PATC_OECD_EUROPE", "PATC_JA")),
    ("china", "China", ("PATC_CH",), ()),
    ("india", "India", ("PATC_IN",), ()),
    ("other_non_oecd", "Other non-OECD", ("PATC_NON_OECD",), ("PATC_CH", "PATC_IN")),
]


def steo_series_ids() -> list[str]:
    """Every STEO id the global balance needs (without the STEO./.M wrapper)."""
    ids: set[str] = set(STEO_TOTALS.values())
    for _, _, plus, minus in SUPPLY_BLOCS + DEMAND_BLOCS:
        ids.update(plus)
        ids.update(minus)
    return sorted(ids)


@dataclass
class SteoBundle:
    series: dict[str, Series]          # id -> {YYYYMM: value}
    last_hist: dict[str, str]          # id -> last historical YYYYMM
    released: str                      # ISO date of the newest series update


def parse_steo_bulk(zip_path: Path, wanted: Sequence[str] | None = None) -> SteoBundle:
    """Read the monthly series we need out of EIA's key-free STEO bulk zip."""
    want = {f"STEO.{s}.M" for s in (wanted or steo_series_ids())}
    series: dict[str, Series] = {}
    last_hist: dict[str, str] = {}
    released = ""
    with zipfile.ZipFile(zip_path) as z, z.open(z.namelist()[0]) as fh:
        for line in fh:
            row = json.loads(line)
            sid = row.get("series_id")
            if sid not in want:
                continue
            key = sid[len("STEO."):-len(".M")]
            series[key] = {p: float(v) for p, v in row["data"] if v is not None}
            last_hist[key] = str(row.get("lastHistoricalPeriod") or "")
            released = max(released, str(row.get("last_updated") or "")[:10])
    missing = sorted(want - {f"STEO.{k}.M" for k in series})
    if missing:
        raise KeyError(f"STEO bulk file is missing series: {missing}")
    return SteoBundle(series=series, last_hist=last_hist, released=released)


def _resolve(plus: Sequence[str], minus: Sequence[str], series: Mapping[str, Series], period: str) -> float | None:
    try:
        return sum(series[i][period] for i in plus) - sum(series[i][period] for i in minus)
    except KeyError:
        return None


def days_in_month(period: str) -> int:
    return calendar.monthrange(int(period[:4]), int(period[4:6]))[1]


def build_global_balance(bundle: SteoBundle, start: str = "201501") -> dict:
    """Monthly world liquids balance with bloc detail and data-status flags.

    ``status`` per month: ``hist`` (supply, demand and stocks all reported),
    ``est`` (supply/stocks reported but world demand is still an EIA
    estimate, the normal state for the trailing ~18 months of non-OECD
    data), ``fcst`` (STEO forecast).
    """
    s = bundle.series
    sup_id, dem_id, stk_id = (STEO_TOTALS[k] for k in ("supply", "demand", "oecd_stocks"))
    periods = sorted(p for p in s[sup_id] if p >= start and p in s[dem_id])
    sup_hist, dem_hist = bundle.last_hist[sup_id], bundle.last_hist[dem_id]
    stk_hist = bundle.last_hist[stk_id]

    out: dict = {
        "periods": periods,
        "status": [],
        "supply": {k: [] for k, _, _, _ in SUPPLY_BLOCS},
        "demand": {k: [] for k, _, _, _ in DEMAND_BLOCS},
        "supply_total": [],
        "demand_total": [],
        "implied_build": [],      # mb/d, + = stocks building
        "oecd_stocks": [],        # mb, end of period
        "oecd_build": [],         # mb/d change in OECD commercial stocks
        "unexplained": [],        # implied_build - oecd_build
        "brent": [],
        "wti": [],
        "labels": {
            "supply": {k: lab for k, lab, _, _ in SUPPLY_BLOCS},
            "demand": {k: lab for k, lab, _, _ in DEMAND_BLOCS},
        },
        "last_hist": {"supply": sup_hist, "demand": dem_hist, "oecd_stocks": stk_hist},
    }
    stocks = s[stk_id]
    for p in periods:
        out["status"].append("hist" if p <= dem_hist else "est" if p <= sup_hist else "fcst")
        for k, _, plus, minus in SUPPLY_BLOCS:
            out["supply"][k].append(_round(_resolve(plus, minus, s, p)))
        for k, _, plus, minus in DEMAND_BLOCS:
            out["demand"][k].append(_round(_resolve(plus, minus, s, p)))
        sup, dem = s[sup_id][p], s[dem_id][p]
        implied = sup - dem
        prev = _prev_month(p)
        build = (stocks[p] - stocks[prev]) / days_in_month(p) if p in stocks and prev in stocks else None
        out["supply_total"].append(_round(sup))
        out["demand_total"].append(_round(dem))
        out["implied_build"].append(_round(implied))
        out["oecd_stocks"].append(_round(stocks.get(p), 1))
        out["oecd_build"].append(_round(build))
        out["unexplained"].append(_round(implied - build) if build is not None else None)
        out["brent"].append(_round(s[STEO_TOTALS["brent"]].get(p), 2))
        out["wti"].append(_round(s[STEO_TOTALS["wti"]].get(p), 2))
    return out


def _prev_month(period: str) -> str:
    y, m = int(period[:4]), int(period[4:6])
    return f"{y - 1}12" if m == 1 else f"{y}{m - 1:02d}"


def _round(v: float | None, nd: int = 3) -> float | None:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), nd)


# --- calibration: implied build -> observed OECD build ----------------------
@dataclass
class StockModel:
    alpha: float      # mb/d of OECD build when the implied build is zero
    beta: float       # share of an implied global build that lands in OECD tanks
    sigma: float      # residual std, mb/d per month
    r2: float
    n: int

    def predict(self, implied: float) -> float:
        return self.alpha + self.beta * implied

    def as_dict(self) -> dict:
        return {k: _round(getattr(self, k), 4) for k in ("alpha", "beta", "sigma", "r2")} | {"n": self.n}


def fit_stock_model(implied: Sequence[float | None], observed: Sequence[float | None]) -> StockModel:
    """OLS of observed OECD build on implied global build over paired months."""
    pairs = [(x, y) for x, y in zip(implied, observed, strict=True) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        raise ValueError(f"need at least 3 paired months to fit, got {n}")
    mx, my = fmean(x for x, _ in pairs), fmean(y for _, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    beta = sxy / sxx if sxx > 0 else 0.0
    alpha = my - beta * mx
    resid = [y - (alpha + beta * x) for x, y in pairs]
    sse = sum(e * e for e in resid)
    sst = sum((y - my) ** 2 for _, y in pairs)
    sigma = math.sqrt(sse / (n - 2))
    return StockModel(alpha=alpha, beta=beta, sigma=sigma, r2=1 - sse / sst if sst > 0 else 0.0, n=n)


def project_oecd_stocks(balance: dict, model: StockModel, anchor_period: str | None = None) -> dict:
    """Roll the calibrated model forward from the last observed OECD stock level.

    Returns the model's own stock path with a 1-sigma band that widens with
    sqrt(months), next to EIA's published path, for every month after the
    anchor. Independent monthly errors is an assumption, and a generous one:
    the unexplained term is autocorrelated, so treat the band as a floor.
    """
    periods = balance["periods"]
    anchor = anchor_period or balance["last_hist"]["oecd_stocks"]
    if anchor not in periods:
        return {"anchor": anchor, "periods": [], "model": [], "lo": [], "hi": [], "eia": []}
    i0 = periods.index(anchor)
    level = balance["oecd_stocks"][i0]
    path: dict = {"anchor": anchor, "anchor_level": level, "periods": [], "model": [], "lo": [], "hi": [], "eia": []}
    var = 0.0
    for i in range(i0 + 1, len(periods)):
        p = periods[i]
        d = days_in_month(p)
        level += model.predict(balance["implied_build"][i]) * d
        var += (model.sigma * d) ** 2
        band = math.sqrt(var)
        path["periods"].append(p)
        path["model"].append(round(level, 1))
        path["lo"].append(round(level - band, 1))
        path["hi"].append(round(level + band, 1))
        path["eia"].append(balance["oecd_stocks"][i])
    return path


# --- variance bridge --------------------------------------------------------
def variance_bridge(balance: dict, idx_now: Sequence[int], idx_base: Sequence[int]) -> dict:
    """Decompose the change in implied build between two sets of months.

    Each set is averaged first (so a quarter can be bridged against the
    prior-year quarter). Supply increases add to the build, demand increases
    subtract from it; the steps reconcile exactly to ``build_now - build_base``.
    """
    def avg(vals: Sequence[float | None], idx: Sequence[int]) -> float:
        return fmean(v for v in (vals[i] for i in idx) if v is not None)

    steps = []
    for side, sign in (("supply", 1.0), ("demand", -1.0)):
        for key, series in balance[side].items():
            delta = avg(series, idx_now) - avg(series, idx_base)
            steps.append({
                "side": side,
                "key": key,
                "label": balance["labels"][side][key],
                "delta": round(sign * delta, 3),
            })
    base = avg(balance["implied_build"], idx_base)
    now = avg(balance["implied_build"], idx_now)
    return {
        "base": round(base, 3),
        "now": round(now, 3),
        "steps": steps,
        "check": round(base + sum(s["delta"] for s in steps) - now, 3),
    }


# --- US weekly crude balance ------------------------------------------------
US_WEEKLY_SERIES = {
    "stocks_total": "WCRSTUS1",   # kb, incl. SPR
    "stocks_comm": "WCESTUS1",    # kb, commercial
    "stocks_spr": "WCSSTUS1",     # kb
    "production": "WCRFPUS2",     # kb/d
    "imports": "WCRIMUS2",
    "exports": "WCREXUS2",
    "refinery_inputs": "WCRRIUS2",
}
US_FLOW_KEYS = ("production", "imports", "exports", "refinery_inputs")
US_FLOW_SIGN = {"production": 1.0, "imports": 1.0, "exports": -1.0, "refinery_inputs": -1.0}


def build_us_weekly(raw: Mapping[str, Series], start: str = "2015-01-01", norm_years: int = 5) -> dict:
    """Weekly US crude bridge in mb/d, with the residual "adjustment".

    ``raw`` maps the keys of ``US_WEEKLY_SERIES`` to ``{iso_date: value}`` in
    EIA's native units (kb and kb/d). ``modelled_build`` is production +
    imports - exports - refinery inputs; ``adjustment`` is what EIA has to
    add to make the reported stock change reconcile. Seasonal norms are the
    mean and std of each quantity for the same ISO week over the prior
    ``norm_years`` years (current year excluded, so no look-ahead).
    """
    stocks = raw["stocks_total"]
    dates_all = sorted(d for d in stocks if all(d in raw[k] for k in US_FLOW_KEYS))
    rows: list[dict] = []
    for d in dates_all:
        prev = _iso_minus_days(d, 7)
        if prev not in stocks:
            continue
        flows = {k: raw[k][d] / 1000.0 for k in US_FLOW_KEYS}
        modelled = sum(US_FLOW_SIGN[k] * v for k, v in flows.items())
        build = (stocks[d] - stocks[prev]) / 7000.0
        comm = raw["stocks_comm"]
        spr = raw["stocks_spr"]
        rows.append({
            "date": d,
            **flows,
            "modelled_build": modelled,
            "build": build,
            "adjustment": build - modelled,
            "build_comm": (comm[d] - comm[prev]) / 7000.0 if d in comm and prev in comm else None,
            "build_spr": (spr[d] - spr[prev]) / 7000.0 if d in spr and prev in spr else None,
            "stocks_comm": comm.get(d, float("nan")) / 1000.0,
        })

    keys = (*US_FLOW_KEYS, "modelled_build", "build", "adjustment", "build_comm", "build_spr", "stocks_comm")
    by_week: dict[tuple[int, int], dict] = {}
    for r in rows:
        y, w = _iso_year_week(r["date"])
        by_week[(y, min(w, 52))] = r

    kept = [r for r in rows if r["date"] >= start]
    out: dict = {"dates": [r["date"] for r in kept]}
    for k in keys:
        out[k] = [_round(r[k]) for r in kept]
        norm, sd = [], []
        for r in kept:
            y, w = _iso_year_week(r["date"])
            hist = [
                by_week[(y - j, min(w, 52))][k]
                for j in range(1, norm_years + 1)
                if (y - j, min(w, 52)) in by_week and by_week[(y - j, min(w, 52))][k] is not None
            ]
            norm.append(_round(fmean(hist)) if len(hist) >= 3 else None)
            sd.append(_round(pstdev(hist)) if len(hist) >= 3 else None)
        out[f"{k}_norm"] = norm
        out[f"{k}_sd"] = sd

    adj = [r["adjustment"] for r in kept[-156:]]
    out["adjustment_stats"] = {
        "window_weeks": len(adj),
        "mean": _round(fmean(adj)) if adj else None,
        "sigma": _round(pstdev(adj)) if len(adj) > 1 else None,
    }
    return out


def _iso_minus_days(d: str, n: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(d) - timedelta(days=n)).isoformat()


def _iso_year_week(d: str) -> tuple[int, int]:
    from datetime import date

    iso = date.fromisoformat(d).isocalendar()
    return iso[0], iso[1]
