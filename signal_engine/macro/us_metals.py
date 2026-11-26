"""US copper and aluminum: imports by origin, COMEX warehouse stocks, Midwest premium.

Pure parsing; scripts/us_metals.py does the I/O.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

HS = {"copper": "7403", "aluminum": "7601"}  # refined copper; unwrought aluminum
WORLD = 0
LB_PER_TONNE = 2204.62262
MONTH_CODES = "FGHJKMNQUVXZ"


def parse_imports(payload: dict[str, Any], names: dict[int, str], period: str) -> dict[str, Any] | None:
    """One Comtrade month -> per-metal totals and origins in tonnes. None if not yet published."""
    rows = payload.get("data") or []
    if not rows:
        return None
    out: dict[str, Any] = {}
    for metal, code in HS.items():
        sel = [r for r in rows if r["cmdCode"] == code and str(r["period"]) == period and r["flowCode"] == "M"]
        world = [r for r in sel if r["partnerCode"] == WORLD]
        if len(world) != 1:
            raise ValueError(f"{period} {metal}: expected one world row, got {len(world)}")
        origins: dict[str, dict[str, Any]] = {}
        for r in sel:
            if r["partnerCode"] == WORLD:
                continue
            name = names.get(r["partnerCode"], f"code {r['partnerCode']}")
            if name in origins:
                raise ValueError(f"{period} {metal}: duplicate origin {name}")
            origins[name] = {"t": (r["netWgt"] or 0) / 1000, "usd": r["primaryValue"] or 0,
                             "est": bool(r["isNetWgtEstimated"])}
        usd, t = world[0]["primaryValue"] or 0, (world[0]["netWgt"] or 0) / 1000
        gap = abs(sum(o["usd"] for o in origins.values()) - usd)
        if usd and gap / usd > 0.01:
            raise ValueError(f"{period} {metal}: origins miss the world value by {gap / usd:.1%}")
        out[metal] = {"t": t, "usd": usd, "est": bool(world[0]["isNetWgtEstimated"]), "origins": origins}
    return out


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_comex(cells: list[list[Any]]) -> dict[str, Any]:
    """CME depository report cells -> activity date, registered, eligible, total.

    Written from the published layout, not yet run on a real file, so it refuses
    anything whose totals do not add up.
    """
    text = " ".join(str(c) for row in cells for c in row).upper()
    metal = next((m for m in HS if m.upper() in text), None)
    if metal is None:
        raise ValueError("not a copper or aluminum report")
    unit = next((u for u in ("SHORT TON", "METRIC TON") if u in text), None)
    if unit is None:
        raise ValueError("unit not stated")
    m = re.search(r"ACTIVITY DATE:?\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if m is None:
        raise ValueError("no activity date")
    col = next((i for row in cells for i, c in enumerate(row) if "TOTAL TODAY" in str(c).upper()), None)
    if col is None:
        raise ValueError("no TOTAL TODAY column")
    got: dict[str, float] = {}
    for row in cells:
        label = str(row[0]).strip().upper() if row else ""
        key = ("registered" if label.startswith("TOTAL REGISTERED") else
               "eligible" if label.startswith("TOTAL ELIGIBLE") else
               "total" if label in (f"TOTAL {metal.upper()}", "COMBINED TOTAL", "GRAND TOTAL") else None)
        v = _num(row[col]) if key and len(row) > col else None
        if key and v is not None:
            if key in got:
                raise ValueError(f"two {key} rows")
            got[key] = v
    if set(got) != {"registered", "eligible", "total"}:
        raise ValueError(f"found only {sorted(got)}")
    if abs(got["registered"] + got["eligible"] - got["total"]) > 1:
        raise ValueError("registered + eligible does not equal total")
    return {"metal": metal, "date": datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat(),
            "unit": unit.lower(), **got}


def contract_months(root: str, start: date, n: int) -> list[tuple[str, str]]:
    """(Yahoo symbol, YYYY-MM) for the next n delivery months, including this one."""
    out = []
    for k in range(n):
        y, m = start.year + (start.month - 1 + k) // 12, (start.month - 1 + k) % 12
        out.append((f"{root}{MONTH_CODES[m]}{y % 100:02d}.CMX", f"{y}-{m + 1:02d}"))
    return out


def all_in(ali_per_t: float, premium_per_lb: float) -> float:
    """US delivered aluminum, $/t: exchange price plus Midwest premium."""
    return ali_per_t + premium_per_lb * LB_PER_TONNE
