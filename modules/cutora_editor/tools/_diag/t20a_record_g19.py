# -*- coding: utf-8 -*-
"""One-process record of g19_stitch_timeline. Allows dirty T20a product files."""
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

CASE = "g19_stitch_timeline"
ALLOWED_DIRTY_PREFIXES = (
    "cutora/batch/script_stitcher.py",
    "cutora/batch/timeline_stitch.py",
    "cutora/core/cmds/batch_ops.py",
    "cutora/core/cmds/specs_batch.py",
    "cutora/model/project_io.py",
    "cutora/ui/main_window.py",
    "tests/test_t20a_timeline_stitch.py",
    "tests/golden/g19_stitch_timeline/",
    "tests/golden/README.md",
    "tests/golden/_media/",
    "docs/plan/",
    "tools/_diag/t20a_",
    "tools/model_compare.py",
)


def _allowed(path: str) -> bool:
    norm = path.replace("\\", "/")
    for p in ALLOWED_DIRTY_PREFIXES:
        if norm == p.rstrip("/") or norm.startswith(p):
            return True
    return False


def main() -> int:
    import golden_check as gc

    case_dir = os.path.join(gc.GOLDEN_ROOT, CASE)
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
    dirty = fp.get("dirty_files") or []
    bad = []
    for ln in dirty:
        path = gc._porcelain_path(ln).replace("\\", "/")
        if path.startswith("cutora/") and not _allowed(path):
            bad.append(ln)
    if bad:
        print("REFUSING unexpected cutora/ dirty", bad, flush=True)
        return 2
    manifest = gc.load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    _data, tasks = gc.load_tasks(host, project)
    gc.reset_case_caches()
    tmp = tempfile.mkdtemp(prefix="%s_record_" % CASE)
    mode = manifest.get("mode") or "quality"
    use_gpu = bool(manifest.get("gpu", False))
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
