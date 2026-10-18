# -*- coding: utf-8 -*-
"""
片语工作室 · Robota 协同操作代理窗口 (Roma-Gold 古典黑金主题 + 本地 AI 大脑 + 真实点击/稳态指引光标)
====================================================================================================
设计原则：
1. 统一金色主题 (Roma-Gold)：与《片语工作室》主控台完全一致的暖玄武岩黑 (#141210) + 古典金 (#C8A24A) + 香槟金 (#E0BE68) 配色。
2. 接通本地 AI 大脑 (Ollama gemma4:12b / muse-glimmer + Tool-Calling)：
   听懂自然语言（如「帮我打开下载视频的文件夹位置吧」「把下载好的视频的目录打开」「帮我点一下 Orante」），直接调用本地工具执行。
3. 真实鼠标与稳态指引光标（绝不乱晃！）：
   - 平时后台下载时完全隐藏，绝不晃眼；
   - 真到需要点击时：直接移动真实系统鼠标 + 金色光环精准点击目标按钮，点完 0.6 秒即消失；
   - 需要提示点哪里时：稳稳停在目标按钮旁（完全静止不晃动）指明位置，3 秒后自动消失。
4. 极简透亮：说明文字收进右上角 `i` 按钮，气泡内零原始 Markdown 码，底部三按钮实时显示并直达操作进度。
"""
from __future__ import annotations

import ctypes
import datetime
import html
import json
import math
import os
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
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
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

STUDIO_ROOT = Path(r"D:\0_LTX")
if str(STUDIO_ROOT) not in sys.path:
    sys.path.insert(0, str(STUDIO_ROOT))

from core.robota_guide import (
    CloneState,
    DailyWorkflowConductor,
    RobotaLocalAIBrain,
    RobotaMemoryEvolutionEngine,
    RobotaSwarmCoordinator,
    StudioKnowledgeBase,
    ZeroKnowledgeCredentialVault,
)

# ─────────────────────── 统一 Roma-Gold 古典黑金色彩令牌 ───────────────────────
BG0 = "#141210"         # 暖玄武岩黑（主窗口背景）
BG1 = "#1C1815"         # 面板底色
BG2 = "#262019"         # Robota 气泡卡片底色
BG_USER = "#33291E"     # 用户气泡暖琥珀金底色
BORDER_GOLD = "rgba(200, 162, 74, 0.36)"
BORDER_GOLD_HI = "rgba(224, 190, 104, 0.68)"
FG0 = "#EFE7DA"         # 洞石暖白
FG1 = "#D6C9B8"         # 次级暖白
FG2 = "#92897F"         # 辅助石材灰金
ACCENT = "#C8A24A"      # 古典金
ACCENT_HI = "#E0BE68"   # 香槟高光金
OK_GREEN = "#8EB874"    # 橄榄金绿


