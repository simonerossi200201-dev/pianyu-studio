# -*- coding: utf-8 -*-
"""T04f-6b-1 A: probe sentence-list fill paths. Read-only — no product change."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

G09 = os.path.join(REPO, "tests", "golden", "g09_linebreak_latin", "project.asproj.json")
OUT = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b1_transcript_probe.txt")
PACK = os.path.join(REPO, "resources", "stylepacks", "minimal-white")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f6b1_a_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.transcript import TranscriptPanel
    from cutora.ui.command_bodies import CommandBodies

    log = []
    orig_reload = TranscriptPanel.reload
    orig_refresh = CommandBodies._refresh_task_view
    orig_align = MainWindow.on_align_done

    def _log(msg):
        print(msg, flush=True)
        log.append(msg)

    def wrap_reload(self, *args, **kwargs):
        n = len(self.segments())
        _log("CALL TranscriptPanel.reload segs=%d cards_before=%d" % (
            n, len(self._cards)))
        orig_reload(self, *args, **kwargs)
        _log("  -> cards_after=%d empty_visible=%s" % (
            len(self._cards), self.empty_box.isVisible()))

    def wrap_refresh(self, idx, *args, **kwargs):
        _log("CALL _refresh_task_view idx=%s" % idx)
        return orig_refresh(self, idx, *args, **kwargs)

    def wrap_align(self, segs, is_batch):
        _log("CALL on_align_done segs=%d is_batch=%s" % (len(segs or []), is_batch))
        return orig_align(self, segs, is_batch)

    TranscriptPanel.reload = wrap_reload
    CommandBodies._refresh_task_view = wrap_refresh
    MainWindow.on_align_done = wrap_align

    app = QApplication.instance() or QApplication(["t04f6b1-a"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()

    def pump(ms=80):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    pump(80)
    win.show_editor()
    pump(40)
    panel = win.transcript_panel
    win.transcript_inner.setCurrentWidget(panel)
    _log("=== after show_editor (no project) cards=%d empty=%s ===" % (
        len(panel._cards), panel.empty_box.isVisible()))

    _log("=== open g09 (has 6 segments) ===")
    win.command_project_open(path=G09)
    pump(250)
    segs = len((win.tasks[win.curr_idx].get("segments") or []) if win.tasks else [])
    _log("task.segments=%d cards=%d empty=%s" % (
        segs, len(panel._cards), panel.empty_box.isVisible()))

    class _FakeAlign:
        align_note = None
        approximate_words = False

    win.worker_ai = _FakeAlign()
    _log("=== path 1: on_align_done with current segments (no style click) ===")
    segs_data = list(win.tasks[win.curr_idx].get("segments") or [])
    win.on_align_done(segs_data, False)
    pump(120)
    _log("after on_align_done cards=%d empty=%s" % (
        len(panel._cards), panel.empty_box.isVisible()))

    _log("=== path 2: stylepack.apply (the click-style path) ===")
    win.bus.execute("stylepack.apply", {
        "path": PACK,
        "apply_animation": True,
    }, source="probe")
    pump(120)
    _log("after stylepack.apply cards=%d empty=%s" % (
        len(panel._cards), panel.empty_box.isVisible()))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    _log("wrote " + OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
