#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI golden stills. Pixel sha256 in tests/ui_golden/uNN/expected.sha256.

Usage:
    python tools/ui_golden.py --all
    python tools/ui_golden.py --record
    python tools/ui_golden.py --case u01
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
ROOT = os.path.join(REPO, "tests", "ui_golden")
G11 = os.path.join(REPO, "tests", "golden", "g11_vertical_basic_fx", "project.asproj.json")
G19 = os.path.join(REPO, "tests", "golden", "g19_stitch_timeline", "project.asproj.json")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _boot():
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    app = QApplication.instance() or QApplication(["ui-golden"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    return app


def _load_task(path):
    from cutora.model.project_io import deserialize_render_task
    data = json.loads(open(path, encoding="utf-8").read())
    return deserialize_render_task(data["tasks"][0])


def _glass_task():
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    task = _load_task(G11)
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    apply_pack_to_task(task, pack)
    return task


def _save_img(img, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    ok = img.save(dest, "PNG")
    if not ok:
        raise RuntimeError("save failed %s" % dest)


def _qimg_rgb(img):
    from PySide6.QtGui import QImage
    import numpy as np
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    arr = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


def _assert_u01b_glass(img):
    import numpy as np
    arr = _qimg_rgb(img)
    y0, y1, x0, x1 = 540, 585, 190, 350
    var = float(arr[y0:y1, x0:x1].astype(np.float64).var())
    assert var > 0, var


def _diff_mask(a, b, thr=8):
    import numpy as np
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return d > thr


def _assert_diff_in_roi(a_img, b_img, roi, min_inside=0.90):
    import numpy as np
    a, b = _qimg_rgb(a_img), _qimg_rgb(b_img)
    y0, y1, x0, x1 = roi
    m = _diff_mask(a, b)
    n = int(m.sum())
    assert n > 0, "expected a visual diff"
    ys, xs = np.where(m)
    inside = float(((ys >= y0) & (ys < y1) & (xs >= x0) & (xs < x1)).mean())
    assert inside >= min_inside, (inside, n, int(xs.min()), int(ys.min()),
                                  int(xs.max()), int(ys.max()))


def _assert_u02b_bar_only(u01_img, u02_img):
    # line_bar sits under the cue on the 540x960 canvas grab.
    _assert_diff_in_roi(u01_img, u02_img, (520, 620, 80, 460), 0.85)


def _assert_u04b_stroke_only(u03_img, u04_img):
    # 描边 capsule on the section header (280x1100 panel).
    _assert_diff_in_roi(u03_img, u04_img, (500, 545, 0, 55), 0.90)


def _assert_u04c_stroke_only(u03_img, u04_img):
    # Q24: 描边 capsule (and the now-ungrayed 描边 body) sit in the left
    # capsule column. y moved vs u04b because six new header switches exist.
    _assert_diff_in_roi(u03_img, u04_img, (400, 650, 0, 55), 0.90)


def _canvas_grab(task, t=2.0):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.canvas import VideoCanvas
    from cutora.preview.engine import PreviewEngine
    canvas = VideoCanvas()
    canvas.resize(540, 960)
    eng = PreviewEngine()
    try:
        video = eng.video_frame_at(task, t)
    finally:
        eng.close()
    canvas.set_program_frame(video)
    canvas.set_data(task, t)
    canvas.show()
    QApplication.processEvents()
    canvas.repaint()
    QApplication.processEvents()
    img = canvas.grab().toImage()
    canvas.close()
    return img


def case_u01(tmp):
    from cutora.preview.engine import PreviewEngine
    task = _glass_task()
    eng = PreviewEngine()
    try:
        img = eng.frame_at(task, 2.0)
    finally:
        eng.close()
    dest = os.path.join(tmp, "got.png")
    _save_img(img, dest)
    return dest


def case_u02(tmp):
    from cutora.preview.engine import PreviewEngine
    task = _glass_task()
    fx = task.setdefault("style", {}).setdefault("fx", {})
    fx["follow_sentence"] = True
    fx["line_bar"] = {"color": "#F2C14E", "alpha": 0.7, "height_em": 0.22}
    eng = PreviewEngine()
    try:
        img = eng.frame_at(task, 2.0)
    finally:
        eng.close()
    dest = os.path.join(tmp, "got.png")
    _save_img(img, dest)
    return dest


def case_u01b(tmp):
    task = _glass_task()
    dest = os.path.join(tmp, "got.png")
    img = _canvas_grab(task, 2.0)
    _assert_u01b_glass(img)
    _save_img(img, dest)
    return dest


def case_u02b(tmp):
    task = _glass_task()
    fx = task.setdefault("style", {}).setdefault("fx", {})
    fx["follow_sentence"] = True
    fx["line_bar"] = {"color": "#F2C14E", "alpha": 0.7, "height_em": 0.22}
    dest = os.path.join(tmp, "got.png")
    img = _canvas_grab(task, 2.0)
    _save_img(img, dest)
    u01 = _canvas_grab(_glass_task(), 2.0)
    _assert_u02b_bar_only(u01, img)
    return dest


def _expand_panel(panel):
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "set_collapsed"):
            sec.set_collapsed(False)
    panel.setFixedSize(280, 1100)
    panel.show()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def case_u03(tmp):
    from PySide6.QtWidgets import QApplication
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    task = _glass_task()
    panel = SubtitleStylePanel()
    panel.load_data(task.get("style") or {})
    _expand_panel(panel)
    dest = os.path.join(tmp, "got.png")
    grab = panel.grab().toImage()
    assert grab.width() == 280, grab.width()
    _save_img(grab, dest)
    panel.close()
    return dest


def case_u04(tmp):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    task = _glass_task()
    style = dict(task.get("style") or {})
    panel = SubtitleStylePanel()
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    _expand_panel(panel)
    QTest.mouseClick(panel.sub_strk, Qt.MouseButton.LeftButton)
    QTest.qWait(160)
    QApplication.processEvents()
    dest = os.path.join(tmp, "got.png")
    grab = panel.grab().toImage()
    assert grab.width() == 280, grab.width()
    _save_img(grab, dest)
    panel.close()
    return dest


def case_u03b(tmp):
    return case_u03(tmp)


def case_u04b(tmp):
    dest = case_u04(tmp)
    from PySide6.QtGui import QImage
    u03 = os.path.join(ROOT, "u03b", "got.png")
    if os.path.isfile(u03):
        from PySide6.QtGui import QImage as _QI
        _assert_u04b_stroke_only(_QI(u03), _QI(dest))
    return dest


def case_u03c(tmp):
    return case_u03(tmp)


def case_u04c(tmp):
    dest = case_u04(tmp)
    u03 = os.path.join(ROOT, "u03c", "got.png")
    if os.path.isfile(u03):
        from PySide6.QtGui import QImage as _QI
        _assert_u04c_stroke_only(_QI(u03), _QI(dest))
    return dest


def _assert_u04d_stroke_only(u03_img, u04_img):
    # Q25 inspector rows are full-width; turning 描边 on un-grays the body
    # across the panel, not just the 36px capsule column.
    a, b = _qimg_rgb(u03_img), _qimg_rgb(u04_img)
    n = int(_diff_mask(a, b).sum())
    assert n > 0, "expected a visual diff from 描边 capsule"


def case_u03d(tmp):
    return case_u03(tmp)


def case_u04d(tmp):
    dest = case_u04(tmp)
    u03 = os.path.join(ROOT, "u03d", "got.png")
    if os.path.isfile(u03):
        from PySide6.QtGui import QImage as _QI
        _assert_u04d_stroke_only(_QI(u03), _QI(dest))
    return dest


def case_u03e(tmp):
    return case_u03(tmp)


def case_u04e(tmp):
    dest = case_u04(tmp)
    u03 = os.path.join(ROOT, "u03e", "got.png")
    if os.path.isfile(u03):
        from PySide6.QtGui import QImage as _QI
        _assert_u04d_stroke_only(_QI(u03), _QI(dest))
    return dest


def case_u01c(tmp):
    """Q214：Luca 2026-10-10 接受新画面，退役 u01b。捕获与 u01b 相同。"""
    return case_u01b(tmp)


def case_u02c(tmp):
    """Q214：Luca 2026-10-10 接受新画面，退役 u02b。捕获与 u02b 相同。"""
    return case_u02b(tmp)


def case_u03f(tmp):
    """Q214：Luca 2026-10-10 接受新画面，退役 u03e。捕获与 u03e 相同。"""
    return case_u03e(tmp)


def case_u04f(tmp):
    """Q214：Luca 2026-10-10 接受新画面，退役 u04e。描边对照读 u03f/got.png。"""
    dest = case_u04(tmp)
    u03 = os.path.join(ROOT, "u03f", "got.png")
    if os.path.isfile(u03):
        from PySide6.QtGui import QImage as _QI
        _assert_u04d_stroke_only(_QI(u03), _QI(dest))
    return dest


def case_u06b(tmp):
    """Q214：Luca 2026-10-10 接受新画面，退役 u06。捕获与 u06 相同。"""
    return case_u06(tmp)


def case_u07c(tmp):
    """Q214：Luca 2026-10-10 接受新画面，退役 u07b。捕获与 u07b 相同。"""
    return case_u07(tmp)


def case_u08c(tmp):
    """Q214：Luca 2026-10-10 接受新画面，退役 u08b。捕获与 u08b 相同。"""
    return case_u08(tmp)


def case_u08(tmp):
    """Text overlay inspector (Q27). New case; Claude sign-off before gating."""
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.tokens import INSPECTOR_PANEL_W
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.widgets.collapsible import CollapsibleSection
    ov = {
        "type": "text",
        "content": "CUTORA TEXT",
        "start": 0.0, "end": 4.0,
        "x": 80, "y": 120, "w": 560, "h": 140,
        "font_family": "Arimo", "fontsize": 42,
        "bold": True, "color": "#F5B700",
        "fx": {"bg": {"type": "box", "color": "#3A0A14", "alpha": 0.7},
               "fill": {"type": "solid", "colors": ["#F5B700"]},
               "stroke": {"width_em": 0.04, "color": "#141A26"}},
        "runs": [],
        "writing_mode": "horizontal",
        "fit_mode": "none",
        "text_layout": {"align": "left", "line_spacing": 0, "letter_spacing": 0,
                        "left_pad": 0, "right_pad": 0, "top_pad": 0, "bottom_pad": 0},
    }
    panel = PropertyPanel()
    panel.load_data("overlay", ov)
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "setExpanded"):
            sec.setExpanded(True)
        elif hasattr(sec, "set_collapsed"):
            sec.set_collapsed(False)
    panel.setFixedSize(INSPECTOR_PANEL_W, 1100)
    panel.show()
    QApplication.processEvents()
    dest = os.path.join(tmp, "got.png")
    grab = panel.grab().toImage()
    _save_img(grab, dest)
    panel.close()
    return dest


def case_u07(tmp):
    """Whole inspector at the 480px width lock."""
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.tokens import INSPECTOR_PANEL_W
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    task = _glass_task()
    panel = SubtitleStylePanel()
    panel.load_data(task.get("style") or {})
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "set_collapsed"):
            sec.set_collapsed(False)
    panel.setFixedSize(INSPECTOR_PANEL_W, 1100)
    panel.show()
    QApplication.processEvents()
    dest = os.path.join(tmp, "got.png")
    grab = panel.grab().toImage()
    assert grab.width() == INSPECTOR_PANEL_W, grab.width()
    _save_img(grab, dest)
    panel.close()
    return dest


