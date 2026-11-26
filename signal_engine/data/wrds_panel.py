"""Survivorship-free energy and mining panel from CRSP + Compustat (research only).

Pure transforms; scripts/wrds_panel.py does the I/O. Output is licensed data and
must stay in gitignored data_store/wrds/.

Conventions
  * Membership uses the SIC code, share type and exchange CRSP carried on that day.
  * CIZ `dlyret` already contains the delisting return on the delisting row.
  * Compustat quarterly values are the latest restatement, NOT point in time. Dates
    are: earnings become known at rdq, statement items at the later of rdq and a
    45-day (90-day for fiscal Q4) reporting lag. Missing rdq falls back to the lag.

Known limits (bound these in any test that uses the panel)
  * `fwd_suspended`: the name left its exchange mid-month without a CRSP delisting
    (mostly bankruptcies later relisted under the same permno). The off-exchange
    loss is absent from `fwd_ret_1m`, so rerun with these set to -100%.
  * `fwd_missing_delist`: CRSP has no delisting return; `fwd_ret_1m` is null.
  * Quarterly oibdpq can include impairments (CHK 2020 Q1), so EBITDA drops names
    from the value signal after write-downs.
  * ADR market cap covers only the ADR line, so `ebitda_ev` is null for ADRs.
    Unlisted share classes and units are likewise missing from market cap.
"""

from __future__ import annotations

import polars as pl

# First match wins.
GROUPS: list[tuple[str, int, int]] = [
    ("coal", 1200, 1299),
    ("oilfield_services", 1380, 1389),
    ("oilfield_services", 3533, 3533),
    ("upstream", 1300, 1379),
    ("mining", 1000, 1199),
    ("mining", 1400, 1499),
    ("refining", 2900, 2999),
    ("smelting", 3330, 3341),
    ("shipping", 4412, 4413),
    ("pipelines", 4600, 4699),
    ("pipelines", 4922, 4923),
    ("gas_utilities", 4924, 4925),
    ("power", 4900, 4919),
    ("power", 4930, 4939),
    ("power", 4991, 4991),
    ("fuel_distribution", 5171, 5172),
    ("royalty", 6792, 6792),
]
SHARE_TYPES = ["NS", "UG", "AD"]  # common, units (MLPs), ADRs
MAX_STATEMENT_AGE_DAYS = 300


def sic_sql(column: str) -> str:
    return "(" + " or ".join(f"{column} between {a} and {b}" for _, a, b in GROUPS) + ")"


def group_expr(column: str = "siccd") -> pl.Expr:
    out = pl.lit(None, dtype=pl.Utf8)
    for name, lo, hi in reversed(GROUPS):
        out = pl.when(pl.col(column).is_between(lo, hi)).then(pl.lit(name)).otherwise(out)
    return out.alias("group")


def monthly_returns(daily: pl.DataFrame) -> pl.DataFrame:
    """Compound daily returns by month. A delisting row dated later than the month
    after the last trade is pulled back so the final holder still bears it.
    A month with a missing delisting return is null, never silently dropped."""
    d = daily.sort("permno", "date").with_columns(
        pl.col("date").dt.truncate("1mo").alias("month"),
        pl.col("date").shift(1).over("permno").dt.truncate("1mo").dt.offset_by("1mo").alias("_next"),
    )
    d = d.with_columns(
        pl.when((pl.col("delist") == "Y") & pl.col("_next").is_not_null())
        .then(pl.min_horizontal("month", "_next"))
        .otherwise(pl.col("month"))
        .alias("month")
    )
    return (
        d.group_by("permno", "month", maintain_order=True)
        .agg(
            ((1 + pl.col("ret").fill_null(0.0)).product() - 1).alias("_r"),
            ((pl.col("delist") == "Y") & pl.col("ret").is_null()).any().alias("missing_delist_ret"),
            (pl.col("delist") == "Y").any().alias("delisted"),
            pl.col("ret").is_not_null().any().alias("_any"),
            (pl.col("conditionaltype").last() == "NT").alias("suspended"),
        )
        .with_columns(
            pl.when(pl.col("missing_delist_ret") | ~pl.col("_any"))
            .then(None)
            .otherwise(pl.col("_r"))
            .alias("ret_1m")
        )
        .drop("_r", "_any")
        .sort("permno", "month")
    )


def month_end_members(daily: pl.DataFrame) -> pl.DataFrame:
    """Eligible names on each month's last market day."""
    last = daily.select(pl.col("date").unique()).group_by(
        pl.col("date").dt.truncate("1mo").alias("month")
    ).agg(pl.col("date").max())
    return (
        daily.join(last, on="date")
        .with_columns(
            group_expr(),
            (pl.col("cap").sum().over("permco", "date") / 1000.0).alias("company_mcap_mm"),
        )
        .filter(
            pl.col("group").is_not_null()
            & (pl.col("securitytype") == "EQTY")
            & (pl.col("securitysubtype") == "COM")
            & pl.col("sharetype").is_in(SHARE_TYPES)
            & pl.col("primaryexch").is_in(["N", "A", "Q"])
            & (pl.col("conditionaltype") == "RW")
            & (pl.col("tradingstatusflg") == "A")
            & (pl.col("delist") != "Y")
        )
    )


