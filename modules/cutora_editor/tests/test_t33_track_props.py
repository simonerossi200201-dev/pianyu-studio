# -*- coding: utf-8 -*-
"""Q16 / T33: track props, layer opacity, pin batch command + undo."""
from __future__ import annotations

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t33-track-props"])


def _identity():
    from cutora.render.track_transform import IDENTITY_TRACK_PROPS
    return dict(IDENTITY_TRACK_PROPS)


def test_track_transform_composes_after_clip():
    """clip x=100 + track x=50 → 150. Parent transform after clip."""
    _qapp()
    from PySide6.QtCore import QRectF
    from cutora.render.track_transform import compose_track_transform

    clip_rect = QRectF(100.0, 20.0, 200.0, 80.0)
    props = _identity()
    props["x"] = 50.0
    out = compose_track_transform(clip_rect, props, (1920, 1080))
    assert abs(out.x() - 150.0) < 1e-6, out
    assert abs(out.y() - 20.0) < 1e-6, out
    assert abs(out.width() - 200.0) < 1e-6, out
    assert abs(out.height() - 80.0) < 1e-6, out


def test_track_opacity_applies_to_layer_not_clips():
    """Two overlapping clips + track opacity 0.5 must not stack to 0.75."""
    _qapp()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.model.timeline_model import Track
    from cutora.render.track_transform import blit_track_layer, track_props_of

    w, h = 80, 80
    layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(0)
    lp = QPainter(layer)
    try:
        lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        lp.fillRect(10, 10, 40, 40, QColor(255, 255, 255, 255))
        lp.fillRect(30, 30, 40, 40, QColor(255, 255, 255, 255))
    finally:
        lp.end()

    tr = Track("video")
    tr.props = _identity()
    tr.props["opacity"] = 0.5
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor(0, 0, 0, 255))
    cp = QPainter(canvas)
    try:
        blit_track_layer(cp, layer, track_props_of(tr), w, h)
    finally:
        cp.end()

    overlap = canvas.pixelColor(40, 40)
    # layer-then-opacity: white 0.5 over black ≈ 128
    # per-clip 0.5 stacked: 1 - 0.5*0.5 = 0.75 → ≈ 191
    assert overlap.red() < 160, (
        "overlap got %s — track opacity stacked per clip (0.75) instead of layer"
        % overlap.red()
    )
    assert 110 <= overlap.red() <= 145, overlap.red()
    outside = canvas.pixelColor(2, 2)
    assert outside.red() <= 8, outside.red()


def _dump_timeline(task):
    from cutora.model.project_io import _rj_serialize_timeline
    raw = _rj_serialize_timeline(task["timeline"])
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_pinned_tracks_one_command_one_undo():
    """Pin three tracks, one track.set_props, undo restores JSON byte-for-byte."""
    from cutora.core.commands import CommandBus, get_registry
    from cutora.core.history import HistoryManager
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.model.track_guard import find_track

    tl = Timeline(1920, 1080, 30)
    tracks = [Track("video") for _ in range(3)]
    for tr in tracks:
        tl.add_track(tr)
    task = {"timeline": tl}
    pinned_ids = [str(tr.id) for tr in tracks]

    class _Win:
        def __init__(self):
            self.tasks = [task]
            self.curr_idx = 0
            self.project_path = "mem"
            self.history_mgr = HistoryManager()
            self.timeline = type("TLW", (), {"_pinned_ids": set(pinned_ids)})()

        def push_undo(self, action_name="操作", command=None, params=None, source=None):
            self.history_mgr.push(action_name, self._snap())

        def capture_undo_after(self):
            self.history_mgr.set_after(self._snap())

        def _snap(self):
            return {
                "tasks": copy.deepcopy(self.tasks),
                "curr_idx": self.curr_idx,
            }

        def undo(self):
            state = self.history_mgr.undo()
            if state:
                self.tasks[:] = state["tasks"]
                self.curr_idx = state["curr_idx"]

        def command_set_track_props(self, track_ids, patch):
            from cutora.render.track_transform import apply_track_props_patch
            apply_track_props_patch(self.tasks[self.curr_idx], track_ids, patch)
            return {"track_ids": list(track_ids), "patch": dict(patch)}

    win = _Win()
    before = _dump_timeline(win.tasks[0])
    bus = CommandBus(get_registry())
    bus.bind_window(win)
    hist_before = len(win.history_mgr.history)
    bus.execute(
        "track.set_props",
        {"track_ids": pinned_ids, "patch": {"x": 40.0, "opacity": 0.5}},
        source="ui",
    )
    assert len(win.history_mgr.history) == hist_before + 1
    after = _dump_timeline(win.tasks[0])
    assert after != before
    for tid in pinned_ids:
        tr = find_track(win.tasks[0], track_id=tid)
        assert abs(float(tr.props["x"]) - 40.0) < 1e-9
        assert abs(float(tr.props["opacity"]) - 0.5) < 1e-9
    win.undo()
    restored = _dump_timeline(win.tasks[0])
    assert restored == before, (before, restored)


def test_track_props_default_identity_and_pinned_not_saved():
    from cutora.model.project_io import _rj_serialize_track
    from cutora.model.timeline_model import Track
    from cutora.render.track_transform import track_needs_layer, track_props_of

    tr = Track("video")
    props = track_props_of(tr)
    assert props["x"] == 0.0
    assert props["y"] == 0.0
    assert props["scale"] == 1.0
    assert props["rotation"] == 0.0
    assert props["flip_x"] is False
    assert props["flip_y"] is False
    assert props["opacity"] == 1.0
    assert props["blend_mode"] == "normal"
    assert track_needs_layer(tr) is False
    tr.pinned = True
    raw = _rj_serialize_track(tr)
    assert raw.get("pinned") is False
    assert "props" in raw
    assert raw["props"]["opacity"] == 1.0


def test_track_set_props_command_is_undoable():
    from cutora.core.commands import get_registry
    spec = get_registry().get("track.set_props")
    assert spec.undoable is True
    assert spec.portable is False
    props = spec.params_schema.get("properties") or {}
    assert "track_ids" in props
    assert "patch" in props


def test_property_panel_track_mode():
    _qapp()
    from cutora.model.timeline_model import Track
    from cutora.ui.panels.property import PropertyPanel
    panel = PropertyPanel()
    tr = Track("video")
    tr.name = "A轨"
    panel.load_data("track", tr)
    assert panel.stack.currentIndex() == 5
    assert panel.trk_name.text() == "A轨"
    assert panel.trk_banner.isHidden()
    panel.close()


def test_header_name_button_and_pin_glyph():
    src = open(
        os.path.join(REPO, "cutora", "ui", "timeline", "header.py"),
        encoding="utf-8",
    ).read()
    assert "_name_rects" in src
    assert "📌" in src
    assert "主题金描边" in src or "t.accent" in src
    psrc = open(
        os.path.join(REPO, "cutora", "ui", "panels", "property.py"),
        encoding="utf-8",
    ).read()
    assert "正在编辑" in psrc
    assert "init_track_panel" in psrc