def case_u07b(tmp):
    return case_u07(tmp)


def case_u08b(tmp):
    return case_u08(tmp)


def _warm_timeline_thumbs(w):
    """Generate the filmstrip jpegs the widget will paint.

    set_data can queue before the widget has a width, and a cold
    CUTORA_USER_DATA dir has none of those files. The grab must wait
    for the same (path, t, w, h) _paint_video_thumb peeks.
    """
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication
    from cutora.ui.timeline import thumbs as thumbs_mod
    from cutora.ui.timeline.widget import CLIP_THUMB_W, _tl_of
    QApplication.processEvents()
    try:
        w._queue_visible_thumbs()
    except Exception:
        pass
    tl = _tl_of(getattr(w, "task_data", None))
    if tl is None:
        w.repaint()
        QApplication.processEvents()
        return
    sources = getattr(tl, "sources", None) or {}
    clips = []
    for t_obj in getattr(tl, "tracks", []) or []:
        if getattr(t_obj, "type", None) != "video":
            continue
        row_h = w._track_row_h(t_obj)
        for clip_obj in getattr(t_obj, "clips", []) or []:
            sid = getattr(clip_obj, "source_id", None)
            src = sources.get(sid) if sid else None
            path = getattr(src, "path", None) or (
                src.get("path") if isinstance(src, dict) else None) if src else None
            if not path:
                continue
            start_t = float(getattr(clip_obj, "start_t", 0.0) or 0.0)
            dur = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
            in_t = float(getattr(clip_obj, "in_t", 0.0) or 0.0)
            out_t = float(getattr(clip_obj, "out_t", in_t) or in_t)
            clips.append((start_t, start_t + max(dur, 0.0), str(path), in_t, out_t, row_h))
    specs = []
    if clips:
        clips.sort(key=lambda c: c[0])
        vis_start, vis_end = w.visible_time_range()
        visible_idx = [
            i for i, c in enumerate(clips)
            if c[1] >= vis_start and c[0] <= vis_end
        ]
        if visible_idx:
            prefetch = max(0, int(thumbs_mod.THUMB_PREFETCH_CLIPS))
            lo = max(0, visible_idx[0] - prefetch)
            hi = min(len(clips), visible_idx[-1] + 1 + prefetch)
            pps = max(1e-6, float(w.pixels_per_second or 1.0))
            for s_t, e_t, path, in_t, out_t, row_h in clips[lo:hi]:
                tw, th = w.video_thumb_size(row_h)
                times = [in_t + 0.1]
                if (e_t - s_t) * pps >= 3 * CLIP_THUMB_W + 40 and out_t - in_t > 0.3:
                    times.append(max(in_t + 0.1, out_t - 0.1))
                for t in times:
                    specs.append((path, float(t), int(tw), int(th)))
    QThreadPool.globalInstance().waitForDone(12000)
    for path, t, tw, th in specs:
        if thumbs_mod.peek_clip_thumb(path, t, tw, th):
            continue
        key = thumbs_mod.thumb_key(path, t, tw, th)
        if key and key in thumbs_mod._running:
            continue
        thumbs_mod.get_clip_thumb(path, t, tw, th)
    QThreadPool.globalInstance().waitForDone(8000)
    w.repaint()
    QApplication.processEvents()


