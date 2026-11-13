"""Runs the JavaScript pricing-engine checks (terminal/bs.js) under pytest."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_black_scholes_engine():
    res = subprocess.run(["node", str(ROOT / "tests" / "test_bs.mjs")], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[-2000:]
    assert "all checks passed" in res.stdout
