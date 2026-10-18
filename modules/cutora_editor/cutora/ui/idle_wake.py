# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q189h：空闲停帧（Q189g）的「叫醒」。

Q189g 让 200 ms 心跳在「暂停 + 播放头没动 + 还是同一个工程」时不再要预览帧（空转 0.4 核 → 0.06 核）。
但以前改参数（抠像 / 调整层 / 图层拖动……）有一部分是靠这个心跳在 200 ms 内把新画面带出来的；
停帧以后这些改动会「改了画面不变」。这里补三道保险，任何一道触发就让下一拍（≤200 ms）重新出帧：

1. 用户输入：鼠标按下 / 松开 / 拖动（按着键移动）/ 滚轮 / 键盘 / 拖放 —— 应用级事件过滤器；
2. 编辑漏斗：``update_preview`` / ``push_undo`` / ``mark_dirty`` / 撤销重做（在 main_window 里调 ``wake``）；
3. 兜底：停帧最多 ``IDLE_PREVIEW_REFRESH_S`` 秒就自己刷新一张（后台算完的东西，如 AI 抠像，最晚这么久露出来）。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, Qt

IDLE_PREVIEW_REFRESH_S = 5.0

_WAKE_TYPES = frozenset({
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonRelease,
    QEvent.Type.MouseButtonDblClick,
    QEvent.Type.Wheel,
    QEvent.Type.KeyPress,
    QEvent.Type.KeyRelease,
    QEvent.Type.Drop,
})
_MOVE = QEvent.Type.MouseMove


def wake(win) -> None:
    """让下一拍心跳重新出帧（清掉停帧签名）。"""
    try:
        win._sync_idle_task = None
    except Exception:
        pass


def hold_expired(win, now: float | None = None) -> bool:
    """停帧太久了（兜底刷新）。"""
    ts = float(getattr(win, "_sync_idle_ts", 0.0) or 0.0)
    return ((time.monotonic() if now is None else now) - ts) >= IDLE_PREVIEW_REFRESH_S


class IdleWakeFilter(QObject):
    """装在 QApplication 上。只在停帧中才动手（一个属性判断），否则立刻放行。"""

    def __init__(self, win):
        super().__init__(win)
        self._win = win

    def eventFilter(self, obj, ev):           # noqa: N802 (Qt 命名)
        try:
            win = self._win
            if getattr(win, "_sync_idle_task", None) is not None:
                t = ev.type()
                if t in _WAKE_TYPES or (t == _MOVE and ev.buttons() != Qt.MouseButton.NoButton):
                    win._sync_idle_task = None
        except Exception:
            pass
        return False


def install(win, app=None):
    """主窗口建好后调一次；返回过滤器（测试用）。"""
    if app is None:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    f = IdleWakeFilter(win)
    if app is not None:
        app.installEventFilter(f)
    win._idle_wake_filter = f
    return f