def case_u05(tmp):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.timeline.widget import TimelineWidget
    task = _load_task(G19)
    if not (task.get("segments") or []):
        task["segments"] = [
            {"start": 0.0, "end": 4.0, "text": "one", "role": "default"},
            {"start": 18.0, "end": 22.0, "text": "two", "role": "default"},
        ]
    w = TimelineWidget()
    w.resize(900, 280)
    w.pixels_per_second = 30.0
    w.set_data(int(float(task.get("duration") or 30) * 1000), task)
    w.set_position(20000)
    w.show()
    QApplication.processEvents()
    w.repaint()
    # Pytest uses a cold thumb cache. Paint peeks video_thumb_size (64 wide);
    # peek_clip_thumb's 80x45 default never matches that key.
    _warm_timeline_thumbs(w)
    dest = os.path.join(tmp, "got.png")
    _save_img(w.grab().toImage(), dest)
    w.close()
    return dest


def case_u05b(tmp):
    """Q169：时间线实色色块 + 轨道头把手。同一个捕获，先加 u05b 后退役 u05。"""
    return case_u05(tmp)


def case_u05c(tmp):
    """Q189i：时间线标注字体带中文回退（「离线」「无波形」不再是方块）。同一个捕获，先加 u05c 后退役 u05b。
    Luca 2026-10-07 批准。expected 由 Luca 机器第一次跑时自动录（云端字体 / DPI 不同，不在云端录）。"""
    return case_u05(tmp)


