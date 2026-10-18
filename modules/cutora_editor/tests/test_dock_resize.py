# -*- coding: utf-8 -*-
"""T03c r2: dock separator mouse-drag grows the dock AND the timeline canvas.

Must run in a subprocess so init_torch_runtime() precedes PySide6.
The assertion is a real QTest mouse drag on the QMainWindow separator —
not resizeDocks().
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _qt_child(timeout: int = 180) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [VENV_PY, os.path.abspath(__file__), "--run"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out
    return proc.stdout or ""


def test_dock_resize_pro_quick_three_docks():
    _qt_child()


def _pump(app, ms: int = 50) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _mid(a: int, b: int) -> int:
    return (int(a) + int(b)) // 2


def _timeline_sep_points(win, fractions):
    """Points on the horizontal separator between central and dock_timeline."""
    from PySide6.QtCore import QPoint

    c = win.centralWidget()
    d = win.dock_timeline
    c_bl = c.mapTo(win, QPoint(0, c.height()))
    c_br = c.mapTo(win, QPoint(c.width(), c.height()))
    d_tl = d.mapTo(win, QPoint(0, 0))
    d_tr = d.mapTo(win, QPoint(d.width(), 0))
    x0 = max(c_bl.x(), d_tl.x())
    x1 = min(c_br.x(), d_tr.x())
    gap_top = min(c_bl.y(), d_tl.y())
    gap_bot = max(c_bl.y(), d_tl.y())
    # Stay on the central side of the gap so the hit is the separator, not the title strip.
    if gap_bot - gap_top >= 2:
        y = int(gap_top) + 1
    else:
        y = int(d_tl.y()) - 4
    width = max(1, x1 - x0)
    pts = []
    for frac in fractions:
        x = int(x0 + width * frac)
        pts.append(QPoint(x, y))
    return pts, {
        "c_bl": (c_bl.x(), c_bl.y()),
        "d_tl": (d_tl.x(), d_tl.y()),
        "gap": gap_bot - gap_top,
        "y": y,
        "x0": x0,
        "x1": x1,
    }


def _vsep_point(win, dock, side: str, frac: float = 0.5):
    """Point on the vertical separator between a side dock and the central widget."""
    from PySide6.QtCore import QPoint

    c = win.centralWidget()
    if side == "left":
        d_edge = dock.mapTo(win, QPoint(dock.width(), 0))
        c_edge = c.mapTo(win, QPoint(0, 0))
        x = _mid(d_edge.x(), c_edge.x()) if abs(d_edge.x() - c_edge.x()) >= 2 else d_edge.x() + 4
    else:
        c_edge = c.mapTo(win, QPoint(c.width(), 0))
        d_edge = dock.mapTo(win, QPoint(0, 0))
        x = _mid(c_edge.x(), d_edge.x()) if abs(d_edge.x() - c_edge.x()) >= 2 else d_edge.x() - 4
    y0 = max(dock.mapTo(win, QPoint(0, 0)).y(), c.mapTo(win, QPoint(0, 0)).y())
    y1 = min(
        dock.mapTo(win, QPoint(0, dock.height())).y(),
        c.mapTo(win, QPoint(0, c.height())).y(),
    )
    y = int(y0 + max(1, y1 - y0) * frac)
    return QPoint(x, y)


def _drag(win, start, dx: int, dy: int) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    target = win.windowHandle() or win
    if target is win.windowHandle():
        origin = win.mapToGlobal(start)
        to_local = target.mapFromGlobal
        p0 = to_local(origin)
    else:
        p0 = start
        to_local = None
    QTest.mousePress(
        target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0
    )
    QTest.qWait(20)
    steps = 12
    for i in range(1, steps + 1):
        delta = QPoint(int(dx * i / steps), int(dy * i / steps))
        if to_local is not None:
            pt = to_local(win.mapToGlobal(start + delta))
        else:
            pt = start + delta
        QTest.mouseMove(target, pt)
        QTest.qWait(8)
    QTest.mouseRelease(
        target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        pt if to_local is not None else start + QPoint(dx, dy),
    )
    QTest.qWait(30)


def _reset_timeline_h(win, mode: str) -> None:
    win._timeline_h_user_set = False
    win.set_ui_mode(mode, persist=False)


def _run() -> None:
    from cutora.core.runtime import init_torch_runtime

    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    from PySide6.QtWidgets import QApplication
    from cutora.ui.main_window import MainWindow

    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t03c-dock-resize"])
    win = MainWindow()
    win.resize(1600, 1100)
    win.show()
    win.raise_()
    win.activateWindow()
    app.setActiveWindow(win)
    win.show_editor()
    _pump(app, 200)

    failures = []
    for mode in ("pro",):
        for i, frac in enumerate((0.10, 0.50, 0.90)):
            _reset_timeline_h(win, mode)
            _pump(app, 120)
            pts, meta = _timeline_sep_points(win, (frac,))
            start = pts[0]
            dock_h0 = win.dock_timeline.height()
            canvas_h0 = win.timeline_container.timeline.height()
            _drag(win, start, 0, -120)
            _pump(app, 80)
            dock_h1 = win.dock_timeline.height()
            canvas_h1 = win.timeline_container.timeline.height()
            dh = dock_h1 - dock_h0
            ch = canvas_h1 - canvas_h0
            tag = "timeline %s x=%.0f%%" % (mode, (10, 50, 90)[i])
            if dh < 100 or ch < 100:
                child = win.childAt(start)
                failures.append(
                    "%s dock %d→%d (Δ%d) canvas %d→%d (Δ%d) start=%s meta=%s child=%s"
                    % (
                        tag,
                        dock_h0,
                        dock_h1,
                        dh,
                        canvas_h0,
                        canvas_h1,
                        ch,
                        (start.x(), start.y()),
                        meta,
                        None if child is None else type(child).__name__,
                    )
                )

        # Left dock: drag separator right to grow.
        _reset_timeline_h(win, mode)
        _pump(app, 80)
        start = _vsep_point(win, win.dock_left, "left", 0.5)
        w0 = win.dock_left.width()
        _drag(win, start, 120, 0)
        _pump(app, 80)
        w1 = win.dock_left.width()
        if w1 - w0 < 100:
            child = win.childAt(start)
            failures.append(
                "left %s %d→%d (Δ%d) start=%s child=%s"
                % (
                    mode,
                    w0,
                    w1,
                    w1 - w0,
                    (start.x(), start.y()),
                    None if child is None else type(child).__name__,
                )
            )

        # Right dock: drag separator left to grow. Cap is 420 (QW-02).
        from PySide6.QtCore import Qt as _Qt
        win.resizeDocks([win.dock_right], [280], _Qt.Orientation.Horizontal)
        _pump(app, 80)
        start = _vsep_point(win, win.dock_right, "right", 0.5)
        w0 = win.dock_right.width()
        _drag(win, start, -120, 0)
        _pump(app, 80)
        w1 = win.dock_right.width()
        if w1 - w0 < 100:
            child = win.childAt(start)
            failures.append(
                "right %s %d→%d (Δ%d) start=%s child=%s"
                % (
                    mode,
                    w0,
                    w1,
                    w1 - w0,
                    (start.x(), start.y()),
                    None if child is None else type(child).__name__,
                )
            )

    win.close()
    if failures:
        print("FAIL")
        for line in failures:
            print(line)
        raise SystemExit(1)
    print("ok")


if __name__ == "__main__":
    if "--run" in sys.argv:
        _run()
    else:
        raise SystemExit("usage: test_dock_resize.py --run")
