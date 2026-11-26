"""Shanghai Futures Exchange weekly warehouse stocks for base metals.

The SHFE publishes a weekly report (Friday, or the last trading day of the
week) as public HTML. For each metal the "Total" row holds, for last week
and this week, deliverable stocks and stocks on warrant (registered for
delivery against futures), in tonnes.

On-warrant stocks are the ones that can be delivered against futures, so
large drops in them are the usual sign of a squeeze. Seasonal norms use the
same ISO week over the prior five years, current year excluded. Chinese stocks
follow the Lunar New Year, which moves between late January and mid February,
so ISO-week norms are loose in weeks 3 to 10.
"""

from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser
from statistics import fmean

from signal_engine.macro.oil_balance import _iso_year_week, _round

METALS = {"COPPER": "copper", "ALUMINUM": "aluminum", "ZINC": "zinc", "LEAD": "lead", "NICKEL": "nickel", "TIN": "tin"}
LABELS = {"copper": "Copper", "aluminum": "Aluminium", "zinc": "Zinc", "lead": "Lead", "nickel": "Nickel", "tin": "Tin"}


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append(" ".join(self.cell.split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell += data


def parse_weekly(html: str) -> dict:
    """English weekly report -> {"date": iso, "metals": {metal: {deliverable, warrant}}}.

    Raises if the date is missing or a metal's total does not reconcile
    (this week minus last week must equal the reported change)."""
    p = _Rows()
    p.feed(html)
    m = re.search(r"DATE:\s*(\d{4}-\d{2}-\d{2})", html)
    if not m:
        raise ValueError("SHFE weekly report has no DATE")
    out: dict = {"date": m.group(1), "metals": {}}
    current = None
    for r in p.rows:
        if len(r) >= 2 and r[1].startswith("Unit"):
            current = METALS.get(r[0].strip())
            continue
        if current and r and r[0] == "Total" and len(r) >= 7:
            prev_d, prev_w, d, w, cd, cw = (float(x.replace(",", "")) for x in r[1:7])
            if abs(d - prev_d - cd) > 1 or abs(w - prev_w - cw) > 1:
                raise ValueError(f"SHFE {current} total does not reconcile: {r[:7]}")
            out["metals"][current] = {"deliverable": d, "warrant": w}
            current = None
    missing = set(METALS.values()) - set(out["metals"])
    if missing:
        raise ValueError(f"SHFE report {out['date']} is missing {sorted(missing)}")
    return out


def build(reports: list[dict], keep: str = "2020-01-01", years: int = 5) -> dict:
    """Weekly reports -> per-metal series with week change and 5-year norms."""
    reports = sorted({r["date"]: r for r in reports}.values(), key=lambda r: r["date"])
    out: dict = {"labels": LABELS, "metals": {}, "summary": {}}
    for metal in LABELS:
        rows = [{"date": r["date"], **r["metals"][metal]} for r in reports if metal in r["metals"]]
        by_week: dict[tuple[int, int], dict] = {}
        for r in rows:
            y, w = _iso_year_week(r["date"])
            by_week[(y, min(w, 52))] = r
        kept = [r for r in rows if r["date"] >= keep]
        s: dict = {"dates": [r["date"] for r in kept]}
        for k in ("deliverable", "warrant"):
            s[k] = [r[k] for r in kept]
            s[f"{k}_chg"] = [None if i == 0 else r[k] - kept[i - 1][k] for i, r in enumerate(kept)]
            for stat in ("avg", "min", "max"):
                s[f"{k}_{stat}"] = []
            for r in kept:
                y, w = _iso_year_week(r["date"])
                hist = [h[k] for j in range(1, years + 1) if (h := by_week.get((y - j, min(w, 52))))]
                ok = len(hist) >= 3
                s[f"{k}_avg"].append(_round(fmean(hist), 0) if ok else None)
                s[f"{k}_min"].append(min(hist) if ok else None)
                s[f"{k}_max"].append(max(hist) if ok else None)
        out["metals"][metal] = s
        out["summary"][metal] = summary(s)
    return out


def summary(s: dict) -> dict:
    if not s["dates"]:
        return {}
    i = len(s["dates"]) - 1
    d, w, a, lo, hi = s["deliverable"][i], s["warrant"][i], s["deliverable_avg"][i], s["deliverable_min"][i], s["deliverable_max"][i]
    j4 = max(0, i - 4)
    t = date.fromisoformat(s["dates"][i]).toordinal() - 364
    yago = [k for k, z in enumerate(s["dates"]) if abs(date.fromisoformat(z).toordinal() - t) <= 4]
    return {
        "date": s["dates"][i], "deliverable": d, "warrant": w, "warrant_share": _round(w / d) if d else None,
        "chg_1w": s["deliverable_chg"][i], "chg_4w": d - s["deliverable"][j4] if i else None,
        "warrant_chg_1w": s["warrant_chg"][i],
        "vs_1y": d - s["deliverable"][yago[0]] if yago else None,
        "vs_5y": _round(d - a, 0) if a is not None else None,
        "in_range": _round((d - lo) / (hi - lo)) if None not in (lo, hi) and hi != lo else None,
    }