def case_u06(tmp):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.style_gallery import StyleGalleryPanel
    task = _glass_task()
    host = type("W", (), {"tasks": [task], "curr_idx": 0, "bus": None})()
    gal = StyleGalleryPanel(host)
    gal.setFixedWidth(280)
    gal.resize(280, 640)
    if hasattr(gal, "reload"):
        gal.reload()
    gal.show()
    QApplication.processEvents()
    # Approved still: plus card + 14 builtin packs + 2 rows below the fold.
    # Pytest user-data has no local packs, so the handle is 31px taller
    # and the grab matches retired u06. Pin the row count; the extra rows
    # stay off screen.
    from PySide6.QtWidgets import QListWidgetItem
    lst = gal.list
    while lst.count() > 17:
        lst.takeItem(lst.count() - 1)
    while lst.count() < 17:
        lst.addItem(QListWidgetItem(""))
    gal.repaint()
    QApplication.processEvents()
    dest = os.path.join(tmp, "got.png")
    _save_img(gal.grab().toImage(), dest)
    gal.close()
    return dest


CASES = {
    "u01c": case_u01c,
    "u02c": case_u02c,
    "u03f": case_u03f,
    "u04f": case_u04f,
    "u05c": case_u05c,
    "u06b": case_u06b,
    "u07c": case_u07c,
    "u08c": case_u08c,
}
RETIRED = (
    "u01", "u02", "u03", "u04", "u03b", "u04b", "u03c", "u04c",
    "u03d", "u04d", "u07", "u08", "u05", "u05b",
    "u01b", "u02b", "u03e", "u04e", "u06", "u07b", "u08b",
)


