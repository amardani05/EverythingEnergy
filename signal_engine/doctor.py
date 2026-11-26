"""Data QA: is everything the site and engine read fresh and well formed?

Freshness is judged by the last DATA date inside each artifact, never by when the
file was written (that is how six-month-old prices once wore a green chip).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

OK, WARN, FAIL = "OK", "WARN", "FAIL"
MARKET_LIMIT = 3  # weekdays; covers a holiday plus a late close
SERIES_FAIL_SHARE = 0.10


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def to_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def age(v: Any, today: date, *, weekdays: bool) -> int:
    d = to_date(v)
    return int(np.busday_count(d, today)) if weekdays else (today - d).days


def fresh(name: str, v: Any, today: date, limit: int, *, weekdays: bool = False, level: str = FAIL) -> Check:
    if v is None:
        return Check(FAIL, name, "no date")
    d, n = to_date(v), age(v, today, weekdays=weekdays)
    unit = "weekdays" if weekdays else "days"
    if d > today:
        return Check(FAIL, name, f"{d} is in the future")
    if n > limit:
        return Check(level, name, f"{d}, {n} {unit} old (limit {limit})")
    return Check(OK, name, f"{d}")


def at_least(name: str, n: int, floor: int) -> Check:
    return Check(OK if n >= floor else FAIL, name, f"{n} (floor {floor})")


def _reject(token: str) -> None:
    raise ValueError(f"invalid JSON constant {token}")


def load(path: Path) -> Any:
    """Strict JSON: browsers reject the bare NaN that Python accepts."""
    return json.loads(path.read_text(), parse_constant=_reject)


def _guard(name: str, fn: Callable[[], list[Check]]) -> list[Check]:
    """A check that cannot run is a failure, never a skip."""
    try:
        return fn()
    except Exception as e:
        return [Check(FAIL, name, f"{type(e).__name__}: {e}")]


def check_market(d: dict[str, Any], today: date) -> list[Check]:
    last = {s["symbol"]: s["dates"][-1] if s["dates"] else None for s in d["series"]}
    stale = [s for s, v in last.items() if v is None or age(v, today, weekdays=True) > MARKET_LIMIT]
    newest = max((v for v in last.values() if v), default=None)
    out = [fresh("market_data", newest, today, MARKET_LIMIT, weekdays=True), at_least("market_data series", len(last), 35)]
    if stale:
        lvl = FAIL if len(stale) > SERIES_FAIL_SHARE * len(last) else WARN
        out.append(Check(lvl, "market_data stale series", ", ".join(f"{s} ({last[s]})" for s in stale)))
    return out


def published(root: Path, today: date) -> list[Check]:
    """Artifacts committed to the repo and served by the site."""

    def dashboard() -> list[Check]:
        m = load(root / "dashboard_data.json")["meta"]
        return [fresh("dashboard prices", m["prices_through"], today, MARKET_LIMIT, weekdays=True),
                at_least("dashboard constituents", m["n_constituents"], 180)]

    def signals() -> list[Check]:
        m = load(root / "signals_latest.json")["meta"]
        return [fresh("signals", m["as_of"], today, MARKET_LIMIT, weekdays=True),
                at_least("signals ranked", m["n_ranked"], 100)]

    def oil() -> list[Check]:
        d = load(root / "oil_balance.json")
        m, pr = d["meta"], d.get("products")
        out = [fresh("STEO release", m["steo_released"], today, 40),
               fresh("WPSR week", m["us_week_ending"], today, 14)]
        if not pr:
            return [*out, Check(FAIL, "products balance", "missing from oil_balance.json")]
        g = d.get("gas")
        out += ([fresh("gas storage week", g["storage"]["summary"]["l48"]["date"], today, 14)] if g
                else [Check(FAIL, "gas balance", "missing from oil_balance.json")])
        return [*out, fresh("products week", min(v["date"] for v in pr["summary"].values()), today, 14)]

    def options() -> list[Check]:
        d = load(root / "options_data.json")
        empty = [u["symbol"] for u in d["underlyings"] if not u["expiries"]]
        out = [fresh("options", d["meta"]["as_of"], today, MARKET_LIMIT, weekdays=True),
               at_least("options underlyings", len(d["underlyings"]), 18)]
        return out + ([Check(WARN, "options without expiries", ", ".join(empty))] if empty else [])

    def curated() -> list[Check]:
        return [fresh(n, load(root / f"{n}.json")["meta"]["compiled"], today, limit, level=WARN)
                for n, limit in (("flows_data", 90), ("commodities", 365))]

    out: list[Check] = []
    for name, fn in (("dashboard", dashboard), ("signals", signals), ("oil_balance", oil),
                     ("market_data", lambda: check_market(load(root / "market_data.json"), today)),
                     ("options", options), ("curated data", curated)):
        out += _guard(name, fn)
    return out


def local(root: Path, today: date) -> list[Check]:
    """Local database, caches and ledgers."""

    def database() -> list[Check]:
        import duckdb

        with duckdb.connect(str(root / "data_store" / "signal_engine.duckdb"), read_only=True) as con:
            def top(sql: str) -> tuple[Any, ...]:
                return con.execute(sql).fetchone() or (None, 0)

            px, fl = top("select max(date), count(distinct ticker) from prices"), top("select max(filed), 0 from edgar_facts")
            bad = top("select count(*), 0 from prices where date >= current_date - 30 and (close is null or close <= 0)")[0]
            late = con.execute(
                "select ticker, max(date) d from prices group by 1 having max(date) < (select max(date) from prices) - 10"
                " and max(date) > (select max(date) from prices) - 60 order by 1").fetchall()
        out = [fresh("db prices", px[0], today, MARKET_LIMIT, weekdays=True), at_least("db price tickers", px[1], 180),
               fresh("db EDGAR filings", fl[0], today, 14),
               Check(OK if not bad else WARN if bad <= 5 else FAIL, "db bad closes (30d)", str(bad))]
        return out + ([Check(WARN, "db tickers gone quiet", ", ".join(f"{t} ({d})" for t, d in late))] if late else [])

    def cache() -> list[Check]:
        import pandas as pd

        out = []
        for n, share in (("price_cache", 0.9), ("driver_cache", 1.0)):
            df = pd.read_parquet(root / f"{n}.parquet")
            last = df.apply(lambda c: c.last_valid_index())
            lag = last[[v is None or pd.isna(v) or age(v, today, weekdays=True) > MARKET_LIMIT for v in last]]
            out.append(fresh(n, df.dropna(how="all").index.max(), today, MARKET_LIMIT, weekdays=True))
            if len(lag):
                level = FAIL if 1 - len(lag) / len(last) < share else WARN
                out.append(Check(level, f"{n} stale columns", ", ".join(f"{k} ({str(v)[:10]})" for k, v in lag.items())))
        return out

    def ledgers() -> list[Check]:
        from signal_engine.data.databento_futures import Store
        from signal_engine.validation.ledger import trials

        path = root / "data_store" / "trial_ledger.jsonl"
        if not path.exists():
            return [Check(FAIL, "trial ledger", "missing")]
        rows = trials(path)
        open_ = [r["signal"] for r in rows if r["status"] == "unfinished"]
        Store(root / "data_store" / "databento").events()  # raises if corrupt
        out = [at_least("trial ledger", len(rows), 1)]
        return out + ([Check(WARN, "unfinished trials", ", ".join(open_))] if open_ else [])

    def metals() -> list[Check]:
        d = load(root / "us_metals.json")
        out = [fresh("Midwest premium", d["premium"]["dates"][-1], today, MARKET_LIMIT, weekdays=True),
               fresh("US metal imports month", d["imports"][-1]["month"] + "-01", today, 150, level=WARN)]
        for m, rows in d["stocks"].items():  # manual drops: remind, do not fail
            out.append(fresh(f"COMEX {m} stocks", rows[-1]["date"], today, 10, level=WARN) if rows
                       else Check(WARN, f"COMEX {m} stocks", "no file dropped yet"))
        sh = load(root / "metals_stocks.json")
        out += [fresh("SHFE stocks week", sh["meta"]["as_of"], today, 14),
                at_least("SHFE metals", sum(bool(v) for v in sh["summary"].values()), 6)]
        return out

    def curves() -> list[Check]:
        from signal_engine.data.curve_archive import MIN_CONTRACTS, latest, read

        df = latest(read(root / "data_store" / "curves"))
        if df.is_empty():
            return [Check(FAIL, "curve archive", "empty")]
        view = load(root / "curves.json")["roots"]
        out = [at_least("curves.json roots", len(view), len(MIN_CONTRACTS)),
               fresh("curves.json oldest", min((v["asof"] for v in view.values()), default=None), today, MARKET_LIMIT, weekdays=True)]
        for r, floor in MIN_CONTRACTS.items():
            x = df.filter(df["root"] == r)
            if x.is_empty():
                out.append(Check(FAIL, f"curve {r}", "no data"))
                continue
            day = to_date(x["date"].max())
            c = fresh(f"curve {r}", day, today, MARKET_LIMIT, weekdays=True)
            n = x.filter(x["date"] >= day - timedelta(days=7))["symbol"].n_unique()
            out.append(c if c.level != OK else at_least(f"curve {r}", n, floor))
        return out

    out: list[Check] = []
    for name, fn in (("metals", metals), ("curves", curves), ("database", database), ("caches", cache), ("ledgers", ledgers)):
        out += _guard(name, fn)
    return out
