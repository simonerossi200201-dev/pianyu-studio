# -*- coding: utf-8 -*-
"""T09: security_scan 业务代码栏必须为零。"""
from __future__ import annotations

import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_security_scan_code_hits_zero():
    script = os.path.join(REPO, "tools", "security_scan.ps1")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert "HITS_CODE=0" in out, out[-4000:]
    assert r.returncode == 0, out[-4000:]
