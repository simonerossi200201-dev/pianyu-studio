# -*- coding: utf-8 -*-
"""Render one golden case and print ONLY its audio-stream md5. For bisecting."""
from __future__ import annotations
import os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
if not os.path.isfile(FFMPEG):
    FFMPEG = "ffmpeg"

def main() -> int:
    case = sys.argv[1] if len(sys.argv) > 1 else "g08_fx_static"
    import golden_check as gc
    cd = os.path.join(gc.GOLDEN_ROOT, case)
    man = gc.load_manifest(cd)
    host, _app = gc.boot_host()
    _d, tasks = gc.load_tasks(host, os.path.join(cd, man.get("project", "project.asproj.json")))
    gc.reset_case_caches()
    tmp = tempfile.mkdtemp(prefix="am_")
    outs = gc.render_serial(host, tasks, tmp, man.get("mode") or "quality",
                            bool(man.get("gpu", False)))
    p = subprocess.run([FFMPEG, "-v", "error", "-i", outs[0], "-map", "0:a:0",
                        "-c", "copy", "-f", "md5", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print("AUDIOMD5 %s" % p.stdout.strip().replace("MD5=", "")[:16], flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
