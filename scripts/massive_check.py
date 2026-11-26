#!/usr/bin/env python
"""Trial of Massive Options Basic: its closes against the Yahoo last prices in options_data.json.

    scripts/massive_check.py --symbols USO,GLD --per 4     (about 12.5 s per call)

Saves bars to gitignored data_store/massive/. Needs MASSIVE_API_KEY.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta

from signal_engine.config import REPO_ROOT
from signal_engine.data.massive import Massive

OUT = REPO_ROOT / "data_store" / "massive"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="USO,GLD")
    ap.add_argument("--per", type=int, default=4, help="strikes nearest spot, per symbol")
    a = ap.parse_args()
    m = Massive()
    d = json.loads((REPO_ROOT / "options_data.json").read_text())
    as_of = d["meta"]["as_of"]
    start = (date.fromisoformat(as_of) - timedelta(days=725)).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    diffs = []
    for u in (x for x in d["underlyings"] if x["symbol"] in a.symbols.split(",")):
        exp = min((e for e in u["expiries"] if e["dte"] >= 20), key=lambda e: e["dte"])
        rows = {r["k"]: r for r in sorted(exp["rows"], key=lambda r: abs(r["k"] - u["spot"]))[: a.per]}
        for c in m.contracts(u["symbol"], exp["date"], min(rows), max(rows)):
            if c["contract_type"] not in ("call", "put") or c["strike_price"] not in rows:
                continue
            bars = m.bars(c["ticker"], start, as_of)
            (OUT / f"{c['ticker'].replace(':', '_')}.json").write_text(json.dumps(bars))
            y = rows[c["strike_price"]].get(f"{c['contract_type'][0]}l")
            day = datetime.fromtimestamp(bars[-1]["t"] / 1000, UTC).date().isoformat() if bars else None
            same = day == as_of
            print(f"{c['ticker']:<24}{len(bars):>4} bars, last {day}  massive {bars[-1]['c'] if bars else None}  yahoo {y}")
            if same and y:
                diffs.append(abs(bars[-1]["c"] / y - 1))
    if diffs:
        diffs.sort()
        print(f"{len(diffs)} same-day pairs, median difference {diffs[len(diffs) // 2]:.2%}, worst {diffs[-1]:.2%}")
    else:
        print("no same-day pairs")


if __name__ == "__main__":
    main()
