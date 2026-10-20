# -*- coding: utf-8 -*-
"""片语工作室 (Cutora LTX Studio v3.0) — 9:16 竖屏海报级总控台 (D:\\0_LTX\\studio_launcher.py)。

设计规范：
1. 9:16 竖屏黄金比例 (540 × 960)，遵循 CUTORA Roma-Gold 古典黑金视觉体系。
2. 完整内嵌五大核心软件与模块的专属高清徽章图标：
   - 片语工作室主徽章 (`resources/brand/app.png`)
   - Orante 2.0 制片台 (`resources/brand/orante_me.png`)
   - 片语 Studio · CUTORA (`resources/brand/cutora_me.png`)
   - 右键表格工具 (`resources/brand/table_tool_me.png`)
   - 本地算力网关 · 7870 (`resources/brand/ltx_gateway_me.png`)
   - Robota 自动巡检代理 (`resources/brand/robota_me.png`)
3. 全圆角渐变金边卡片、平滑悬停/按压交互动画 (`QVariantAnimation` + `OutCubic`)、内置字体渲染。
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import sys
from pathlib import Path

LTX_ROOT = Path(__file__).resolve().parent
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))

from core.portable import rewrite_pyvenv_cfg
from core.shortcut import ensure_shortcuts
from core.hardware_guard import get_hardware_guard
from core.spine_db import get_spine_db
from core.robota_autopilot import get_autopilot
from core.delivery_worker import get_delivery_worker

# 启动即自动修正便携 Python 路径并确保桌面快捷方式存在
rewrite_pyvenv_cfg(LTX_ROOT)
ensure_shortcuts(LTX_ROOT, force=False, quiet=True)

# ─────────────────────── CUTORA Roma-Gold 色彩令牌 ───────────────────────
BG0 = "#141210"         # 暖玄武岩黑
BG1 = "#1C1815"         # 面板底色
BG2 = "#262019"         # 卡片底色
BG3 = "#332A22"         # 悬停底色
LINE = "#524944"        # 分隔线
BORDER = "#5D5550"      # 基础边框
FG0 = "#EFE7DA"         # 洞石暖白（主标题）
FG1 = "#B3A695"         # 次级石材灰（正文说明）
FG2 = "#92897F"         # 辅助说明文字
ACCENT = "#C8A24A"      # 古典金
ACCENT_HI = "#E0BE68"   # 香槟高光金
ACCENT_DIM = "#8A6E2E"  # 暗古铜金
INFO = "#8FA89B"        # 青铜绿（标语点缀）
OK = "#7D9B6A"          # 橄榄绿（在线状态）
WARN = "#C8913A"        # 赭金

WIN_W = 540
WIN_H = 960


def _python_exe(gui: bool = False) -> str:
    name = "pythonw.exe" if gui else "python.exe"
    cand = LTX_ROOT / "runtime" / "venv" / "Scripts" / name
    if cand.is_file():
        return str(cand)
    cand2 = LTX_ROOT / "runtime" / "python" / name
    if cand2.is_file():
        return str(cand2)
    return sys.executable


import time

_CUTORA_LAUNCH_ACCEPTED = False
_MODULE_CLICK_ACCEPTED: set[str] = set()
_CUTORA_PROC: subprocess.Popen | None = None
_CUTORA_START_TS: float = 0.0
_MODULE_PROCS: dict[str, subprocess.Popen] = {}
_MODULE_START_TS: dict[str, float] = {}


def is_cutora_running() -> bool:
    """检查系统内是否已有正在运行的 CUTORA 实例（基于 Windows 命名互斥体）。"""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenMutexW(0x00100000, False, "Local\\CUTORA_SingleInstance_Mutex_v1")
        if h:
            kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def is_cutora_active() -> bool:
    """判断 CUTORA 是否正在启动中或正在运行中。若进程已退出且超过启动防抖窗口，自动解锁。"""
    global _CUTORA_LAUNCH_ACCEPTED
    if is_cutora_running():
        return True
    if _CUTORA_PROC is not None and _CUTORA_PROC.poll() is None:
        return True
    if _CUTORA_START_TS > 0.0 and (time.monotonic() - _CUTORA_START_TS) < 8.0:
        return True
    _CUTORA_LAUNCH_ACCEPTED = False
    _MODULE_CLICK_ACCEPTED.discard("cutora")
    return False


def is_module_active(mod_id: str) -> bool:
    """判断模块是否处于启动防抖窗口或进程仍在运行。"""
    if mod_id == "cutora":
        return is_cutora_active()
    proc = _MODULE_PROCS.get(mod_id)
    if proc is not None and proc.poll() is None:
        return True
    ts = _MODULE_START_TS.get(mod_id, 0.0)
    if ts > 0.0 and (time.monotonic() - ts) < 6.0:
        return True
    _MODULE_CLICK_ACCEPTED.discard(mod_id)
    return False


ORANTE_RELAUNCH_COOLDOWN_SEC = 5.0
_ORANTE_PROCS: list[subprocess.Popen] = []


def orante_cooldown_left() -> float:
    """Orante 允许多开：仅在上次启动后的 5 秒内忽略重复点击（防止一次手抖连开）。"""
    ts = _MODULE_START_TS.get("orante", 0.0)
    if ts <= 0.0:
        return 0.0
    return max(0.0, ORANTE_RELAUNCH_COOLDOWN_SEC - (time.monotonic() - ts))


def orante_instance_count() -> int:
    alive = [p for p in _ORANTE_PROCS if p.poll() is None]
    _ORANTE_PROCS[:] = alive
    return len(alive)


def launch_orante() -> bool:
    """启动 Orante 2.0。不限制多开数量；仅在两次启动间隔小于 5 秒时忽略（防误触只对 CUTORA 做单实例锁定）。"""
    if orante_cooldown_left() > 0.0:
        return False
    _MODULE_START_TS["orante"] = time.monotonic()
    orante_dir = LTX_ROOT / "modules" / "orante_desk"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PATH"] = f"{LTX_ROOT / 'runtime' / 'bin'};{LTX_ROOT / 'runtime' / 'ffmpeg'};{env.get('PATH', '')}"
    proc = subprocess.Popen([_python_exe(gui=True), "-m", "orante"], cwd=str(orante_dir), env=env)
    _MODULE_PROCS["orante"] = proc
    _ORANTE_PROCS.append(proc)
    get_spine_db().emit_event("launcher.orante_started", "launcher", "orante", {})
    return True


def launch_cutora(room: str = "") -> bool:
    """启动 CUTORA（防误触单次点击保护 + 全局单实例限制；当 CUTORA 退出或关闭后可重新启动）。"""
    global _CUTORA_LAUNCH_ACCEPTED, _CUTORA_PROC, _CUTORA_START_TS
    if is_cutora_active():
        _CUTORA_LAUNCH_ACCEPTED = True
        _MODULE_CLICK_ACCEPTED.add("cutora")
        return False
    _CUTORA_LAUNCH_ACCEPTED = True
    _MODULE_CLICK_ACCEPTED.add("cutora")
    _CUTORA_START_TS = time.monotonic()
    try:
        from core.robota_guide import DailyWorkflowConductor
        cond = DailyWorkflowConductor()
        cond.launch_cutora_room(room)
        if getattr(cond, "_last_cutora_proc", None) is not None:
            _CUTORA_PROC = cond._last_cutora_proc
    except Exception:
        if is_cutora_running():
            return False
        cutora_dir = LTX_ROOT / "modules" / "cutora_editor"
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["CUTORA_RUNTIME"] = str(LTX_ROOT / "runtime")
        env["PATH"] = f"{LTX_ROOT / 'runtime' / 'bin'};{LTX_ROOT / 'runtime' / 'ffmpeg'};{env.get('PATH', '')}"
        args = [_python_exe(gui=True), "-m", "cutora"]
        if room:
            env["CUTORA_START_ROOM"] = room
        _CUTORA_PROC = subprocess.Popen(args, cwd=str(cutora_dir), env=env)
    get_spine_db().emit_event("launcher.cutora_started", "launcher", "cutora", {"room": room})
    return True


def launch_table_tool(target_dir: str | None = None) -> bool:
    """启动右键表格工具（防误触保护，关闭后可再次打开）。"""
    if is_module_active("table"):
        return False
    _MODULE_CLICK_ACCEPTED.add("table")
    _MODULE_START_TS["table"] = time.monotonic()
    try:
        from core.robota_guide import DailyWorkflowConductor
        DailyWorkflowConductor().launch_table_tool_in_dir(Path(target_dir) if target_dir else None)
    except Exception:
        script = LTX_ROOT / "modules" / "table_tool" / "app" / "表格工具.py"
        cmd = [_python_exe(gui=True), str(script)]
        if target_dir:
            cmd.append(target_dir)
        proc = subprocess.Popen(cmd, cwd=str(script.parent))
        _MODULE_PROCS["table"] = proc
    get_spine_db().emit_event("launcher.table_tool_started", "launcher", "table_tool", {})
    return True


def launch_studio_gateway() -> bool:
    """启动本地算力网关（单次点击防误触保护）。"""
    if is_module_active("gateway"):
        return False
    _MODULE_CLICK_ACCEPTED.add("gateway")
    _MODULE_START_TS["gateway"] = time.monotonic()
    bat = LTX_ROOT / "启动本地算力网关.bat"
    proc = subprocess.Popen(["cmd.exe", "/c", "start", "", str(bat)], cwd=str(LTX_ROOT))
    _MODULE_PROCS["gateway"] = proc
    get_spine_db().emit_event("launcher.gateway_started", "launcher", "gateway", {"port": 7870})
    return True


def run_gui(screenshot_path: str | None = None) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LTXStudio.PianYu.3.0")
        except Exception:
            pass

    from PySide6.QtCore import (
        QEasingCurve,
        QPoint,
        QPointF,
        QRectF,
        QSize,
        Qt,
        QTimer,
        QVariantAnimation,
        Signal,
    )
    from PySide6.QtGui import (
        QColor,
        QFont,
        QFontDatabase,
        QIcon,
        QLinearGradient,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
        QRadialGradient,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QVBoxLayout,
        QWidget,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("片语工作室")
    app.setOrganizationName("CUTORA")

    ico_path = LTX_ROOT / "resources" / "brand" / "app.ico"
    if ico_path.is_file():
        app.setWindowIcon(QIcon(str(ico_path)))

    # 注册 CUTORA 内置字体 (Inter / Noto Sans SC / JetBrains Mono)
    cutora_res = LTX_ROOT / "modules" / "cutora_editor" / "resources"
    for rel_font in (
        Path("fonts_pending") / "Inter" / "Interopsz_wght.ttf",
        Path("fonts_pending") / "JetBrains_Mono" / "JetBrainsMonowght.ttf",
        Path("fonts") / "Noto_Sans_SC" / "NotoSansSCwght.ttf",
    ):
        fp = cutora_res / rel_font
        if fp.is_file():
            QFontDatabase.addApplicationFont(str(fp))

    def make_font(px: int, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False, letter_spacing: float = 0.0) -> QFont:
        f = QFont()
        if mono:
            f.setFamilies(["JetBrains Mono", "Consolas", "Microsoft YaHei UI"])
        else:
            f.setFamilies(["Inter", "Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei"])
        f.setPixelSize(px)
        f.setWeight(weight)
        if letter_spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
        return f

    def blend_color(c1: QColor, c2: QColor, t: float) -> QColor:
        t = max(0.0, min(1.0, float(t)))
        return QColor(
            int(c1.red() + (c2.red() - c1.red()) * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue() + (c2.blue() - c1.blue()) * t),
            int(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
        )

    def load_smooth_pixmap(path: Path, target_px: int) -> QPixmap:
        if not path.is_file():
            return QPixmap()
        pm = QPixmap(str(path))
        if pm.isNull():
            return pm
        dpr = 2.0
        scaled = pm.scaled(
            int(target_px * dpr),
            int(target_px * dpr),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        return scaled

    # ─────────────────────── 顶部窗口控制圆钮 ───────────────────────
    class WindowControlBtn(QWidget):
        clicked = Signal()

        def __init__(self, symbol: str, is_close: bool = False, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._symbol = symbol
            self._is_close = is_close
            self._hover_t = 0.0
            self.setFixedSize(26, 26)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(150)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

        def _on_anim(self, val: float) -> None:
            self._hover_t = float(val)
            self.update()

        def enterEvent(self, event) -> None:
            self._anim.stop()
            self._anim.setStartValue(self._hover_t)
            self._anim.setEndValue(1.0)
            self._anim.start()
            super().enterEvent(event)

        def leaveEvent(self, event) -> None:
            self._anim.stop()
            self._anim.setStartValue(self._hover_t)
            self._anim.setEndValue(0.0)
            self._anim.start()
            super().leaveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)

            bg_idle = QColor(28, 24, 21, 140)
            bg_hover = QColor("#B4544A") if self._is_close else QColor(200, 162, 74, 65)
            border_idle = QColor(93, 85, 80, 110)
            border_hover = QColor("#E0BE68") if not self._is_close else QColor("#D87A70")

            p.setBrush(blend_color(bg_idle, bg_hover, self._hover_t))
            p.setPen(QPen(blend_color(border_idle, border_hover, self._hover_t), 1.0))
            p.drawRoundedRect(r, 7.0, 7.0)

            fg = blend_color(QColor(FG1), QColor(FG0), self._hover_t)
            p.setPen(QPen(fg, 1.4))
            p.setFont(make_font(13, QFont.Weight.DemiBold))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._symbol)
            p.end()

    # ─────────────────────── 圆角金韵交互按钮 ───────────────────────
    class AnimatedGoldButton(QWidget):
        clicked = Signal()

        def __init__(
            self,
            text: str,
            primary: bool = True,
            compact: bool = False,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self._text = text
            self._orig_text = text
            self._primary = primary
            self._compact = compact
            self._locked = False
            self._hover_t = 0.0
            self._press_t = 0.0
            self.setCursor(Qt.CursorShape.PointingHandCursor)

            f = make_font(11 if compact else 12, QFont.Weight.DemiBold)
            self.setFont(f)
            self._recalc_size()

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(160)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

        def _recalc_size(self) -> None:
            fm = self.fontMetrics()
            pad_x = 18 if self._compact else 24
            h = 26 if self._compact else 30
            self.setFixedSize(fm.horizontalAdvance(self._text) + pad_x, h)

        def set_text(self, text: str) -> None:
            self._text = text
            self._recalc_size()
            self.update()

        def set_locked(self, locked: bool, text: str | None = None) -> None:
            self._locked = locked
            if locked:
                if text is not None:
                    self._text = text
                    self._recalc_size()
            else:
                self._text = text if text is not None else self._orig_text
                self._recalc_size()
            self._anim.stop()
            self._hover_t = 0.0
            self._press_t = 0.0
            self.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor)
            self.update()

        def _on_anim(self, val: float) -> None:
            self._hover_t = float(val)
            self.update()

        def enterEvent(self, event) -> None:
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(1.0)
                self._anim.start()
            super().enterEvent(event)

        def leaveEvent(self, event) -> None:
            self._press_t = 0.0
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(0.0)
                self._anim.start()
            super().leaveEvent(event)

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._press_t = 1.0
                if not self._locked:
                    self.update()
            super().mousePressEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if self._press_t > 0:
                self._press_t = 0.0
                self.update()
                if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
                    self.clicked.emit()
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            if self._press_t > 0 and not self._locked:
                rect = rect.adjusted(0.5, 1.0, -0.5, 0.5)

            radius = 7.5 if self._compact else 8.5

            if self._locked:
                p.setBrush(QColor(34, 30, 26, 210))
                p.setPen(QPen(QColor(115, 102, 78, 130), 1.0))
                p.drawRoundedRect(rect, radius, radius)
                p.setPen(QColor("#8E8477"))
                p.setFont(self.font())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
                p.end()
                return

            if self._primary:
                grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
                c_top = blend_color(QColor("#D6B056"), QColor("#EAD084"), self._hover_t)
                c_bot = blend_color(QColor("#9E7D34"), QColor("#C8A24A"), self._hover_t)
                if self._press_t > 0:
                    c_top = QColor("#8A6E2E")
                    c_bot = QColor("#6E5622")
                grad.setColorAt(0.0, c_top)
                grad.setColorAt(1.0, c_bot)
                p.setBrush(grad)
                border_col = blend_color(QColor("#E5C675"), QColor("#FFF0B8"), self._hover_t)
                p.setPen(QPen(border_col, 1.1))
                p.drawRoundedRect(rect, radius, radius)

                p.setPen(QColor("#16120E"))
                p.setFont(self.font())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
            else:
                bg_idle = QColor(38, 32, 25, 195)
                bg_hover = QColor(72, 58, 36, 235)
                p.setBrush(blend_color(bg_idle, bg_hover, self._hover_t))
                b_idle = QColor(142, 118, 72, 150)
                b_hover = QColor("#E0BE68")
                p.setPen(QPen(blend_color(b_idle, b_hover, self._hover_t), 1.1))
                p.drawRoundedRect(rect, radius, radius)

                fg_col = blend_color(QColor("#D8CBB8"), QColor("#F6E7C4"), self._hover_t)
                p.setPen(fg_col)
                p.setFont(self.font())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
            p.end()

    # ─────────────────────── 海报级软件模块卡片 ───────────────────────
    class PosterModuleCard(QWidget):
        def __init__(
            self,
            index_str: str,
            icon_path: Path,
            title: str,
            badge_text: str,
            desc: str,
            actions: list[tuple[str, bool, callable]],
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self._index_str = index_str
            self._pixmap = load_smooth_pixmap(icon_path, 58)
            self._title = title
            self._badge_text = badge_text
            self._orig_badge_text = badge_text
            self._desc = desc
            self._locked = False
            self._hover_t = 0.0
            self._primary_callback = actions[-1][2] if actions else None

            self.setFixedHeight(104)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(190)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

            # 右下角动作按钮组
            self._buttons: list[AnimatedGoldButton] = []
            btn_row = QHBoxLayout(self)
            btn_row.setContentsMargins(92, 64, 14, 10)
            btn_row.setSpacing(6)
            btn_row.addStretch(1)
            for label, is_prim, cb in actions:
                btn = AnimatedGoldButton(label, primary=is_prim, compact=True, parent=self)
                btn.clicked.connect(cb)
                btn_row.addWidget(btn)
                self._buttons.append(btn)

        def set_locked(
            self,
            locked: bool,
            primary_text: str = "✓ 已启动 · 再次点击无效",
            lock_all_buttons: bool = True,
        ) -> None:
            self._locked = locked
            self._anim.stop()
            self._hover_t = 0.0
            self.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor)
            self._badge_text = "已接受启动 · 防误触锁定" if locked else self._orig_badge_text
            for idx, btn in enumerate(self._buttons):
                is_last = idx == len(self._buttons) - 1
                if is_last:
                    btn.set_locked(locked, primary_text if locked else None)
                elif lock_all_buttons:
                    btn.set_locked(locked)
            self.update()

        def _on_anim(self, val: float) -> None:
            self._hover_t = float(val)
            self.update()

        def enterEvent(self, event) -> None:
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(1.0)
                self._anim.start()
            super().enterEvent(event)

        def leaveEvent(self, event) -> None:
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(0.0)
                self._anim.start()
            super().leaveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                if not any(b.geometry().contains(pos) for b in self._buttons):
                    if self.rect().contains(pos) and callable(self._primary_callback):
                        self._primary_callback()
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            lift = self._hover_t * 1.5
            rect = QRectF(self.rect()).adjusted(1.5, 2.0 - lift, -1.5, -1.5 - lift)
            radius = 13.0

            # 卡片玄武岩半透明渐变底色
            bg_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            c0 = blend_color(QColor(30, 25, 21, 212), QColor(48, 39, 30, 238), self._hover_t)
            c1 = blend_color(QColor(22, 19, 16, 224), QColor(34, 28, 22, 244), self._hover_t)
            bg_grad.setColorAt(0.0, c0)
            bg_grad.setColorAt(1.0, c1)

            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, bg_grad)

            # 悬停时软件图标背后的暖金柔光晕
            icon_cx = rect.left() + 43.0
            icon_cy = rect.center().y()
            if self._hover_t > 0.01:
                glow = QRadialGradient(QPointF(icon_cx, icon_cy), 56.0)
                glow.setColorAt(0.0, QColor(224, 190, 104, int(72 * self._hover_t)))
                glow.setColorAt(1.0, QColor(224, 190, 104, 0))
                p.save()
                p.setClipPath(path)
                p.fillRect(rect, glow)
                p.restore()

            # 顶部微光流线与圆角渐变金边
            border_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            b_top = blend_color(QColor(112, 96, 74, 155), QColor("#E5C778"), self._hover_t)
            b_bot = blend_color(QColor(68, 59, 51, 130), QColor("#8A6E2E"), self._hover_t)
            border_grad.setColorAt(0.0, b_top)
            border_grad.setColorAt(1.0, b_bot)
            p.setPen(QPen(border_grad, 1.2 + 0.4 * self._hover_t))
            p.drawPath(path)

            # 右上角古典序号暗纹 (01 / 02 / 03 / 04)
            p.setFont(make_font(11, QFont.Weight.Bold, mono=True, letter_spacing=1.2))
            idx_col = blend_color(QColor(146, 137, 127, 110), QColor(224, 190, 104, 215), self._hover_t)
            p.setPen(idx_col)
            p.drawText(
                QRectF(rect.right() - 52, rect.top() + 10, 38, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._index_str,
            )

            # 绘制对应软件的真实高清徽章图标（带轻微悬停缩放）
            if not self._pixmap.isNull():
                scale = 1.0 + 0.05 * self._hover_t
                draw_sz = 58.0 * scale
                p.save()
                p.translate(icon_cx, icon_cy)
                p.scale(scale, scale)
                p.drawPixmap(int(-29), int(-29), self._pixmap)
                p.restore()

            # 标题 + 胶囊标签
            text_x = rect.left() + 84.0
            title_font = make_font(14, QFont.Weight.Bold)
            p.setFont(title_font)
            title_col = blend_color(QColor(FG0), QColor("#FFF7E8"), self._hover_t)
            p.setPen(title_col)
            fm_t = p.fontMetrics()
            tw = fm_t.horizontalAdvance(self._title)
            p.drawText(QPointF(text_x, rect.top() + 25), self._title)

            # 金色职能胶囊标签
            badge_f = make_font(10, QFont.Weight.Medium)
            p.setFont(badge_f)
            fm_b = p.fontMetrics()
            bw = fm_b.horizontalAdvance(self._badge_text) + 14
            badge_rect = QRectF(text_x + tw + 8, rect.top() + 10.5, bw, 18)
            p.setBrush(QColor(200, 162, 74, int(28 + 24 * self._hover_t)))
            p.setPen(QPen(QColor(200, 162, 74, int(95 + 65 * self._hover_t)), 0.9))
            p.drawRoundedRect(badge_rect, 5.0, 5.0)
            p.setPen(QColor("#E0BE68"))
            p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, self._badge_text)

            # 功能描述
            p.setFont(make_font(11, QFont.Weight.Normal))
            p.setPen(blend_color(QColor(FG1), QColor("#D6C9B8"), self._hover_t))
            desc_rect = QRectF(text_x, rect.top() + 33, rect.width() - 98, 32)
            p.drawText(
                desc_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                self._desc,
            )
            p.end()

    # ─────────────────────── 底部 Robota 向导与一心三用群集矩阵卡 ───────────────────────
    class SpineFooterCard(QWidget):
        guide_requested = Signal()
        patrol_requested = Signal()
        shortcut_requested = Signal()
        refresh_requested = Signal()

        def __init__(self, robota_icon: Path, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._robota_pm = load_smooth_pixmap(robota_icon, 38)
            self._summary = {"tasks_total": 0, "gaps_pending": 0, "uploads_pending": 0, "unacked_events": 0}
            self._latest_signal = "Robota 在线待命 · 发链接或直接对话即可在后台自动下载与排片"
            self._pulse_phase = 0.0
            self.setFixedHeight(186)

            lay = QVBoxLayout(self)
            lay.setContentsMargins(14, 142, 14, 12)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)

            self.btn_guide = AnimatedGoldButton("✦ 打开 Robota · 开始今天的工作", primary=True, compact=False, parent=self)
            self.btn_patrol = AnimatedGoldButton("自动巡检", primary=False, compact=False, parent=self)
            self.btn_shortcut = AnimatedGoldButton("桌面图标", primary=False, compact=False, parent=self)
            self.btn_refresh = AnimatedGoldButton("刷新", primary=False, compact=False, parent=self)

            self.btn_guide.clicked.connect(self.guide_requested.emit)
            self.btn_patrol.clicked.connect(self.patrol_requested.emit)
            self.btn_shortcut.clicked.connect(self.shortcut_requested.emit)
            self.btn_refresh.clicked.connect(self.refresh_requested.emit)

            btn_row.addWidget(self.btn_guide)
            btn_row.addWidget(self.btn_patrol)
            btn_row.addWidget(self.btn_shortcut)
            btn_row.addStretch(1)
            btn_row.addWidget(self.btn_refresh)
            lay.addLayout(btn_row)

        def update_data(self, summary: dict, latest_signal: str) -> None:
            self._summary = dict(summary)
            if latest_signal:
                self._latest_signal = latest_signal
            self.update()

        def set_pulse(self, phase: float) -> None:
            self._pulse_phase = phase
            self.update()

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            radius = 14.0

            bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
            bg.setColorAt(0.0, QColor(28, 24, 20, 228))
            bg.setColorAt(1.0, QColor(19, 16, 14, 240))
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, bg)

            b_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            b_grad.setColorAt(0.0, QColor(200, 162, 74, 135))
            b_grad.setColorAt(1.0, QColor(82, 73, 68, 135))
            p.setPen(QPen(b_grad, 1.15))
            p.drawPath(path)

            # 1. 顶部：Robota 真实头像徽章 + 标题 + 在线呼吸灯
            if not self._robota_pm.isNull():
                p.drawPixmap(int(rect.left() + 14), int(rect.top() + 11), self._robota_pm)

            p.setFont(make_font(13, QFont.Weight.Bold))
            p.setPen(QColor(FG0))
            p.drawText(QPointF(rect.left() + 60, rect.top() + 26), "Robota · 协同操作代理")

            p.setFont(make_font(10, QFont.Weight.Normal))
            p.setPen(QColor(FG2))
            p.drawText(QPointF(rect.left() + 60, rect.top() + 43), "对话发链接自动建夹 · 下载改名 v01(1)(2) · 同步 Orante 一做到底")

            # 右上角在线胶囊：显示当前活跃日期与推进批次（替代旧『三分身在线』）
            active_day_s = str(self._summary.get("active_day", ""))[-5:]
            active_batch_s = str(self._summary.get("active_batch", "A"))
            pill_label = f"{active_day_s} · {active_batch_s}批次" if active_day_s else "多子代理就绪"

            pill_r = QRectF(rect.right() - 124, rect.top() + 14, 110, 22)
            p.setBrush(QColor(125, 155, 106, 32))
            p.setPen(QPen(QColor(125, 155, 106, 120), 1.0))
            p.drawRoundedRect(pill_r, 11.0, 11.0)

            pulse_alpha = int(160 + 90 * (0.5 + 0.5 * math.sin(self._pulse_phase)))
            p.setBrush(QColor(142, 184, 116, pulse_alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(pill_r.left() + 13, pill_r.center().y()), 3.6, 3.6)

            p.setFont(make_font(10, QFont.Weight.Medium))
            p.setPen(QColor("#A5C490"))
            p.drawText(pill_r.adjusted(22, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, pill_label)

            # 2. 中部：四项实时指标圆角小卡
            tasks_cnt = int(self._summary.get("tasks_total", 0))
            gaps_cnt = int(self._summary.get("gaps_pending", 0))
            clips_cnt = int(self._summary.get("clips_count", 0))
            up_ok_cnt = int(self._summary.get("uploaded_ok_count", 0))
            prev_up_cnt = int(self._summary.get("prev_uploaded_ok", 0))
            prev_day_s = str(self._summary.get("prev_completed_day", ""))[-2:].lstrip("0")

            first_metric = ("当期任务", str(tasks_cnt)) if tasks_cnt > 0 else ("当期批次", f"{active_batch_s}批")
            second_metric = ("待补片段", str(gaps_cnt)) if gaps_cnt > 0 else ("生成片段", str(clips_cnt))
            if up_ok_cnt > 0:
                third_metric = ("已传闭环", f"{up_ok_cnt}✓")
            elif prev_up_cnt > 0:
                third_metric = (f"{prev_day_s or '前'}号已传", f"{prev_up_cnt}✓")
            else:
                third_metric = ("已导成品", str(self._summary.get("uploads_pending", 0)))

            metrics = [
                first_metric,
                second_metric,
                third_metric,
                ("今日信号", str(self._summary.get("unacked_events", 0))),
            ]
            m_y = rect.top() + 55
            m_w = (rect.width() - 28 - 24) / 4.0
            for i, (m_label, m_val) in enumerate(metrics):
                mx = rect.left() + 14 + i * (m_w + 8)
                mr = QRectF(mx, m_y, m_w, 34)
                p.setBrush(QColor(38, 32, 25, 190))
                p.setPen(QPen(QColor(93, 85, 80, 120), 0.9))
                p.drawRoundedRect(mr, 7.0, 7.0)

                p.setFont(make_font(10, QFont.Weight.Normal))
                p.setPen(QColor(FG1))
                p.drawText(mr.adjusted(9, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, m_label)

                p.setFont(make_font(14, QFont.Weight.Bold, mono=True))
                p.setPen(QColor(ACCENT_HI))
                p.drawText(mr.adjusted(4, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, m_val)

            # 底部实时信号流水行
            sig_r = QRectF(rect.left() + 14, rect.top() + 97, rect.width() - 28, 32)
            p.setBrush(QColor(20, 18, 16, 195))
            p.setPen(QPen(QColor(82, 73, 68, 125), 0.9))
            p.drawRoundedRect(sig_r, 7.0, 7.0)

            p.setFont(make_font(10, QFont.Weight.Medium))
            p.setPen(QColor(ACCENT))
            p.drawText(sig_r.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "◆ 闭环矩阵")

            p.setFont(make_font(10, QFont.Weight.Normal))
            p.setPen(QColor(FG1))
            fm_s = p.fontMetrics()
            elided = fm_s.elidedText(self._latest_signal, Qt.TextElideMode.ElideRight, int(sig_r.width() - 88))
            p.drawText(sig_r.adjusted(78, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
            p.end()

    # ─────────────────────── 顶部轻提示横幅 (Toast) ───────────────────────
    class GoldToastBanner(QWidget):
        def __init__(self, parent: QWidget) -> None:
            super().__init__(parent)
            self._text = ""
            self._opacity = 0.0
            self.setFixedSize(WIN_W - 56, 38)
            self.move(28, 52)
            self.hide()

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(220)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

            self._hide_timer = QTimer(self)
            self._hide_timer.setSingleShot(True)
            self._hide_timer.timeout.connect(self._fade_out)

        def show_toast(self, text: str, duration_ms: int = 2800) -> None:
            self._text = text
            self.show()
            self.raise_()
            self._anim.stop()
            self._anim.setStartValue(self._opacity)
            self._anim.setEndValue(1.0)
            self._anim.start()
            self._hide_timer.start(duration_ms)

        def _fade_out(self) -> None:
            self._anim.stop()
            self._anim.setStartValue(self._opacity)
            self._anim.setEndValue(0.0)
            self._anim.start()

        def _on_anim(self, val: float) -> None:
            self._opacity = float(val)
            if self._opacity <= 0.01 and not self._hide_timer.isActive():
                self.hide()
            self.update()

        def paintEvent(self, event) -> None:
            if self._opacity <= 0.01:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setOpacity(self._opacity)
            r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            p.setBrush(QColor(36, 30, 23, 245))
            p.setPen(QPen(QColor(ACCENT_HI), 1.3))
            p.drawRoundedRect(r, 10.0, 10.0)

            p.setFont(make_font(12, QFont.Weight.DemiBold))
            p.setPen(QColor(FG0))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"◆  {self._text}")
            p.end()

    # ─────────────────────── 9:16 竖屏海报主窗口 ───────────────────────
    class PosterLauncherWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("片语工作室")
            self.setFixedSize(WIN_W, WIN_H)
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Window
                | Qt.WindowType.WindowMinimizeButtonHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

            self._drag_offset: QPoint | None = None
            self._pulse_phase = 0.0

            brand_dir = LTX_ROOT / "resources" / "brand"
            self._bg_pm = QPixmap(str(brand_dir / "poster_bg_9x16.png"))
            self._hero_pm = load_smooth_pixmap(brand_dir / "app.png", 86)

            guard = get_hardware_guard()
            self._profile = guard.probe_system()
            self._spine = get_spine_db()

            self._build_ui(brand_dir)
            self._toast = GoldToastBanner(self)

            # 呼吸光效与实时状态定时器
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setInterval(45)
            self._pulse_timer.timeout.connect(self._tick_pulse)
            self._pulse_timer.start()

            self._status_timer = QTimer(self)
            self._status_timer.setInterval(6000)
            self._status_timer.timeout.connect(lambda: self.refresh_status(silent=True))
            self._status_timer.start()

            self.refresh_status(silent=True)

        def _build_ui(self, brand_dir: Path) -> None:
            root = QVBoxLayout(self)
            root.setContentsMargins(22, 14, 22, 18)
            root.setSpacing(0)

            # 1. 顶部栏（最小化 / 关闭按钮）
            top_bar = QHBoxLayout()
            top_bar.setContentsMargins(4, 0, 0, 0)
            top_bar.setSpacing(6)
            top_bar.addStretch(1)

            btn_min = WindowControlBtn("—", is_close=False, parent=self)
            btn_close = WindowControlBtn("×", is_close=True, parent=self)
            btn_min.clicked.connect(self.showMinimized)
            btn_close.clicked.connect(self.close)
            top_bar.addWidget(btn_min)
            top_bar.addWidget(btn_close)
            root.addLayout(top_bar)

            # 2. 预留顶部海报主视觉区高度 (Hero Poster Header 由 paintEvent 精绘)
            root.addSpacing(212)

            # 3. 四大软件与核心算力内嵌卡片区
            cards_layout = QVBoxLayout()
            cards_layout.setContentsMargins(0, 0, 0, 0)
            cards_layout.setSpacing(10)

            self._accepted_clicks: set[str] = set()

            self._card_orante = PosterModuleCard(
                index_str="01",
                icon_path=brand_dir / "orante_medallion.png",
                title="Orante 2.0 制片台",
                badge_text="排片 · 生片 · 审片",
                desc="Grok / Flow / LTX-2.5 批量视频生成、分镜排片、首尾帧接力与缺片回流补生",
                actions=[
                    ("启动制片台  ▸", True, self._on_open_orante),
                ],
                parent=self,
            )

            self._card_cutora = PosterModuleCard(
                index_str="02",
                icon_path=brand_dir / "cutora_me.png",
                title="片语 Studio · CUTORA",
                badge_text="剪辑 · 拼接 · AI 工坊",
                desc="直读 Orante 任务表免粘贴智能拼接，内置去人物、音频深修与人像提亮",
                actions=[
                    ("智能拼接", False, lambda: self._on_open_cutora("stitch")),
                    ("✦ AI 工坊", False, lambda: self._on_open_cutora("ai_studio")),
                    ("进入剪辑台  ▸", True, lambda: self._on_open_cutora("")),
                ],
                parent=self,
            )

            self._card_table = PosterModuleCard(
                index_str="03",
                icon_path=brand_dir / "table_tool_me.png",
                title="右键表格工具",
                badge_text="同色折叠 · 自动(1)(2)",
                desc="网盘同夹素材默认同色折叠，填前缀 v01 即自动编为 v01(1)(2) 下载与上传",
                actions=[
                    ("打开表格工具  ▸", True, self._on_open_table),
                ],
                parent=self,
            )

            self._card_gateway = PosterModuleCard(
                index_str="04",
                icon_path=brand_dir / "ltx_gateway_me.png",
                title="本地算力网关 · 7870",
                badge_text="离线推理 · 乐高插件",
                desc="LTX-2.5 22B · 七大 IC-LoRA · FLUX.2 · Simone v6h · 乐高模型包按需拼装",
                actions=[
                    ("🧩 扩展包", False, self._on_open_pack_manager),
                    ("唤醒算力网关  ▸", True, self._on_open_gateway),
                ],
                parent=self,
            )

            cards_layout.addWidget(self._card_orante)
            cards_layout.addWidget(self._card_cutora)
            cards_layout.addWidget(self._card_table)
            cards_layout.addWidget(self._card_gateway)
            root.addLayout(cards_layout)

            if is_cutora_running():
                self._accepted_clicks.add("cutora")
                self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")

            root.addSpacing(12)

            # 4. 底部 Robota 向导与一心三用群集矩阵卡
            self._robota_win = None
            self._footer_card = SpineFooterCard(brand_dir / "robota_me.png", parent=self)
            self._footer_card.guide_requested.connect(self._on_open_robota_guide)
            self._footer_card.patrol_requested.connect(self._on_run_patrol)
            self._footer_card.shortcut_requested.connect(self._on_create_shortcut)
            self._footer_card.refresh_requested.connect(lambda: self.refresh_status(silent=False))
            root.addWidget(self._footer_card)
            root.addStretch(1)

        def _sync_module_locks(self) -> None:
            """同步模块运行与防误触状态：正在启动或运行中保持锁定，关闭或退出后自动恢复可点击。"""
            if is_cutora_active():
                if "cutora" not in self._accepted_clicks or not self._card_cutora._locked:
                    self._accepted_clicks.add("cutora")
                    self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")
            else:
                if "cutora" in self._accepted_clicks or self._card_cutora._locked:
                    self._accepted_clicks.discard("cutora")
                    self._card_cutora.set_locked(False)

            # Orante 允许多开：5 秒冷却结束后自动解锁卡片
            if orante_cooldown_left() <= 0.0 and ("orante" in self._accepted_clicks or self._card_orante._locked):
                self._accepted_clicks.discard("orante")
                self._card_orante.set_locked(False)

            for mod_id, card in (
                ("table", self._card_table),
            ):
                if not is_module_active(mod_id):
                    if mod_id in self._accepted_clicks or card._locked:
                        self._accepted_clicks.discard(mod_id)
                        card.set_locked(False)

        def _tick_pulse(self) -> None:
            self._pulse_phase = (self._pulse_phase + 0.09) % (math.pi * 2.0)
            self._footer_card.set_pulse(self._pulse_phase)
            self._pulse_ticks = getattr(self, "_pulse_ticks", 0) + 1
            if self._pulse_ticks % 20 == 0:
                self._sync_module_locks()
            # 仅重绘顶部英雄区光环
            self.update(0, 0, WIN_W, 255)

        def _on_open_robota_guide(self) -> None:
            from core.pack_manager import is_module_installed
            if not is_module_installed("pack-app-robota"):
                self._toast.show_toast("当前版本尚未安装 Robota 协同代理扩展包，可一键无缝安装！")
                self._on_open_pack_manager(highlight_pack_id="pack-app-robota")
                return
            try:
                if self._robota_win is not None and self._robota_win.poll() is None:
                    self._toast.show_toast("Robota 协同操作窗口已在运行中（二次点击不再重复启动）")
                    return
                script = LTX_ROOT / "robota_assistant_window.py"
                env = os.environ.copy()
                env["PYTHONUTF8"] = "1"
                self._robota_win = subprocess.Popen(
                    [_python_exe(gui=True), str(script)],
                    cwd=str(LTX_ROOT),
                    env=env,
                )
                self._toast.show_toast("已打开 Robota · 准备开始今天的工作！")
            except Exception as e:
                self._toast.show_toast(f"启动异常：{e}")

        def _on_open_orante(self) -> None:
            self._sync_module_locks()
            left = orante_cooldown_left()
            if left > 0.0:
                self._toast.show_toast(f"Orante 2.0 刚刚启动，{left:.0f} 秒后可再开一个窗口（支持多开）")
                return
            from core.pack_manager import is_module_installed
            if not is_module_installed("pack-app-orante"):
                self._toast.show_toast("当前版本尚未安装 Orante 2.0 制片台扩展包，可一键无缝安装！")
                self._on_open_pack_manager(highlight_pack_id="pack-app-orante")
                return
            if not launch_orante():
                return
            self._accepted_clicks.add("orante")
            self._card_orante.set_locked(True, "✓ 已启动 · 5 秒后可再开")
            QTimer.singleShot(int(ORANTE_RELAUNCH_COOLDOWN_SEC * 1000) + 100, self._sync_module_locks)
            n = orante_instance_count()
            self._toast.show_toast(f"已启动 Orante 2.0 制片台（当前 {n} 个窗口，5 秒后可继续多开）")
            self.refresh_status(silent=True)

        def _on_open_cutora(self, room: str = "") -> None:
            self._sync_module_locks()
            if "cutora" in self._accepted_clicks:
                self._toast.show_toast("CUTORA 已接受过启动点击，第二次误点不被接受（防误触锁定）")
                return
            if is_cutora_running():
                self._accepted_clicks.add("cutora")
                self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")
                self._toast.show_toast("CUTORA 已在运行中（全局仅允许同时启动 1 个 CUTORA，重复点击已忽略）")
                return
            self._accepted_clicks.add("cutora")
            self._card_cutora.set_locked(True, "✓ 已启动 · 再次点击无效")
            ok = launch_cutora(room)
            if not ok:
                self._toast.show_toast("CUTORA 已接受过启动或正在运行，重复点击不被接受")
                return
            label = {"stitch": "片段智能拼接", "ai_studio": "AI 工坊"}.get(room, "片语剪辑台")
            self._toast.show_toast(f"已接受点击并打开 CUTORA · {label}（后续重复点击已锁定忽略）")
            self.refresh_status(silent=True)

        def _on_open_table(self) -> None:
            self._sync_module_locks()
            if "table" in self._accepted_clicks or is_module_active("table"):
                self._toast.show_toast("右键表格工具已接受过启动点击，第二次点击不再被接受（防误触锁定）")
                return
            self._accepted_clicks.add("table")
            self._card_table.set_locked(True, "✓ 已启动 · 再次点击无效")
            launch_table_tool()
            self._toast.show_toast("已接受点击并启动右键表格工具（后续重复点击已锁定忽略）")
            self.refresh_status(silent=True)

        def _on_open_gateway(self) -> None:
            if not is_module_active("gateway"):
                self._accepted_clicks.discard("gateway")
                self._card_gateway.set_locked(False, lock_all_buttons=False)
            if "gateway" in self._accepted_clicks:
                self._toast.show_toast("算力网关已接受过唤醒点击，第二次点击不再被接受（防误触锁定）")
                return
            self._accepted_clicks.add("gateway")
            self._card_gateway.set_locked(True, "✓ 已唤醒 · 再次点击无效", lock_all_buttons=False)
            launch_studio_gateway()
            self._toast.show_toast("已接受点击并唤醒本地算力网关 (127.0.0.1:7870)")
            self.refresh_status(silent=True)

        def _on_open_pack_manager(self, highlight_pack_id: str = "") -> None:
            from PySide6.QtWidgets import QDialog, QFileDialog, QPushButton, QTextBrowser
            from core.pack_manager import (
                evaluate_all_packs,
                find_inbox_archive,
                install_pack_archive,
                scan_and_auto_install_inbox,
            )

            dlg = QDialog(self)
            dlg.setWindowTitle("片语工作室 · 乐高模块化扩展包中心 (Pack Manager)")
            dlg.resize(560, 680)
            dlg.setStyleSheet(
                f"QDialog {{ background: {BG1}; color: {FG0}; }}"
                f"QTextBrowser {{ background: {BG2}; color: {FG0}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px; font-size: 12px; }}"
                f"QPushButton {{ background: {BG3}; color: {ACCENT_HI}; border: 1px solid {ACCENT}; border-radius: 6px; padding: 8px 14px; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {ACCENT}; color: {BG0}; }}"
            )
            lay = QVBoxLayout(dlg)
            browser = QTextBrowser(dlg)
            lay.addWidget(browser, 1)

            def _render_info() -> None:
                info = evaluate_all_packs(self._profile)
                summ = info.get("summary", {})
                lines = [
                    f"<h3 style='color:{ACCENT_HI};margin:0;'>🧩 {summ.get('edition_mode', '')}</h3>",
                    f"<p style='color:{FG1};margin:4px 0 10px 0;'><b>内置标准核心底座 (~25GB)</b>：统一总控台 · 右键表格工具 · CUTORA 剪辑台 · 自动字幕 (Whisper) · 智能拼接 · AI视频抠像 (SAM2.1/RMBG2) · 立体声与降噪（支持 i7 + 32GB 内存无独显流畅运行）</p>",
                ]
                if highlight_pack_id:
                    lines.append(
                        f"<div style='background:#332618;border:1px solid {ACCENT_HI};padding:8px;border-radius:6px;margin-bottom:8px;'>"
                        f"💡 <b>提示</b>：您刚刚点击的功能模块需要扩展包 <code>{highlight_pack_id}.ltxpack</code>。请将其放入 <code>packs/</code> 目录或点击下方按钮一键无缝安装！"
                        f"</div>"
                    )
                lines.append("<hr/>")
                for pk in info.get("packs", []):
                    is_hi = pk["id"] == highlight_pack_id
                    border_style = f"border-left:3px solid {ACCENT_HI};padding-left:8px;" if is_hi else ""
                    lines.append(
                        f"<p style='margin:6px 0;{border_style}'><b>{pk['name']}</b> "
                        f"<span style='color:{ACCENT_HI};'>({pk['approx_size_gb']} GB · <code>{pk['id']}.ltxpack</code>)</span><br/>"
                        f"状态：{pk['status_badge']}<br/>"
                        f"<span style='color:{FG1};'>{pk['description']}</span></p>"
                    )
                browser.setHtml("".join(lines))

            _render_info()
            btn_row = QHBoxLayout()
            btn_auto = QPushButton("⚡ 自动扫描 packs/ 目录一键安装", dlg)
            btn_pick = QPushButton("📂 选择 .ltxpack 文件一键安装…", dlg)
            btn_close = QPushButton("关闭", dlg)

            def _do_auto() -> None:
                if highlight_pack_id:
                    cand = find_inbox_archive(highlight_pack_id)
                    if cand:
                        r = install_pack_archive(cand)
                        _render_info()
                        if r.get("ok"):
                            self._toast.show_toast(f"已一键安装：{highlight_pack_id}！")
                            return
                res = scan_and_auto_install_inbox()
                _render_info()
                if res:
                    self._toast.show_toast(f"已成功自动安装 {len(res)} 个扩展包！")
                else:
                    self._toast.show_toast("packs/ 目录下暂无未安装的新扩展包")

            def _do_pick() -> None:
                fn, _ = QFileDialog.getOpenFileName(
                    dlg,
                    "选择要安装的片语工作室扩展包 (.ltxpack / .zip)",
                    str(LTX_ROOT),
                    "片语扩展包 (*.ltxpack *.zip)",
                )
                if fn:
                    r = install_pack_archive(fn)
                    _render_info()
                    if r.get("ok"):
                        self._toast.show_toast(f"安装完成：{r.get('pack_id')} ({r.get('installed_size_gb')} GB)")
                    else:
                        self._toast.show_toast(f"安装失败：{r.get('error')}")

            btn_auto.clicked.connect(_do_auto)
            btn_pick.clicked.connect(_do_pick)
            btn_close.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_auto)
            btn_row.addWidget(btn_pick)
            btn_row.addWidget(btn_close)
            lay.addLayout(btn_row)
            dlg.exec()


        def _on_run_patrol(self) -> None:
            res = get_autopilot().patrol_and_heal_once()
            up_res = get_delivery_worker().sync_pending_uploads_to_drive()
            self.refresh_status(silent=True)
            self._toast.show_toast(
                f"巡检完成：扫描 {res['events_scanned']} 条信号 · 触发 {len(res['replenished'])} 组补片 · 同步 {up_res.get('uploaded', 0)} 个文件"
            )

        def _on_create_shortcut(self) -> None:
            ensure_shortcuts(LTX_ROOT, force=True, quiet=True)
            self._toast.show_toast("已在桌面与开始菜单生成『片语工作室』快捷方式")

        def refresh_status(self, silent: bool = False) -> None:
            self._sync_module_locks()
            st = self._spine.get_dashboard_summary()
            ev = self._spine.get_latest_event()
            active_day = st.get("active_day", "")
            active_batch = st.get("active_batch", "A")
            stage_lbl = st.get("stage_label", "待启动")
            prev_day = st.get("prev_completed_day", "")
            prev_up = st.get("prev_uploaded_ok", 0)
            ws_brief = f"[{active_day} · {active_batch}批次] {stage_lbl}"
            if prev_day and prev_up > 0:
                latest_str = f"{ws_brief} ｜ ✅ {prev_day[-5:]} 已全部上传完结 ({prev_up}/{prev_up})"
            elif ev:
                latest_str = f"{ws_brief} ｜ 信号 #{ev['id']} [{ev['topic']}]"
            else:
                latest_str = f"{ws_brief} ｜ 多子代理并行 · 配方 A/B 全流程代理"
            self._footer_card.update_data(st, latest_str)
            if not silent:
                self._toast.show_toast("工作室实时指标与信号总线已刷新")

        # 窗口任意空白处拖拽移动
        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
                self.move(event.globalPosition().toPoint() - self._drag_offset)
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            self._drag_offset = None
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            outer = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            win_radius = 18.0
            clip_path = QPainterPath()
            clip_path.addRoundedRect(outer, win_radius, win_radius)

            # 1. 绘制 9:16 罗马石材与古典金光影海报底图
            p.save()
            p.setClipPath(clip_path)
            if not self._bg_pm.isNull():
                p.drawPixmap(outer.toRect(), self._bg_pm)
            else:
                p.fillRect(outer, QColor(BG0))
            p.restore()

            # 2. 顶部题头纪年铭文
            p.setFont(make_font(10, QFont.Weight.DemiBold, mono=True, letter_spacing=2.6))
            p.setPen(QColor(ACCENT))
            p.drawText(
                QRectF(26, 18, 360, 20),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "◆  CUTORA · ORANTE · ROBOTA · LTX",
            )

            # 3. 英雄区主徽章呼吸金环与《片语工作室》海报排版
            cx = WIN_W / 2.0
            cy = 92.0
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase)

            # 呼吸光晕
            halo = QRadialGradient(QPointF(cx, cy), 68.0)
            halo.setColorAt(0.0, QColor(224, 190, 104, int(55 + 30 * pulse)))
            halo.setColorAt(0.65, QColor(200, 162, 74, int(20 + 14 * pulse)))
            halo.setColorAt(1.0, QColor(200, 162, 74, 0))
            p.setBrush(halo)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 68.0, 68.0)

            # 外侧古典细金环与两侧金线
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(224, 190, 104, int(90 + 55 * pulse)), 1.1))
            p.drawEllipse(QPointF(cx, cy), 48.0 + 1.5 * pulse, 48.0 + 1.5 * pulse)

            line_Grad_l = QLinearGradient(44, cy, cx - 58, cy)
            line_Grad_l.setColorAt(0.0, QColor(200, 162, 74, 0))
            line_Grad_l.setColorAt(1.0, QColor(200, 162, 74, 130))
            p.setPen(QPen(line_Grad_l, 1.0))
            p.drawLine(QPointF(44, cy), QPointF(cx - 58, cy))

            line_Grad_r = QLinearGradient(cx + 58, cy, WIN_W - 44, cy)
            line_Grad_r.setColorAt(0.0, QColor(200, 162, 74, 130))
            line_Grad_r.setColorAt(1.0, QColor(200, 162, 74, 0))
            p.setPen(QPen(line_Grad_r, 1.0))
            p.drawLine(QPointF(cx + 58, cy), QPointF(WIN_W - 44, cy))

            if not self._hero_pm.isNull():
                p.drawPixmap(int(cx - 43), int(cy - 43), self._hero_pm)

            # 海报主标题：片 语 工 作 室
            p.setFont(make_font(23, QFont.Weight.Bold, letter_spacing=4.5))
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(QRectF(0, 145, WIN_W, 30), Qt.AlignmentFlag.AlignCenter, "片语工作室")
            p.setPen(QColor(FG0))
            p.drawText(QRectF(0, 144, WIN_W, 30), Qt.AlignmentFlag.AlignCenter, "片语工作室")

            # CUTORA 经典标语
            p.setFont(make_font(12, QFont.Weight.Medium, letter_spacing=1.2))
            p.setPen(QColor(INFO))
            p.drawText(QRectF(0, 175, WIN_W, 20), Qt.AlignmentFlag.AlignCenter, "让每一句话，找到它的位置。")

            # 硬件自适应状态胶囊条
            prof = self._profile
            gpu_short = (prof.gpu_name or "CPU 模式").replace("NVIDIA GeForce ", "")
            vram_gb = int(round(prof.gpu_vram_total_gb))
            ram_gb = int(round(prof.ram_total_gb))
            tier_cn = "旗舰工作站" if vram_gb >= 12 else ("主流独显档" if vram_gb >= 6 else "高内存 CPU 档")
            hw_text = f"✦  {tier_cn}   ·   {gpu_short} ({vram_gb}GB)   ·   {prof.cpu_cores}核 CPU   ·   {ram_gb}GB 内存"
            hw_rect = QRectF(36, 202, WIN_W - 72, 24)
            p.setBrush(QColor(28, 24, 21, 195))
            p.setPen(QPen(QColor(200, 162, 74, 95), 1.0))
            p.drawRoundedRect(hw_rect, 12.0, 12.0)

            p.setFont(make_font(10, QFont.Weight.Medium))
            p.setPen(QColor("#D8CBB8"))
            p.drawText(hw_rect, Qt.AlignmentFlag.AlignCenter, hw_text)

            # 4. 窗口外框双层古典金圆角描边
            frame_grad = QLinearGradient(outer.topLeft(), outer.bottomRight())
            frame_grad.setColorAt(0.0, QColor("#E0BE68"))
            frame_grad.setColorAt(0.45, QColor("#9C7B34"))
            frame_grad.setColorAt(1.0, QColor("#5D5043"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(frame_grad, 1.6))
            p.drawRoundedRect(outer, win_radius, win_radius)

            inner_frame = outer.adjusted(1.5, 1.5, -1.5, -1.5)
            p.setPen(QPen(QColor(239, 231, 218, 26), 1.0))
            p.drawRoundedRect(inner_frame, win_radius - 1.5, win_radius - 1.5)
            p.end()

    win = PosterLauncherWindow()
    win.show()

    if screenshot_path:
        def _capture_and_quit() -> None:
            app.processEvents()
            pm = win.grab()
            out_p = Path(screenshot_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            pm.save(str(out_p), "PNG")
            app.quit()

        QTimer.singleShot(350, _capture_and_quit)

    app.exec()


if __name__ == "__main__":
    if "--patrol-once" in sys.argv:
        print(json.dumps(get_autopilot().patrol_and_heal_once(), ensure_ascii=False, indent=2))
    elif "--status" in sys.argv:
        print(json.dumps({
            "hardware": get_hardware_guard().probe_system().to_dict(),
            "spine": get_spine_db().get_dashboard_summary(),
        }, ensure_ascii=False, indent=2))
    elif "--screenshot" in sys.argv:
        idx = sys.argv.index("--screenshot")
        shot_out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else str(LTX_ROOT / "launcher_preview.png")
        run_gui(screenshot_path=shot_out)
    else:
        run_gui()