def add_momentum_and_forward(members: pl.DataFrame, rets: pl.DataFrame) -> pl.DataFrame:
    """12-1 momentum (11 months, skipping the latest) and next-month return.
    Both are calendar-aligned, so a gap in a name's history yields null."""
    last_month = rets["month"].max()
    hist = [
        rets.select("permno", pl.col("month").dt.offset_by(f"{k}mo"), pl.col("ret_1m"))
        for k in range(1, 12)
    ]
    mom = (
        pl.concat(hist)
        .group_by("permno", "month")
        .agg(((1 + pl.col("ret_1m")).product() - 1).alias("mom_12_1"), pl.col("ret_1m").count().alias("_n"))
        .filter(pl.col("_n") == 11)
        .drop("_n")
    )
    fwd = rets.select(
        "permno",
        pl.col("month").dt.offset_by("-1mo"),
        pl.col("ret_1m").alias("fwd_ret_1m"),
        pl.col("delisted").alias("fwd_delisted"),
        pl.col("missing_delist_ret").alias("fwd_missing_delist"),
        pl.col("suspended").alias("fwd_suspended"),
    )
    return (
        members.join(mom, on=["permno", "month"], how="left")
        .join(fwd, on=["permno", "month"], how="left")
        .with_columns((pl.col("month") < last_month).alias("has_fwd_month"))
    )


def prepare_fundamentals(fq: pl.DataFrame) -> pl.DataFrame:
    """TTM EBITDA, net debt and seasonal SUE per fiscal quarter, USD reporters only."""
    f = (
        fq.filter(pl.col("curcdq") == "USD")
        .sort("gvkey", "datadate", "fyearq", "fqtr")
        .unique(["gvkey", "datadate"], keep="last", maintain_order=True)
    )
    lag = pl.when(pl.col("fqtr") == 4).then(pl.lit(90)).otherwise(pl.lit(45))
    g = "gvkey"
    f = f.with_columns(
        (pl.col("datadate") + pl.duration(days=lag)).alias("_lagdate"),
        (pl.col("datadate") - pl.col("datadate").shift(3).over(g)).dt.total_days().alias("_s3"),
        (pl.col("datadate") - pl.col("datadate").shift(4).over(g)).dt.total_days().alias("_s4"),
        (pl.col("epspxq") / pl.col("ajexq")).alias("_eps"),
    ).with_columns(
        pl.max_horizontal("rdq", "_lagdate").alias("stmt_avail"),
        pl.coalesce("rdq", "_lagdate").alias("eps_avail"),
        pl.when(pl.col("_s3").is_between(265, 285))
        .then(pl.col("oibdpq").rolling_sum(4, min_samples=4).over(g))
        .alias("ebitda_ttm"),
        (pl.col("dlttq").fill_null(0) + pl.col("dlcq").fill_null(0) - pl.col("cheq").fill_null(0)
         + pl.col("pstkq").fill_null(0) + pl.col("mibq").fill_null(0)).alias("net_debt_mm"),
        pl.when(pl.col("_s4").is_between(355, 375))
        .then(pl.col("_eps") - pl.col("_eps").shift(4).over(g))
        .alias("_de"),
    )
    sd = pl.col("_de").shift(1).rolling_std(8, min_samples=6).over(g)
    return f.with_columns(
        pl.when(sd > 1e-9).then(pl.col("_de") / sd).alias("sue")
    ).drop("_lagdate", "_s3", "_s4", "_eps", "_de")


def attach_links(members: pl.DataFrame, links: pl.DataFrame) -> pl.DataFrame:
    ok = links.filter(pl.col("linktype").is_in(["LU", "LC"]) & pl.col("linkprim").is_in(["P", "C"]))
    hit = (
        members.select("permno", "date").join(ok, on="permno")
        .filter(
            (pl.col("linkdt").is_null() | (pl.col("linkdt") <= pl.col("date")))
            & (pl.col("linkenddt").is_null() | (pl.col("linkenddt") >= pl.col("date")))
        )
        .sort("permno", "date", "linkprim", "gvkey", descending=[False, False, True, False])
        .unique(["permno", "date"], keep="first", maintain_order=True)
        .select("permno", "date", "gvkey")
    )
    return members.join(hit, on=["permno", "date"], how="left")


def attach_fundamentals(panel: pl.DataFrame, f: pl.DataFrame) -> pl.DataFrame:
    """Latest statement and latest SUE known strictly before each panel date."""
    def latest(avail: str, cols: list[str]) -> pl.DataFrame:
        # A late filing for an old quarter must not displace a newer quarter.
        src = f.filter(pl.col(avail).is_not_null()).select("gvkey", avail, "datadate", *cols)
        return (
            panel.filter(pl.col("gvkey").is_not_null()).select("permno", "date", "gvkey")
            .join(src, on="gvkey")
            .filter(
                (pl.col(avail) < pl.col("date"))
                & (pl.col("datadate") >= pl.col("date") - pl.duration(days=MAX_STATEMENT_AGE_DAYS))
            )
            .sort("datadate")
            .unique(["permno", "date"], keep="last", maintain_order=True)
            .drop("gvkey")
        )

    stmt = latest("stmt_avail", ["ebitda_ttm", "net_debt_mm"]).rename({"datadate": "stmt_period"})
    eps = latest("eps_avail", ["sue"]).rename({"datadate": "eps_period"})
    out = panel.join(stmt, on=["permno", "date"], how="left").join(eps, on=["permno", "date"], how="left")
    ev = pl.col("company_mcap_mm") + pl.col("net_debt_mm")
    return out.with_columns(
        pl.when((ev > 0) & (pl.col("ebitda_ttm") > 0) & (pl.col("sharetype") != "AD")).then(pl.col("ebitda_ttm") / ev).alias("ebitda_ev")
    )


def build_panel(daily: pl.DataFrame, links: pl.DataFrame, fq: pl.DataFrame) -> pl.DataFrame:
    panel = add_momentum_and_forward(month_end_members(daily), monthly_returns(daily))
    panel = attach_fundamentals(attach_links(panel, links), prepare_fundamentals(fq))
    return panel.sort("date", "permno")
