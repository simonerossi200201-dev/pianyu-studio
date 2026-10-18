# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q200 · transport-row role mixer. Replaces Q199's temporary sliders.

Sits in the existing 40 px preview_tools row. Three role faders (72–90 px
including mute), a spatial switch, and a preset MENU — not a combo.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSlider,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from cutora.ui.i18n import tr

ROLES = ("main", "bgm", "sfx")
ROLE_TIP = {"main": "原声", "bgm": "配乐", "sfx": "音效"}
# Cluster width includes the mute button. Design: 72–90 px.
FADER_MIN_W = 72
FADER_MAX_W = 90
MUTE_W = 18
BAR_H = 32


class RoleFader(QWidget):
    """One role: horizontal fader + mute. Linear 0.00–2.00 as slider 0–200."""

    moved = Signal(str, int)
    released = Signal(str)
    mute_toggled = Signal(str, bool)

    def __init__(self, role: str, parent=None, init=100):
        super().__init__(parent)
        self.role = role
        self.setObjectName("roleFaderCluster_%s" % role)
        self.setFixedHeight(BAR_H)
        self.setMinimumWidth(FADER_MIN_W)
        self.setMaximumWidth(FADER_MAX_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setObjectName("roleFader_%s" % role)
        self.slider.setRange(0, 200)
        self.slider.setValue(int(init))
        self.slider.setMaximumHeight(18)
        self.slider.setToolTip(tr(ROLE_TIP[role]))
        self.slider.valueChanged.connect(lambda v, r=role: self.moved.emit(r, int(v)))
        self.slider.sliderReleased.connect(lambda r=role: self.released.emit(r))
        self.mute_btn = QToolButton(self)
        self.mute_btn.setObjectName("roleMute_%s" % role)
        self.mute_btn.setCheckable(True)
        self.mute_btn.setAutoRaise(True)
        self.mute_btn.setFixedSize(MUTE_W, MUTE_W)
        self.mute_btn.setIconSize(QSize(12, 12))
        self.mute_btn.setToolTip(tr("静音") + " · " + tr(ROLE_TIP[role]))
        self.mute_btn.toggled.connect(self._on_mute)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.mute_btn, 0)
        self._bind_mute_icon(False)

    def _on_mute(self, on):
        self._bind_mute_icon(bool(on))
        self.mute_toggled.emit(self.role, bool(on))

    def _bind_mute_icon(self, muted: bool):
        try:
            from cutora.ui.icons import bind_icon
            bind_icon(
                self.mute_btn,
                "volume-x" if muted else "volume-2",
                size=12,
                color_token="accent" if muted else "fg1",
            )
        except Exception:
            self.mute_btn.setText("M" if muted else "")

    def set_linear(self, linear, enabled=True):
        sl = self.slider
        sl.blockSignals(True)
        sl.setValue(int(round(max(0.0, min(2.0, float(linear))) * 100.0)))
        sl.blockSignals(False)
        self.setEnabled(bool(enabled))
        sl.setEnabled(bool(enabled))

    def linear(self) -> float:
        return max(0.0, self.slider.value() / 100.0)

    def set_muted(self, on: bool):
        btn = self.mute_btn
        btn.blockSignals(True)
        btn.setChecked(bool(on))
        btn.blockSignals(False)
        self._bind_mute_icon(bool(on))


