# -*- coding: utf-8 -*-
"""Sequential one-process-per-case record of g11-g18."""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
ONE = os.path.join(REPO, "tools", "_diag", "t11g_record_one.py")
CASES = [
    "g11_vertical_basic_fx",
    "g12_vertical_fx_path",
    "g13_horizontal_it_fx",
    "g14_stitch_fx",
    "g15_scatter_insert_fx",
    "g16_parallel_fx",
    "g17_cli_fx",
    "g18_linebreak_latin_fx",
]


def main() -> int:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    for case in CASES:
        print("======== RECORD %s ========" % case, flush=True)
        r = subprocess.run([PY, "-u", ONE, case], cwd=REPO, env=env)
        if r.returncode != 0:
            print("FAILED", case, r.returncode, flush=True)
            return r.returncode or 2
    print("ALL RECORDED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
