#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Q40 unattended beta smoke: welcome → import → ASR fixture → style → timeline → export.

Screenshots and a machine-readable result.json land in
``docs/plan/reports/Q40_smoke/``. Exit 0 only if every step passed.
Does not run a real ASR model: AlignmentWorker.run is replaced with the
frozen g01 segments from ``tests/golden/g01_vertical_basic/project.asproj.json``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

OUT_DIR = os.path.join(REPO, "docs", "plan", "reports", "Q40_smoke")
G01_MP4 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
G01_PROJ = os.path.join(
    REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")

SHOTS = (
    "01_welcome.png",
    "02_new_import.png",
    "03_asr.png",
    "04_style.png",
    "05_timeline.png",
    "06_export.png",
)

LOG = []
RESULT = {
    "ok": False,
    "steps": {},
    "errors": [],
}


def _log(msg: str) -> None:
    line = msg if isinstance(msg, str) else str(msg)
    print(line, flush=True)
    LOG.append(line)


def _head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() or "UNKNOWN"
    except Exception as exc:
        return "UNKNOWN:%s" % exc


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_log() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "smoke.log")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(LOG) + "\n")


def _write_result() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "result.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(RESULT, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _fail(step: str, why: str) -> None:
    RESULT["steps"][step] = {"ok": False, "why": why}
    RESULT["errors"].append("%s: %s" % (step, why))
    _log("FAIL %s %s" % (step, why))
    raise RuntimeError("%s: %s" % (step, why))


def _ok(step: str, **extra) -> None:
    row = {"ok": True}
    row.update(extra)
    RESULT["steps"][step] = row
    _log("OK %s %s" % (step, json.dumps(extra, ensure_ascii=False) if extra else ""))


def _pump(app, ms: int = 40) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()
    app.processEvents()


