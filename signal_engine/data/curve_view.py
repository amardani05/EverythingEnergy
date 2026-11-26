"""Terminal view of the curve archive: shape, spreads, roll yield, cracks.

Comparisons with the past always hold the CONTRACT fixed (Jan 27 now vs Jan 27 a month
ago), which is valid on backfill. Nothing here rebuilds a rolling front month.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import polars as pl

INFO = {
    "CL": ("WTI crude", "$/bbl", "energy"), "BZ": ("Brent crude", "$/bbl", "energy"),
    "NG": ("Henry Hub gas", "$/MMBtu", "energy"), "RB": ("RBOB gasoline", "$/gal", "energy"),
    "HO": ("ULSD heating oil", "$/gal", "energy"), "GC": ("Gold", "$/oz", "metals"),
    "SI": ("Silver", "$/oz", "metals"), "HG": ("Copper", "$/lb", "metals"),
    "ALI": ("Aluminum", "$/t", "metals"), "AUP": ("Aluminum Midwest premium", "$/lb", "metals"),
}
MOST_ACTIVE_FRONT = {"GC", "SI", "HG", "ALI"}  # nearest month is often an illiquid delivery month
SKIP_CURRENT = {"AUP"}  # settles to the month average, so the current month is partly fixed
ACTIVE_SHARE = 0.05
HISTORY_DAYS, HISTORY_CONTRACTS, CURVE_CONTRACTS = 252, 14, 36
LOOKBACK = {"w1": 7, "m1": 30, "y1": 365}
GAL = 42.0
VOLUME_SESSIONS = 5


def months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:]) - int(a[5:])


def roll_yield(near: float, far: float, months: int) -> float | None:
    """Annualized return from holding `far` until it is priced like `near` (positive in backwardation)."""
    if near <= 0 or far <= 0 or months <= 0:
        return None
    return math.log(near / far) * 12 / months


def session(x: pl.DataFrame) -> date:
    """Newest date with at least 80% of the usual number of contracts, so that one early
    or partial print cannot define the curve."""
    n = x.group_by("date").len().sort("date").tail(10)
    floor = 0.8 * float(n["len"].median())  # type: ignore[arg-type]
    d: date = n.filter(pl.col("len") >= floor)["date"].max()  # type: ignore[assignment]
    return d


def on_or_before(dates: list[date], target: date, slack: int = 5) -> date | None:
    ok = [d for d in dates if target - timedelta(days=slack) <= d <= target]
    return max(ok, default=None)


def root_view(x: pl.DataFrame, root: str) -> dict[str, Any]:
    """x: one root of curve_archive.latest()."""
    asof = session(x)
    x = x.filter(pl.col("date") <= asof)
    now = x.filter(pl.col("date") == asof).sort("delivery").head(CURVE_CONTRACTS)
    dates = sorted(x["date"].unique().to_list())
    px = {(s, d): c for s, d, c in x.select("symbol", "date", "close").iter_rows()}
    back = {k: on_or_before(dates, asof - timedelta(days=n)) for k, n in LOOKBACK.items()}
    recent = dates[-VOLUME_SESSIONS:]
    vol = dict(x.filter(pl.col("date").is_in(recent)).group_by("symbol").agg(pl.col("volume").median()).iter_rows())

    rows = now.select("symbol", "delivery", "close").to_dicts()
    syms = [r["symbol"] for r in rows]
    v = [vol.get(s) or 0.0 for s in syms]
    front = v.index(max(v)) if root in MOST_ACTIVE_FRONT else 0
    if root in SKIP_CURRENT and len(rows) > 1 and rows[0]["delivery"] == f"{asof:%Y-%m}":
        front = 1
    nxt = next((i for i in range(front + 1, len(rows)) if root not in MOST_ACTIVE_FRONT or v[i] >= ACTIVE_SHARE * v[front]),
               front + 1 if front + 1 < len(rows) else None)
    for i, r in enumerate(rows):
        r["volume"] = v[i]
        for k, d in back.items():
            r[k] = px.get((r["symbol"], d)) if d else None
        if i + 1 < len(rows):
            n = rows[i + 1]
            gap = months_between(r["delivery"], n["delivery"])
            r["spread"], r["roll"] = r["close"] - n["close"], roll_yield(r["close"], n["close"], gap)
        else:
            r["spread"] = r["roll"] = None
    f = rows[front]
    year = next((i for i in range(front + 1, len(rows)) if months_between(f["delivery"], rows[i]["delivery"]) >= 12), None)

    def pair(j: int | None) -> dict[str, Any] | None:
        if j is None:
            return None
        o = rows[j]
        return {"to": o["delivery"], "spread": f["close"] - o["close"],
                "roll": roll_yield(f["close"], o["close"], months_between(f["delivery"], o["delivery"]))}

    hist = dates[-HISTORY_DAYS:]
    pick = list(dict.fromkeys([*syms[:HISTORY_CONTRACTS], syms[front], *([syms[year]] if year is not None else [])]))
    name, unit, group = INFO[root]
    return {
        "name": name, "unit": unit, "group": group, "asof": asof.isoformat(),
        "compare": {k: d.isoformat() if d else None for k, d in back.items()},
        "front": front, "front_rule": "most active" if root in MOST_ACTIVE_FRONT else "next month" if root in SKIP_CURRENT else "nearest",
        "next": pair(nxt), "year": pair(year), "curve": rows,
        "history": {"dates": [d.isoformat() for d in hist],
                    "closes": {next(r["delivery"] for r in rows if r["symbol"] == s): [px.get((s, d)) for d in hist] for s in pick}},
    }


def cracks(roots: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Same-delivery-month cracks in $/bbl, where WTI, RBOB and ULSD share a session."""
    if not {"CL", "RB", "HO"} <= set(roots):
        return None
    legs = {r: roots[r] for r in ("CL", "RB", "HO")}
    if len({leg["asof"] for leg in legs.values()}) != 1:
        return None
    by = {r: {c["delivery"]: c for c in leg["curve"]} for r, leg in legs.items()}

    def calc(delivery: str, key: str) -> dict[str, float] | None:
        cl, rb, ho = (by[r][delivery].get(key) for r in ("CL", "RB", "HO"))
        if cl is None or rb is None or ho is None:
            return None
        return {"rbob": rb * GAL - cl, "ulsd": ho * GAL - cl, "c321": (2 * rb * GAL + ho * GAL - 3 * cl) / 3}

    out = []
    for dl in sorted(set(by["CL"]) & set(by["RB"]) & set(by["HO"]))[:18]:
        c = calc(dl, "close")
        if c:
            prev = {k: (p["c321"] if (p := calc(dl, k)) else None) for k in LOOKBACK}
            out.append({"delivery": dl, **c, **prev})
    return {"asof": legs["CL"]["asof"], "rows": out} if out else None


def build(latest: pl.DataFrame) -> dict[str, Any]:
    roots = {}
    for r in INFO:
        x = latest.filter(pl.col("root") == r)
        if x.height:
            roots[r] = root_view(x, r)
    return {"roots": roots, "cracks": cracks(roots)}


def rounded(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: rounded(v) for k, v in o.items()}
    if isinstance(o, list):
        return [rounded(v) for v in o]
    return round(o, 5) if isinstance(o, float) else o