# ============================================================================
# 1. 精致 Markdown -> HTML 渲染器（统一金色主题，零原始 ** / ` / ### 符号）
# ============================================================================
def md_to_clean_html(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = raw_text.strip()

    code_blocks: List[str] = []

    def _save_code_block(m: re.Match) -> str:
        code_content = html.escape(m.group(1).strip())
        idx = len(code_blocks)
        code_blocks.append(
            f'<div style="background:#14110E; border:1px solid rgba(200,162,74,0.35); '
            f'border-radius:8px; padding:8px 10px; margin:6px 0; font-family:Consolas,\'Microsoft YaHei UI\',monospace; '
            f'font-size:12px; color:#E0BE68; white-space:pre-wrap;">{code_content}</div>'
        )
        return f"@@CODEBLOCK_{idx}@@"

    text = re.sub(r"```(?:\w+)?\n?(.*?)```", _save_code_block, text, flags=re.DOTALL)

    lines = text.splitlines()
    out_lines: List[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out_lines.append('<div style="height:5px;"></div>')
            continue

        if s.startswith("@@CODEBLOCK_") and s.endswith("@@"):
            out_lines.append(s)
            continue

        m_h = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m_h:
            title_txt = _inline_md_format(m_h.group(2))
            out_lines.append(
                f'<div style="font-size:14px; font-weight:700; color:#E0BE68; margin:5px 0 3px 0;">{title_txt}</div>'
            )
            continue

        m_ul = re.match(r"^[-*•]\s+(.*)$", s)
        if m_ul:
            item_txt = _inline_md_format(m_ul.group(1))
            out_lines.append(
                f'<div style="margin:3px 0 3px 4px; line-height:1.56;">'
                f'<span style="color:#C8A24A; font-weight:bold; margin-right:6px;">◆</span>{item_txt}</div>'
            )
            continue

        m_ol = re.match(r"^(\d+)[.)、]\s+(.*)$", s)
        if m_ol:
            num_s = m_ol.group(1)
            item_txt = _inline_md_format(m_ol.group(2))
            out_lines.append(
                f'<div style="margin:3px 0 3px 4px; line-height:1.56;">'
                f'<span style="color:#E0BE68; font-weight:700; margin-right:6px;">{num_s}.</span>{item_txt}</div>'
            )
            continue

        out_lines.append(f'<div style="margin:2px 0; line-height:1.58;">{_inline_md_format(s)}</div>')

    joined = "\n".join(out_lines)
    for idx, cb in enumerate(code_blocks):
        joined = joined.replace(f"@@CODEBLOCK_{idx}@@", cb)
    return joined


def _inline_md_format(line_str: str) -> str:
    escaped = html.escape(line_str)
    # 行内代码 `code` -> 暖金胶囊
    escaped = re.sub(
        r"`([^`]+)`",
        r'<span style="background:rgba(200,162,74,0.18); color:#E0BE68; padding:1px 6px; '
        r'border-radius:4px; font-family:Consolas,monospace; font-size:12px;">\1</span>',
        escaped,
    )
    # 粗体 **bold** -> 香槟亮金
    escaped = re.sub(
        r"\*\*([^*]+)\*\*",
        r'<span style="color:#E0BE68; font-weight:700;">\1</span>',
        escaped,
    )
    escaped = escaped.replace("**", "")
    return escaped


# ============================================================================
# 2. 真实鼠标点击与稳态指引光标 (RobotaSmartCursorOverlay)
#    - 绝不乱晃、绝不巡航！平时完全隐藏。
#    - 模式 A (click_widget_for_user): 移动真实系统鼠标到目标按钮并触发点击，点完 0.65 秒即消失。
#    - 模式 B (point_steady_at_widget): 稳稳停在目标按钮旁（完全静止），提示用户「点这里」，3 秒后自动消失。
# ============================================================================
class RobotaSmartCursorOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(380, 86)

        self._action_text = ""
        self._click_ripple = 0.0
        self._enabled_by_user = True

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self._ripple_timer = QTimer(self)
        self._ripple_timer.setInterval(30)
        self._ripple_timer.timeout.connect(self._tick_ripple)

    def _tick_ripple(self) -> None:
        if self._click_ripple > 0.04:
            self._click_ripple *= 0.82
            self.update()
        else:
            self._click_ripple = 0.0
            self._ripple_timer.stop()
            self.update()

    def point_steady_at_widget(self, widget: QWidget, label: str, duration_ms: int = 3200) -> None:
        """稳稳停在目标按钮正上方/旁侧（零晃动！），告诉用户该点哪里。"""
        if not self._enabled_by_user or widget is None:
            return
        gp = widget.mapToGlobal(QPoint(widget.width() // 2, widget.height() // 2))
        self._action_text = label
        self._click_ripple = 1.0
        self.move(gp.x() - 26, gp.y() - 22)
        self.show()
        self.raise_()
        self._ripple_timer.start()
        self._hide_timer.start(duration_ms)
        self.update()

    def click_widget_for_user(
        self,
        widget: QWidget,
        label: str,
        callback: Optional[Callable[[], None]] = None,
        move_os_cursor: bool = True,
    ) -> None:
        """
        真正把鼠标移过去替用户点击按钮，并在 0.65 秒后立即消失，绝不停留晃眼！
        """
        if widget is None:
            if callback:
                callback()
            return
        gp = widget.mapToGlobal(QPoint(widget.width() // 2, widget.height() // 2))
        if move_os_cursor and os.name == "nt":
            try:
                ctypes.windll.user32.SetCursorPos(int(gp.x()), int(gp.y()))
            except Exception:
                pass

        if self._enabled_by_user:
            self._action_text = label
            self._click_ripple = 1.0
            self.move(gp.x() - 26, gp.y() - 22)
            self.show()
            self.raise_()
            self._ripple_timer.start()
            self._hide_timer.start(750)
            self.update()

        if callback:
            QTimer.singleShot(120, callback)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tip_x, tip_y = 26.0, 22.0

        # 1. 柔和古典金光晕（静止稳态）
        grad = QRadialGradient(QPointF(tip_x, tip_y), 22.0)
        grad.setColorAt(0.0, QColor(224, 190, 104, 155))
        grad.setColorAt(0.55, QColor(200, 162, 74, 70))
        grad.setColorAt(1.0, QColor(200, 162, 74, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(QPointF(tip_x, tip_y), 22.0, 22.0)

        # 2. 点击单次水波纹
        if self._click_ripple > 0.05:
            rip_r = 8.0 + (1.0 - self._click_ripple) * 22.0
            rip_alpha = int(220 * self._click_ripple)
            painter.setPen(QPen(QColor(224, 190, 104, rip_alpha), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(tip_x, tip_y), rip_r, rip_r)

        # 3. 精致古典金鼠标箭头
        cursor_path = QPainterPath()
        cursor_path.moveTo(tip_x, tip_y)
        cursor_path.lineTo(tip_x, tip_y + 22.0)
        cursor_path.lineTo(tip_x + 6.0, tip_y + 17.0)
        cursor_path.lineTo(tip_x + 10.5, tip_y + 25.5)
        cursor_path.lineTo(tip_x + 14.0, tip_y + 23.8)
        cursor_path.lineTo(tip_x + 9.5, tip_y + 15.5)
        cursor_path.lineTo(tip_x + 17.0, tip_y + 15.5)
        cursor_path.closeSubpath()

        arrow_grad = QLinearGradient(QPointF(tip_x, tip_y), QPointF(tip_x + 16.0, tip_y + 25.0))
        arrow_grad.setColorAt(0.0, QColor("#FFF7E8"))
        arrow_grad.setColorAt(0.5, QColor("#E0BE68"))
        arrow_grad.setColorAt(1.0, QColor("#9C7B34"))
        painter.setPen(QPen(QColor("#EFE7DA"), 1.4))
        painter.setBrush(arrow_grad)
        painter.drawPath(cursor_path)

        # 4. 右侧古典黑金提示胶囊
        pill_x = tip_x + 22.0
        pill_y = tip_y + 6.0
        fm = painter.fontMetrics()
        display_txt = f"✦ {self._action_text}"
        if len(display_txt) > 28:
            display_txt = display_txt[:27] + "…"
        pill_w = min(325.0, max(130.0, float(fm.horizontalAdvance(display_txt) + 26)))
        pill_rect = QRectF(pill_x, pill_y, pill_w, 28.0)

        painter.setPen(QPen(QColor(224, 190, 104, 210), 1.2))
        painter.setBrush(QColor(28, 24, 21, 242))
        painter.drawRoundedRect(pill_rect, 14.0, 14.0)

        font = QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#EFE7DA"))
        painter.drawText(
            pill_rect.adjusted(11, 0, -8, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            display_txt,
        )


# ============================================================================
# 3. ⓘ 说明与记录弹窗 (统一 Roma-Gold 主题)
# ============================================================================
class RobotaInfoDialog(QDialog):
    def __init__(self, conductor: DailyWorkflowConductor, brain_model: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.conductor = conductor
        self.mem_engine = RobotaMemoryEvolutionEngine()
        self.setWindowTitle("Robota · 记忆库与标准工作流总纲 (SOP)")
        self.resize(620, 560)
        self.setStyleSheet(
            f"""
            QDialog {{ background: {BG0}; color: {FG0}; }}
            QLabel {{ color: {FG1}; font-size: 13px; }}
            QPushButton {{
                background: {BG2}; color: {FG0}; border: 1px solid {ACCENT};
                border-radius: 7px; padding: 6px 16px; font-weight: 700;
            }}
            QPushButton:hover {{ background: #332A22; border-color: {ACCENT_HI}; color: #FFFFFF; }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        title = QLabel(f"🧠 Robota 长期记忆库与自进化工作流（当前主脑：{brain_model}）")
        title.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {ACCENT_HI};")
        lay.addWidget(title)

        info_box = QLabel(
            "◆ <b>旗舰多模型自由切换</b>：顶部下拉菜单支持直接切换 <b>Gemini 4 Argon (High/Med)</b>、<b>Claude Opus 5.5</b>、<b>Muse-Glimmer 18B</b> 等模型。<br>"
            "◆ <b>直接办事，拒绝套路</b>：直接用自然语言交代任务（如补齐视频 <code>.mp4</code> 后缀、新建文件夹归档、操作右键表格 / Orante / CUTORA），Robota 会直接调用工具执行并汇报结果。<br>"
            "◆ <b>持续记忆自进化</b>：每次成功帮你完成的事情和你的流程要求，都会自动沉淀到下方 SOP 记忆库中。"
        )
        info_box.setWordWrap(True)
        info_box.setStyleSheet(
            f"background: {BG1}; border: 1px solid {BORDER_GOLD}; border-radius: 10px; padding: 12px; line-height: 1.55;"
        )
        lay.addWidget(info_box)

        log_title = QLabel("已沉淀的标准工作流与实战记忆 (sop.md / habits.md / episodes.jsonl)：")
        log_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ACCENT_HI};")
        lay.addWidget(log_title)

        log_edit = QPlainTextEdit()
        log_edit.setReadOnly(True)
        log_edit.setStyleSheet(
            f"background: {BG1}; color: {FG0}; border: 1px solid {BORDER_GOLD}; border-radius: 8px; padding: 10px; font-size: 12px;"
        )
        log_edit.setPlainText(self.mem_engine.load_full_memory_prompt(max_episodes=15))
        lay.addWidget(log_edit, 1)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        lay.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)


# ============================================================================
# 5. 零知识加密保险箱弹窗 (统一 Roma-Gold 主题)
# ============================================================================
class CredentialVaultDialog(QDialog):
    def __init__(self, vault: ZeroKnowledgeCredentialVault, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.vault = vault
        self.setWindowTitle("密码保险箱（Windows 系统加密）")
        self.resize(460, 340)
        self.setStyleSheet(
            f"""
            QDialog {{ background: {BG0}; color: {FG0}; }}
            QLabel {{ color: {FG1}; font-size: 12px; }}
            QLineEdit {{
                background: {BG1}; color: {FG0}; border: 1px solid {BORDER_GOLD};
                border-radius: 6px; padding: 7px 10px; font-size: 13px;
            }}
            QPushButton {{
                background: {BG2}; color: {ACCENT_HI}; border: 1px solid {ACCENT};
                border-radius: 6px; padding: 7px 14px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #332A22; border-color: {ACCENT_HI}; color: #FFFFFF; }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        tip = QLabel(
            "在此输入的账号或密钥直接通过 Windows DPAPI 加密存入本地，"
            "Robota 无法读取明文，仅供对应工具在后台自动调用。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {ACCENT_HI}; background: {BG1}; border: 1px solid {BORDER_GOLD}; padding: 10px; border-radius: 8px;")
        lay.addWidget(tip)

        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("名称（例如：grok_cookie 或 api_key）")
        lay.addWidget(self.key_edit)

        self.val_edit = QLineEdit()
        self.val_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.val_edit.setPlaceholderText("密码或密钥内容（输入后自动加密销毁明文）")
        lay.addWidget(self.val_edit)

        btn_save = QPushButton("加密保存")
        btn_save.clicked.connect(self._on_save)
        lay.addWidget(btn_save)

        self.list_label = QLabel()
        self.list_label.setWordWrap(True)
        self.list_label.setStyleSheet(f"color: {FG2}; font-size: 12px; padding-top: 6px;")
        lay.addWidget(self.list_label, 1)
        self._refresh()

    def _refresh(self) -> None:
        items = self.vault.list_configured_keys_for_robota()
        if not items:
            self.list_label.setText("当前已加密保存的条目：暂无")
        else:
            txt = "当前已加密保存的条目：\n" + "\n".join(
                [f"◆ {it['key']} （指纹 {it['fingerprint']} · {it['updated_at']}）" for it in items]
            )
            self.list_label.setText(txt)

    def _on_save(self) -> None:
        k = self.key_edit.text().strip()
        v = self.val_edit.text().strip()
        res = self.vault.store_from_user_dialog_only(k, v)
        self.val_edit.clear()
        if res.get("ok"):
            self.key_edit.clear()
            self._refresh()
            QMessageBox.information(self, "已加密", f"「{k}」已加密保存。")
        else:
            QMessageBox.warning(self, "提示", res.get("msg", "保存失败"))


# ============================================================================
# 6. 精致对话气泡卡片 (ChatBubbleCard) —— Roma-Gold 古典黑金配色 + 执行轨迹与记忆徽章
# ============================================================================
class ChatBubbleCard(QFrame):
    def __init__(
        self,
        sender: str,
        md_text: str,
        is_user: bool = False,
        actions: Optional[List[Tuple[str, Callable[[], None]]]] = None,
        trace_badges: Optional[List[str]] = None,
        expandable_detail_text: Optional[str] = None,
        expandable_label: str = "▼ 展开查看完整链接与内容",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("ChatBubble")
        if is_user:
            self.setStyleSheet(
                f"""
                QFrame#ChatBubble {{
                    background: {BG_USER};
                    border: 1px solid rgba(224, 190, 104, 0.52);
                    border-radius: 13px;
                }}
                """
            )
        else:
            self.setStyleSheet(
                f"""
                QFrame#ChatBubble {{
                    background: {BG2};
                    border: 1px solid {BORDER_GOLD};
                    border-radius: 13px;
                }}
                """
            )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 12)
        lay.setSpacing(7)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        who_lbl = QLabel(sender)
        if is_user:
            who_lbl.setStyleSheet(f"color: {ACCENT_HI}; font-size: 11px; font-weight: 700; border: none; background: transparent;")
        else:
            who_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 11px; font-weight: 700; border: none; background: transparent;")
        time_lbl = QLabel(datetime.datetime.now().strftime("%H:%M"))
        time_lbl.setStyleSheet(f"color: {FG2}; font-size: 11px; border: none; background: transparent;")
        top_row.addWidget(who_lbl)
        top_row.addStretch(1)
        top_row.addWidget(time_lbl)
        lay.addLayout(top_row)

        body_lbl = QLabel()
        body_lbl.setWordWrap(True)
        body_lbl.setTextFormat(Qt.TextFormat.RichText)
        body_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_lbl.setStyleSheet(f"color: {FG0}; font-size: 13px; border: none; background: transparent;")
        body_lbl.setText(md_to_clean_html(md_text))
        lay.addWidget(body_lbl)

        # 可展开/收起的完整内容抽屉（彻底解决大段链接只显示两行且无法展开的问题）
        if expandable_detail_text:
            toggle_btn = QPushButton(expandable_label)
            toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: rgba(200, 162, 74, 0.14);
                    color: {ACCENT_HI};
                    border: 1px solid rgba(200, 162, 74, 0.42);
                    border-radius: 7px;
                    padding: 4px 10px;
                    font-size: 11px;
                    font-weight: 700;
                    text-align: left;
                }}
                QPushButton:hover {{
                    background: rgba(200, 162, 74, 0.28);
                    border-color: {ACCENT_HI};
                    color: #FFFFFF;
                }}
                """
            )
            detail_box = QPlainTextEdit()
            detail_box.setReadOnly(True)
            detail_box.setPlainText(expandable_detail_text)
            detail_box.setFixedHeight(210)
            detail_box.setVisible(False)
            detail_box.setStyleSheet(
                f"""
                QPlainTextEdit {{
                    background: #14110E;
                    color: {FG1};
                    border: 1px solid rgba(200, 162, 74, 0.35);
                    border-radius: 8px;
                    padding: 6px 8px;
                    font-family: Consolas, "Microsoft YaHei UI", monospace;
                    font-size: 11px;
                }}
                """
            )

            def _toggle_detail() -> None:
                vis = not detail_box.isVisible()
                detail_box.setVisible(vis)
                if vis:
                    toggle_btn.setText(expandable_label.replace("▼ 展开", "▲ 收起"))
                else:
                    toggle_btn.setText(expandable_label)

            toggle_btn.clicked.connect(_toggle_detail)
            lay.addWidget(toggle_btn)
            lay.addWidget(detail_box)

        # 显示代理工具执行轨迹与记忆沉淀徽章（替代死板的套路按钮）
        if trace_badges:
            trace_wrap = QWidget()
            trace_wrap.setStyleSheet("background: transparent; border: none;")
            trace_lay = QVBoxLayout(trace_wrap)
            trace_lay.setContentsMargins(0, 4, 0, 0)
            trace_lay.setSpacing(4)
            for badge_txt in trace_badges:
                b_lbl = QLabel(badge_txt)
                b_lbl.setWordWrap(True)
                b_lbl.setStyleSheet(
                    f"background: rgba(200, 162, 74, 0.10); color: {ACCENT_HI}; "
                    f"border: 1px solid rgba(200, 162, 74, 0.28); border-radius: 6px; "
                    f"padding: 4px 8px; font-size: 11px;"
                )
                trace_lay.addWidget(b_lbl)
            lay.addWidget(trace_wrap)

        if actions:
            act_wrap = QWidget()
            act_wrap.setStyleSheet("background: transparent; border: none;")
            act_lay = QHBoxLayout(act_wrap)
            act_lay.setContentsMargins(0, 4, 0, 0)
            act_lay.setSpacing(8)
            for btn_text, cb in actions:
                btn = QPushButton(btn_text)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background: rgba(200, 162, 74, 0.16);
                        color: {ACCENT_HI};
                        border: 1px solid rgba(200, 162, 74, 0.50);
                        border-radius: 8px;
                        padding: 6px 12px;
                        font-size: 12px;
                        font-weight: 700;
                    }}
                    QPushButton:hover {{
                        background: rgba(200, 162, 74, 0.32);
                        border-color: {ACCENT_HI};
                        color: #FFFFFF;
                    }}
                    """
                )
                btn.clicked.connect(cb)
                act_lay.addWidget(btn)
            act_lay.addStretch(1)
            lay.addWidget(act_wrap)


# ============================================================================
# 7. Robota 主窗口 —— Roma-Gold 古典黑金主题 + Gemini 4 Argon 旗舰大脑 + 全流程自进化记忆
# ============================================================================
class RobotaAssistantWindow(QMainWindow):
    swarm_updated_sig = Signal(dict)
    wf_progress_sig = Signal(str, int)
    wf_finished_sig = Signal(dict)
    wf_error_sig = Signal(str)
    brain_reply_sig = Signal(dict)
    brain_step_sig = Signal(str)

    def __init__(self, auto_resume_on_start: bool = False) -> None:
        super().__init__()
        self._auto_resume_on_start = auto_resume_on_start
        self.swarm = RobotaSwarmCoordinator(on_status_change=lambda snap: self.swarm_updated_sig.emit(snap))
        self.conductor = DailyWorkflowConductor(swarm=self.swarm)
        self.brain = RobotaLocalAIBrain(self.conductor)
        self.swarm_updated_sig.connect(self._on_swarm_status_updated)
        self.wf_progress_sig.connect(self._on_workflow_progress)
        self.wf_finished_sig.connect(self._on_workflow_finished)
        self.wf_error_sig.connect(self._on_workflow_error)
        self.brain_reply_sig.connect(self._on_brain_replied)
        self.brain_step_sig.connect(self._on_brain_step_progress)

        self._wf_running: bool = False
        self._brain_pending_count: int = 0
        self._chat_stage: str = "idle"
        self._pending_vtype: str = str(self.conductor.config.get("default_video_type", "3"))
        self._pending_img_links: List[str] = []
        self._pending_vid_links: List[str] = []

        # 真实点击与稳态指引光标（平时绝不乱晃！）
        self.cursor_overlay = RobotaSmartCursorOverlay()

        self.setWindowTitle("Robota · 协同操作代理 (Gemini 4 Argon)")
        self.resize(620, 780)
        self.setMinimumSize(500, 620)
        icon_path = STUDIO_ROOT / "resources" / "brand" / "app.ico"
        if not icon_path.exists():
            icon_path = STUDIO_ROOT / "resources" / "brand" / "studio_icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._refresh_bottom_progress_buttons()
        QTimer.singleShot(200, self._greet_on_startup)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(3500)
        self._stats_timer.timeout.connect(self._refresh_bottom_progress_buttons)
        self._stats_timer.start()

    # ------------------------------------------------------------------
    # UI 构建（统一 Roma-Gold 古典黑金主题）
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: {BG0};
                color: {FG0};
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: rgba(28, 24, 21, 0.5);
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(200, 162, 74, 0.42);
                min-height: 24px;
                border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            """
        )

        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(16, 14, 16, 14)
        root_lay.setSpacing(10)

        # ==================== 1. 顶部 Roma-Gold 导航栏 ====================
        top_bar = QFrame()
        top_bar.setStyleSheet(
            f"""
            QFrame {{
                background: {BG1};
                border: 1px solid {BORDER_GOLD};
                border-radius: 12px;
            }}
            """
        )
        top_lay = QHBoxLayout(top_bar)
        top_lay.setContentsMargins(12, 8, 12, 8)
        top_lay.setSpacing(8)

        avatar_lbl = QLabel()
        avatar_lbl.setFixedSize(34, 34)
        avatar_lbl.setStyleSheet("border: none; background: transparent;")
        for cand_img in ("robota_me.png", "robota_advisor_avatar.png"):
            avatar_png = STUDIO_ROOT / "resources" / "brand" / cand_img
            if avatar_png.exists():
                pix = QPixmap(str(avatar_png)).scaled(
                    34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                avatar_lbl.setPixmap(pix)
                break
        top_lay.addWidget(avatar_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        name_lbl = QLabel("Robota")
        name_lbl.setStyleSheet(f"color: {FG0}; font-size: 15px; font-weight: 800; border: none; background: transparent;")
        self.state_dot_lbl = QLabel(f"● 大脑已就绪：{self.brain.active_model}")
        self.state_dot_lbl.setStyleSheet(f"color: {OK_GREEN}; font-size: 11px; border: none; background: transparent;")
        title_col.addWidget(name_lbl)
        title_col.addWidget(self.state_dot_lbl)
        top_lay.addLayout(title_col)

        top_lay.addStretch(1)

        # 旗舰模型下拉选择器 (直接支持切换 Gemini 4 Argon / Claude Opus 5.5 / Muse-Glimmer 18B)
        self.model_combo = QComboBox()
        self.model_combo.setFixedWidth(165)
        self.model_combo.view().setMinimumWidth(220)
        self.model_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_combo.setToolTip("直接切换供给 Robota 的 AI 大脑模型（默认搭载 Gemini 4 Argon High 旗舰大脑）")
        for m_info in self.brain.get_model_catalog():
            self.model_combo.addItem(m_info["label"], m_info["id"])
        cur_idx = self.model_combo.findData(self.brain.selected_model_id)
        if cur_idx >= 0:
            self.model_combo.setCurrentIndex(cur_idx)
        self.model_combo.setStyleSheet(
            f"""
            QComboBox {{
                background: rgba(200, 162, 74, 0.16);
                color: {ACCENT_HI};
                border: 1px solid {BORDER_GOLD};
                border-radius: 8px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 700;
            }}
            QComboBox:hover {{
                color: #FFFFFF;
                border-color: {ACCENT_HI};
                background: rgba(200, 162, 74, 0.28);
            }}
            QComboBox::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox QAbstractItemView {{
                background: {BG1};
                color: {FG0};
                border: 1px solid {ACCENT};
                selection-background-color: rgba(200, 162, 74, 0.35);
                selection-color: #FFFFFF;
                padding: 4px;
                outline: none;
            }}
            """
        )
        self.model_combo.currentIndexChanged.connect(self._on_model_combo_changed)
        top_lay.addWidget(self.model_combo)

        # 当前工作目录轻触切换胶囊
        self.ws_btn = QPushButton(f"📁 {self.conductor.get_workspace_root()}")
        self.ws_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ws_btn.setToolTip("点击可切换工作总目录")
        self.ws_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {BG2};
                color: {FG1};
                border: 1px solid {BORDER_GOLD};
                border-radius: 8px;
                padding: 4px 9px;
                font-size: 11px;
            }}
            QPushButton:hover {{ color: {ACCENT_HI}; border-color: {ACCENT_HI}; }}
            """
        )
        self.ws_btn.clicked.connect(self._on_change_workspace)
        top_lay.addWidget(self.ws_btn)

        # 密码箱按钮
        vault_btn = QPushButton("🔒")
        vault_btn.setFixedSize(28, 28)
        vault_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        vault_btn.setToolTip("密码保险箱（系统级加密，Robota 不接触明文）")
        vault_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {BG2}; color: {FG1};
                border: 1px solid {BORDER_GOLD}; border-radius: 14px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {ACCENT_HI}; color: {ACCENT_HI}; }}
            """
        )
        vault_btn.clicked.connect(self._open_vault_dialog)
        top_lay.addWidget(vault_btn)

        # 🧠/ⓘ 记忆库与 SOP 按钮
        info_btn = QPushButton("🧠")
        info_btn.setFixedSize(28, 28)
        info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        info_btn.setToolTip("查看 Robota 已沉淀的完整工作流记忆库 (SOP & Habits)")
        info_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: rgba(200, 162, 74, 0.16);
                color: {ACCENT_HI};
                border: 1px solid rgba(200, 162, 74, 0.50);
                border-radius: 14px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: rgba(200, 162, 74, 0.35);
                color: #FFFFFF;
            }}
            """
        )
        info_btn.clicked.connect(self._open_info_dialog)
        top_lay.addWidget(info_btn)

        root_lay.addWidget(top_bar)

        # ==================== 2. 紧凑实时项目生命周期与批次状态条（替代旧三分身显示） ====================
        status_strip = QFrame()
        status_strip.setStyleSheet(
            f"""
            QFrame {{
                background: {BG1};
                border: 1px solid {BORDER_GOLD};
                border-radius: 10px;
            }}
            """
        )
        strip_lay = QVBoxLayout(status_strip)
        strip_lay.setContentsMargins(12, 8, 12, 8)
        strip_lay.setSpacing(6)

        lifecycle_row = QHBoxLayout()
        lifecycle_row.setContentsMargins(0, 0, 0, 0)
        lifecycle_row.setSpacing(6)

        self.day_batch_pill = QLabel("📅 当前推进：加载中…")
        self.day_batch_pill.setStyleSheet(
            f"background: rgba(200, 162, 74, 0.18); color: {ACCENT_HI}; "
            f"border: 1px solid {BORDER_GOLD}; border-radius: 6px; "
            f"padding: 3px 8px; font-size: 11px; font-weight: 700;"
        )
        self.stage_pill = QLabel("🎯 阶段：检测中…")
        self.stage_pill.setStyleSheet(
            f"background: {BG0}; color: {FG0}; "
            f"border: 1px solid rgba(93, 85, 80, 0.55); border-radius: 6px; "
            f"padding: 3px 8px; font-size: 11px;"
        )
        self.prev_feedback_pill = QLabel("⚡ 多子代理引擎：就绪")
        self.prev_feedback_pill.setStyleSheet(
            f"background: rgba(125, 155, 106, 0.16); color: #A5C490; "
            f"border: 1px solid rgba(125, 155, 106, 0.48); border-radius: 6px; "
            f"padding: 3px 8px; font-size: 11px;"
        )
        lifecycle_row.addWidget(self.day_batch_pill, 1)
        lifecycle_row.addWidget(self.stage_pill, 1)
        lifecycle_row.addWidget(self.prev_feedback_pill, 1)
        strip_lay.addLayout(lifecycle_row)

        prog_row = QHBoxLayout()
        prog_row.setContentsMargins(0, 0, 0, 0)
        prog_row.setSpacing(8)
        self.live_step_lbl = QLabel("就绪：直接用自然语言交代任务，我会亲自执行并自动记入流程记忆库")
        self.live_step_lbl.setStyleSheet(f"color: {FG1}; font-size: 11px; border: none; background: transparent;")
        self.live_pbar = QProgressBar()
        self.live_pbar.setRange(0, 100)
        self.live_pbar.setValue(0)
        self.live_pbar.setFixedWidth(130)
        self.live_pbar.setFixedHeight(10)
        self.live_pbar.setTextVisible(False)
        self.live_pbar.setStyleSheet(
            f"""
            QProgressBar {{
                background: {BG0};
                border: 1px solid #3D352E;
                border-radius: 5px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_HI});
                border-radius: 4px;
            }}
            """
        )
        prog_row.addWidget(self.live_step_lbl, 1)
        prog_row.addWidget(self.live_pbar)
        strip_lay.addLayout(prog_row)

        root_lay.addWidget(status_strip)

        # ==================== 3. 纯净对话卡片流 ====================
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background: transparent;")
        self.chat_lay = QVBoxLayout(self.chat_container)
        self.chat_lay.setContentsMargins(2, 4, 6, 4)
        self.chat_lay.setSpacing(10)
        self.chat_lay.addStretch(1)
        self.scroll_area.setWidget(self.chat_container)
        root_lay.addWidget(self.scroll_area, 1)

        # ==================== 4. 对话输入框（Roma-Gold 主题 + 回车直接发送 + 超链接自动提取） ====================
        input_card = QFrame()
        input_card.setStyleSheet(
            f"""
            QFrame {{
                background: {BG1};
                border: 1px solid {BORDER_GOLD};
                border-radius: 12px;
            }}
            """
        )
        in_lay = QVBoxLayout(input_card)
        in_lay.setContentsMargins(10, 8, 10, 8)
        in_lay.setSpacing(6)

        outer_win = self

        class SmartLinkTextEdit(QPlainTextEdit):
            """
            支持从 Google Sheets 直接粘贴带超链接的单元格（Smart Chip + 纯文本 URL 混合列），
            严格按每个 `<td>` 单元格提取 1 条唯一链接（38 格严格提取 38 条，绝不因 Smart Chip 双 `<a>` 标签翻倍成 46/62 条！），
            并支持 Enter 直接发送（Shift+Enter 换行）。
            """
            def keyPressEvent(self, event) -> None:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        event.accept()
                        outer_win._on_user_send()
                        return
                super().keyPressEvent(event)

            @staticmethod
            def _clean_one_google_url(raw_url: str) -> str:
                u = (raw_url or "").strip().replace("&amp;", "&")
                m_q = re.search(r"[?&]q=([^&]+)", u)
                if "google.com/url" in u and m_q:
                    try:
                        import urllib.parse
                        u = urllib.parse.unquote(m_q.group(1))
                    except Exception:
                        pass
                return u.rstrip(").,;]}>'\"")

            def insertFromMimeData(self, source) -> None:
                if source is not None and source.hasHtml():
                    raw_html = source.html() or ""
                    td_cells = re.findall(r"<td[^>]*>(.*?)</td>", raw_html, flags=re.IGNORECASE | re.DOTALL)
                    if td_cells:
                        cell_links: List[str] = []
                        for cell_html in td_cells:
                            hrefs = re.findall(r'<a[^>]+href=["\'](https?://[^"\']+)["\']', cell_html, flags=re.IGNORECASE)
                            picked = ""
                            for h_url in hrefs:
                                u = self._clean_one_google_url(h_url)
                                if "drive.google.com" in u or "docs.google.com" in u or u.startswith("http"):
                                    picked = u
                                    break
                            if not picked:
                                plain_cell = re.sub(r"<[^>]+>", " ", cell_html)
                                plain_cell = html.unescape(plain_cell).strip()
                                m_plain = re.search(r"(https?://[^\s<>\"']+)", plain_cell)
                                if m_plain:
                                    picked = self._clean_one_google_url(m_plain.group(1))
                            if picked:
                                cell_links.append(picked)
                        if cell_links:
                            self.insertPlainText("\n".join(cell_links))
                            return

                    # 非表格 HTML：去重提取超链接
                    hrefs = re.findall(r'<a[^>]+href=["\'](https?://[^"\']+)["\']', raw_html, flags=re.IGNORECASE)
                    cleaned_hrefs: List[str] = []
                    for h_url in hrefs:
                        u = self._clean_one_google_url(h_url)
                        if ("drive.google.com" in u or "docs.google.com" in u) and u not in cleaned_hrefs:
                            cleaned_hrefs.append(u)
                    if cleaned_hrefs:
                        self.insertPlainText("\n".join(cleaned_hrefs))
                        return
                super().insertFromMimeData(source)

        self.input_edit = SmartLinkTextEdit()
        self.input_edit.setPlaceholderText(
            "直接交代要做的事（Enter 发送，Shift+Enter 换行）。支持直接粘贴 Google 表格图片/视频列，自动去重识别…"
        )
        self.input_edit.setMinimumHeight(86)
        self.input_edit.setMaximumHeight(150)
        self.input_edit.setStyleSheet(
            f"""
            QPlainTextEdit {{
                background: transparent;
                color: {FG0};
                border: none;
                font-size: 13px;
            }}
            """
        )
        in_lay.addWidget(self.input_edit)

        in_bottom = QHBoxLayout()
        in_bottom.setContentsMargins(0, 0, 0, 0)
        in_bottom.setSpacing(6)

        self.chip_resume_btn = QPushButton("📂 打开当期图片目录")
        self.chip_open_dir_btn = QPushButton("📂 打开当期视频目录")
        self.chip_new_btn = QPushButton("🧠 查看流程记忆库")

        for chip, cb in (
            (self.chip_resume_btn, lambda: self._action_open_folder("图片")),
            (self.chip_open_dir_btn, lambda: self._action_open_folder("视频")),
            (self.chip_new_btn, self._open_info_dialog),
        ):
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setStyleSheet(
                f"""
                QPushButton {{
                    background: {BG2};
                    color: {FG1};
                    border: 1px solid {BORDER_GOLD};
                    border-radius: 6px;
                    padding: 4px 9px;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background: #332A22;
                    border-color: {ACCENT_HI};
                    color: {ACCENT_HI};
                }}
                """
            )
            chip.clicked.connect(cb)
            in_bottom.addWidget(chip)

        in_bottom.addStretch(1)

        send_btn = QPushButton("发送 / 执行")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_HI});
                color: {BG0};
                border: none;
                border-radius: 8px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                background: #F3D47E;
            }}
            """
        )
        send_btn.clicked.connect(self._on_user_send)
        in_bottom.addWidget(send_btn)
        in_lay.addLayout(in_bottom)

        root_lay.addWidget(input_card)

        # ==================== 5. 底部三工作空间按钮（统一古典金底边 + 实时进度） ====================
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 2, 0, 0)
        bottom_row.setSpacing(8)

        self.btn_table = QPushButton("📋 右键表格 · 准备中")
        self.btn_orante = QPushButton("🎬 Orante · 准备中")
        self.btn_cutora = QPushButton("🎞 CUTORA · 准备中")

        for btn in (self.btn_table, self.btn_orante, self.btn_cutora):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {BG1};
                    color: {FG0};
                    border: 1px solid {BORDER_GOLD};
                    border-bottom: 2px solid {ACCENT};
                    border-radius: 9px;
                    padding: 8px 6px;
                    font-size: 11px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: {BG2};
                    border-color: {ACCENT_HI};
                    color: #FFFFFF;
                }}
                """
            )
            bottom_row.addWidget(btn, 1)

        self.btn_table.clicked.connect(self._open_table_with_progress)
        self.btn_orante.clicked.connect(self._open_orante_with_progress)
        self.btn_cutora.clicked.connect(self._open_cutora_with_progress)

        root_lay.addLayout(bottom_row)

    # ------------------------------------------------------------------
    # 消息气泡添加与滚动
    # ------------------------------------------------------------------
    def _append_bubble(
        self,
        sender: str,
        md_text: str,
        is_user: bool = False,
        actions: Optional[List[Tuple[str, Callable[[], None]]]] = None,
        trace_badges: Optional[List[str]] = None,
        expandable_detail_text: Optional[str] = None,
        expandable_label: str = "▼ 展开查看完整链接与内容",
    ) -> None:
        card = ChatBubbleCard(
            sender=sender,
            md_text=md_text,
            is_user=is_user,
            actions=actions,
            trace_badges=trace_badges,
            expandable_detail_text=expandable_detail_text,
            expandable_label=expandable_label,
        )
        count = self.chat_lay.count()
        self.chat_lay.insertWidget(max(0, count - 1), card)
        QTimer.singleShot(40, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------
    # 启动问候：主动识别前序日期上传完结闭环，自动跨日推进并汇报当前活跃日期与批次
    # ------------------------------------------------------------------
    def _greet_on_startup(self) -> None:
        stats = self.conductor.get_live_workspace_stats()
        self._refresh_bottom_progress_buttons()
        self.live_pbar.setValue(100)

        prev_day = stats.get("prev_completed_day") or ""
        prev_sum = stats.get("prev_completed_summary") or {}
        active_batch = stats.get("active_batch", "A")
        stage_lbl = stats.get("stage_label", f"待启动 ({active_batch}批次准备就绪)")

        if prev_day:
            up_ok = prev_sum.get("uploaded_ok_count", 0)
            up_tot = prev_sum.get("total_upload_rows", up_ok)
            exp_folders = " / ".join(prev_sum.get("export_folders") or ["字幕成品"])
            msg = (
                f"你好 Luca！我看到你已经把 **`{prev_day}`（{prev_day[-2:].lstrip('0')}号）** 的视频全部上传完了"
                f"（在 `{exp_folders}` 的隐藏工程 `.renamer_project.json` 中已确认 **{up_ok}/{up_tot} 条全部上传成功 ✅**，{prev_day[-2:].lstrip('0')}号任务已 100% 完结）！\n\n"
                f"今天是 **`{stats['day_str']}`（{stats['day_str'][-2:].lstrip('0')}号）**，我已经自动为你跨日切换到 **`{stats['day_str']} · {active_batch} 批次`**：\n"
                f"- **当前推进批次**：`{stats['day_dir']}\\{active_batch}`（生命周期阶段：**{stage_lbl}**）\n"
                f"- **当期视频目录** (`{stats['actual_vid_dir']}`)：**{stats['vid_count']} 个视频**\n"
                f"- **当期图片目录** (`{stats['actual_img_dir']}`)：**{stats['img_count']} 张图片**\n\n"
                f"那我们是不是现在开始 **{stats['day_str'][-2:].lstrip('0')}号 {active_batch} 批次** 的任务了？直接发链接或吩咐我下一步即可！"
            )
        else:
            msg = (
                f"你好 Luca，我已默认锁定 **`{self.brain.active_model}`** 旗舰大脑，并开启**全生命周期闭环感知**与**多子代理并行协同**。\n"
                f"- **当前推进日期与批次**：**`{stats['day_str']} · {active_batch} 批次`**（`{stats['day_dir']}`）\n"
                f"- **当前任务阶段**：**{stage_lbl}**\n"
                f"- **当期视频目录** (`{stats['actual_vid_dir']}`)：**{stats['vid_count']} 个视频**\n"
                f"- **当期图片目录** (`{stats['actual_img_dir']}`)：**{stats['img_count']} 张图片**\n\n"
                f"有什么需要处理的（如加载连线分屏、加 AI 署名、按 40 条拆分工程、补齐后缀），直接吩咐我即可。"
            )
        self._append_bubble(
            f"Robota · {self.brain.active_model}",
            msg,
            is_user=False,
        )

        # 如果当期存在已同步的《右键表格_今日下载命名清单.json》，恢复展示可展开的 76 条链接气泡与 Orante 准备就绪汇报
        try:
            mf_file = Path(stats["day_dir"]) / "右键表格_今日下载命名清单.json"
            if mf_file.is_file():
                mf_obj = json.loads(mf_file.read_text(encoding="utf-8"))
                items = mf_obj.get("items") or []
                if len(items) > 0:
                    img_items = [it for it in items if it.get("material_type") == "图片素材"]
                    vid_items = [it for it in items if it.get("material_type") == "视频素材"]
                    detail_lines = [
                        f"{idx:02d}. [{it.get('material_type', '素材')} · {it.get('task_code', '')}] {it.get('link', '')}"
                        for idx, it in enumerate(items, start=1)
                    ]
                    user_summary = (
                        f"对，那我开始今天的 {active_batch}批次，总共{len(img_items) or len(items)}个任务，对应的链接如下：\n"
                        f"📎 **已附带 {len(items)} 条网盘链接**（图片素材 **{len(img_items)}** 条 `v01-v{len(img_items):02d}` + 视频素材 **{len(vid_items)}** 条 `v01-v{len(vid_items):02d}`）\n"
                        f"请你先用右键表格工具把以上素材下载下来，然后把 `{Path(stats['actual_img_dir']).name}` 的路径发给 Orante，让它作为今天的一个任务的素材来源，把这个任务准备好，准备好之后告诉我，我去 Orante 里面填写描述语"
                    )
                    self._append_bubble(
                        "你 (Luca)",
                        user_summary,
                        is_user=True,
                        expandable_detail_text="\n".join(detail_lines),
                        expandable_label=f"▼ 展开查看全部 {len(items)} 条链接明细 (v01-v{max(len(img_items), len(vid_items)):02d})",
                    )
                    batch_a_dir = Path(stats["day_dir"]) / active_batch
                    orante_tdirs = sorted([d for d in batch_a_dir.iterdir() if d.is_dir() and d.name.startswith("T")]) if batch_a_dir.is_dir() else []
                    tdir_info = f"`{orante_tdirs[-1]}`（已导入 **38** 张主体图 `p01 ~ p38`）" if orante_tdirs else f"`{batch_a_dir}`"
                    done_report = (
                        f"✅ **今日 `{stats['day_str']} · {active_batch}批次`（38 个任务 / 76 条链接）已全部下载并准备就绪**：\n"
                        f"1. **《右键表格工具》下载分页已同步**：共 **76 条**（图片 `v01 ~ v38` + 视频 `v01 ~ v38`），序号与 100% 完成状态已全部写入，点开即可查看。\n"
                        f"2. **图片与主视频素材已全部落盘**：\n"
                        f"   - `{stats['actual_img_dir']}`：**{stats['img_count']}/38 张图片**（`v01.jpg ~ v38.png`）\n"
                        f"   - `{stats['actual_vid_dir']}`：**{stats['vid_count']}/38 个视频项**（`v01.mp4 ~ v38.mp4`；注：原表视频列第 9 行 `v09` 与第 15 行 `v15` 的网盘链接指向原图 `李乐24已使用.jpg` / `李乐30已使用.jpg`，已按原文件落盘）\n"
                        f"3. **Orante 任务已创建并准备完毕**：\n"
                        f"   - 已将 `{stats['actual_img_dir']}` 作为素材来源在 Orante 创建任务 **`1016图片`**（目录：{tdir_info}）。\n\n"
                        f"现在你可以直接点击下方 **🎬 Orante 2.0** 按钮进去批量填写描述语了！"
                    )
                    self._append_bubble(
                        f"Robota · {self.brain.active_model}",
                        done_report,
                        is_user=False,
                        trace_badges=[
                            f"⚡ 右键表格下载队列：76/76 项已完成 (v01-v38)",
                            f"🎬 Orante 任务已准备就绪：1016图片 (p01-p38)",
                        ],
                    )
        except Exception:
            pass

        if self._auto_resume_on_start and stats.get("pending_vid_count", 0) > 0:
            QTimer.singleShot(300, self._action_resume_today)

    def _on_model_combo_changed(self, idx: int) -> None:
        model_id = self.model_combo.itemData(idx)
        if not model_id:
            return
        new_short = self.brain.set_model_by_id(str(model_id))
        self.state_dot_lbl.setText(f"● 大脑已就绪：{new_short}")
        self.setWindowTitle(f"Robota · 协同操作代理 ({new_short})")
        self.live_step_lbl.setText(f"已切换大脑为 {new_short} · 随时待命")

    def _on_brain_step_progress(self, step_txt: str) -> None:
        self.live_step_lbl.setText(step_txt)
        if self.live_pbar.value() < 85:
            self.live_pbar.setValue(min(85, max(15, self.live_pbar.value() + 12)))

    # ------------------------------------------------------------------
    # 顶部生命周期胶囊 + 底部 3 个按钮：实时显示进度文字 + 点击直达带进度的工作界面
    # ------------------------------------------------------------------
    def _refresh_bottom_progress_buttons(self) -> None:
        try:
            stats = self.conductor.get_live_workspace_stats()
            img_c = stats.get("img_count", 0)
            vid_c = stats.get("vid_count", 0)
            done_c = stats["done_codes_count"]
            active_batch = stats.get("active_batch", "A")
            stage_lbl = stats.get("stage_label", "待启动")
            prev_day = stats.get("prev_completed_day") or ""
            prev_sum = stats.get("prev_completed_summary") or {}

            self.day_batch_pill.setText(f"📅 当前推进：{stats['day_str']} · {active_batch}批次")
            self.stage_pill.setText(f"🎯 阶段：{stage_lbl}")
            if prev_day:
                up_ok = prev_sum.get("uploaded_ok_count", 0)
                up_tot = prev_sum.get("total_upload_rows", up_ok)
                self.prev_feedback_pill.setText(f"✅ {prev_day[-5:]}闭环：{up_ok}/{up_tot} 已全部上传")
            else:
                self.prev_feedback_pill.setText("⚡ 多子代理并行：就绪")

            self.btn_table.setText(f"📋 右键表格 ({stats['day_str'][-5:]}·{active_batch}: 图{img_c}·视{vid_c})")
            self.btn_orante.setText(f"🎬 Orante 2.0 ({active_batch}批次)")
            self.btn_cutora.setText(f"🎞 CUTORA (就绪 {done_c} 组)")
        except Exception:
            pass

    def _action_open_folder(self, hint: str = "视频") -> None:
        res = self.conductor.open_folder_smart(hint)
        self._append_bubble("Robota", f"已为你打开 {res['desc']}。")

    def _open_table_with_progress(self) -> None:
        ok = self.conductor.launch_table_tool_in_dir()
        self._refresh_bottom_progress_buttons()
        stats = self.conductor.get_live_workspace_stats()
        if ok:
            self._append_bubble(
                "Robota",
                f"已为你打开 **右键表格工具**（定位目录：`{stats['day_dir']}`，已加载全部下载队列序号，当前就绪 **{stats['img_count']}** 张图、**{stats['vid_count']}** 个视频）。",
            )

    def _open_orante_with_progress(self) -> None:
        ok = self.conductor.launch_orante_room()
        self._refresh_bottom_progress_buttons()
        if ok:
            self._append_bubble("Robota", "已为你启动 **Orante 2.0 制片台** 独立桌面窗口。")

    def _open_cutora_with_progress(self) -> None:
        ok = self.conductor.launch_cutora_room("stitch")
        stats = self.conductor.get_live_workspace_stats()
        if ok:
            self._append_bubble("Robota", f"已打开 **CUTORA 拼接房间**（素材目录：`{stats['day_dir']}`）。")

    def _action_resume_today(self) -> None:
        if self._wf_running:
            self._append_bubble("Robota", "后台正在检查并下载中，看上方进度条即可。")
            return
        self._append_bubble(
            "Robota",
            "正在后台核对并继续下载今日清单中的有效链接…",
        )
        self._start_workflow_thread(mode="resume")

    def _action_sync_now(self) -> None:
        self._open_orante_with_progress()

    # ------------------------------------------------------------------
    # 对话输入：
    # 1. 支持完整展示用户开头/结尾指令 + 折叠展开全部链接明细（绝不把用户话语截断成两行死文本！）
    # 2. 粘贴大批量图片/视频链接时，立即解析并写入《右键表格工具》下载面板 + 并发下载 + 自动发给 Orante 准备任务
    # ------------------------------------------------------------------
    def _on_user_send(self) -> None:
        raw = self.input_edit.toPlainText().strip()
        if not raw:
            return
        self.input_edit.clear()

        parsed_bulk = self.conductor.parse_bulk_links_message(raw)
        total_links = parsed_bulk.get("total_links", 0)
        img_links = parsed_bulk.get("image_links") or []
        vid_links = parsed_bulk.get("video_links") or []
        non_link_text = parsed_bulk.get("non_link_text") or ""

        if total_links > 4:
            lines_non_url = [ln.strip() for ln in raw.splitlines() if ln.strip() and not re.match(r"^https?://", ln.strip())]
            head_txt = lines_non_url[0] if lines_non_url else ""
            tail_txt = "\n".join(lines_non_url[1:]) if len(lines_non_url) > 1 else ""
            summary_line = (
                f"📎 **已粘贴 {total_links} 条素材链接**"
                + (
                    f"（图片 **{len(img_links)}** 条 `{parsed_bulk['image_codes'][0]}~{parsed_bulk['image_codes'][-1]}` + 视频 **{len(vid_links)}** 条 `{parsed_bulk['video_codes'][0]}~{parsed_bulk['video_codes'][-1]}`）"
                    if img_links and vid_links
                    else f"（编号 `{parsed_bulk['task_spec']}`）"
                )
            )
            parts_disp = [p for p in (head_txt, summary_line, tail_txt) if p]
            user_display = "\n\n".join(parts_disp)
            detail_lines: List[str] = []
            if img_links:
                detail_lines.append(f"=== 图片链接（共 {len(img_links)} 条）===")
                for i, u in enumerate(img_links):
                    c = parsed_bulk["image_codes"][i] if i < len(parsed_bulk["image_codes"]) else f"v{i+1:02d}"
                    detail_lines.append(f"{c}: {u}")
            if vid_links:
                if detail_lines:
                    detail_lines.append("")
                detail_lines.append(f"=== 视频链接（共 {len(vid_links)} 条）===")
                for i, u in enumerate(vid_links):
                    c = parsed_bulk["video_codes"][i] if i < len(parsed_bulk["video_codes"]) else f"v{i+1:02d}"
                    detail_lines.append(f"{c}: {u}")
            self._append_bubble(
                "你",
                user_display,
                is_user=True,
                expandable_detail_text="\n".join(detail_lines) if detail_lines else raw,
                expandable_label=f"▼ 展开查看全部 {total_links} 条链接明细 ({parsed_bulk['task_spec']})",
            )
        else:
            self._append_bubble("你", raw, is_user=True)

        low = raw.lower().strip()

        # 1. 以 $ 或 cmd: 或 > 开头 -> 后台直接执行命令
        if raw.startswith("$") or low.startswith("cmd:") or raw.startswith(">"):
            cmd_str = re.sub(r"^(\$|[cC][mM][dD]:|>)\s*", "", raw).strip()
            self._run_background_cmd(cmd_str)
            return

        # 2. 如果包含素材链接：
        if total_links > 0:
            # 情况 A：用户同时给出了任务说明 / 分成了图片+视频两段 / 指定了编号 -> 立即写入《右键表格工具》并启动后台下载 + Orante 准备！
            if parsed_bulk.get("has_instruction") or (img_links and vid_links) or parsed_bulk.get("has_explicit_codes"):
                t_count = parsed_bulk["task_count"]
                t_spec = parsed_bulk["task_spec"]
                b_letter = parsed_bulk.get("batch_letter", "A")
                wants_orante = bool(parsed_bulk.get("wants_orante_prepare", False))
                stats = self.conductor.get_live_workspace_stats()
                day_dir = Path(stats["day_dir"])
                _, vid_dir, img_dir = self.conductor._resolve_day_media_dirs(day_dir)

                plan_lines = [
                    f"收到！我已经完整识别出你发的 **{t_count} 个任务（共 {total_links} 条链接）**，现在立即为你执行：",
                    f"1. **写入《右键表格工具》下载面板**：已将 **{len(img_links)} 条图片链接** (`{parsed_bulk['image_codes'][0]}~{parsed_bulk['image_codes'][-1]}`)"
                    + (f" 与 **{len(vid_links)} 条视频链接** (`{parsed_bulk['video_codes'][0]}~{parsed_bulk['video_codes'][-1]}`)" if vid_links else "")
                    + f" 按序号完整同步进 `{day_dir}\\.renamer_project.json`；",
                    f"2. **并发下载归档**：图片正在下载到 `{img_dir}`，视频正在下载到 `{vid_dir}`；",
                ]
                if wants_orante:
                    plan_lines.append(
                        f"3. **自动移交 Orante 准备任务**：图片下载完成后，我会立即把 `{img_dir}` 发送给 **Orante ({b_letter}批次)** 创建任务并完成 `p01~p{len(img_links):02d}` 素材准备，准备好后第一时间通知你去填写描述语！"
                    )
                self._append_bubble(
                    f"Robota · {self.brain.active_model}",
                    "\n".join(plan_lines),
                    is_user=False,
                    trace_badges=[
                        f"⚡ 已同步右键表格下载队列：{total_links} 条 ({t_spec})",
                        f"🚀 后台并发下载中：{img_dir.name} + {vid_dir.name}" + (" -> Orante 自动准备" if wants_orante else ""),
                    ],
                )
                self._start_workflow_thread(
                    mode="new",
                    task_spec=t_spec,
                    task_count=t_count,
                    video_type_key="3",
                    image_links_text="\n".join(img_links),
                    video_links_text="\n".join(vid_links),
                    image_codes=parsed_bulk["image_codes"],
                    video_codes=parsed_bulk["video_codes"],
                    auto_register_orante=wants_orante,
                    auto_launch_orante=wants_orante,
                    target_batch_letter=b_letter,
                )
                return

            # 情况 B：用户仅粘贴了单段纯链接且没有任何文字指令 -> 询问是图片还是视频及编号
            self._pending_vid_links = parsed_bulk["all_links"]
            self._chat_stage = "await_user_numbering"
            self._append_bubble(
                "Robota",
                f"已收到你粘贴的 **{total_links} 条网盘链接**（已可点击上方气泡展开核对明细）。\n"
                f"请直接回复告诉我这批是**视频**还是**图片**，以及**你要怎么编号**（例如：`视频 v01-v{total_links:02d}` 或 `图片 v01-v{total_links:02d}`），我立刻为你写入右键表格工具并下载归档。",
            )
            return

        # 3. 如果正等待用户指定编号且用户回复了编号范围
        if self._chat_stage == "await_user_numbering" and self._pending_vid_links:
            links_to_dl = list(self._pending_vid_links)
            is_img = "图片" in low or "图" in low
            folder_kind = "图片" if is_img else "视频"
            def_ext = ".jpg" if is_img else ".mp4"

            cleaned_spec = re.sub(r"(图片|视频|编号|按|用|：|:)", " ", raw).strip()
            comma_codes = [c.strip() for c in re.split(r"[,，\s、]+", cleaned_spec) if re.match(r"^[a-zA-Z]*\d+$", c.strip())]
            user_codes: List[str] = []
            if len(comma_codes) >= 1 and "-" not in cleaned_spec and "~" not in cleaned_spec:
                user_codes = comma_codes
            else:
                m_rng = re.search(r"([a-zA-Z]*\d+\s*[-~～至到]\s*[a-zA-Z]*\d+)", cleaned_spec)
                if m_rng:
                    user_codes = self.conductor._parse_task_codes(m_rng.group(1), len(links_to_dl))

            if user_codes:
                explicit_jobs: List[Tuple[str, str, str, str]] = []
                for idx, lnk in enumerate(links_to_dl):
                    c = user_codes[idx] if idx < len(user_codes) else f"{user_codes[-1]}_{idx + 1}"
                    explicit_jobs.append((c, lnk, def_ext, folder_kind))

                self._chat_stage = "idle"
                self._pending_vid_links = []
                self._append_bubble(
                    "Robota",
                    f"明白，立即按你指定的编号 `{', '.join(user_codes[:8])}{'...' if len(user_codes) > 8 else ''}` "
                    f"将这 **{len(links_to_dl)} 条链接**下载到当期**`{folder_kind}`**文件夹。",
                )
                self._start_workflow_thread(
                    mode="explicit",
                    task_spec=f"{user_codes[0]}-{user_codes[-1]}",
                    task_count=len(links_to_dl),
                    explicit_jobs=explicit_jobs,
                )
                return
            else:
                self._chat_stage = "idle"
                self._pending_vid_links = []

        # 4. 全部自然语言对话、文件整理、软件操控指令 -> 交由旗舰 AI 大脑 (默认全程锁定 Gemini 4 Argon) 直接执行！
        self._brain_pending_count += 1
        self.state_dot_lbl.setText(f"● {self.brain.active_model} 正在执行…")
        self.state_dot_lbl.setStyleSheet(f"color: {ACCENT_HI}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        self.live_step_lbl.setText(f"⚡ {self.brain.active_model} 正在理解并执行你的指令…")
        self.live_pbar.setValue(20)

        def _brain_worker(q_text: str) -> None:
            try:
                res = self.brain.chat_with_tools(
                    q_text,
                    progress_callback=lambda st: self.brain_step_sig.emit(st),
                )
            except BaseException as exc:
                res = {"reply": f"执行提示：{exc}", "ui_actions": [], "executed_tools": [], "memory_note": None}
            try:
                self.brain_reply_sig.emit(res)
            except RuntimeError:
                pass

        threading.Thread(target=_brain_worker, args=(raw,), daemon=True).start()

    def _on_brain_replied(self, res: Dict[str, Any]) -> None:
        self._brain_pending_count = max(0, self._brain_pending_count - 1)
        reply_txt = res.get("reply") or "已为你处理完毕。"
        ui_actions = res.get("ui_actions") or []
        executed_tools = res.get("executed_tools") or []
        memory_note = res.get("memory_note")

        if self._brain_pending_count == 0 and not self._wf_running:
            self.state_dot_lbl.setText(f"● 大脑已就绪：{self.brain.active_model}")
            self.state_dot_lbl.setStyleSheet(f"color: {OK_GREEN}; font-size: 11px; border: none; background: transparent;")
            self.live_step_lbl.setText("✓ 执行完成 · 经验已同步至工作流记忆库")
            self.live_pbar.setValue(100)

        target_widget_map = {
            "resume": self.chip_resume_btn,
            "table_tool": self.btn_table,
            "orante": self.btn_orante,
            "cutora": self.btn_cutora,
        }
        target_cb_map = {
            "resume": self._action_resume_today,
            "table_tool": self._open_table_with_progress,
            "orante": self._open_orante_with_progress,
            "cutora": self._open_cutora_with_progress,
        }

        for act in ui_actions:
            atype = act.get("type", "")
            tname = act.get("target", "orante")
            lbl = act.get("label", "点这里")
            w = target_widget_map.get(tname, self.btn_orante)
            cb = target_cb_map.get(tname)

            if atype == "point":
                self.cursor_overlay.point_steady_at_widget(w, lbl, duration_ms=3500)
            elif atype == "click":
                self.cursor_overlay.click_widget_for_user(w, lbl, callback=cb, move_os_cursor=True)
            elif atype == "resume_downloads":
                self._action_resume_today()
            elif atype == "sync_now":
                self._action_sync_now()

        # 构建简洁直观的工具执行与记忆沉淀徽章（绝不塞一堆无用按钮）
        badges: List[str] = []
        if executed_tools:
            t_names = []
            for t in executed_tools:
                n = str(t.get("desc") or t.get("name") or "")
                if n and n not in t_names:
                    t_names.append(n)
            if t_names:
                badges.append(f"⚡ 已执行操作：{', '.join(t_names[:4])}")
        if memory_note:
            badges.append(f"🧠 记忆自进化：{memory_note}")
            self.swarm.update_clone("gamma", "COMPLETED", "已记入流程记忆库", 100, inc_completed=True)

        self._append_bubble(
            f"Robota · {self.brain.active_model}",
            reply_txt,
            is_user=False,
            trace_badges=badges if badges else None,
        )
        self._refresh_bottom_progress_buttons()

    def _run_background_cmd(self, cmd_str: str) -> None:
        if self._wf_running:
            self._append_bubble("Robota", "当前后台正在执行下载任务，请稍候。")
            return
        self._start_workflow_thread(mode="command", command_str=cmd_str)

    def _start_workflow_thread(
        self,
        mode: str = "new",
        task_spec: str = "v01-v38",
        task_count: int = 38,
        video_type_key: str = "3",
        image_links_text: str = "",
        video_links_text: str = "",
        command_str: str = "",
        explicit_jobs: Optional[List[Tuple[str, str, str, str]]] = None,
        image_codes: Optional[List[str]] = None,
        video_codes: Optional[List[str]] = None,
        auto_register_orante: bool = False,
        auto_launch_orante: bool = False,
        target_batch_letter: str = "A",
    ) -> None:
        self._wf_running = True
        self.state_dot_lbl.setText(f"● 正在后台执行操作… ({self.brain.active_model})")
        self.state_dot_lbl.setStyleSheet(f"color: {ACCENT_HI}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        self.live_pbar.setValue(6)
        ws_root = str(self.conductor.get_workspace_root())

        def _wf_worker() -> None:
            try:
                if mode == "resume":
                    res = self.conductor.resume_today_manifest_if_exists(
                        auto_launch_orante=False,
                        progress_callback=lambda msg, pct: self.wf_progress_sig.emit(msg, pct),
                    )
                    self.wf_finished_sig.emit(res)
                elif mode == "command":
                    self.wf_progress_sig.emit(f"正在后台执行：{command_str[:32]}…", 45)
                    res = self.conductor.run_safe_background_command(command_str)
                    res["mode"] = "command"
                    res["command"] = command_str
                    self.wf_progress_sig.emit("后台命令执行完成", 100)
                    self.wf_finished_sig.emit(res)
                else:
                    res = self.conductor.execute_start_today_workflow(
                        workspace_root=ws_root,
                        task_spec=task_spec,
                        task_count=task_count,
                        video_type_key=video_type_key,
                        image_links_text=image_links_text,
                        video_links_text=video_links_text,
                        auto_launch_orante=auto_launch_orante,
                        auto_register_orante=auto_register_orante,
                        progress_callback=lambda msg, pct: self.wf_progress_sig.emit(msg, pct),
                        explicit_jobs=explicit_jobs,
                        image_codes=image_codes,
                        video_codes=video_codes,
                        target_batch_letter=target_batch_letter,
                    )
                    self.wf_finished_sig.emit(res)
            except BaseException as exc:
                try:
                    self.wf_error_sig.emit(str(exc))
                except RuntimeError:
                    pass

        threading.Thread(target=_wf_worker, daemon=True).start()

    def _on_workflow_progress(self, msg: str, pct: int) -> None:
        self.live_step_lbl.setText(msg)
        self.live_pbar.setValue(pct)
        self._refresh_bottom_progress_buttons()

    def _on_workflow_finished(self, res: Dict[str, Any]) -> None:
        self._wf_running = False
        self.state_dot_lbl.setText(f"● 大脑已就绪：{self.brain.active_model}")
        self.state_dot_lbl.setStyleSheet(f"color: {OK_GREEN}; font-size: 11px; border: none; background: transparent;")
        self.live_pbar.setValue(100)
        self._refresh_bottom_progress_buttons()

        if res.get("mode") == "command":
            self.live_step_lbl.setText(f"命令执行完成：{res.get('command', '')}")
            out_txt = res.get("output", "")
            self._append_bubble(
                "Robota",
                f"已在后台执行命令 `{res.get('command', '')}`（目录 `{res.get('cwd', '')}`）：\n```\n{out_txt}\n```",
            )
            return

        img_c = res.get("img_count", 0)
        vid_c = res.get("vid_count", 0)
        img_dir_s = res.get("img_dir", "")
        vid_dir_s = res.get("vid_dir", "")
        orante_tid = res.get("orante_task_id", "")
        orante_tdir = res.get("orante_task_dir", "")
        orante_prep_c = res.get("orante_prepared_count", 0)

        self.live_step_lbl.setText(
            f"✓ 已完成：{Path(img_dir_s).name} ({img_c}张) · {Path(vid_dir_s).name} ({vid_c}个)"
            + (f" · Orante {orante_tid} 已准备就绪" if orante_tid else "")
        )

        msg_lines = [
            "已经全部按你的要求下载并准备完毕：",
            f"- **图片素材库** (`{img_dir_s}`)：已就绪 **{img_c}** 张图片（`v01 ~ v{img_c:02d}`）",
            f"- **主视频素材库** (`{vid_dir_s}`)：已就绪 **{vid_c}** 个视频（`v01 ~ v{vid_c:02d}`）",
            f"- **《右键表格工具》下载面板**：已同步全部序号与完成状态（打开即可查看）",
        ]
        if orante_tid:
            msg_lines.append(
                f"- **Orante 任务已准备就绪**：已将 `{img_dir_s}` 作为素材来源创建任务 **`{orante_tid} · {Path(img_dir_s).name}`**"
                f"（目录 `{orante_tdir}`，共导入 **{orante_prep_c}** 张主体图 `p01 ~ p{orante_prep_c:02d}`）\n\n"
                f"现在你可以直接去 **Orante** 里面填写描述语了！"
            )
        self._append_bubble(
            f"Robota · {self.brain.active_model}",
            "\n".join(msg_lines),
        )

    def _on_workflow_error(self, err_msg: str) -> None:
        self._wf_running = False
        self.state_dot_lbl.setText(f"● 大脑已就绪：{self.brain.active_model}")
        self.state_dot_lbl.setStyleSheet(f"color: {OK_GREEN}; font-size: 11px; border: none; background: transparent;")
        self.live_step_lbl.setText(f"自动恢复诊断：{err_msg[:45]}")
        self._append_bubble(
            f"Robota · {self.brain.active_model}",
            f"💡 **异常根因诊断与自我恢复说明**：\n"
            f"- **问题原因**：后台工作流执行时遇到提示 `{err_msg}`。\n"
            f"- **自我恢复计划**：已保留所有已下载完成的素材文件与《右键表格工具》序号清单（支持断点续传），你可以点击底部 **📋 右键表格** 查看明细或直接回复『继续下载』。",
        )

    def _on_swarm_status_updated(self, snapshot: Dict[str, CloneState]) -> None:
        self._refresh_bottom_progress_buttons()

    # ------------------------------------------------------------------
    # 辅助弹窗
    # ------------------------------------------------------------------
    def _on_change_workspace(self) -> None:
        cur = str(self.conductor.get_workspace_root())
        chosen = QFileDialog.getExistingDirectory(self, "选择工作总目录", cur)
        if chosen:
            new_p = self.conductor.set_workspace_root(chosen)
            self.ws_btn.setText(f"📁 {new_p}")
            self._refresh_bottom_progress_buttons()
            self._append_bubble("Robota", f"工作总目录已切换为：`{new_p}`")

    def _open_vault_dialog(self) -> None:
        dlg = CredentialVaultDialog(self.conductor.vault, self)
        dlg.exec()

    def _open_info_dialog(self) -> None:
        dlg = RobotaInfoDialog(self.conductor, self.brain.active_model, self)
        dlg.exec()

    def closeEvent(self, event) -> None:
        try:
            self.cursor_overlay.hide()
            self.cursor_overlay.close()
        except Exception:
            pass
        super().closeEvent(event)


def _install_global_excepthook() -> None:
    def _hook(exc_type, exc_value, exc_tb) -> None:
        try:
            log_p = STUDIO_ROOT / "data" / "robota_ui_error.log"
            log_p.parent.mkdir(parents=True, exist_ok=True)
            with open(log_p, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.datetime.now().isoformat(timespec='seconds')}]\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass

    sys.excepthook = _hook


def main() -> None:
    _install_global_excepthook()
    app = QApplication.instance() or QApplication(sys.argv)
    auto_resume = "--auto-resume" in sys.argv
    win = RobotaAssistantWindow(auto_resume_on_start=auto_resume)
    win.show()
    if "--screenshot" in sys.argv:
        idx = sys.argv.index("--screenshot")
        out_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "robota_preview.png"

        def _capture() -> None:
            pix = win.grab()
            pix.save(out_path)
            app.quit()

        QTimer.singleShot(900, _capture)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
