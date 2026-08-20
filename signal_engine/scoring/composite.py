"""Composite scoring - the layer between raw factor values and a tradable rank.

Pipeline per config.yaml `scoring` + `signals.selection`:

  raw component values (factors/)                      e.g. ev_ebitda, sue
    -> sign-orient (lower-is-better components flip)   SIGN_CONVENTIONS
    -> winsorize at cross-sectional percentiles        scoring.winsorize_pct
    -> z-score within the name's PEER GROUP            taxonomy basket
    -> family score = mean of component z's
    -> composite = weighted mean of enabled family scores (weights from
       config), renormalized over the families available for each name;
       names with fewer than MIN_FAMILIES families are not ranked.

Neutralization basis (changed 2026-07-28): the peer group is the taxonomy
BASKET, not the GICS-ish sector. In an all-energy universe every name maps
to the same sector label, so a sector z neutralized nothing and a structural
upstream-vs-utilities tilt would have been reported as stock selection. The
ladder is basket -> super-basket (SUPER_BASKET, commodity-chain groupings)
-> global, falling back whenever a group holds fewer than MIN_SECTOR_N
names; only 10 of the 23 baskets clear that bar on their own. Each row
carries `neutralization_level` so the basis is auditable per name.

PIT: every factor input arrives through the as_of_* read API (the factors
themselves enforce this); the sector map is a slowly-varying attribute
(SIC reclassification is rare and carries no forward return information),
so it uses the nearest available submissions snapshot rather than a strict
knowledge-date gate - documented deviation, revisit if SIC-based signals
are ever added.

Missing-PEAD semantics: a name with no earnings event inside the hold
window has no `sue` component - the pead family simply drops out of that
name's composite (weights renormalize). No news is treated as no drift
signal, not as a zero surprise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import polars as pl

from signal_engine.atlas.clusters import energy_universe_tickers, ticker_to_basket
from signal_engine.config import Config
from signal_engine.data.sectors import UNCLASSIFIED, load_sic_ranges, sic_to_sector
from signal_engine.factors.momentum import momentum_as_of
from signal_engine.factors.pead import compute_sue
from signal_engine.factors.quality import compute_quality
from signal_engine.factors.value import compute_value

log = logging.getLogger(__name__)

# +1 = higher is better; -1 = lower is better (flipped before z-scoring).
SIGN_CONVENTIONS: dict[str, int] = {
    "fcf_yield": +1,
    "ev_ebitda": -1,
    "roic": +1,
    "accruals": -1,
    "margin_stability": +1,
    "momentum": +1,
    "sue": +1,
}

# Which factor family owns which component columns.
FAMILY_COMPONENTS: dict[str, list[str]] = {
    "value": ["fcf_yield", "ev_ebitda"],
    "quality": ["roic", "accruals", "margin_stability"],
    "momentum": ["momentum"],
    "pead": ["sue"],
}

# A name must have at least this many families present to receive a
# composite - one lone family would rank on a different effective scale.
MIN_FAMILIES = 2

# Groups with fewer names than this fall back a level - a 3-name group z is
# noise dressed up as neutralization.
MIN_SECTOR_N = 8

# Coarse groupings of the 23 taxonomy baskets. Most baskets are too thin to
# support their own z (only 10 of 23 clear MIN_SECTOR_N), so the neutralization
# falls back basket -> super-basket -> global. The groupings follow the
# commodity chain, which is what actually drives co-movement here.
SUPER_BASKET: dict[str, str] = {
    "iocs_integrated": "upstream",
    "upstream_oil_eandp": "upstream",
    "upstream_gas_eandp": "upstream",
    "upstream_offshore_drillers": "upstream",
    "minerals_royalty": "upstream",

    "ofs_onshore": "oilfield_services",
    "ofs_offshore": "oilfield_services",
    "ofs_equipment": "oilfield_services",

    "midstream_gpt": "midstream",
    "midstream_pipelines": "midstream",
    "lng_terminals": "midstream",
    "tanker_shipping": "midstream",

    "downstream_refiners": "downstream",
    "petrochem": "downstream",
    "downstream_retail": "downstream",
    "downstream_biofuels": "downstream",

    "power_utilities_regulated_gas": "power",
    "power_utilities_regulated_ldc": "power",
    "power_ipps_merchant": "power",
    "power_renewables": "power",
    "nuclear_smr_developers": "power",

    "coal": "fuel_minerals",
    "uranium_nuclear_fuel": "fuel_minerals",
}
UNMAPPED_BASKET = "unmapped"

# PEAD hold window: most recent SUE within ~63 trading days of as_of.
PEAD_HOLD_CALENDAR_DAYS = 92


@dataclass(frozen=True)
class SignalConfig:
    """Parsed signals.selection entry."""
    name: str
    enabled: bool
    weight: float
    components: list[str]


def selection_signals(cfg: Config) -> list[SignalConfig]:
    """Parse signals.selection from config; components default to the
    family's full component list."""
    out: list[SignalConfig] = []
    for entry in cfg.raw["signals"]["selection"]:
        name = entry["name"]
        out.append(SignalConfig(
            name=name,
            enabled=bool(entry.get("enabled", False)),
            weight=float(entry.get("weight", 1.0)),
            components=list(entry.get("components", FAMILY_COMPONENTS.get(name, [name]))),
        ))
    return out


