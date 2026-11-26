"""Trial ledger: every backtest configuration ever run, counted once.

`run_trial` is the only way scripts may run a portfolio backtest (enforced by
tests/test_ledger.py). It writes a `start` event BEFORE the backtest runs, so a
trial that crashes or is abandoned still counts, then a `result` or `error`.

The file is append-only JSONL in gitignored data_store/, because results on
licensed (WRDS) data may not be published with this public repo. Back it up.

Trial identity is (signal, version, dataset, resolved parameters). Reruns on
newer data keep the key; bump `version` whenever the signal formula changes.
"""

from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import math
import subprocess
import uuid
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from signal_engine.config import REPO_ROOT
from signal_engine.validation.backtest import BacktestResult, walk_forward_ls

LEDGER_PATH = REPO_ROOT / "data_store" / "trial_ledger.jsonl"
_DEFAULTS = {
    k: p.default
    for k, p in inspect.signature(walk_forward_ls).parameters.items()
    if p.default is not inspect.Parameter.empty
}


def resolve_params(bt_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults so omitted and explicit defaults are the same trial, and
    reduce an explicit calendar to its start and spacing so new months do not
    make a new trial."""
    unknown = set(bt_kwargs) - set(_DEFAULTS)
    if unknown:
        raise TypeError(f"unknown backtest parameters: {sorted(unknown)}")
    p = {**_DEFAULTS, **bt_kwargs}
    grid = p.pop("rebalance_dates")
    if grid is not None:
        days = sorted(grid)
        weeks = round(float(np.median([(b - a).days for a, b in pairwise(days)])) / 7)
        p["rebalance_every"] = f"grid from {days[0]} every {weeks}w"
    if p["min_book"] is None:
        p["min_book"] = max(5, p["min_names"] // p["n_quantiles"])
    return {
        k: float(v) if isinstance(v, int) and not isinstance(v, bool) else v for k, v in sorted(p.items())
    }


def trial_key(signal: str, version: str, dataset: str, params: dict[str, Any]) -> str:
    blob = json.dumps([signal, version, dataset, params], sort_keys=True, allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def permutation_pvalue(res: BacktestResult, n_perm: int = 500, seed: int = 0) -> float:
    """One-sided p for the gross Sharpe against signals shuffled within each date.

    Gross, not net: shuffled signals churn far more than a real one, so a net
    comparison would reward persistence and understate p. Each draw ranks the
    same tradable names into the same book sizes as the real run.
    """
    if n_perm < 1:
        raise ValueError("n_perm must be at least 1")
    rows = res.period_returns
    if len(res.name_returns) != rows.height:
        raise ValueError("result carries no per-name returns")
    rng = np.random.default_rng(seed)
    null = np.empty((n_perm, rows.height))
    for j, (r, nl, ns) in enumerate(
        zip(res.name_returns, rows["n_long"], rows["n_short"], strict=True)
    ):
        draws = rng.permuted(np.tile(r, (n_perm, 1)), axis=1)
        null[:, j] = draws[:, -nl:].mean(axis=1) - draws[:, :ns].mean(axis=1)

    def sharpe(x: np.ndarray) -> np.ndarray:
        sd = x.std(axis=-1, ddof=1)
        return np.divide(x.mean(axis=-1), sd, out=np.zeros_like(sd), where=sd > 0)

    if rows.height < 2:
        return 1.0
    obs = sharpe(rows["ls_gross"].to_numpy()[None, :])[0]
    return float((1 + (sharpe(null) >= obs).sum()) / (n_perm + 1))


def read_events(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    """All events. A malformed line raises: a ledger that skips lines undercounts."""
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{i}: corrupt ledger line") from e
    return out


def append_event(event: dict[str, Any], path: Path = LEDGER_PATH) -> None:
    line = json.dumps(event, sort_keys=True, default=str, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        f.flush()


def trials(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    """One record per distinct configuration, in first-run order, with its latest result."""
    out: dict[str, dict[str, Any]] = {}
    for e in read_events(path):
        t = out.setdefault(e["key"], {"key": e["key"], "first": e["at"], "runs": 0, "status": "unfinished"})
        if e["event"] == "start":
            t.update({k: e[k] for k in ("signal", "version", "dataset", "params", "hypothesis")})
            t.update(runs=t["runs"] + 1, last=e["at"])
        elif e["event"] == "result":
            t.update(status="done", summary=e["summary"], perm_pvalue=e["perm_pvalue"], note=e.get("note"))
        elif t["status"] != "done":
            t["status"] = "error"
    return list(out.values())


def _git() -> str:
    def run(*a: str) -> str:
        return subprocess.run(["git", "-C", str(REPO_ROOT), *a], capture_output=True, text=True,
                              check=False).stdout.strip()
    code = ["signal_engine", "scripts"]
    return run("rev-parse", "--short", "HEAD") + ("+dirty" if run("status", "--porcelain", "--", *code) else "")


def _finite(d: dict[str, float]) -> dict[str, float | None]:
    return {k: float(v) if math.isfinite(v) else None for k, v in d.items()}


def run_trial(
    signals: pl.DataFrame,
    prices: pl.DataFrame,
    *,
    signal: str,
    dataset: str,
    hypothesis: str,
    version: str = "1",
    n_perm: int = 500,
    seed: int = 0,
    path: Path | None = None,
    **bt_kwargs: Any,
) -> BacktestResult:
    path = path or LEDGER_PATH
    params = resolve_params(bt_kwargs)
    base = {"run": uuid.uuid4().hex[:12], "key": trial_key(signal, version, dataset, params)}

    def log(event: str, **kw: Any) -> None:
        append_event({**base, "event": event, "at": datetime.now(UTC).isoformat(timespec="seconds"), **kw}, path)

    log("start", signal=signal, version=version, dataset=dataset, params=params, hypothesis=hypothesis,
        git=_git(), data={"signal_rows": signals.height, "signals_through": str(signals["date"].max()),
                          "prices_through": str(prices["date"].max())})
    try:
        res = walk_forward_ls(signals, prices, **bt_kwargs)
        p = permutation_pvalue(res, n_perm=n_perm, seed=seed)
    except BaseException as e:
        log("error", error=f"{type(e).__name__}: {e}")
        raise
    log("result", summary=_finite(res.summary), perm_pvalue=p, n_perm=n_perm, seed=seed)
    return res


def backfill(
    *, signal: str, dataset: str, hypothesis: str, at: str, summary: dict[str, float],
    note: str, version: str = "1", path: Path | None = None, **bt_kwargs: Any,
) -> bool:
    """Record a trial that predates the ledger. Idempotent on (key, date)."""
    path = path or LEDGER_PATH
    date.fromisoformat(at[:10])
    params = resolve_params(bt_kwargs)
    key = trial_key(signal, version, dataset, params)
    if any(e["key"] == key and e["at"] == at for e in read_events(path)):
        return False
    base = {"run": uuid.uuid4().hex[:12], "key": key, "at": at}
    append_event({**base, "event": "start", "signal": signal, "version": version, "dataset": dataset,
                  "params": params, "hypothesis": hypothesis, "git": None, "data": None}, path)
    append_event({**base, "event": "result", "summary": _finite(summary), "perm_pvalue": None, "note": note}, path)
    return True
