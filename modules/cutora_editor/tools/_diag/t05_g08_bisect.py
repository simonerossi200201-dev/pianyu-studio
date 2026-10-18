# -*- coding: utf-8 -*-
"""E1/E2 bisect for g08 weight collapse. Prints QFontInfo of addText font."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

EXPECTED = "24b8953560f0647c1831cc39806ea895a589960bd7cd444c73a1d36dc9cdc75c"

_SEEN: list[tuple[str, str, int]] = []


def _patch_run_path() -> None:
    from PySide6.QtGui import QFontInfo
    from cutora.render.textfx import raster
    orig = raster._run_path

    def wrapped(run, style, spec=None, t=None, tracking_em=None):
        from cutora.render.textfx.layout import _qfont
        font = _qfont(
            run.font_family, run.fontsize_px,
            bool(style.get("bold", True)), bool(style.get("italic", False)),
            run.tracking_em if tracking_em is None else tracking_em)
        if len(_SEEN) < 12:
            info = QFontInfo(font)
            rec = (info.family(), info.styleName(), int(info.weight()))
            if rec not in _SEEN:
                _SEEN.append(rec)
                print(
                    "QFONTINFO family=%r styleName=%r weight=%s exactMatch=%s "
                    "requested_family=%r bold=%s"
                    % (rec[0], rec[1], rec[2], info.exactMatch(),
                       run.font_family, bool(style.get("bold", True))),
                    flush=True)
        return orig(run, style, spec, t, tracking_em)

    raster._run_path = wrapped


def _export_g08() -> str:
    import golden_check as gc
    GOLDEN_ROOT, load_manifest, load_tasks, render_serial = (
        gc.GOLDEN_ROOT, gc.load_manifest, gc.load_tasks, gc.render_serial)
    reset_case_caches, sha256_file = gc.reset_case_caches, gc.sha256_file
    reset_case_caches()
    _SEEN.clear()
    host = _export_g08.host
    case_dir = os.path.join(GOLDEN_ROOT, "g08_fx_static")
    manifest = load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    data, tasks = load_tasks(host, project)
    tmp = tempfile.mkdtemp(prefix="g08_bisect_")
    outs = render_serial(
        host, tasks, tmp, manifest.get("mode") or "quality",
        bool(manifest.get("gpu", False)))
    return sha256_file(outs[0])


def main() -> int:
    import golden_check as gc
    boot_host = gc.boot_host
    import cutora.subtitle.font_coverage as fc

    host, _app = boot_host()
    _export_g08.host = host
    _patch_run_path()
    orig_merge = fc.apply_no_font_merging
    orig_split = fc.split_font_runs

    print("=== E1 keep split, drop NoFontMerging ===", flush=True)
    fc.apply_no_font_merging = lambda font: None
    h1 = _export_g08()
    print("E1 hash=%s match=%s seen=%s" % (h1, h1.lower() == EXPECTED, _SEEN), flush=True)

    print("=== E2 keep NoFontMerging, split passthrough ===", flush=True)
    fc.apply_no_font_merging = orig_merge

    def _pass(family, text, script=None):
        return [(text, family)] if text != "" else []

    fc.split_font_runs = _pass
    h2 = _export_g08()
    print("E2 hash=%s match=%s seen=%s" % (h2, h2.lower() == EXPECTED, _SEEN), flush=True)

    fc.split_font_runs = orig_split
    print("EXPECTED %s" % EXPECTED, flush=True)
    print("E1_CULPRIT_IF_MATCH (NoFontMerging is innocent) match=%s" % (h1.lower() == EXPECTED), flush=True)
    print("E2_CULPRIT_IF_MATCH (split_font_runs is innocent) match=%s" % (h2.lower() == EXPECTED), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
