# -*- coding: utf-8 -*-
"""One-process record of a single golden case (T04f-6b3 discipline)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: t11g_record_one.py <case>")
        return 2
    case = sys.argv[1]
    import golden_check as gc

    case_dir = os.path.join(gc.GOLDEN_ROOT, case)
    host, _app = gc.boot_host()
    fp = gc.collect_fingerprint()
    print(gc.format_fingerprint(fp), flush=True)
    if fp.get("fonts_count") != 240:
        print("REFUSING fonts_count=%s" % fp.get("fonts_count"), flush=True)
        return 2
    want = "80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f"
    if str(fp.get("fonts_digest") or "") != want:
        print("FONT SET CHANGED", fp.get("fonts_digest"), flush=True)
        return 2
    # New case dirs under tests/golden/ trip source_dirty. Product code must
    # still be the 599013d tree (cutora/ clean).
    dirty = fp.get("dirty_files") or []
    cutora_dirty = [ln for ln in dirty if "cutora/" in ln.replace("\\", "/")]
    if cutora_dirty:
        print("REFUSING cutora/ dirty", cutora_dirty, flush=True)
        return 2
    manifest = gc.load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    _data, tasks = gc.load_tasks(host, project)
    gc.reset_case_caches()
    tmp = tempfile.mkdtemp(prefix="%s_record_" % case)
    mode = manifest.get("mode") or "quality"
    use_gpu = bool(manifest.get("gpu", False))
    workers = int(manifest.get("workers") or 1)
    if workers > 1 and len(tasks) > 1:
        outs = gc.render_parallel(
            host, tasks, os.path.join(tmp, "par"), mode, use_gpu, workers)
    else:
        outs = gc.render_serial(host, tasks, tmp, mode, use_gpu)
    exp_names = manifest.get("expected") or ["expected.mp4"]
    if len(outs) != len(exp_names):
        print("count mismatch produced %d expected %d" % (len(outs), len(exp_names)))
        return 2
    lines = []
    for got, name in zip(outs, exp_names):
        dest = os.path.join(case_dir, name)
        shutil.copy2(got, dest)
        digest = gc.sha256_file(got)
        lines.append("%s  %s" % (digest, os.path.basename(name)))
        print("RECORDED %s %s" % (name, digest), flush=True)
    sha_path = os.path.join(case_dir, "expected.sha256")
    with open(sha_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("WROTE %s" % sha_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
