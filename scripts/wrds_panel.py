#!/usr/bin/env python
"""Pull CRSP + Compustat and build the energy/mining panel. Manual, never in the nightly job.

    .venv/bin/python scripts/wrds_panel.py            # pull, then build
    .venv/bin/python scripts/wrds_panel.py --offline  # rebuild from the latest pull

Auth: ~/.pgpass (approve the Duo prompt if shown). Output: data_store/wrds/<date>/.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from signal_engine.data.wrds_panel import build_panel, sic_sql

ROOT = Path(__file__).resolve().parents[1] / "data_store" / "wrds"
HOST = "wrds-pgdata.wharton.upenn.edu"
START = "2004-01-01"

UNIVERSE = f"""select distinct permno from crsp.stksecurityinfohist
  where secinfoenddt >= '{START}' and securitytype = 'EQTY' and {sic_sql('siccd')}"""
LINKED = f"select gvkey from crsp.ccmxpf_lnkhist where lpermno in ({UNIVERSE})"
QUERIES = {
    "daily": f"""select permno, permco, dlycaldt as date, ticker, siccd, sharetype, securitytype,
        securitysubtype, primaryexch, conditionaltype, tradingstatusflg, dlydelflg as delist,
        dlyprc::float8 as prc, dlycap::float8 as cap, dlyret::float8 as ret, dlyvol::float8 as vol
        from crsp.dsf_v2 where dlycaldt >= '{START}' and permno in ({UNIVERSE})""",
    "links": f"""select gvkey, lpermno as permno, linktype, linkprim, linkdt, linkenddt
        from crsp.ccmxpf_lnkhist where lpermno in ({UNIVERSE})""",
    "fundq": f"""select gvkey, datadate, fyearq, fqtr, curcdq, rdq, epspxq::float8, ajexq::float8,
        oibdpq::float8, dlttq::float8, dlcq::float8, cheq::float8, pstkq::float8, mibq::float8
        from comp.fundq where indfmt='INDL' and consol='C' and popsrc='D' and datafmt='STD'
        and datadate >= '2000-01-01' and gvkey in ({LINKED})""",
    "delists": f"""select permno, delistingdt, delactiontype, delreasontype, delret::float8, deldlydt
        from crsp.stkdelists where delistingdt >= '{START}' and permno in ({UNIVERSE})""",
}
INTS = ("permno", "permco", "siccd", "fyearq", "fqtr")


def pull(out: Path) -> None:
    import psycopg2

    user = os.environ.get("PGUSER")
    if not user:
        for line in (Path.home() / ".pgpass").read_text().splitlines():
            if line.startswith(HOST):
                user = line.split(":")[3]
    con = psycopg2.connect(host=HOST, port=9737, dbname="wrds", user=user,
                           sslmode="require", connect_timeout=150)
    con.set_session(readonly=True)
    out.mkdir(parents=True, exist_ok=True)
    for name, sql in QUERIES.items():
        with con.cursor(f"c_{name}") as cur:
            cur.itersize = 200_000
            cur.execute(sql)
            frames: list[pl.DataFrame] = []
            while rows := cur.fetchmany(200_000):
                cols = [c[0] for c in cur.description]
                df = pl.DataFrame(rows, schema=cols, orient="row", infer_schema_length=None)
                frames.append(df.with_columns(pl.col(c).cast(pl.Int64) for c in INTS if c in cols))
        if not frames:
            raise SystemExit(f"{name}: no rows returned")
        table = pl.concat(frames, how="vertical_relaxed")
        table.write_parquet(out / f"{name}.parquet")
        print(f"[wrds] {name}: {table.height:,} rows", flush=True)
    con.close()


def qa(panel: pl.DataFrame, daily: pl.DataFrame, delists: pl.DataFrame) -> dict[str, Any]:
    live = panel.filter(pl.col("has_fwd_month"))
    dl = daily.filter(pl.col("delist") == "Y").join(delists, on="permno")
    cnt = panel.group_by("date").len()["len"]
    return {
        "rows": panel.height,
        "permnos": panel["permno"].n_unique(),
        "first": str(panel["date"].min()), "last": str(panel["date"].max()),
        "names_per_month_min_median_max": [cnt.min(), cnt.median(), cnt.max()],
        "duplicate_keys": panel.height - panel.unique(["permno", "date"]).height,
        "fwd_delistings": int(live["fwd_delisted"].fill_null(False).sum()),
        "fwd_missing_delist_ret": int(live["fwd_missing_delist"].fill_null(False).sum()),
        "fwd_suspended": int(live["fwd_suspended"].fill_null(False).sum()),
        "fwd_null": int(live["fwd_ret_1m"].null_count()),
        "fwd_ret_min": live["fwd_ret_1m"].min(), "fwd_ret_max": live["fwd_ret_1m"].max(),
        "delist_rows": dl.height,
        "delist_ret_mismatch": dl.filter((pl.col("ret") - pl.col("delret")).abs() > 1e-4).height,
        "coverage": {c: round(1 - panel[c].null_count() / panel.height, 3)
                     for c in ("gvkey", "mom_12_1", "ebitda_ev", "sue")},
        "by_group": dict(panel.group_by("group").agg(pl.col("permno").n_unique()).sort("group").iter_rows()),
        "never_delisted_permnos": panel.filter(~pl.col("permno").is_in(
            daily.filter(pl.col("delist") == "Y")["permno"].unique().to_list()))["permno"].n_unique(),
        "null_daily_ret": int(daily["ret"].null_count()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    if a.offline:
        out = max(p.parent for p in ROOT.glob("*/delists.parquet"))
    else:
        out = ROOT / date.today().isoformat()
        pull(out)
    t = {n: pl.read_parquet(out / f"{n}.parquet") for n in QUERIES}
    panel = build_panel(t["daily"], t["links"], t["fundq"])
    panel.write_parquet(out / "panel.parquet")
    report = qa(panel, t["daily"], t["delists"])
    (out / "qa.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
