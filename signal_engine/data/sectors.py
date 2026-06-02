"""SIC code -> sector bucket. Resolves via signal_engine/data/sic_to_sector.csv.

EDGAR's `submissions` endpoint returns SIC as a 4-digit string per CIK.
GICS is not available without a paid feed; this CSV is the single source of
truth for sector neutralization across the engine.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

MAPPING_CSV = Path(__file__).resolve().parent / "sic_to_sector.csv"

UNCLASSIFIED = "Unclassified"


@dataclass(frozen=True)
class SicRange:
    low: int          # inclusive; -inf encoded as -1
    high: int         # inclusive; +inf encoded as 10_000
    sector: str
    note: str


def _parse_bound(s: str, *, is_low: bool) -> int:
    s = s.strip()
    if s == "*":
        return -1 if is_low else 10_000
    return int(s)


def load_sic_ranges(path: Path = MAPPING_CSV) -> list[SicRange]:
    ranges: list[SicRange] = []
    with path.open() as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            if row[0].strip().lower() == "sic_low":
                continue  # header
            low, high, sector, note = row[0], row[1], row[2], row[3] if len(row) > 3 else ""
            ranges.append(SicRange(
                low=_parse_bound(low, is_low=True),
                high=_parse_bound(high, is_low=False),
                sector=sector.strip(),
                note=note.strip(),
            ))
    return ranges


def sic_to_sector(sic: str | int | None, ranges: list[SicRange] | None = None) -> str:
    """First matching range wins. Empty / None / unparseable -> Unclassified."""
    if sic is None or sic == "":
        return UNCLASSIFIED
    try:
        sic_int = int(sic)
    except (TypeError, ValueError):
        return UNCLASSIFIED
    if ranges is None:
        ranges = load_sic_ranges()
    for r in ranges:
        if r.low <= sic_int <= r.high:
            return r.sector
    return UNCLASSIFIED
