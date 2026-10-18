# -*- coding: utf-8 -*-
"""Q34-4: doctor flags a panel control that writes a retired v1 key."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_q34_doctor_catches_dead_key():
    from cutora.core.doctor import scan_deprecated_writes
    from cutora.subtitle.style_schema import DEPRECATED_UNPAINTED_KEYS

    dead = "adv_bar_color"
    assert dead in DEPRECATED_UNPAINTED_KEYS
    fake = (
        "def on_change(self):\n"
        "    s = self.current_data\n"
        "    s['%s'] = self.sub_adv_bar_c.hex()\n" % dead
    )
    hits = scan_deprecated_writes(fake, path="subtitle_style.py")
    keys = {h.key for h in hits}
    assert dead in keys, hits

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [VENV_PY, "-m", "cutora", "--doctor"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "DOCTOR" in out, out
