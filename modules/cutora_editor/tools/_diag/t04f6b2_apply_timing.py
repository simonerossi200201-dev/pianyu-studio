# -*- coding: utf-8 -*-
"""T04f-6b2: time 10 stylepack.apply calls on a 220-segment task."""
from __future__ import annotations

import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

G09 = os.path.join(REPO, "tests", "golden", "g09_linebreak_latin", "project.asproj.json")
PACK_DIR = os.path.join(REPO, "resources", "stylepacks")
OUT = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b2_apply_timing.txt")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f6b2_a_")
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
    from cutora.subtitle.stylepack import list_pack_dirs

    app = QApplication.instance() or QApplication(["t04f6b2-a"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()

    def pump(ms=40):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    stats = {"reload": 0, "rebuild": 0, "skip": 0}
    orig_reload = TranscriptPanel.reload
    orig_rebuild = TranscriptPanel._rebuild_cards

    def wrap_reload(self, *args, **kwargs):
        stats["reload"] += 1
        return orig_reload(self, *args, **kwargs)

    def wrap_rebuild(self, segs, caps, focused):
        stats["rebuild"] += 1
        return orig_rebuild(self, segs, caps, focused)

    TranscriptPanel.reload = wrap_reload
    TranscriptPanel._rebuild_cards = wrap_rebuild

    pump(80)
    win.show_editor()
    pump(40)
    win.transcript_inner.setCurrentWidget(win.transcript_panel)
    win.command_project_open(path=G09)
    pump(200)
    fat = []
    for i in range(220):
        fat.append({
            "start": float(i) * 0.4,
            "end": float(i) * 0.4 + 0.35,
            "text": "short line number %d ok" % i,
        })
    win.tasks[win.curr_idx]["segments"] = fat
    t0 = time.perf_counter()
    win._refresh_task_view(win.curr_idx)
    pump(20)
    first_ms = (time.perf_counter() - t0) * 1000.0
    panel = win.transcript_panel
    n0 = len(panel._cards)
    fp0 = panel._loaded_fp
    stats["reload"] = 0
    stats["rebuild"] = 0

    packs = list_pack_dirs()
    rows = []
    for i in range(10):
        path = packs[i % len(packs)]
        stats_before = dict(stats)
        t1 = time.perf_counter()
        win.bus.execute("stylepack.apply", {
            "path": path, "apply_animation": True,
        }, source="probe")
        pump(5)
        ms = (time.perf_counter() - t1) * 1000.0
        segs = win.tasks[win.curr_idx].get("segments") or []
        rows.append({
            "i": i + 1,
            "pack": os.path.basename(path),
            "ms": round(ms, 2),
            "reload": stats["reload"] - stats_before["reload"],
            "rebuild": stats["rebuild"] - stats_before["rebuild"],
            "cards": len(panel._cards),
            "segs": len(segs),
            "fp_same": panel._loaded_fp == fp0,
        })
        fp0 = panel._loaded_fp

    lines = [
        "first_refresh_220_ms=%.1f cards=%d" % (first_ms, n0),
        "i pack ms reload rebuild cards segs fp_same",
    ]
    for r in rows:
        lines.append("%d %s %.2f reload=%d rebuild=%d cards=%d segs=%d fp_same=%s" % (
            r["i"], r["pack"], r["ms"], r["reload"], r["rebuild"],
            r["cards"], r["segs"], r["fp_same"]))
    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text, flush=True)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
