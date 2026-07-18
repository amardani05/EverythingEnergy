"""Output — snapshot persistence + day-over-day diff artifacts."""

from signal_engine.output.snapshots import (
    DiffReport,
    diff_snapshots,
    latest_prior_snapshot,
    render_diff_markdown,
    snapshot_path,
    write_diff,
    write_snapshot,
)

__all__ = [
    "DiffReport",
    "diff_snapshots",
    "latest_prior_snapshot",
    "render_diff_markdown",
    "snapshot_path",
    "write_diff",
    "write_snapshot",
]