def _shot(win, name: str, head: str, captured_at: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    pix = win.grab()
    ok = pix.save(path, "PNG")
    _log("SHOT %s %sx%s ok=%s visible=%s" % (
        path, pix.width(), pix.height(), ok, win.isVisible()))
    if not ok or not os.path.isfile(path) or os.path.getsize(path) < 200:
        raise RuntimeError("screenshot failed: %s" % path)
    digest = _sha256(path)
    ev = path[:-4] + "_evidence.txt"
    with open(ev, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("HEAD=%s\n" % head)
        fh.write("captured_at=%s\n" % captured_at)
        fh.write("png=%s\n" % name)
        fh.write("sha256=%s\n" % digest)
        fh.write("width=%s\n" % pix.width())
        fh.write("height=%s\n" % pix.height())
    _log("EVIDENCE %s sha256=%s" % (ev, digest))
    return digest


def _load_frozen_segments() -> list:
    if not os.path.isfile(G01_PROJ):
        raise RuntimeError("missing frozen ASR fixture %s" % G01_PROJ)
    data = json.loads(open(G01_PROJ, encoding="utf-8").read())
    tasks = data.get("tasks") or []
    if not tasks:
        raise RuntimeError("g01 project has no tasks")
    segs = copy.deepcopy(tasks[0].get("segments") or [])
    if not segs:
        raise RuntimeError("g01 project has no frozen segments")
    return segs


def _install_modal_stubs() -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QColorDialog, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox,
    )

    QDialog.exec = lambda *a, **k: 0

    def _no_menu_exec(*a, **k):
        return None

    QMenu.exec = _no_menu_exec
    _qmenu_init = QMenu.__init__

    def _qmenu_init_no_exec(self, *a, **k):
        _qmenu_init(self, *a, **k)
        self.exec = _no_menu_exec

    QMenu.__init__ = _qmenu_init_no_exec
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
    QInputDialog.getText = staticmethod(lambda *a, **k: ("720x1280", True))
    QInputDialog.getInt = staticmethod(lambda *a, **k: (2, False))
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
    QColorDialog.getColor = staticmethod(lambda *a, **k: QColor("#FF00FF"))
    _ok = QMessageBox.StandardButton.Ok
    _yes = QMessageBox.StandardButton.Yes
    QMessageBox.information = staticmethod(lambda *a, **k: _ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: _ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: _ok)
    QMessageBox.question = staticmethod(lambda *a, **k: _yes)
    QMessageBox.about = staticmethod(lambda *a, **k: None)


def _install_asr_fixture(frozen: list) -> None:
    from cutora.asr.align_worker import AlignmentWorker

    def _frozen_run(self):
        try:
            self.align_note = None
            self.approximate_words = False
            self.progress.emit(10)
            self.progress.emit(100)
            self.finished.emit(copy.deepcopy(frozen))
            _log("ASR_FIXTURE emitted %d frozen segments" % len(frozen))
        except Exception as exc:
            _log("ASR_FIXTURE error %s" % exc)
            self.error.emit(str(exc))

    AlignmentWorker.run = _frozen_run
    _log("ASR_FIXTURE patched AlignmentWorker.run n=%d" % len(frozen))


def _modest_target(lo: float, hi: float, cur: float) -> float:
    """Change the value, but stay near the current setting.

    Inspector ranges can be thousands of pixels (pos_x). Jumping to 65% of
    that range throws glyphs off-canvas and the smoke export goes blank.
    """
    span = float(hi) - float(lo)
    if span <= 0:
        return float(cur)
    step = max(abs(float(cur)) * 0.12, span * 0.04, 0.02)
    if step > span * 0.25:
        step = span * 0.25
    target = float(cur) + step
    if target > hi - 1e-9:
        target = float(cur) - step
    if target < lo:
        target = lo + span * 0.1
    if abs(target - float(cur)) < 1e-9:
        target = hi if abs(float(cur) - hi) > 1e-9 else lo
    return target


def _nudge_widget(widget) -> str:
    from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import SliderSpin

    if isinstance(widget, ToggleSwitch):
        before = bool(widget.isChecked())
        widget.setChecked(not before)
        return "ToggleSwitch %s -> %s" % (before, widget.isChecked())
    if isinstance(widget, QCheckBox):
        before = bool(widget.isChecked())
        widget.setChecked(not before)
        return "QCheckBox %s -> %s" % (before, widget.isChecked())
    if isinstance(widget, SliderSpin):
        lo = float(widget.minimum())
        hi = float(widget.maximum())
        cur = float(widget.value())
        target = _modest_target(lo, hi, cur)
        widget.setValue(target)
        return "SliderSpin %s -> %s" % (cur, widget.value())
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        lo = widget.minimum()
        hi = widget.maximum()
        cur = widget.value()
        target = _modest_target(float(lo), float(hi), float(cur))
        if not hasattr(widget, "setDecimals"):
            target = int(round(target))
            if target == int(cur):
                target = int(hi) if int(cur) != int(hi) else int(lo)
        widget.setValue(target)
        return "%s %s -> %s" % (type(widget).__name__, cur, widget.value())
    if isinstance(widget, QComboBox) and widget.count() > 1:
        i = widget.currentIndex()
        nxt = (i + 1) % widget.count()
        widget.setCurrentIndex(nxt)
        return "QComboBox %s -> %s" % (i, nxt)
    return ""


def _change_one_per_section(panel, app) -> list:
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox,
    )
    from cutora.ui.widgets.collapsible import CollapsibleSection, ToggleSwitch
    from cutora.ui.widgets.inspector import SliderSpin

    changed = []
    sections = panel.findChildren(CollapsibleSection)
    if not sections:
        raise RuntimeError("subtitle style panel has no CollapsibleSection")
    for sec in sections:
        title = str(getattr(sec, "section_title", "") or "")
        toggle = getattr(sec, "_toggle_cb", None)
        if toggle is not None and hasattr(toggle, "isChecked") and not toggle.isChecked():
            toggle.setChecked(True)
            app.processEvents()
        sec.set_collapsed(False)
        app.processEvents()
        picked = None
        for kind in (SliderSpin, QDoubleSpinBox, QSpinBox, QComboBox, ToggleSwitch, QCheckBox):
            kids = [
                w for w in sec.findChildren(kind)
                if w is not toggle and w.isVisible()
            ]
            if kids:
                picked = kids[0]
                break
        if picked is None:
            _log("STYLE_SECTION %s no nudgeable widget" % title)
            continue
        how = _nudge_widget(picked)
        if not how:
            _log("STYLE_SECTION %s nudge skipped type=%s" % (title, type(picked).__name__))
            continue
        changed.append({"section": title, "change": how})
        _log("STYLE_SECTION %s %s" % (title, how))
    return changed


def _video_track(task):
    from cutora.model.track_guard import iter_tracks
    for tr in iter_tracks(task):
        if getattr(tr, "type", None) == "video":
            return tr
    tracks = list(iter_tracks(task))
    return tracks[0] if tracks else None