# ---------- universe + sector plumbing ----------

def universe_ticker_to_cik(con: duckdb.DuckDBPyConnection, as_of: date,
                           min_price_history_days: int,
                           *, tickers: list[str] | None = None) -> dict[str, int]:
    """Selection universe: the hand-curated ENERGY taxonomy, resolved through
    the latest ticker->CIK map and filtered to names with enough price history
    at `as_of`. This engine is energy-only by design; the broad-market S&P 600
    is not in scope.

    `tickers` overrides the default energy universe (tests inject their own).
    Survivorship note: the taxonomy is a fixed curated list applied to all
    history, so delisted energy names are absent; every output row carries
    survivorship_clean = False."""
    universe = [t.upper() for t in (tickers if tickers is not None
                                    else energy_universe_tickers())]
    incoming = pl.DataFrame({"ticker": universe}, schema={"ticker": pl.Utf8})
    con.register("_universe", incoming)
    rows = con.execute("""
        WITH cmap AS (
            SELECT ticker, cik FROM (
                SELECT ticker, cik,
                       row_number() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
                FROM ticker_cik_map
            ) WHERE rn = 1
        ), hist AS (
            SELECT ticker, count(*) AS n_days FROM prices
            WHERE date <= ? GROUP BY ticker
        )
        SELECT u.ticker, cmap.cik
        FROM _universe u
        JOIN cmap USING (ticker)
        JOIN hist USING (ticker)
        WHERE hist.n_days >= ?
    """, [as_of, min_price_history_days]).fetchall()
    con.unregister("_universe")
    return {t: int(c) for t, c in rows}


def sector_map(con: duckdb.DuckDBPyConnection, as_of: date) -> pl.DataFrame:
    """cik -> sector via SIC. Nearest submissions snapshot at-or-before
    `as_of`, else the earliest available (SIC is a slowly-varying attribute
    - see module docstring). Returns columns: cik, sector."""
    sics = con.execute("""
        SELECT cik, sic FROM (
            SELECT cik, sic, snapshot_date,
                   row_number() OVER (
                       PARTITION BY cik
                       ORDER BY (snapshot_date <= ?) DESC,
                                CASE WHEN snapshot_date <= ? THEN snapshot_date END DESC,
                                snapshot_date ASC
                   ) AS rn
            FROM edgar_submissions
        ) WHERE rn = 1
    """, [as_of, as_of]).fetchall()
    ranges = load_sic_ranges()
    return pl.DataFrame(
        {"cik": [int(c) for c, _ in sics],
         "sector": [sic_to_sector(s, ranges) for _, s in sics]},
        schema={"cik": pl.Int64, "sector": pl.Utf8},
    )


# ---------- normalization primitives ----------

def winsorize_series(s: pl.Series, lo: float, hi: float) -> pl.Series:
    """Clip to the [lo, hi] cross-sectional quantiles (nulls untouched)."""
    valid = s.drop_nulls()
    if valid.len() < 3:
        return s
    lo_v, hi_v = valid.quantile(lo, "linear"), valid.quantile(hi, "linear")
    assert lo_v is not None and hi_v is not None
    return s.clip(lo_v, hi_v)


def zscore_expr(col: str, *, over: str | None = None) -> pl.Expr:
    """(x - mean) / std as an expression; std==0 or n<2 -> 0.0 (flat group
    carries no ranking information, not a divide-by-zero)."""
    mean = pl.col(col).mean().over(over) if over else pl.col(col).mean()
    std = pl.col(col).std(ddof=1).over(over) if over else pl.col(col).std(ddof=1)
    z = (pl.col(col) - mean) / std
    return (
        pl.when(pl.col(col).is_null()).then(None)
        .when(std.is_null() | (std == 0.0)).then(0.0)
        .otherwise(z)
    )


