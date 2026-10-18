"""Robota voice worker protocol helper (runs inside the local_tts venvs; stdlib only).

Protocol: one JSON object per line on stdin -> one JSON object per line on stdout.
Model libraries often print to stdout, so the real stdout is kept for the protocol
and sys.stdout is redirected to stderr.
"""
from __future__ import annotations

import json
import sys
import traceback

_OUT = sys.stdout
sys.stdout = sys.stderr


def serve(handle) -> None:
    _OUT.write(json.dumps({"ready": True}) + "\n")
    _OUT.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            res = handle(req)
            res.setdefault("ok", True)
        except Exception as e:  # report, keep serving
            traceback.print_exc()
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        _OUT.write(json.dumps(res, ensure_ascii=False) + "\n")
        _OUT.flush()
