# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 3863-3976

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QScrollBar,
    QSizePolicy,
    QToolButton,
    QButtonGroup,
)
from PySide6.QtCore import (
    Qt,
    QTimer,
)
from PySide6.QtCore import Qt
from cutora.ui.timeline.header import TrackHeaderPanel
from cutora.ui.timeline.header import TrackHeaderPanel

def _tl_of(*a, **k):
    from cutora.ui.main_window import _tl_of as _impl
    return _impl(*a, **k)

def track_supports_batch(*a, **k):
    from cutora.ui.main_window import track_supports_batch as _impl
    return _impl(*a, **k)

# --- Timeline Container (Wrap with ScrollBar) ---
class TimelineContainer(QWidget):
    def __init__(self, timeline_widget):
        super().__init__()
        self.timeline = timeline_widget
        self.track_header = TrackHeaderPanel(self.timeline)
        self.setObjectName("TimelineContainer")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(0,0,0,0); self.layout.setSpacing(0)
        from cutora.ui.i18n import tr
        tools = QHBoxLayout(); tools.setContentsMargins(6, 2, 6, 2); tools.setSpacing(4)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self.btn_tool_select = QToolButton(); self.btn_tool_select.setText(tr("选择"))
        self.btn_tool_razor = QToolButton(); self.btn_tool_razor.setText(tr("切刀"))
        self.btn_tool_speed = QToolButton(); self.btn_tool_speed.setText(tr("变速"))
        self.btn_magnet = QToolButton(); self.btn_magnet.setText(tr("磁铁"))
        for b in (self.btn_tool_select, self.btn_tool_razor, self.btn_tool_speed, self.btn_magnet):
            b.setCheckable(True)
            b.setAutoRaise(True)
            tools.addWidget(b)
        self._tool_group.addButton(self.btn_tool_select)
        self._tool_group.addButton(self.btn_tool_razor)
        self._tool_group.addButton(self.btn_tool_speed)
        self.btn_tool_select.setChecked(True)
        self.btn_magnet.setChecked(bool(getattr(self.timeline, "snap_enabled", True)))
        self.btn_magnet.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_magnet.customContextMenuRequested.connect(self._magnet_menu)
        self.btn_tool_select.clicked.connect(lambda: self.timeline.set_tool("select"))
        self.btn_tool_razor.clicked.connect(lambda: self.timeline.set_tool("razor"))
        self.btn_tool_speed.clicked.connect(lambda: self.timeline.set_tool("speed"))
        self.btn_magnet.toggled.connect(lambda on: self.timeline.set_snap_enabled(bool(on)))
        # Q145：时间线里按 V / C / R 切工具，工具栏按钮跟着亮
        self.btn_tool_select.setToolTip(tr("选择工具（V）"))
        self.btn_tool_razor.setToolTip(tr("切刀工具（C）"))
        self.btn_tool_speed.setToolTip(tr("变速工具（R）"))
        self.timeline.tool_changed.connect(self._on_tool_changed)

        # ✨ 剪辑台时间线原生 AI 灵巧直调胶囊按钮组（直接在时间线生成轨道与素材条，并自动记入 AI 卡牌）
        tools.addSpacing(10)
        _ai_Quick_Btns = [
            ("✨ AI 创作与卡牌", "ltx25_i2v_t2v", "在时间线直接调用全部 21 项 AI 能力，自动上轨并生成 AI 卡牌", True),
            ("🎬 生图/生视频", "flux2_klein", "直接在当前时间线游标处生成分镜图或视频并建轨", False),
            ("🎵 生成配乐", "musicgen_bgm", "MusicGen 直接在时间线生成情绪背景配乐轨", False),
            ("🔊 生成音效", "stable_audio_sfx", "Stable Audio 直接在时间线生成立体声效/观众掌声轨", False),
            ("🎙️ 克隆人声", "chatterbox_clone", "Chatterbox / XTTS-v2 吸取原声直接在时间线克隆配音", False),
            ("💎 提画质/打光", "simone_relight_v6h", "7种视频画质修复 / Simone v6h 人像重打光 / Autograde 3D LUT", False),
        ]
        for _blab, _bcap, _btip, _is_main in _ai_Quick_Btns:
            _ab = QToolButton()
            _ab.setText(_blab)
            _ab.setToolTip(_btip)
            _ab.setCursor(Qt.CursorShape.PointingHandCursor)
            if _is_main:
                _ab.setStyleSheet(
                    "QToolButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(229,169,60,0.28), stop:1 rgba(184,125,28,0.20)); "
                    "color: #F3C668; border: 1px solid rgba(229,169,60,0.68); "
                    "border-radius: 6px; padding: 2px 8px; font-weight: 700; font-size: 11px; }"
                    "QToolButton:hover { background: #E5A93C; color: #120E09; border-color: #F5D082; }"
                )
            else:
                _ab.setStyleSheet(
                    "QToolButton { background: #1A1610; color: #E8DEC8; border: 1px solid rgba(229,169,60,0.32); "
                    "border-radius: 6px; padding: 2px 7px; font-size: 11px; }"
                    "QToolButton:hover { background: rgba(229,169,60,0.16); border-color: #E5A93C; color: #F3C668; }"
                )
            _ab.clicked.connect(lambda _=False, cid=_bcap: self._open_timeline_ai_capsule(cid))
            tools.addWidget(_ab)

        tools.addStretch(1)
        self.layout.addLayout(tools)
        row = QHBoxLayout(); row.setContentsMargins(0,0,0,0); row.setSpacing(0)
        row.addWidget(self.track_header)
        row.addWidget(self.timeline, 1)
        self.v_scrollbar = QScrollBar(Qt.Orientation.Vertical)
        self.v_scrollbar.valueChanged.connect(self.timeline.set_scroll_y)
        self.v_scrollbar.valueChanged.connect(self.track_header.set_scroll_y)
        row.addWidget(self.v_scrollbar)
        self.layout.addLayout(row, 1)
        # HScrollbar row: spacer to align under timeline (skip header width)
        hrow = QHBoxLayout(); hrow.setContentsMargins(0,0,0,0); hrow.setSpacing(0)
        hrow.addSpacing(TrackHeaderPanel.PANEL_W)
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.valueChanged.connect(self.timeline.set_scroll_x)
        hrow.addWidget(self.scrollbar, 1)
        from cutora.ui.i18n import tr
        self.total_label = QLabel(tr("总时长 {t}", t="00:00.00"))
        self.total_label.setObjectName("timelineTotal")
        self.total_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.total_label.setContentsMargins(8, 0, 0, 0)
        fm = self.total_label.fontMetrics()
        self.total_label.setMinimumWidth(
            fm.horizontalAdvance("总时长 00:00:00.00") + 16)
        hrow.addWidget(self.total_label)
        self.layout.addLayout(hrow, 0)
        
        self.timer = QTimer(self); self.timer.setInterval(100); self.timer.timeout.connect(self.update_scroll_range)
        self.timer.start()
        # Sync track header repaint when timeline repaints
        self.timeline.installEventFilter(self)
        # Connect track header actions
        self.track_header.track_action.connect(self._on_track_action)


    def _on_tool_changed(self, name: str) -> None:
        btn = {"select": self.btn_tool_select, "razor": self.btn_tool_razor,
               "speed": self.btn_tool_speed}.get(str(name))
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    def _open_timeline_ai_capsule(self, cap_id: str = "ltx25_i2v_t2v") -> None:
        """在剪辑页面呼出灵巧 AI 创作胶囊（自动落轨并生成对应 AI 卡牌）。"""
        try:
            from cutora.ui.ai_studio_room import open_quick_ai_creator
            win = self.window()
            open_quick_ai_creator(win=win, initial_cap_id=cap_id, parent=self)
        except Exception:
            pass

    def _on_track_action(self, track, action):
        """NLE Gen-2: 响应 TrackHeaderPanel 按钮点击。
        track 参数是 Track 对象（不再是 name 字符串）。"""
        if track is None:
            return

        tl = _tl_of(self.timeline.task_data)

        if action == "up":
            if tl:
                tl.move_track_up(track)
            else:
                track.order += 1
        elif action == "down":
            if tl:
                tl.move_track_down(track)
            else:
                track.order -= 1
        elif action == "name":
            self.timeline.selected_track = track
            self.timeline.selected_video_clip = None
            self.timeline.selected_audio_idx = None
            self.timeline.selected_overlay = None
            self.timeline.selected_transition = None
            self.timeline.selected_indices.clear()
            self.timeline.item_selected.emit("track", track)
            self.timeline.update()
            self.track_header.update()
            return
        elif action == "pin":
            from cutora.render.track_transform import toggle_session_pin
            toggle_session_pin(self.timeline, track)
            win = self.window()
            panel = getattr(win, "prop_panel", None) if win is not None else None
            if panel is not None and getattr(panel, "current_type", None) == "track":
                cur = getattr(panel, "_track_obj", None)
                if cur is not None:
                    panel.load_data("track", cur)
        elif action == "eye":
            if tl:
                tl.toggle_visibility(track)
            else:
                track.visible = not track.visible
        elif action == "batch":
            if not track_supports_batch(track):
                return
            if tl:
                tl.toggle_batch_enable(track)
            else:
                track.enable_batch = not track.enable_batch
        elif action == "mute":
            from cutora.core.commands import get_bus
            get_bus().execute(
                "timeline.set_track_mute",
                {"track_id": getattr(track, "id", ""),
                 "on": not bool(getattr(track, "mute", False))},
                source="ui",
            )
        elif action == "solo":
            tid = str(getattr(track, "id", ""))
            solos = getattr(self.timeline, "_solo_ids", None) or set()
            from cutora.core.commands import get_bus
            get_bus().execute(
                "timeline.track.solo",
                {"track_id": tid, "on": tid not in solos},
                source="ui",
            )
        elif action == "lock":
            from cutora.core.commands import get_bus
            get_bus().execute(
                "timeline.set_track_locked",
                {"track_id": getattr(track, "id", ""),
                 "on": not bool(getattr(track, "locked", False))},
                source="ui",
            )
        elif action == "import":
            # 通过信号通知 MainWindow 打开对应类型的文件选择器
            self.timeline.import_to_track.emit(track)
            return  # 不需要立即 update，导入完成后才刷新
        self.timeline.update()
        self.track_header.update()

    def _magnet_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        from cutora.ui.i18n import tr
        menu = QMenu(self)
        kinds = dict(getattr(self.timeline, "snap_kinds", None) or {})
        labels = (
            ("clip", tr("吸附到片段边界")),
            ("playhead", tr("吸附到播放头")),
            ("marker", tr("吸附到标记")),
            ("silence", tr("吸附到静音段边界")),
        )
        for key, label in labels:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(bool(kinds.get(key, True)))

            def _toggle(on, k=key):
                setter = getattr(self.timeline, "set_snap_kind", None)
                if callable(setter):
                    setter(k, bool(on))
                else:
                    kinds = dict(getattr(self.timeline, "snap_kinds", None) or {})
                    kinds[k] = bool(on)
                    self.timeline.snap_kinds = kinds
                    self.timeline._snap_edge_cache = None
                    self.timeline.update()
            act.toggled.connect(_toggle)
        menu.exec(self.btn_magnet.mapToGlobal(pos))

    def eventFilter(self, obj, event):
        if obj is self.timeline and event.type() == event.Type.Paint:
            self.track_header.update()
        return False

    def update_scroll_range(self):
        if not hasattr(self, "scrollbar") or not hasattr(self, "v_scrollbar"):
            return
        content_w = int((self.timeline.duration / 1000.0) * self.timeline.pixels_per_second)
        visible_w = self.timeline.width()
        content_h = self.timeline.get_content_height()
        available_h = max(1, self.timeline.height() - self.timeline.RULER_H - self.timeline.TOP_PAD - self.timeline.BOTTOM_PAD)
        key = (content_w, visible_w, content_h, available_h, int(self.timeline.duration or 0))
        if key == getattr(self, "_scroll_range_key", None):
            return
        self._scroll_range_key = key
        self.scrollbar.setRange(0, max(0, content_w - visible_w + 500))
        self.scrollbar.setPageStep(visible_w)
        self.v_scrollbar.setRange(0, max(0, int(content_h - available_h)))
        self.v_scrollbar.setPageStep(available_h)
        from cutora.ui.i18n import tr
        self.total_label.setText(tr(
            "总时长 {t}",
            t=self.timeline._format_time_label(self.timeline.duration / 1000.0),
        ))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            zoom_factor = 1.25 if delta > 0 else 1.0 / 1.25
            zoom = getattr(self.timeline, "zoom_at_playhead", None)
            if callable(zoom):
                zoom(zoom_factor)
            else:
                old_pps = self.timeline.pixels_per_second
                new_pps = max(1, min(300, old_pps * zoom_factor))
                t = self.timeline.position / 1000.0
                screen_x = t * old_pps - self.timeline.scroll_x
                self.timeline.pixels_per_second = new_pps
                self.timeline.scroll_x = max(0.0, t * new_pps - screen_x)
                self.scrollbar.setValue(int(self.timeline.scroll_x))
                self.update_scroll_range()
                self.timeline.update()
            event.accept()
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift + 滚轮 = 左右滚动（时间轴横向）
            delta = event.angleDelta().y()
            self.scrollbar.setValue(self.scrollbar.value() - delta)
            event.accept()
        else:
            # 滚轮 = 上下滚动（查看不同轨道图层）
            delta = event.angleDelta().y()
            self.v_scrollbar.setValue(self.v_scrollbar.value() - delta)
