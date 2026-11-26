#!/usr/bin/env python
"""Data QA. Exit 1 if anything fails (warnings do not).

    scripts/doctor.py              everything
    scripts/doctor.py --published  committed site artifacts only (used by CI)
    scripts/doctor.py --notify     also raise a macOS notification on failure
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date

from signal_engine.config import REPO_ROOT
from signal_engine.doctor import FAIL, OK, WARN, local, published


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--published", action="store_true")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="hide passing checks")
    a = ap.parse_args()
    today = date.today()
    checks = published(REPO_ROOT, today) + ([] if a.published else local(REPO_ROOT, today))
    for c in checks:
        if not (a.quiet and c.level == OK):
            print(f"{c.level:<5}{c.name:<28}{c.detail}")
    bad = [c for c in checks if c.level == FAIL]
    print(f"[doctor] {len(checks)} checks, {sum(c.level == WARN for c in checks)} warnings, {len(bad)} failures")
    if bad and a.notify:
        msg = ", ".join(c.name for c in bad)[:150].replace('"', "").replace("\\", "")
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "EverythingEnergy data failure"'],
                       check=False)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