# ---------- raw component collection ----------

def raw_components(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    t2c: dict[str, int],
    families: list[str],
) -> pl.DataFrame:
    """One wide row per ticker with every needed raw component column."""
    base = pl.DataFrame(
        {"ticker": list(t2c.keys()), "cik": [t2c[t] for t in t2c]},
        schema={"ticker": pl.Utf8, "cik": pl.Int64},
    )
    if "momentum" in families:
        mom = momentum_as_of(con, as_of, tickers=list(t2c.keys()))
        base = base.join(mom.select(["ticker", "momentum"]), on="ticker", how="left")
    if "value" in families:
        val = compute_value(con, as_of, t2c)
        base = base.join(val.select(["ticker", "fcf_yield", "ev_ebitda"]),
                         on="ticker", how="left")
    if "quality" in families:
        qual = compute_quality(con, as_of, t2c)
        base = base.join(qual.select(["ticker", "roic", "accruals", "margin_stability"]),
                         on="ticker", how="left")
    if "pead" in families:
        events = compute_sue(con, as_of, t2c)
        window_start = as_of - timedelta(days=PEAD_HOLD_CALENDAR_DAYS)
        recent = (
            events.filter(
                (pl.col("signal_date") > window_start)
                & (pl.col("signal_date") <= as_of)
            )
            .sort("signal_date")
            .group_by("ticker").agg(pl.col("sue").last())
        )
        base = base.join(recent, on="ticker", how="left")
    return base


# ---------- the composite ----------

