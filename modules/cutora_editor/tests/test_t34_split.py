# -*- coding: utf-8 -*-
"""Q17 / T34: project-level split screen — layout, render, commands, UI."""
from __future__ import annotations

import copy
import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRESETS = (
    "top_bottom", "left_right", "left_right_fit", "grid_2x2", "grid_3x3",
    "left1_right2", "top1_bottom2",
)


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t34-split"])


def _cell_by_id(split, cid):
    for c in split.get("cells") or []:
        if str(c.get("id")) == str(cid):
            return c
    return None


def _area(rect):
    return float(rect[2]) * float(rect[3])


def test_split_preset_cells_cover_unit_and_no_overlap():
    """Every preset: cell areas sum to 1 and pairwise overlap is 0."""
    from cutora.render.split_layout import apply_layout, cells_overlap_area

    for name in PRESETS:
        split = apply_layout({"enabled": True, "layout": name})
        cells = list(split.get("cells") or [])
        assert cells, name
        total = sum(_area(c["rect"]) for c in cells)
        assert abs(total - 1.0) < 1e-9, (name, total, cells)
        for i, a in enumerate(cells):
            for b in cells[i + 1:]:
                ov = cells_overlap_area(a["rect"], b["rect"])
                assert ov < 1e-9, (name, a["id"], b["id"], ov)


def test_split_recompute_top_bottom_pos_0_6():
    """Drag d1.pos 0.5 → 0.6: A height 0.6, B height 0.4."""
    from cutora.render.split_layout import apply_layout, recompute, set_divider

    split = apply_layout({"enabled": True, "layout": "top_bottom"})
    a = _cell_by_id(split, "A")["rect"]
    b = _cell_by_id(split, "B")["rect"]
    assert abs(a[3] - 0.5) < 1e-9, a
    assert abs(b[3] - 0.5) < 1e-9, b
    split = set_divider(split, "d1", {"pos": 0.6})
    split = recompute(split)
    a = _cell_by_id(split, "A")["rect"]
    b = _cell_by_id(split, "B")["rect"]
    assert abs(a[0] - 0.0) < 1e-9 and abs(a[1] - 0.0) < 1e-9
    assert abs(a[2] - 1.0) < 1e-9
    assert abs(a[3] - 0.6) < 1e-9, a
    assert abs(b[0] - 0.0) < 1e-9
    assert abs(b[1] - 0.6) < 1e-9, b
    assert abs(b[2] - 1.0) < 1e-9
    assert abs(b[3] - 0.4) < 1e-9, b


def test_left_right_fit_divider_moves_like_left_right():
    from cutora.render.split_layout import apply_layout, set_divider

    split = apply_layout({"enabled": True, "layout": "left_right_fit"})
    assert split["layout"] == "left_right_fit"
    split = set_divider(split, "d1", {"pos": 0.4})
    a = _cell_by_id(split, "A")["rect"]
    b = _cell_by_id(split, "B")["rect"]
    assert abs(a[2] - 0.4) < 1e-9, a
    assert abs(b[0] - 0.4) < 1e-9, b
    assert abs(b[2] - 0.6) < 1e-9, b


