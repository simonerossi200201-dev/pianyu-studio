# -*- coding: utf-8 -*-
"""Watch expand job J00140. Print DONE/FAILED only."""
import json, time, urllib.request, sys
from pathlib import Path

JID = "J00140"
URL = "http://127.0.0.1:9400/api/jobs/" + JID
LOG = Path(r"D:\0_Dev\Orante\data\logs\J00140.log")
TRACE = Path(r"D:\0_Dev\Orante\data\logs\watch-j00140.txt")

def snap():
    try:
        with urllib.request.urlopen(URL, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}

def tail(n=8):
    if not LOG.is_file():
        return ""
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return " | ".join(lines[-n:])

prev = None
while True:
    d = snap()
    job = (d.get("job") or d) if isinstance(d, dict) else {}
    st = job.get("status") or ""
    TRACE.write_text(json.dumps(job, ensure_ascii=False)[-4000:], encoding="utf-8")
    if st in ("done", "success"):
        print("DONE: J00140 " + tail(3))
        sys.exit(0)
    if st in ("failed", "error"):
        print("FAILED: J00140 " + str(job.get("error") or "") + " " + tail(5))
        sys.exit(1)
    if st in ("cancelled",):
        print("FAILED: J00140 cancelled " + tail(3))
        sys.exit(1)
    time.sleep(20)