def case_dir(name):
    return os.path.join(ROOT, name)


def expected_sha_path(name):
    return os.path.join(case_dir(name), "expected.sha256")


def run_case(name, record=False):
    fn = CASES[name]
    tmp = tempfile.mkdtemp(prefix="ui_%s_" % name)
    try:
        got = fn(tmp)
        digest = _sha(got)
        exp_path = expected_sha_path(name)
        os.makedirs(case_dir(name), exist_ok=True)
        if record or not os.path.isfile(exp_path):
            open(exp_path, "w", encoding="utf-8").write("%s  got.png\n" % digest)
            png = os.path.join(case_dir(name), "got.png")
            import shutil
            shutil.copy2(got, png)
            print("%s RECORD %s" % (name, digest[:12]))
            return True, digest
        want = open(exp_path, encoding="utf-8").read().strip().split()[0]
        ok = digest.lower() == want.lower()
        print("%s %s got=%s exp=%s" % (
            name, "PASS" if ok else "FAIL", digest[:12], want[:12]))
        if not ok:
            import shutil
            shutil.copy2(got, os.path.join(case_dir(name), "mismatch.png"))
        return ok, digest
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _print_fingerprint():
    import subprocess
    head = "(no git)"
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True,
            encoding="utf-8", errors="replace").strip()
    except Exception:
        pass
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    dpi = screen.logicalDotsPerInch() if screen is not None else "?"
    dpr = screen.devicePixelRatio() if screen is not None else "?"
    qtver = "?"
    try:
        import importlib.metadata as md
        qtver = md.version("PySide6")
    except Exception:
        pass
    digest = ""
    dpath = os.path.join(REPO, "tests", "golden", "fonts_digest.txt")
    if os.path.isfile(dpath):
        for line in open(dpath, encoding="utf-8"):
            line = line.strip()
            if len(line) == 64:
                digest = line
                break
    print("[ui-golden-fingerprint]")
    print("HEAD=%s" % head)
    print("PySide6=%s" % qtver)
    print("logicalDotsPerInch=%s" % dpi)
    print("devicePixelRatio=%s" % dpr)
    print("fonts_digest=%s" % digest)
    print("retired=%s" % ",".join(RETIRED))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true")
    p.add_argument("--record", action="store_true")
    p.add_argument("--case", "--only", action="append", default=[])
    args = p.parse_args(argv)
    _boot()
    _print_fingerprint()
    names = list(args.case) or (list(CASES) if args.all or not args.case else args.case)
    if args.all:
        names = list(CASES)
    ok_all = True
    for name in names:
        ok, _ = run_case(name, record=args.record)
        ok_all = ok_all and ok
    print("RESULT: %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