def test_left_right_fit_contain_and_blur_plate_leave_left_right_alone():
    """左右完整 forces fit inside each half and a cover plate behind. 左右 still fills."""
    _qapp()
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.split_layout import apply_layout
    from cutora.render.split_screen import paint_split_video_tracks

    class Clip:
        def __init__(self):
            self.fit_mode = "fill"

    class Track:
        def __init__(self, cell, color):
            self.split_cell = cell
            self.id = cell
            self.clips = [Clip()]
            self.color = QColor(color)

    def _paint(task, layout):
        modes = []

        def paint_one(p, tr, dw, dh):
            modes.append((tr.id, tr.clips[0].fit_mode, int(dw), int(dh)))
            p.fillRect(0, 0, int(dw), int(dh), tr.color)

        task["split"] = apply_layout({"enabled": True, "layout": layout})
        canvas = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(0)
        painter = QPainter(canvas)
        tracks = [Track("A", "#FF2040"), Track("B", "#20C040")]
        try:
            paint_split_video_tracks(painter, task, tracks, 200, 100, paint_one)
        finally:
            painter.end()
        return modes, tracks, canvas

    modes, tracks, canvas = _paint({}, "left_right_fit")
    fills = [m for m in modes if m[1] == "fill"]
    fits = [m for m in modes if m[1] == "fit"]
    assert fills == [("A", "fill", 200, 100)], modes
    assert len(fits) == 2, modes
    assert {m[0] for m in fits} == {"A", "B"}
    assert all(m[2] < 200 and m[3] == 100 for m in fits), modes
    assert tracks[0].clips[0].fit_mode == "fill"
    assert tracks[1].clips[0].fit_mode == "fill"
    assert canvas.pixelColor(100, 50).alpha() > 0

    modes_lr, tracks_lr, _canvas_lr = _paint({}, "left_right")
    assert modes_lr and all(m[1] == "fill" and m[2] < 200 for m in modes_lr), modes_lr
    assert tracks_lr[0].clips[0].fit_mode == "fill"


def test_split_rotation_mask_edge_matches_stroke_path():
    """rotation 30°: mask edge and stroke path coincide (20 samples, < 1 px)."""
    _qapp()
    from cutora.render.split_layout import apply_layout, set_divider
    from cutora.render.split_screen import mask_edge_points, stroke_path_points

    split = apply_layout({"enabled": True, "layout": "top_bottom"})
    split = set_divider(split, "d1", {"rotation_deg": 30.0, "feather_px": 24})
    canvas = (200, 400)
    edge = mask_edge_points(split, "d1", canvas, n=20)
    stroke = stroke_path_points(split, "d1", canvas, n=20)
    assert len(edge) == 20, len(edge)
    assert len(stroke) == 20, len(stroke)
    for i, ((ex, ey), (sx, sy)) in enumerate(zip(edge, stroke)):
        dist = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
        assert dist < 1.0, (i, dist, (ex, ey), (sx, sy))


def _solid_qimage(w, h, rgb):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(rgb[0], rgb[1], rgb[2], 255))
    return img


def _alpha_at(img, x, y):
    c = img.pixelColor(int(x), int(y))
    return c.alpha()


def test_split_feather_alpha_monotonic():
    """top_bottom + feather 24: divider-band alpha of cell A goes 1 → 0 monotonically."""
    _qapp()
    from cutora.render.split_layout import apply_layout
    from cutora.render.split_screen import cell_mask_qimage

    split = apply_layout({"enabled": True, "layout": "top_bottom"})
    w, h = 80, 200
    mask = cell_mask_qimage(split, "A", w, h)
    # vertical column through centre; A is the top cell
    xs = w // 2
    band_y0 = int(h * 0.5) - 30
    band_y1 = int(h * 0.5) + 30
    alphas = [_alpha_at(mask, xs, y) for y in range(band_y0, band_y1 + 1)]
    assert alphas[0] >= 250, alphas[0]
    assert alphas[-1] <= 5, alphas[-1]
    # non-increasing (allow tiny noise of 1)
    for i in range(1, len(alphas)):
        assert alphas[i] <= alphas[i - 1] + 1, (i, alphas[i - 1], alphas[i], alphas)


def _glass_split():
    from cutora.render.split_layout import apply_layout
    split = apply_layout({"enabled": True, "layout": "top_bottom"})
    split["border"] = {
        "style": "glass", "width_px": 20, "color": "#F2C14E",
        "color2": "#FFFFFF", "curve": "straight", "shadow_px": 12, "glass_sigma": 16,
    }
    # Sharp cell edge so a pixel 8px off the divider is pure cell color
    # without glass, and mixed only if _draw_glass_band actually sampled.
    for d in split.get("dividers") or []:
        d["feather_px"] = 0.0
    return split