def _clip_count(track) -> int:
    return len(list(getattr(track, "clips", None) or []))


def run() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q40_smoke_")
    path_env = os.environ.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path_env):
        os.environ["PATH"] = FFMPEG_DIR + os.pathsep + path_env

    os.makedirs(OUT_DIR, exist_ok=True)
    head = _head()
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RESULT["head"] = head
    RESULT["captured_at"] = captured_at
    RESULT["user_data"] = os.environ["CUTORA_USER_DATA"]
    _log("HEAD %s" % head)
    _log("CAPTURED_AT %s" % captured_at)
    _log("G01 %s exists=%s" % (G01_MP4, os.path.isfile(G01_MP4)))
    if not os.path.isfile(G01_MP4):
        _fail("import", "missing %s" % G01_MP4)

    frozen = _load_frozen_segments()
    RESULT["asr_fixture_segments"] = len(frozen)
    _log("FROZEN_ASR n=%d from %s" % (len(frozen), G01_PROJ))

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    from cutora.core.log import setup_logging
    from cutora.core.paths import ensure_user_layout
    from cutora.render import export_guard
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from PySide6.QtWidgets import QApplication, QStyleFactory

    ensure_user_layout()
    setup_logging()
    apply_ui_locale()
    export_guard.disable_console_quickedit()
    app = QApplication.instance() or QApplication(["q40-smoke-beta"])
    _log("PLATFORM %s" % app.platformName())
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    _install_modal_stubs()
    _install_asr_fixture(frozen)

    win = MainWindow()
    win._suppress_close_prompt = True
    win.show()
    _pump(app, 80)
    _log("WINDOW visible=%s size=%sx%s stack=%s" % (
        win.isVisible(), win.width(), win.height(),
        win.shell_stack.currentIndex()))
    if not win.isVisible() or win.width() < 200 or win.height() < 200:
        _fail("welcome", "window not really up")

    # ---- 1. welcome: 720×1280 is the primary button ----
    win.show_welcome()
    _pump(app, 80)
    page = win.welcome_page
    from cutora.model.canvas import CANVAS_PRESETS
    first = (int(CANVAS_PRESETS[0][0]), int(CANVAS_PRESETS[0][1]))
    if first != (720, 1280):
        _fail("welcome", "CANVAS_PRESETS[0] is %s not 720x1280" % (first,))
    btn = page.btn_portrait
    if btn.objectName() != "btnPrimary":
        _fail("welcome", "btn_portrait objectName=%s" % btn.objectName())
    mapped = page._size_buttons.get((720, 1280))
    if mapped is not btn:
        _fail("welcome", "720x1280 is not the primary size button")
    if "720" not in (btn.text() or "") or "1280" not in (btn.text() or ""):
        _fail("welcome", "primary button text is %r" % btn.text())
    RESULT["welcome_primary"] = [720, 1280]
    RESULT["welcome_primary_text"] = btn.text()
    _shot(win, "01_welcome.png", head, captured_at)
    _ok("welcome", primary=[720, 1280], text=btn.text())

    # ---- 2. new 720×1280 + import g01 ----
    page.btn_portrait.click()
    _pump(app, 120)
    if win.shell_stack.currentIndex() != 1:
        _fail("import", "primary click did not enter editor (stack=%s)" %
              win.shell_stack.currentIndex())
    tpl = getattr(win, "_timeline_template", None) or {}
    canvas_after_new = [int(tpl.get("width") or 0), int(tpl.get("height") or 0)]
    RESULT["canvas_after_new"] = canvas_after_new
    _log("CANVAS_AFTER_NEW %s" % canvas_after_new)
    if canvas_after_new != [720, 1280]:
        _fail("import", "after 720×1280 click, template is %s" % canvas_after_new)
    from cutora.core.commands import get_bus
    bus = get_bus()
    bus.bind_window(win)
    bus.execute("media.import", {"paths": [G01_MP4]}, source="ui")
    _pump(app, 200)
    if not win.tasks:
        _fail("import", "media.import left tasks empty")
    win.curr_idx = 0
    task = win.tasks[0]
    video = str(task.get("video") or "")
    if os.path.normcase(os.path.abspath(video)) != os.path.normcase(os.path.abspath(G01_MP4)):
        _fail("import", "imported video is %r" % video)
    from cutora.model.canvas import canvas_wh
    wh = list(canvas_wh(task))
    RESULT["canvas_after_import"] = wh
    _log("CANVAS_AFTER_IMPORT %s duration=%s" % (wh, task.get("duration")))
    if wh != [720, 1280]:
        _log("NOTE first drop overwrote 720x1280 canvas to %s "
             "(on_drop._apply_canvas_from_first_source); not patched this task"
             % wh)
    _shot(win, "02_new_import.png", head, captured_at)
    _ok("import", video=video, canvas=wh, canvas_after_new=canvas_after_new,
        tasks=len(win.tasks))

    # ---- 3. auto subtitle via frozen ASR fixture ----
    task["asr_force_language"] = "en"
    task["asr_language"] = "en"
    if not (task.get("text") or "").strip():
        task["text"] = " ".join(
            str(s.get("text") or "") for s in frozen if isinstance(s, dict))
        if hasattr(win, "text_input") and win.text_input is not None:
            win.text_input.setPlainText(task["text"])
    win._ensure_asr_model = lambda: True
    win._run_asr_id = "q40-frozen"
    try:
        bus.execute("subtitle.auto", {"force": True, "media_id": 0, "language": "en"},
                    source="ui")
    except Exception as exc:
        _fail("asr", "subtitle.auto raised: %s" % exc)
    t0 = __import__("time").time()
    segs = []
    while __import__("time").time() - t0 < 20:
        _pump(app, 80)
        segs = list((win.tasks[0] or {}).get("segments") or [])
        if segs:
            break
        worker = getattr(win, "worker_ai", None)
        if worker is not None and worker.isFinished() and not segs:
            break
    if not segs:
        _fail("asr", "frozen ASR produced 0 segments")
    RESULT["asr_segments"] = len(segs)
    _shot(win, "03_asr.png", head, captured_at)
    _ok("asr", segments=len(segs), fixture=True)

    # ---- 4. style pack + one control per inspector section ----
    from cutora.subtitle.stylepack import builtin_dir
    pack_path = os.path.join(builtin_dir(), "glass-white")
    if not os.path.isdir(pack_path):
        _fail("style", "missing pack %s" % pack_path)
    style_before = copy.deepcopy((win.tasks[0].get("style") or {}))
    bus.execute("stylepack.apply", {
        "path": pack_path,
        "apply_animation": False,
    }, source="ui")
    _pump(app, 80)
    style_after = win.tasks[0].get("style") or {}
    if style_after == style_before:
        _fail("style", "stylepack.apply did not change task.style")
    win.dock_right.show()
    win.dock_right.raise_()
    win.tabs.setCurrentWidget(win.sub_panel)
    _pump(app, 80)
    panel = win.sub_panel
    panel.block_signals = False
    changed = _change_one_per_section(panel, app)
    _pump(app, 80)
    if len(changed) < 8:
        _fail("style", "only %d sections changed (need one per Q19–Q26 area)" %
              len(changed))
    RESULT["style_pack"] = "glass-white"
    RESULT["style_sections_changed"] = changed
    _shot(win, "04_style.png", head, captured_at)
    _ok("style", pack="glass-white", sections=len(changed))

    # ---- 5. timeline: split, transition, track props ----
    from cutora.ui.timeline.snap import clip_span
    task = win.tasks[0]
    tr = _video_track(task)
    if tr is None:
        _fail("timeline", "no video track after import")
    n_before = _clip_count(tr)
    dur = float(task.get("duration") or 0.0)
    if dur <= 0.2:
        clips = list(getattr(tr, "clips", None) or [])
        if clips:
            _s, _e = clip_span(clips[0])
            dur = max(dur, _e - _s)
    split_t = max(0.4, min(dur * 0.5, dur - 0.4)) if dur > 0.8 else 0.4
    split_out = bus.execute("timeline.split_at", {"time": float(split_t)}, source="ui")
    _pump(app, 80)
    tr = _video_track(win.tasks[0])
    n_after = _clip_count(tr)
    if n_after < 2 or n_after <= n_before:
        _fail("timeline", "split_at t=%.3f clips %s -> %s out=%s" % (
            split_t, n_before, n_after, split_out))
    clips = sorted(
        list(getattr(tr, "clips", None) or []),
        key=lambda c: float(getattr(c, "start_t", 0.0) or 0.0))
    seam = float(getattr(clips[1], "start_t", split_t) or split_t)
    trans = bus.execute("timeline.add_transition", {
        "track_id": str(tr.id),
        "start_t": float(seam),
        "duration": 0.35,
        "preset": "cross_dissolve",
        "domain": "visual",
    }, source="ui")
    _pump(app, 40)
    bag = list(getattr(tr, "transitions", None) or [])
    if not bag:
        _fail("timeline", "add_transition left transitions empty out=%s" % trans)
    op_before = float((getattr(tr, "props", None) or {}).get("opacity") or 1.0)
    props_out = bus.execute("track.set_props", {
        "track_ids": [str(tr.id)],
        "patch": {"opacity": 0.85},
    }, source="ui")
    _pump(app, 40)
    tr = _video_track(win.tasks[0])
    op_after = float((getattr(tr, "props", None) or {}).get("opacity") or 1.0)
    if abs(op_after - 0.85) > 1e-6:
        _fail("timeline", "track opacity %s -> %s out=%s" % (
            op_before, op_after, props_out))
    RESULT["clips_before_split"] = n_before
    RESULT["clips_after_split"] = n_after
    RESULT["split_t"] = split_t
    RESULT["transition_preset"] = "cross_dissolve"
    RESULT["track_opacity"] = op_after
    _shot(win, "05_timeline.png", head, captured_at)
    _ok("timeline", clips_after=n_after, split_t=split_t, opacity=op_after)

    # ---- 6. export → verify → contact sheet ----
    if hasattr(win, "chk_gpu") and win.chk_gpu is not None:
        win.chk_gpu.setChecked(False)
    export_path = os.path.join(OUT_DIR, "export.mp4")
    if os.path.isfile(export_path):
        os.remove(export_path)
    t_exp = __import__("time").time()
    bus.execute("export.run", {"out": export_path, "confirm": True}, source="api")
    while getattr(win, "is_exporting", False) and __import__("time").time() - t_exp < 480:
        _pump(app, 200)
    elapsed = __import__("time").time() - t_exp
    _log("EXPORT elapsed=%.1fs is_exporting=%s path=%s exists=%s size=%s" % (
        elapsed, getattr(win, "is_exporting", None), export_path,
        os.path.isfile(export_path),
        os.path.getsize(export_path) if os.path.isfile(export_path) else 0))
    if getattr(win, "is_exporting", False):
        _fail("export", "export still running after %.1fs" % elapsed)
    if not os.path.isfile(export_path) or os.path.getsize(export_path) < 10 * 1024:
        _fail("export", "export file missing or tiny")
    from cutora.render.export_verify import (
        contact_sheet_path, verify_export, write_contact_sheet,
    )
    expect_dur = float(win.tasks[0].get("duration") or dur or 0.0)
    ok, why = verify_export(export_path, expected_duration=expect_dur or None)
    _log("VERIFY ok=%s why=%s" % (ok, why))
    RESULT["verify_ok"] = bool(ok)
    RESULT["verify_why"] = list(why or [])
    if not ok:
        _fail("export", "verify_export failed: %s" % "; ".join(why or []))
    sheet = contact_sheet_path(export_path)
    if not os.path.isfile(sheet):
        sheet = write_contact_sheet(export_path, duration=expect_dur) or ""
    if not sheet or not os.path.isfile(sheet):
        _fail("export", "contact sheet missing")
    dest_sheet = os.path.join(OUT_DIR, "contact_sheet.png")
    shutil.copy2(sheet, dest_sheet)
    RESULT["export_path"] = export_path
    RESULT["export_sha256"] = _sha256(export_path)
    RESULT["contact_sheet"] = dest_sheet
    RESULT["contact_sheet_sha256"] = _sha256(dest_sheet)
    _shot(win, "06_export.png", head, captured_at)
    _ok("export", elapsed=round(elapsed, 2), verify=True, sheet=dest_sheet)

    # ---- 7. close, exit 0 ----
    win.close()
    _pump(app, 80)
    app.processEvents()
    _ok("close", visible=bool(win.isVisible()))
    RESULT["ok"] = True
    RESULT["shots"] = list(SHOTS)
    _log("SMOKE_OK")
    return 0


def main() -> int:
    try:
        return run()
    except Exception:
        traceback.print_exc()
        RESULT["ok"] = False
        RESULT["traceback"] = traceback.format_exc()
        _log("SMOKE_FAIL")
        return 1
    finally:
        _write_log()
        _write_result()


if __name__ == "__main__":
    raise SystemExit(main())
