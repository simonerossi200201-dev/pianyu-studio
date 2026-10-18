# -*- coding: utf-8 -*-
"""BUG-08: every inspector / dock page stays inside the viewport at any width.

The asked width MUST actually land: pin dock min=max, then assert
abs(viewport.width() - asked) <= 2. Never pass a silent 182px as 200/240/280.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
SHOT_DIR = os.path.join(REPO, "docs", "plan", "reports", "bug08_dock")
FIXTURE = os.path.join(REPO, "tests", "fixtures", "bug08_task.asproj.json")
G12 = os.path.join(REPO, "tests", "golden", "g12_vertical_fx_path", "project.asproj.json")
WIDTHS = (200, 240, 280, 360, 480, 640)
SHOT_WIDTHS = (200, 280, 480)
WIDTH_TOL = 2


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_bug08_"))
    env.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
    return env


def test_dock_pages_never_overflow():
    if os.environ.get("CUTORA_PYTEST_ISOLATED") == "1":
        assert run_suite() == 0
        return
    proc = subprocess.run(
        [VENV_PY, os.path.join(REPO, "tests", "test_dock_pages_never_overflow.py")],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "BUG08_OK" in (proc.stdout or ""), out


def _page_scrolls(page):
    from PySide6.QtWidgets import QScrollArea, QStackedWidget
    if page is None:
        return []
    if isinstance(page, QScrollArea):
        return [page]
    out = []
    for s in page.findChildren(QScrollArea):
        if not s.isVisible():
            continue
        skip = False
        w = s
        while w is not None:
            p = w.parentWidget()
            if isinstance(p, QStackedWidget):
                cur = p.currentWidget()
                if cur is None or (cur is not w and not cur.isAncestorOf(s)):
                    skip = True
                break
            w = p
        if not skip:
            out.append(s)
    return out


def _measure_vp(page):
    scrolls = _page_scrolls(page)
    if scrolls:
        return int(scrolls[0].viewport().width())
    return int(page.width()) if page is not None else 0


def _pin_dock(dock, width):
    old = (int(dock.minimumWidth()), int(dock.maximumWidth()))
    dock.setMinimumWidth(width)
    dock.setMaximumWidth(width)
    return old


def _unpin_dock(dock, old):
    dock.setMinimumWidth(old[0])
    dock.setMaximumWidth(old[1] if old[1] > 0 else 16777215)


def _force_vp(win, dock, page, target, app, qt):
    """Pin dock so the page viewport equals target ±2. Extra chrome is added to pin."""
    extra = 0
    last = 0
    for _ in range(12):
        pin = max(target, target + extra)
        _pin_dock(dock, pin)
        win.resize(max(1100, pin + 800), 920)
        app.processEvents()
        last = _measure_vp(page)
        if abs(last - target) <= WIDTH_TOL:
            return last, pin
        extra += (target - last)
        if extra > 120:
            break
    return last, target + extra


def _check_bounds(root, vp_widget, label, asked, require_width=True):
    from PySide6.QtWidgets import QWidget, QScrollBar, QScrollArea
    failures = []
    vp_w = int(vp_widget.width())
    if require_width and abs(vp_w - asked) > WIDTH_TOL:
        failures.append("%s asked %d got vp %d" % (label, asked, vp_w))
    hbars = root.findChildren(QScrollBar) if hasattr(root, "findChildren") else []
    for hbar in hbars:
        if hbar.orientation() != __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Orientation.Horizontal:
            continue
        if hbar.isVisible() and hbar.maximum() > 0:
            failures.append("%s h-scrollbar visible max=%s" % (label, hbar.maximum()))
    inner = root
    if isinstance(root, QScrollArea):
        inner = root.widget() or root
        sh = inner.sizeHint()
        if sh.width() > vp_w + 1:
            failures.append("%s sizeHint %s > viewport %s" % (label, sh.width(), vp_w))
    for w in inner.findChildren(QWidget):
        if not w.isVisible() or w.width() <= 0:
            continue
        if isinstance(w, QScrollBar):
            continue
        top_right = w.mapTo(vp_widget, w.rect().topRight())
        if top_right.x() > vp_w + 1:
            par = w.parentWidget()
            failures.append(
                "%s %s right=%s vp=%s geo=%s sh=%s par=%s"
                % (label, w.objectName() or type(w).__name__,
                   top_right.x(), vp_w, tuple(w.geometry().getRect()),
                   (w.sizeHint().width(), w.sizeHint().height()),
                   (par.objectName() if par else "") or type(par).__name__)
            )
    return failures


def _check_page(page, asked, label):
    from PySide6.QtWidgets import QScrollArea
    failures = []
    scrolls = _page_scrolls(page)
    if not scrolls:
        failures.extend(_check_bounds(page, page, label + "#page", asked, True))
        return failures
    failures.extend(
        _check_bounds(scrolls[0], scrolls[0].viewport(), label + "#s0", asked, True)
    )
    for i, scroll in enumerate(scrolls[1:], 1):
        failures.extend(
            _check_bounds(
                scroll, scroll.viewport(), "%s#s%d" % (label, i), asked, False)
        )
    return failures


def _load_task(win, app):
    import json
    src = FIXTURE if os.path.isfile(FIXTURE) else G12
    data = json.loads(open(src, encoding="utf-8").read())
    tasks = data.get("tasks") or []
    task = tasks[0] if tasks else {}
    win.tasks = tasks
    win.curr_idx = 0
    ovs = task.get("text_overlays") or []
    if ovs and hasattr(win, "prop_panel"):
        win.prop_panel.load_data("overlay", ovs[0])
        if hasattr(win, "tabs"):
            for i in range(win.tabs.count()):
                if win.tabs.tabText(i) == "属性":
                    win.tabs.setCurrentIndex(i)
                    break
    segs = task.get("segments") or []
    style = dict(segs[0]) if segs else (task.get("default_style") or {})
    if hasattr(win, "sub_panel") and hasattr(win.sub_panel, "load_data"):
        win.sub_panel.load_data(style)
    app.processEvents()
    n_ov = len(ovs)
    n_seg = len(segs)
    print("BUG08_TASK overlays=%d segments=%d src=%s" % (n_ov, n_seg, src), flush=True)
    if n_ov < 1 or n_seg < 1:
        raise RuntimeError("BUG-08 fixture has no overlay/segments")


def run_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory, QTabWidget
    from PySide6.QtCore import Qt
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["cutora-bug08"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    print("BUG08_MW_START", flush=True)
    win = MainWindow()
    print("BUG08_MW_OK", flush=True)
    sp = getattr(win, "sticker_panel", None)
    if sp is not None:
        sp._want_populate = False
        tmr = getattr(sp, "_icon_timer", None)
        if tmr is not None:
            tmr.stop()
    win.show()
    win.show_editor()
    print("BUG08_SHOWN", flush=True)
    app.processEvents()
    t = getattr(win, "_autosave_timer", None)
    if t is not None:
        t.stop()
    _load_task(win, app)
    os.makedirs(SHOT_DIR, exist_ok=True)

    pages = []
    if hasattr(win, "tabs") and isinstance(win.tabs, QTabWidget):
        for i in range(win.tabs.count()):
            pages.append((
                win.tabs.tabText(i) or "tab%d" % i,
                win.tabs, i, win.tabs.widget(i), "right",
            ))
    if hasattr(win, "left_tabs") and isinstance(win.left_tabs, QTabWidget):
        for i in range(win.left_tabs.count()):
            if not win.left_tabs.isTabVisible(i):
                continue
            pages.append((
                "left-" + (win.left_tabs.tabText(i) or "tab%d" % i),
                win.left_tabs, i, win.left_tabs.widget(i), "left",
            ))

    failures = []
    width_rows = []
    saved_minmax = {
        "right": (win.dock_right.minimumWidth(), win.dock_right.maximumWidth()),
        "left": (win.dock_left.minimumWidth(), win.dock_left.maximumWidth()),
    }

    for width in WIDTHS:
        for name, tabs, idx, page, side in pages:
            if page is None:
                continue
            dock = win.dock_right if side == "right" else win.dock_left
            tabs.setCurrentIndex(idx)
            app.processEvents()
            vp, pin = _force_vp(win, dock, page, width, app, Qt)
            label = "%s@%d" % (name, width)
            width_rows.append((name, width, int(dock.width()), vp, pin))
            print("WIDTH %s asked=%d dock=%d vp=%d pin=%d" % (
                name, width, int(dock.width()), vp, pin), flush=True)
            failures.extend(_check_page(page, width, label))
            if width in SHOT_WIDTHS:
                safe = "".join(c if c.isalnum() else "_" for c in name)
                path = os.path.join(SHOT_DIR, "%s_%d.png" % (safe, width))
                pix = page.grab()
                pix.save(path)
                print("SHOT %s %dx%d" % (path, pix.width(), pix.height()), flush=True)
            _unpin_dock(dock, saved_minmax[side])
            app.processEvents()

    # Simulated hand-drag 640 → 180 on the right dock.
    drag_note = "DRAG skip"
    try:
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QPoint
        win.resize(1400, 920)
        win.dock_right.setMinimumWidth(180)
        win.dock_right.setMaximumWidth(16777215)
        win.resizeDocks([win.dock_right], [640], Qt.Orientation.Horizontal)
        app.processEvents()
        w0 = int(win.dock_right.width())
        win.resizeDocks([win.dock_right], [180], Qt.Orientation.Horizontal)
        app.processEvents()
        w1 = int(win.dock_right.width())
        hbar_hit = False
        from PySide6.QtWidgets import QScrollBar
        for sa_page in (win.tabs.widget(i) for i in range(win.tabs.count())):
            if sa_page is None:
                continue
            win.tabs.setCurrentWidget(sa_page)
            app.processEvents()
            for hbar in sa_page.findChildren(QScrollBar):
                if (hbar.orientation() == Qt.Orientation.Horizontal
                        and hbar.isVisible() and hbar.maximum() > 0):
                    hbar_hit = True
        drag_note = "DRAG 640→180 dock %d→%d hbar=%s" % (w0, w1, hbar_hit)
        print(drag_note, flush=True)
        if hbar_hit:
            failures.append("hand-drag 640→180 showed a horizontal scrollbar")
    except Exception as exc:
        drag_note = "DRAG error %s" % exc
        print(drag_note, flush=True)

    print("WIDTH_TABLE page asked dock vp pin", flush=True)
    for name, asked, dock_w, vp, pin in width_rows:
        print("WIDTH_TABLE %s %d %d %d %d" % (name, asked, dock_w, vp, pin), flush=True)

    if failures:
        print("BUG08_FAIL")
        for line in failures[:60]:
            print(line)
        return 1
    print("BUG08_OK pages=%d drag=%s" % (len(pages), drag_note))
    try:
        win.close()
    except Exception:
        pass
    if os.environ.get("CUTORA_PYTEST_ISOLATED") == "1":
        return 0
    os._exit(0)


if __name__ == "__main__":
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    raise SystemExit(run_suite())