class AudioMixerBar(QWidget):
    """Transport-row mixer: three role faders + spatial switch + preset menu."""

    fader_moved = Signal(str, int)
    fader_released = Signal()
    mute_toggled = Signal(str, bool)
    spatial_toggled = Signal(bool)
    preset_chosen = Signal(str)
    add_bgm_requested = Signal()
    place_sfx_requested = Signal()
    sfx_preview_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("audioMixerBar")
        self.setFixedHeight(BAR_H)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        inits = {"main": 100, "bgm": 22, "sfx": 18}
        self.faders = {}
        for role in ROLES:
            fd = RoleFader(role, self, init=inits[role])
            fd.moved.connect(self.fader_moved)
            fd.released.connect(lambda _r: self.fader_released.emit())
            fd.mute_toggled.connect(self.mute_toggled)
            lay.addWidget(fd, 0)
            self.faders[role] = fd
        self.spatial_btn = QToolButton(self)
        self.spatial_btn.setObjectName("mixerSpatial")
        self.spatial_btn.setCheckable(True)
        self.spatial_btn.setAutoRaise(True)
        self.spatial_btn.setFixedSize(22, 22)
        self.spatial_btn.setToolTip(tr("原声空间立体声"))
        self.spatial_btn.toggled.connect(self._on_spatial)
        lay.addWidget(self.spatial_btn, 0)
        self.menu_btn = QToolButton(self)
        self.menu_btn.setObjectName("mixerPresetMenu")
        self.menu_btn.setAutoRaise(True)
        self.menu_btn.setFixedSize(22, 22)
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setToolTip(tr("空间预设 / 添加配乐"))
        self.menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        lay.addWidget(self.menu_btn, 0)
        self._preset_group = None
        self._build_menu()
        self._bind_chrome()

    def _bind_chrome(self):
        try:
            from cutora.ui.icons import bind_icon
            bind_icon(self.spatial_btn, "headphones", size=14, color_token="fg1")
            bind_icon(self.menu_btn, "ellipsis", size=14, color_token="fg1")
        except Exception:
            self.spatial_btn.setText("S")
            self.menu_btn.setText("▾")

    def _on_spatial(self, on):
        try:
            from cutora.ui.icons import bind_icon
            bind_icon(
                self.spatial_btn, "headphones", size=14,
                color_token="accent" if on else "fg1")
        except Exception:
            pass
        self.spatial_toggled.emit(bool(on))

    def _build_menu(self):
        from PySide6.QtGui import QAction, QActionGroup
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        act_bgm = QAction(tr("添加配乐…"), menu)
        act_bgm.triggered.connect(self.add_bgm_requested.emit)
        menu.addAction(act_bgm)
        act_sfx = QAction(tr("铺音效…"), menu)
        act_sfx.triggered.connect(self.place_sfx_requested.emit)
        menu.addAction(act_sfx)
        self.act_sfx_preview = QAction(tr("预览时播放音效"), menu)
        self.act_sfx_preview.setCheckable(True)
        self.act_sfx_preview.setChecked(True)
        self.act_sfx_preview.toggled.connect(self.sfx_preview_toggled.emit)
        menu.addAction(self.act_sfx_preview)
        menu.addSeparator()
        group = QActionGroup(menu)
        group.setExclusive(True)
        try:
            from cutora.audio.spatial import PRESETS
            items = list(PRESETS.items())
        except Exception:
            items = (("stage", {"label": tr("舞台现场（话筒在台上）")}),)
        self._preset_actions = {}
        for key, spec in items:
            label = (spec or {}).get("label") or key
            act = QAction(tr(str(label)), menu)
            act.setCheckable(True)
            act.setData(key)
            group.addAction(act)
            menu.addAction(act)
            act.triggered.connect(lambda _on=False, k=key: self.preset_chosen.emit(k))
            self._preset_actions[key] = act
        self._preset_group = group
        self.menu_btn.setMenu(menu)

    @property
    def sliders(self):
        return {role: fd.slider for role, fd in self.faders.items()}

    def set_from_task(self, task):
        from cutora.audio.sfx_library import clip_audio_role, gain_db_to_linear
        found = {r: [] for r in ROLES}
        mute = {r: False for r in ROLES}
        spatial_on = False
        tl = None
        if isinstance(task, dict):
            tl = task.get("timeline")
        else:
            tl = getattr(task, "timeline", None)
        for track in getattr(tl, "tracks", []) or []:
            if getattr(track, "type", None) not in ("audio", "bgm", "sfx"):
                continue
            role = str(getattr(track, "audio_role", "") or "")
            if role in mute:
                mute[role] = bool(getattr(track, "mute", False))
            for c in getattr(track, "clips", []) or []:
                cr = clip_audio_role(c)
                if cr in found:
                    found[cr].append(gain_db_to_linear(getattr(c, "gain_db", 0.0)))
                if cr == "main" and bool(getattr(c, "spatial_enabled", False)):
                    spatial_on = True
        fallback = {"main": 1.0, "bgm": 0.22, "sfx": 0.18}
        for role, fd in self.faders.items():
            vals = found.get(role) or []
            linear = float(vals[0]) if vals else fallback[role]
            fd.set_linear(linear, enabled=bool(vals) or role == "main")
            fd.set_muted(mute.get(role, False))
            if vals and max(vals) - min(vals) > 0.02:
                fd.slider.setToolTip(tr("混合") + " · " + tr(ROLE_TIP[role]))
            else:
                fd.slider.setToolTip(tr(ROLE_TIP[role]))
        self.spatial_btn.blockSignals(True)
        self.spatial_btn.setChecked(spatial_on)
        self.spatial_btn.blockSignals(False)
        try:
            from cutora.ui.icons import bind_icon
            bind_icon(
                self.spatial_btn, "headphones", size=14,
                color_token="accent" if spatial_on else "fg1")
        except Exception:
            pass

    def set_sfx_preview(self, on: bool):
        act = getattr(self, "act_sfx_preview", None)
        if act is None:
            return
        act.blockSignals(True)
        act.setChecked(bool(on))
        act.blockSignals(False)

    def set_preset(self, key: str):
        act = (getattr(self, "_preset_actions", None) or {}).get(key)
        if act is None:
            return
        act.blockSignals(True)
        act.setChecked(True)
        act.blockSignals(False)

    def linear(self, role: str) -> float:
        fd = self.faders.get(role)
        return fd.linear() if fd is not None else 1.0
