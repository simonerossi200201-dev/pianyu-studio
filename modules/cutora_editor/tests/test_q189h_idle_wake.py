# SPDX-License-Identifier: GPL-3.0-or-later
"""Q189h：空闲停帧（Q189g）会被编辑 / 输入叫醒，最多停几秒兜底刷新；退出时看门狗不报「卡住」。"""
from __future__ import annotations

import os
import sys
import threading
import time

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q189h"])


def test_idle_hold_woken_by_input_and_expires(monkeypatch):
    app = _qapp()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QWidget
    from cutora.ui import idle_wake as IW

    win = QWidget()
    win._sync_idle_task = object()
    f = IW.install(win, app)
    tgt = QWidget()

    def mouse(kind, buttons):
        return QMouseEvent(kind, QPointF(1, 1), QPointF(1, 1), Qt.MouseButton.NoButton
                           if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton,
                           buttons, Qt.KeyboardModifier.NoModifier)
    # 光标晃来晃去（没按键）不叫醒
    f.eventFilter(tgt, mouse(QEvent.Type.MouseMove, Qt.MouseButton.NoButton))
    assert win._sync_idle_task is not None
    # 按着键拖 → 叫醒
    f.eventFilter(tgt, mouse(QEvent.Type.MouseMove, Qt.MouseButton.LeftButton))
    assert win._sync_idle_task is None
    win._sync_idle_task = object()
    f.eventFilter(tgt, mouse(QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton))
    assert win._sync_idle_task is None
    # 兜底：停帧超过 IDLE_PREVIEW_REFRESH_S 就自己刷
    win._sync_idle_ts = time.monotonic()
    assert not IW.hold_expired(win)
    assert IW.hold_expired(win, now=time.monotonic() + IW.IDLE_PREVIEW_REFRESH_S + 0.1)
    app.removeEventFilter(f)


def test_edit_funnels_wake_idle_hold():
    import inspect
    from cutora.ui import main_window as MW
    M = MW.MainWindow
    for name in ("update_preview", "mark_dirty", "push_undo", "_restore_snapshot"):
        assert "self._sync_idle_task = None" in inspect.getsource(getattr(M, name)), name
    assert "hold_expired(self)" in inspect.getsource(M._idle_preview_hold)
    assert "_sync_idle_ts = time.monotonic()" in inspect.getsource(M._note_idle_preview_sync)
    assert "_install_idle_wake(self)" in inspect.getsource(MW)


def test_watchdog_silent_when_main_thread_has_no_python_frame(monkeypatch):
    """退出时界面线程已经不在 Python 里（取不到栈）= 在退出，不写「卡了 3 秒」。"""
    from cutora.core import stall_watchdog as SW
    logged = []
    monkeypatch.setattr(SW._LOG, "warning", lambda *a, **k: logged.append(a))
    wd = SW.StallWatchdog(threshold_ms=100, poll_ms=10)
    monkeypatch.setattr(wd, "_main_thread_gone", lambda: True)
    wd._beat = time.monotonic() - 5.0
    th = threading.Thread(target=wd._run, daemon=True)
    th.start()
    th.join(2.0)
    assert not th.is_alive() and logged == []


def test_watchdog_stops_on_quit_signals():
    import inspect
    from cutora.core import stall_watchdog as SW
    src = inspect.getsource(SW.StallWatchdog.start)
    assert "aboutToQuit.connect(self.stop)" in src and "lastWindowClosed" in src
    from cutora import app as A
    assert "_wd.stop()" in inspect.getsource(A.run_gui_startup)
