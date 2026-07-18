"""Snapshot + diff layer — the daily artifact a human actually reads.

Per config.yaml `output`:
  * every composite run writes `signals_<as_of>.parquet` into snapshot_dir
  * the diff vs the most recent PRIOR snapshot surfaces only what changed:
      - new entrants to the top decile (config: diff.new_entrant_decile)
      - exits from the top decile
      - rank jumps >= diff.rank_jump_threshold percentile points
  * rendered as markdown next to the parquet (`diff_<as_of>.md`)

Snapshots are keyed by as_of date; re-running the same day overwrites
(idempotent) rather than appending.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "signals_"


@dataclass(frozen=True)
class DiffReport:
    as_of: date
    prev_as_of: date | None
    new_entrants: pl.DataFrame = field(default_factory=pl.DataFrame)   # entered top decile
    exits: pl.DataFrame = field(default_factory=pl.DataFrame)          # left top decile
    rank_jumps: pl.DataFrame = field(default_factory=pl.DataFrame)     # moved >= threshold pctl pts

    @property
    def is_first_snapshot(self) -> bool:
        return self.prev_as_of is None


def snapshot_path(snapshot_dir: Path, as_of: date) -> Path:
    return snapshot_dir / f"{SNAPSHOT_PREFIX}{as_of.isoformat()}.parquet"


def write_snapshot(df: pl.DataFrame, as_of: date, snapshot_dir: Path) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(snapshot_dir, as_of)
    df.write_parquet(path)
    log.info("[snapshot] wrote %s (%d rows)", path, df.height)
    return path


def latest_prior_snapshot(snapshot_dir: Path, before: date) -> tuple[date, pl.DataFrame] | None:
    """Most recent snapshot strictly before `before`, or None."""
    if not snapshot_dir.exists():
        return None
    best: date | None = None
    for p in snapshot_dir.glob(f"{SNAPSHOT_PREFIX}*.parquet"):
        try:
            d = date.fromisoformat(p.stem.removeprefix(SNAPSHOT_PREFIX))
        except ValueError:
            log.warning("[snapshot] skipping unparseable file %s", p.name)
            continue
        if d < before and (best is None or d > best):
            best = d
    if best is None:
        return None
    return best, pl.read_parquet(snapshot_path(snapshot_dir, best))


def diff_snapshots(
    prev: pl.DataFrame,
    curr: pl.DataFrame,
    *,
    as_of: date,
    prev_as_of: date,
    new_entrant_decile: float = 0.10,
    rank_jump_threshold: float = 50.0,
) -> DiffReport:
    """Compare two ranked cross-sections on `pctl` (0 = best)."""
    p = prev.select(["ticker", "pctl", "composite"]).rename(
        {"pctl": "pctl_prev", "composite": "composite_prev"})
    c = curr.select(["ticker", "sector", "pctl", "composite", "n_families"])
    joined = c.join(p, on="ticker", how="full", coalesce=True)

    in_top_now = pl.col("pctl").is_not_null() & (pl.col("pctl") <= new_entrant_decile)
    in_top_before = pl.col("pctl_prev").is_not_null() & (pl.col("pctl_prev") <= new_entrant_decile)

    new_entrants = (
        joined.filter(in_top_now & ~in_top_before)
        .sort("pctl")
        .select(["ticker", "sector", "composite", "pctl", "pctl_prev"])
    )
    exits = (
        joined.filter(in_top_before & ~in_top_now)
        .sort("pctl_prev")
        .select(["ticker", "sector", "composite", "pctl", "pctl_prev"])
    )
    jumps = (
        joined.filter(
            pl.col("pctl").is_not_null() & pl.col("pctl_prev").is_not_null()
            & ((pl.col("pctl_prev") - pl.col("pctl")).abs() * 100.0 >= rank_jump_threshold)
        )
        .with_columns(((pl.col("pctl_prev") - pl.col("pctl")) * 100.0).alias("pctl_gain"))
        .sort("pctl_gain", descending=True)
        .select(["ticker", "sector", "pctl", "pctl_prev", "pctl_gain"])
    )
    return DiffReport(as_of=as_of, prev_as_of=prev_as_of,
                      new_entrants=new_entrants, exits=exits, rank_jumps=jumps)


def _pctl(v: float | None) -> str:
    return "—" if v is None else f"{100 * v:.0f}%"


def render_diff_markdown(diff: DiffReport) -> str:
    lines: list[str] = [f"# Signal diff — {diff.as_of}", ""]
    if diff.is_first_snapshot:
        lines += ["*First snapshot — nothing to diff against.*", ""]
        return "\n".join(lines)
    lines += [f"*vs previous snapshot {diff.prev_as_of}. Percentile 0% = best.*", ""]

    def table(df: pl.DataFrame, cols: list[str], header: str) -> None:
        lines.append(f"## {header} ({df.height})")
        lines.append("")
        if df.height == 0:
            lines.extend(["*none*", ""])
            return
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in df.iter_rows(named=True):
            cells = []
            for cname in cols:
                v = r[cname]
                if cname.startswith("pctl"):
                    cells.append(_pctl(v))
                elif isinstance(v, float):
                    cells.append(f"{v:+.3f}")
                else:
                    cells.append(str(v))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    table(diff.new_entrants, ["ticker", "sector", "composite", "pctl", "pctl_prev"],
          "New top-decile entrants")
    table(diff.exits, ["ticker", "sector", "composite", "pctl", "pctl_prev"],
          "Top-decile exits")
    table(diff.rank_jumps, ["ticker", "sector", "pctl", "pctl_prev", "pctl_gain"],
          "Large rank moves")
    return "\n".join(lines)


def write_diff(diff: DiffReport, snapshot_dir: Path) -> Path:
    path = snapshot_dir / f"diff_{diff.as_of.isoformat()}.md"
    path.write_text(render_diff_markdown(diff))
    log.info("[diff] wrote %s", path)
    return path
