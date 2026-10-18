# -*- coding: utf-8 -*-
"""One-process record of g09_linebreak_latin only (T04f-6b3)."""
from __future__ import annotations

import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    import golden_check as gc
    case = "g09_linebreak_latin"
    case_dir = os.path.join(gc.GOLDEN_ROOT, case)
    host, _app = gc.boot_host()
    fp = gc.collect_fingerprint()
    print(gc.format_fingerprint(fp), flush=True)
    if fp.get("source_dirty"):
        print("REFUSING dirty source tree", flush=True)
        return 2
    if fp.get("fonts_count") != 240:
        print("REFUSING fonts_count=%s" % fp.get("fonts_count"), flush=True)
        return 2
    want = "80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f"
    if str(fp.get("fonts_digest") or "") != want:
        print("FONT SET CHANGED", fp.get("fonts_digest"), flush=True)
        return 2
    manifest = gc.load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    _data, tasks = gc.load_tasks(host, project)
    gc.reset_case_caches()
    import tempfile
    tmp = tempfile.mkdtemp(prefix="g09_record_")
    outs = gc.render_serial(
        host, tasks, tmp, manifest.get("mode") or "quality",
        bool(manifest.get("gpu", False)))
    got = outs[0]
    digest = gc.sha256_file(got)
    dest_mp4 = os.path.join(case_dir, "expected.mp4")
    shutil.copy2(got, dest_mp4)
    sha_path = os.path.join(case_dir, "expected.sha256")
    with open(sha_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("%s  expected.mp4\n" % digest)
    print("RECORDED %s" % digest, flush=True)
    print("WROTE %s" % dest_mp4, flush=True)
    print("WROTE %s" % sha_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