def _paint_split_layers(split, w, h, layers):
    """Product path: paint_split_video_tracks with cell-tagged QImages."""
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.split_screen import _LayerTrack, paint_split_video_tracks

    tracks = [_LayerTrack(layer["cell_id"], layer["image"]) for layer in layers]

    def _paint_one(dest, tr, dw, dh):
        img = tr._image
        dest.fillRect(0, 0, dw, dh, img.pixelColor(img.width() // 2, img.height() // 2))

    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    try:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_split_video_tracks(p, {"split": split}, tracks, w, h, _paint_one)
    finally:
        p.end()
    return out


def test_split_glass_band_variance():
    """Off-path pixel must pick up the other cell only when glass blur runs.

    Feather 0 + highlight rim (pen ~4px) do not reach y = h/2+8. The blur
    band (width/2 + sigma ≈ 14px) does. Variance of a red/blue edge is not
    this test — that is true without glass.
    """
    _qapp()
    from cutora.render.split_screen import reset_cache

    reset_cache()
    split = _glass_split()
    w, h = 64, 128
    red = _solid_qimage(w, h, (220, 20, 20))
    blu = _solid_qimage(w, h, (20, 40, 220))
    layers = [{"cell_id": "A", "image": red}, {"cell_id": "B", "image": blu}]
    glass = _paint_split_layers(split, w, h, layers)
    none_split = copy.deepcopy(split)
    none_split["border"]["style"] = "none"
    none = _paint_split_layers(none_split, w, h, layers)
    xs, ys = w // 2, h // 2 + 8
    g = glass.pixelColor(xs, ys)
    n = none.pixelColor(xs, ys)
    assert n.blue() > 180 and n.red() < 80, (n.red(), n.green(), n.blue())
    assert g.red() > n.red() + 15, (g.red(), n.red(), g.blue(), n.blue())


def test_split_glass_widget_matches_image():
    """QWidget painter (canvas path) must draw glass; QWidget has no copy()."""
    _qapp()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QWidget
    from cutora.render.split_screen import (
        _LayerTrack, mean_abs_diff, paint_split_video_tracks,
    )

    split = _glass_split()
    none_split = copy.deepcopy(split)
    none_split["border"]["style"] = "none"
    w, h = 64, 128
    red = _solid_qimage(w, h, (220, 20, 20))
    blu = _solid_qimage(w, h, (20, 40, 220))
    tracks = [_LayerTrack("A", red), _LayerTrack("B", blu)]

    def _paint_one(dest, tr, dw, dh):
        img = tr._image
        dest.fillRect(0, 0, dw, dh, img.pixelColor(img.width() // 2, img.height() // 2))

    def _paint_to(target, spec):
        p = QPainter(target)
        try:
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, 255))
            paint_split_video_tracks(p, {"split": spec}, tracks, w, h, _paint_one)
        finally:
            p.end()

    on_image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    on_image.fill(QColor(0, 0, 0, 255))
    _paint_to(on_image, split)

    none_image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    none_image.fill(QColor(0, 0, 0, 255))
    _paint_to(none_image, none_split)

    class Host(QWidget):
        def paintEvent(self, ev):
            p = QPainter(self)
            try:
                assert not hasattr(p.device(), "copy")
                p.fillRect(self.rect(), QColor(0, 0, 0, 255))
                paint_split_video_tracks(
                    p, {"split": split}, tracks, w, h, _paint_one)
            finally:
                p.end()

    host = Host()
    host.setFixedSize(w, h)
    host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    host.setAutoFillBackground(False)
    host.show()
    grabbed = host.grab().toImage()
    if grabbed.width() != w or grabbed.height() != h:
        grabbed = grabbed.scaled(w, h)
    xs, ys = w // 2, h // 2 + 8
    g = grabbed.pixelColor(xs, ys)
    n = none_image.pixelColor(xs, ys)
    # Widget device has no copy(); blur must still land (red leaked into blue cell).
    assert n.blue() > 180 and n.red() < 80, (n.red(), n.green(), n.blue())
    assert g.red() > n.red() + 15, (g.red(), n.red(), g.blue(), n.blue())
    host.close()


def _make_color_mp4(path, color, duration=0.6, fps=15, size="48x96"):
    import subprocess
    from cutora.core.config import FFMPEG_PATH
    r = subprocess.run(
        [
            FFMPEG_PATH, "-y", "-f", "lavfi",
            "-i", "color=c=%s:s=%s:d=%.2f:r=%d" % (color, size, duration, fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path,
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 400, (
        r.stderr or "")[-400:]
    return path


def _split_task_two_videos(red_path, blu_path):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.render.split_layout import apply_split_to_task, set_track_cell

    tl = Timeline(48, 96, 15)
    src_a = MediaSource(red_path)
    src_b = MediaSource(blu_path)
    tl.sources[src_a.id] = src_a
    tl.sources[src_b.id] = src_b
    ta, tb = Track("video"), Track("video")
    ta.clips.append(Clip(src_a.id, 0.0, 0.6, 0.0))
    tb.clips.append(Clip(src_b.id, 0.0, 0.6, 0.0))
    tl.add_track(ta)
    tl.add_track(tb)
    task = {
        "timeline": tl, "video": red_path, "segments": [],
        "text_overlays": [], "image_overlays": [],
        "duration": 0.6, "trim_end_sec": 0.6,
    }
    apply_split_to_task(task, layout="top_bottom", enabled=True, border={
        "style": "glass", "width_px": 8, "color": "#F2C14E",
        "color2": "#FFFFFF", "curve": "straight", "shadow_px": 8, "glass_sigma": 6,
    })
    set_track_cell(task, ta.id, "A")
    set_track_cell(task, tb.id, "B")
    return task


def _force_software_decode(monkeypatch):
    import cutora.preview.engine as eng_mod
    orig = eng_mod._open_video_capture
    monkeypatch.setattr(
        eng_mod, "_open_video_capture",
        lambda path, hw=True, alpha_mode=None, threads=None: orig(
            path, hw=False, alpha_mode=alpha_mode, threads=threads),
    )


def _compose_export_frame(task, t):
    """One frame through RenderWorker._compose_export_frame (product export)."""
    import threading
    from PySide6.QtCore import Qt, QThread
    from PySide6.QtGui import QImage
    from cutora.model.canvas import canvas_wh
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_export_render_order
    from cutora.render.render_worker import RenderWorker, _ExportCapPool
    from cutora.render.textfx.spec import task_has_glass_bg

    w, h = canvas_wh(task)
    tl = task.get("timeline")
    rw = RenderWorker.__new__(RenderWorker)
    QThread.__init__(rw)
    rw._geom_last_log_t = 0.0
    rw.project = task
    rw._pipe_stop = threading.Event()
    rw._is_running = True
    video_tracks = video_tracks_for_render(task)
    src_paths = {}
    src_last_end = {}
    frame_states = {}
    for tr in video_tracks:
        for clip_obj in getattr(tr, "clips", []) or []:
            sid = getattr(clip_obj, "source_id", None)
            if not sid:
                continue
            src = tl.sources.get(sid) if tl else None
            src_path = getattr(src, "path", None) if src else None
            if src_path and os.path.exists(src_path):
                src_paths[sid] = src_path
            try:
                cs = float(getattr(clip_obj, "start_t", 0.0) or 0.0)
                eff = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                if eff <= 0:
                    eff = max(0.0, float(getattr(clip_obj, "out_t", 0.0))
                              - float(getattr(clip_obj, "in_t", 0.0)))
                ce = cs + eff
            except Exception:
                ce = float("inf")
            src_last_end[sid] = max(src_last_end.get(sid, 0.0), ce)
    video_caps = _ExportCapPool(src_paths, src_last_end, frame_states)
    ctx = ExportComposeCtx(
        project=task, width=w, height=h,
        video_tracks=video_tracks, video_caps=video_caps,
        frame_states=frame_states, fallback_cap=None,
        overlay_path=None, fallback_clip=None,
        render_order=compute_export_render_order(task),
        overlay_index=ExportRenderer.build_overlay_index(task),
        task_glass=task_has_glass_bg(task),
    )
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.black)
    try:
        return rw._compose_export_frame(float(t), canvas, ctx)
    finally:
        video_caps.clear()


def test_split_export_preview_mad(tmp_path, monkeypatch):
    """PreviewEngine.frame_at vs RenderWorker._compose_export_frame, MAD ≤ 0.015."""
    _qapp()
    from cutora.preview.engine import PreviewEngine
    from cutora.render.split_screen import mean_abs_diff

    _force_software_decode(monkeypatch)
    red = _make_color_mp4(str(tmp_path / "red.mp4"), "red")
    blu = _make_color_mp4(str(tmp_path / "blu.mp4"), "blue")
    task = _split_task_two_videos(red, blu)
    t = 0.2
    eng = PreviewEngine()
    try:
        preview = eng.frame_at(task, t)
        t_snap = eng._snap_t(task, t)
        export = _compose_export_frame(task, t_snap)
        mad = mean_abs_diff(preview, export)
        assert mad <= 0.015, mad
    finally:
        eng.close()


def test_split_cache_frame_cost():
    """Cached mask/path: subsequent frames of same params cost < 1 ms each (mean)."""
    _qapp()
    from cutora.render.split_layout import apply_layout
    from cutora.render.split_screen import reset_cache

    split = apply_layout({"enabled": True, "layout": "top_bottom"})
    w, h = 180, 320
    red = _solid_qimage(w, h, (180, 40, 40))
    blu = _solid_qimage(w, h, (40, 40, 180))
    layers = [{"cell_id": "A", "image": red}, {"cell_id": "B", "image": blu}]
    reset_cache()
    _paint_split_layers(split, w, h, layers)  # warm
    n = 12
    t0 = time.perf_counter()
    for _ in range(n):
        _paint_split_layers(split, w, h, layers)
    elapsed = time.perf_counter() - t0
    per = elapsed / n
    assert per < 0.001, per


def _dump_task(task):
    from cutora.model.project_io import serialize_render_task
    raw = serialize_render_task(task)
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_split_commands_undo_byte_for_byte():
    """split.set_layout / set_divider / track.set_cell: one undo restores JSON bytes."""
    from cutora.core.commands import CommandBus, get_registry
    from cutora.core.history import HistoryManager
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.render.split_layout import apply_split_to_task, set_divider_on_task, set_track_cell

    tl = Timeline(1920, 1080, 30)
    tracks = [Track("video"), Track("video")]
    tracks[0].name = "上"
    tracks[1].name = "下"
    for tr in tracks:
        tl.add_track(tr)
    task = {"timeline": tl}

    class _Win:
        def __init__(self):
            self.tasks = [task]
            self.curr_idx = 0
            self.project_path = "mem"
            self.history_mgr = HistoryManager()

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

        def command_split_set_layout(self, layout, enabled=None, border=None):
            apply_split_to_task(
                self.tasks[self.curr_idx],
                layout=layout, enabled=enabled, border=border)
            return {"layout": layout}

        def command_split_set_divider(self, divider_id, patch):
            set_divider_on_task(self.tasks[self.curr_idx], divider_id, patch)
            return {"id": divider_id, "patch": dict(patch or {})}

        def command_track_set_cell(self, track_id, cell):
            set_track_cell(self.tasks[self.curr_idx], track_id, cell)
            return {"track_id": track_id, "cell": cell}

    win = _Win()
    bus = CommandBus(get_registry())
    bus.bind_window(win)
    before = _dump_task(win.tasks[0])

    bus.execute("split.set_layout", {"layout": "top_bottom", "enabled": True}, source="ui")
    bus.execute(
        "track.set_cell",
        {"track_id": str(tracks[0].id), "cell": "A"},
        source="ui",
    )
    bus.execute(
        "track.set_cell",
        {"track_id": str(tracks[1].id), "cell": "B"},
        source="ui",
    )
    bus.execute(
        "split.set_divider",
        {"id": "d1", "pos": 0.6, "rotation_deg": 8.0, "feather_px": 16},
        source="ui",
    )
    after = _dump_task(win.tasks[0])
    assert after != before
    sp = win.tasks[0].get("split") or {}
    assert sp.get("enabled") is True
    assert abs(float((_cell_by_id(sp, "A") or {}).get("rect")[3]) - 0.6) < 1e-9
    assert getattr(win.tasks[0]["timeline"].tracks[0], "split_cell", None) == "A"

    # three undos: divider, cell B, cell A — then layout. Walk back to origin.
    for _ in range(4):
        win.undo()
    restored = _dump_task(win.tasks[0])
    assert restored == before, (before[:200], restored[:200])


def test_split_commands_registered_undoable():
    from cutora.core.commands import get_registry
    for name in ("split.set_layout", "split.set_divider", "track.set_cell"):
        spec = get_registry().get(name)
        assert spec.undoable is True, name
        assert spec.portable is False, name
        assert spec.headless is False, name
        assert len(spec.doc_zh) >= 40, name


def test_property_panel_split_section():
    _qapp()
    from cutora.model.timeline_model import Track
    from cutora.ui.panels.property import PropertyPanel
    panel = PropertyPanel()
    tr = Track("video")
    tr.name = "A轨"
    panel.load_data("track", tr)
    assert panel.stack.currentIndex() == 5
    assert hasattr(panel, "trk_split_enable")
    assert hasattr(panel, "trk_split_cell")
    assert hasattr(panel, "trk_split_pos")
    assert hasattr(panel, "trk_split_border_style")
    src = open(
        os.path.join(REPO, "cutora", "ui", "panels", "property.py"),
        encoding="utf-8",
    ).read()
    assert "分屏" in src
    assert "全局" in src
    panel.close()


def test_header_split_cell_badge():
    src = open(
        os.path.join(REPO, "cutora", "ui", "timeline", "header.py"),
        encoding="utf-8",
    ).read()
    assert "split_cell" in src
    csrc = open(
        os.path.join(REPO, "cutora", "ui", "canvas.py"),
        encoding="utf-8",
    ).read()
    assert "_draw_split_handles" in csrc
    assert "split.set_divider" in csrc


def test_split_disabled_skips_composite():
    """No task['split'] → split_enabled is False (golden identity path)."""
    from cutora.render.split_layout import split_enabled
    assert split_enabled({}) is False
    assert split_enabled({"split": {"enabled": False}}) is False
    assert split_enabled({"split": {"enabled": True, "layout": "top_bottom"}}) is True


def test_video_tracks_for_render_skips_hidden_and_muted():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.model.track_guard import video_tracks_for_render

    tl = Timeline(48, 96, 15)
    vis, hid, mute, empty = Track("video"), Track("video"), Track("video"), Track("video")
    vis.clips.append(Clip("s", 0.0, 1.0, 0.0))
    hid.clips.append(Clip("s", 0.0, 1.0, 0.0))
    hid.visible = False
    mute.clips.append(Clip("s", 0.0, 1.0, 0.0))
    mute.mute = True
    tl.add_track(vis)
    tl.add_track(hid)
    tl.add_track(mute)
    tl.add_track(empty)
    got = video_tracks_for_render({"timeline": tl})
    assert got == [vis], [id(t) for t in got]