def build_composite(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    cfg: Config,
    *,
    universe: list[str] | None = None,
) -> pl.DataFrame:
    """Ranked, sector-neutral composite cross-section at `as_of`.

    `universe` overrides the default energy-taxonomy selection universe (tests
    inject their own ticker list).

    Returns one row per ranked ticker plus unranked rows (composite null)
    for names failing the MIN_FAMILIES floor. Columns: ticker, cik, sector,
    raw components, z_<comp> (basis per emit_sector_relative), family
    scores score_<family>, composite, n_families, rank, pctl, as_of,
    survivorship_clean.
    """
    signals = [s for s in selection_signals(cfg) if s.enabled]
    if not signals:
        raise ValueError("no enabled selection signals in config")
    families = [s.name for s in signals]

    lo, hi = (float(x) for x in cfg.raw["scoring"]["winsorize_pct"])
    sector_relative = bool(cfg.raw["scoring"].get("emit_sector_relative", True))
    min_hist = int(cfg.raw["universe"].get("min_price_history_days", 252))

    t2c = universe_ticker_to_cik(con, as_of, min_hist, tickers=universe)
    if not t2c:
        raise ValueError(f"empty universe at {as_of} (min_price_history_days={min_hist})")
    log.info("[composite] %s: %d names in universe", as_of, len(t2c))

    df = raw_components(con, as_of, t2c, families)
    df = df.join(sector_map(con, as_of), on="cik", how="left").with_columns(
        pl.col("sector").fill_null(UNCLASSIFIED)
    )

    # Neutralization basis: the taxonomy basket, NOT the GICS-ish sector.
    # In an all-energy universe every name maps to sector "Energy" (or a
    # handful of utility labels), so a sector z neutralizes essentially
    # nothing and an upstream-vs-utilities tilt would masquerade as stock
    # selection. Baskets are the real peer groups here.
    t2b = ticker_to_basket()
    basket_map = pl.DataFrame(
        {"ticker": list(t2b.keys()), "basket": [t2b[k] for k in t2b]},
        schema={"ticker": pl.Utf8, "basket": pl.Utf8},
    )
    df = (
        df.join(basket_map, on="ticker", how="left")
        .with_columns(pl.col("basket").fill_null(UNMAPPED_BASKET))
        .with_columns(
            pl.col("basket").replace_strict(
                SUPER_BASKET, default=UNMAPPED_BASKET
            ).alias("super_basket")
        )
    )

    # Group sizes drive the fallback ladder: basket -> super-basket -> global.
    df = df.with_columns([
        pl.len().over("basket").alias("_basket_n"),
        pl.len().over("super_basket").alias("_super_n"),
    ]).with_columns(
        pl.when(pl.col("_basket_n") >= MIN_SECTOR_N).then(pl.lit("basket"))
        .when(pl.col("_super_n") >= MIN_SECTOR_N).then(pl.lit("super_basket"))
        .otherwise(pl.lit("global"))
        .alias("neutralization_level")
    )

    components = [c for s in signals for c in s.components if c in df.columns]
    for comp in components:
        sign = SIGN_CONVENTIONS[comp]
        oriented = df[comp].cast(pl.Float64) * sign
        df = df.with_columns(
            winsorize_series(oriented, lo, hi).alias(f"_w_{comp}")
        ).with_columns([
            zscore_expr(f"_w_{comp}").alias(f"_zg_{comp}"),
            zscore_expr(f"_w_{comp}", over="basket").alias(f"_zb_{comp}"),
            zscore_expr(f"_w_{comp}", over="super_basket").alias(f"_zs_{comp}"),
        ])
        primary = (
            pl.when(pl.col("neutralization_level") == "basket")
            .then(pl.col(f"_zb_{comp}"))
            .when(pl.col("neutralization_level") == "super_basket")
            .then(pl.col(f"_zs_{comp}"))
            .otherwise(pl.col(f"_zg_{comp}"))
            if sector_relative else pl.col(f"_zg_{comp}")
        )
        df = df.with_columns(primary.alias(f"z_{comp}"))

    # Family scores: mean of available component z's.
    for s in signals:
        cols = [f"z_{c}" for c in s.components if f"z_{c}" in df.columns]
        if not cols:
            df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(f"score_{s.name}"))
            continue
        df = df.with_columns(
            pl.mean_horizontal([pl.col(c) for c in cols]).alias(f"score_{s.name}")
        )

    # Composite: weight-renormalized mean over available families.
    weighted_terms = [
        (pl.col(f"score_{s.name}") * s.weight).fill_null(0.0) for s in signals
    ]
    weight_present = [
        pl.when(pl.col(f"score_{s.name}").is_null()).then(0.0).otherwise(s.weight)
        for s in signals
    ]
    df = df.with_columns([
        pl.sum_horizontal(weighted_terms).alias("_wsum"),
        pl.sum_horizontal(weight_present).alias("_wtot"),
        pl.sum_horizontal([
            pl.col(f"score_{s.name}").is_not_null().cast(pl.Int32) for s in signals
        ]).alias("n_families"),
    ]).with_columns(
        pl.when(pl.col("n_families") >= MIN_FAMILIES)
        .then(pl.col("_wsum") / pl.col("_wtot"))
        .otherwise(None)
        .alias("composite")
    )

    ranked = (
        df.with_columns([
            pl.col("composite").rank(method="ordinal", descending=True).alias("rank"),
            (pl.col("composite").rank(method="average", descending=True)
             / pl.col("composite").count()).alias("pctl"),
        ])
        .with_columns([
            pl.lit(as_of).alias("as_of"),
            # The energy taxonomy is a fixed curated list applied to all
            # history, so delisted names are absent: survivorship-biased.
            pl.lit(False).alias("survivorship_clean"),
        ])
        .sort("rank", nulls_last=True)
    )
    keep = (
        ["ticker", "cik", "sector", "basket", "super_basket", "neutralization_level"]
        + components
        + [f"z_{c}" for c in components]
        + [f"score_{s.name}" for s in signals]
        + ["composite", "n_families", "rank", "pctl", "as_of", "survivorship_clean"]
    )
    return ranked.select(keep)


def family_correlation(df: pl.DataFrame) -> pl.DataFrame:
    """Spearman correlation matrix between family scores (ranked names only).
    High pairwise correlation means two families are buying the same names - diversification is illusory."""
    import numpy as np
    from scipy.stats import spearmanr

    score_cols = [c for c in df.columns if c.startswith("score_")]
    sub = df.filter(pl.col("composite").is_not_null())
    out_rows: list[dict[str, object]] = []
    for a in score_cols:
        row: dict[str, object] = {"family": a.removeprefix("score_")}
        for b in score_cols:
            if a == b:
                row[b.removeprefix("score_")] = 1.0
                continue
            pair = sub.select([a, b]).drop_nulls()
            if pair.height < 10:
                row[b.removeprefix("score_")] = None
                continue
            rho, _ = spearmanr(pair[a].to_numpy(), pair[b].to_numpy())
            row[b.removeprefix("score_")] = round(float(rho), 3) if np.isfinite(rho) else None
        out_rows.append(row)
    return pl.DataFrame(out_rows)
