# -*- coding: utf-8 -*-
"""Q84: every visible ↺ and every「重置本段」restores factory defaults.

The checks load a PropertyPanel the way the app does, with values that are
not the defaults, then click the real buttons. A reset that only logs
"inspector reset with no handler" fails. Subtitle Q60 is left on its own
path (build_inspector + _row_reset).
"""
from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_MISS = object()


def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _close(a, b, tol=1e-3) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    if isinstance(a, str) and isinstance(b, str):
        if a.startswith("#") and b.startswith("#"):
            return a.upper() == b.upper()
        return a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a) | set(b)
        return all(_close(a.get(k), b.get(k), tol) for k in keys)
    return a == b


def _state(obj):
    if isinstance(obj, dict):
        return copy.deepcopy(obj)
    return copy.deepcopy(getattr(obj, "__dict__", {}))


def _walk(state, prefix=""):
    if isinstance(state, dict):
        for key, val in state.items():
            path = "%s.%s" % (prefix, key) if prefix else str(key)
            if isinstance(val, dict):
                yield from _walk(val, path)
            else:
                yield path, val
    else:
        if prefix:
            yield prefix, state


def _get(state, path):
    cur = state
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISS
        cur = cur[part]
    return cur


_IGNORE_PATHS = {
    "geom_user_set",
    "fontsize_pct",
    "start_t",
    "_match_target_dur",
    "id",
    # Derived from tracking_em × fontsize when any text field is saved.
    "letter_spacing",
    # Track owns clips; deepcopy makes a new object every snapshot.
    "clips",
    "transitions",
    "sources",
}


def _ignored(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1]
    return leaf in _IGNORE_PATHS or path in _IGNORE_PATHS


def _diff_matches_factory(before, after, factory) -> list:
    """Paths that changed must land on the factory value (missing+zero is ok)."""
    bad = []
    bmap = dict(_walk(before))
    amap = dict(_walk(after))
    paths = set(bmap) | set(amap)
    for path in sorted(paths):
        if _ignored(path):
            continue
        bv = bmap.get(path, _MISS)
        av = amap.get(path, _MISS)
        if _close(bv, av):
            continue
        fv = _get(factory, path)
        if fv is _MISS and av in (0, 0.0, False, None, "", "none", "straight"):
            continue
        if not _close(av, fv):
            bad.append((path, fv, av))
    return bad


class _Host:
    """Enough of a window for undo and the command bus. Not a MainWindow."""

    def __init__(self, task, panel):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        from cutora.core.history import HistoryManager
        from cutora.ui.command_bodies import CommandBodies

        class _Win(CommandBodies, QWidget):
            def __init__(self, task, panel):
                super().__init__()
                self.tasks = [task]
                self.curr_idx = 0
                self.project_path = "mem://q84"
                self.history_mgr = HistoryManager()
                self.history_mgr.max_steps = 10000
                self.prop_panel = panel
                lay = QVBoxLayout(self)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(panel)

            def push_undo(self, action_name="操作", command=None, params=None, source=None):
                self.history_mgr.push(action_name, {
                    "tasks": copy.deepcopy(self.tasks),
                    "curr_idx": self.curr_idx,
                })

            def capture_undo_after(self):
                self.history_mgr.set_after({
                    "tasks": copy.deepcopy(self.tasks),
                    "curr_idx": self.curr_idx,
                })

            def undo(self):
                state = self.history_mgr.undo()
                if not state:
                    return None
                self._live_tasks = self.tasks
                self.tasks = state["tasks"]
                self.curr_idx = state["curr_idx"]
                return state

            def resume_live(self):
                live = getattr(self, "_live_tasks", None)
                if live is not None:
                    self.tasks = live
                    self._live_tasks = None

            def _refresh_task_view(self, _idx):
                return None

        self.win = _Win(task, panel)

    def __getattr__(self, name):
        return getattr(self.win, name)


def _controls(row):
    from PySide6.QtWidgets import (
        QAbstractSpinBox, QCheckBox, QComboBox, QLabel, QPlainTextEdit,
        QSlider, QToolButton, QWidget,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import ColorSwatch, KeyframeWatch, SliderSpin

    root = row.field
    if root is None:
        return []
    found = []
    nodes = [root]
    if isinstance(root, QWidget):
        nodes.extend(root.findChildren(QWidget))
    for w in nodes:
        if isinstance(w, (QLabel, KeyframeWatch, QSlider)):
            continue
        if isinstance(w, QPlainTextEdit):
            if w.isHidden() and w is not root:
                continue
            found.append(w)
            continue
        if isinstance(w, QToolButton) and (w.objectName() == "inspectorReset" or not w.isCheckable()):
            continue
        parent = w.parent()
        if isinstance(parent, SliderSpin) and not isinstance(w, SliderSpin):
            continue
        if isinstance(w, (SliderSpin, QAbstractSpinBox, QCheckBox, ToggleSwitch,
                          QComboBox, ColorSwatch, QToolButton)) or any(
                hasattr(w, meth) for meth in ("set_colors", "set_flags", "set_pair")):
            if w.isHidden() and w is not root:
                continue
            found.append(w)
    # de-dupe while keeping order
    out = []
    seen = set()
    for w in found:
        if id(w) in seen:
            continue
        seen.add(id(w))
        out.append(w)
    return out


def _read(w):
    from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QToolButton
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import ColorSwatch, SegmentedChoice, SliderSpin, ValueCombo
    if isinstance(w, SliderSpin):
        return ("num", w.value())
    if isinstance(w, QAbstractSpinBox):
        return ("num", w.value())
    if isinstance(w, (QCheckBox, ToggleSwitch, QToolButton)):
        return ("on", bool(w.isChecked()))
    if isinstance(w, ColorSwatch):
        return ("hex", (w.hex() or "").upper())
    if isinstance(w, QPlainTextEdit):
        return ("plain", w.toPlainText())
    if isinstance(w, (ValueCombo, SegmentedChoice)):
        return ("text", w.currentText())
    if isinstance(w, QComboBox):
        data = w.currentData()
        if data is not None:
            return ("data", data)
        return ("text", w.currentText())
    if hasattr(w, "colors") and callable(w.colors):
        return ("colors", tuple(w.colors() or []))
    if hasattr(w, "flags") and callable(w.flags):
        return ("flags", tuple(w.flags() or []))
    if hasattr(w, "pair") and callable(w.pair):
        return ("pair", tuple(w.pair() or []))
    return ("type", type(w).__name__)


def _read_row(row):
    return tuple(_read(w) for w in _controls(row))


def _nudge(w) -> bool:
    from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QToolButton
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import ColorSwatch, SegmentedChoice, SliderSpin, ValueCombo
    if isinstance(w, SliderSpin):
        lo, hi = float(w.minimum()), float(w.maximum())
        cur = float(w.value())
        nxt = lo if abs(cur - lo) > 1e-6 else hi
        if abs(nxt - cur) < 1e-6:
            return False
        w.setValue(nxt)
        return True
    if isinstance(w, QAbstractSpinBox):
        cur = w.value()
        nxt = w.minimum() if cur != w.minimum() else w.maximum()
        if nxt == cur:
            return False
        w.setValue(nxt)
        return True
    if isinstance(w, (QCheckBox, ToggleSwitch)):
        w.setChecked(not w.isChecked())
        return True
    if isinstance(w, QToolButton) and w.isCheckable():
        w.setChecked(not w.isChecked())
        return True
    if isinstance(w, ColorSwatch):
        nxt = "#112233" if (w.hex() or "").upper() != "#112233" else "#ABCDEF"
        w.set_hex(nxt, emit=True)
        return True
    if isinstance(w, QPlainTextEdit):
        w.setPlainText("DIRTY" if w.toPlainText() != "DIRTY" else "OTHER")
        return True
    if isinstance(w, SegmentedChoice):
        cur = w.currentText()
        for val in getattr(w, "_values", []):
            if val != cur:
                w.setCurrentText(val)
                return True
        return False
    if isinstance(w, (ValueCombo, QComboBox)):
        if w.count() < 2:
            return False
        nxt = 0 if w.currentIndex() != 0 else 1
        w.setCurrentIndex(nxt)
        return True
    if hasattr(w, "set_colors") and hasattr(w, "colors"):
        w.set_colors(["#112233", "#ABCDEF"], emit=True)
        return True
    if hasattr(w, "set_flags") and hasattr(w, "flags"):
        cur = list(w.flags() or [])
        w.set_flags(["caps"] if "caps" not in cur else [], emit=True)
        return True
    return False


def _sections(panel):
    from cutora.ui.widgets.collapsible import CollapsibleSection
    return panel.findChildren(CollapsibleSection)


def _expand(panel):
    from PySide6.QtWidgets import QApplication
    for sec in _sections(panel):
        # Do not show() a section the page hid (image page has no text fx).
        if sec.isHidden():
            continue
        sec.set_collapsed(False)
    QApplication.processEvents()


def _row_buttons(panel):
    from PySide6.QtWidgets import QToolButton
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from cutora.ui.widgets.inspector import InspectorRow
    out = []
    for btn in panel.findChildren(QToolButton):
        if (btn.text() or "") != "↺":
            continue
        if not btn.isVisible() or not btn.isEnabled():
            continue
        row = None
        sec = None
        cur = btn.parentWidget()
        while cur is not None:
            if isinstance(cur, InspectorRow) and row is None:
                row = cur
            if isinstance(cur, CollapsibleSection) and sec is None:
                sec = cur
            cur = cur.parentWidget()
        is_section = sec is not None and btn is getattr(sec, "_reset_btn", None)
        if is_section:
            continue
        if row is None:
            continue
        out.append((sec, row, btn))
    return out


def _section_buttons(panel):
    from PySide6.QtWidgets import QToolButton
    from cutora.ui.widgets.collapsible import CollapsibleSection
    out = []
    for sec in _sections(panel):
        btn = getattr(sec, "_reset_btn", None)
        if not isinstance(btn, QToolButton):
            continue
        if not btn.isVisible() or not btn.isEnabled():
            continue
        out.append((sec, btn))
    return out


def _label(sec, row):
    title = getattr(sec, "section_title", "") if sec is not None else ""
    text = row.full_label_text() if row is not None else ""
    key = getattr(row, "reset_key", None) or ""
    return title, text or key or "?", key


def _snap_rows(panel):
    snap = {}
    for sec, row, _btn in _row_buttons(panel):
        title, text, key = _label(sec, row)
        snap[(title, text, key, id(row))] = _read_row(row)
    return snap


def _bind(host):
    from cutora.core.commands import get_bus, reset_bus_for_tests
    reset_bus_for_tests()
    get_bus().bind_window(host.win)


def _task():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    tl = Timeline(320, 180, 30.0)
    video = Track("video")
    image = Track("image")
    text = Track("text")
    tl.tracks.extend([video, image, text])
    return {
        "dims": (320, 180),
        "duration": 4.0,
        "timeline": tl,
        "text_overlays": [],
        "image_overlays": [],
        "segments": [],
        "style": {},
        "grade": None,
    }, video, image, text


def _png(path, w, h):
    from PySide6.QtGui import QColor, QImage
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32)
    img.fill(QColor(20, 140, 200, 255))
    assert img.save(str(path), "PNG")


def _prepare(panel, host, loader):
    """Load factory defaults, remember widget values, then dirty every ↺ row."""
    from PySide6.QtWidgets import QApplication
    loader(factory=True)
    host.win.show()
    panel.resize(420, 900)
    host.win.resize(440, 940)
    _expand(panel)
    QApplication.processEvents()
    # Persist the clean widgets once so the object holds the same fx / grade
    # tree a reset writes, not the pre-load dict that omitted those keys.
    if panel.current_type in ("overlay", "sticker"):
        panel.on_change_ov()
        if hasattr(panel, "_on_ov_grade"):
            panel._on_ov_grade()
    elif panel.current_type == "video_clip":
        panel.on_change_video()
        if hasattr(panel, "_on_vid_grade"):
            panel._on_vid_grade()
    elif panel.current_type == "transition":
        panel.on_change_transition()
    elif panel.current_type == "track":
        panel._commit_track_props()
        if hasattr(panel, "_on_trk_grade"):
            panel._on_trk_grade()
        if hasattr(panel, "_on_prj_grade"):
            panel._on_prj_grade()
    QApplication.processEvents()
    expected = {}
    from cutora.ui.widgets.inspector import InspectorRow
    for sec in _sections(panel):
        if sec.isHidden():
            continue
        for row in sec.findChildren(InspectorRow):
            if not callable(getattr(row, "_on_reset", None)):
                continue
            expected[id(row)] = _read_row(row)
    factory_state = _state(loader(which="object"))
    loader(factory=False)
    _expand(panel)
    QApplication.processEvents()
    missed = []
    for sec, row, _btn in _row_buttons(panel):
        title, text, key = _label(sec, row)
        before = _read_row(row)
        if before != expected.get(id(row)):
            continue
        moved = False
        for w in _controls(row):
            if _nudge(w):
                moved = True
        QApplication.processEvents()
        after = _read_row(row)
        if not _controls(row):
            missed.append((title, text, key, "readable controls", after))
            continue
        if not moved or after == expected.get(id(row)):
            missed.append((title, text, key, expected.get(id(row)), after))
    # Spins that save on editingFinished (track / split) are not persisted
    # by setValue. Flush the same commits a user edit would, once.
    if panel.current_type == "track":
        panel._commit_track_props()
        panel._commit_split_divider()
        panel._commit_split_border()
    elif panel.current_type == "video_clip":
        panel.on_change_video()
        if hasattr(panel, "vid_blend"):
            panel.on_change_video_blend(panel.vid_blend.currentText())
    # Widgets follow what was stored (fontsize clamps to >= 8, and so on).
    loader(reload_only=True)
    _expand(panel)
    QApplication.processEvents()
    return expected, factory_state, missed


def _reload(panel, host, loader):
    from PySide6.QtWidgets import QApplication
    loader(factory=False, reload_only=True)
    _expand(panel)
    QApplication.processEvents()


def test_q84_every_visible_reset_restores(tmp_path):
    """Each visible row ↺ restores the factory widget value and the data."""
    _qapp()
    from PySide6.QtWidgets import QApplication
    from cutora.model.factory_defaults import clip_defaults, overlay_defaults, transition_defaults
    from cutora.model.timeline_model import Clip, MediaSource, TransitionItem
    from cutora.render.keyframe import path_enabled, set_point
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.property import PropertyPanel

    load_embedded_fonts()
    panel = PropertyPanel()
    task, video, _image, _text = _task()
    host = _Host(task, panel)
    _bind(host)
    png = str(tmp_path / "fit.png")
    _png(png, 80, 40)

    pages = []

    def _text_page(factory=True, which=None, reload_only=False):
        if which == "object":
            return task["text_overlays"][0]
        if not reload_only:
            ov = overlay_defaults("text", canvas_w=320, canvas_h=180, end=4.0)
            ov["content"] = "不要被重置清掉"
            ov["_track_id"] = _text.id
            task["text_overlays"] = [ov]
        panel.load_data("overlay", task["text_overlays"][0])

    def _image_page(factory=True, which=None, reload_only=False):
        if which == "object":
            return task["image_overlays"][0]
        if not reload_only:
            ov = overlay_defaults(
                "image", canvas_w=320, canvas_h=180, image_wh=(80, 40),
                path=png, start=0.0, end=4.0, track_id=_image.id, ov_id="ov_q84")
            task["image_overlays"] = [ov]
        panel.load_data("overlay", task["image_overlays"][0])

    def _video_page(factory=True, which=None, reload_only=False):
        if which == "object":
            return video.clips[0]
        if not reload_only:
            clip = Clip("src", 0.0, 2.0, 0.5)
            video.clips = [clip]
            task["timeline"].sources["src"] = _fake_source()
            # Keyframes are the dirty state, not the factory default.
            if factory is False:
                set_point(clip, "x", 0.0, 12.0)
                set_point(clip, "x", 1.0, 40.0)
        panel.load_data("video_clip", (video, video.clips[0]))

    def _trans_page(factory=True, which=None, reload_only=False):
        if which == "object":
            return video.transitions[0]
        if not reload_only:
            item = TransitionItem(1.0, transition_defaults("wipe_left")["duration"], "wipe_left")
            video.transitions = [item]
        panel.load_data("transition", (video, video.transitions[0]))

    def _track_page(factory=True, which=None, reload_only=False):
        if which == "object":
            return video
        if not reload_only:
            video.name = ""
            video.props = dict(clip_props())
            video.blend_mode = "normal"
            video.split_cell = None
            task["split"] = None
            task["grade"] = None
            video.grade = None
        panel.load_data("track", video)

    pages = (
        ("文本层", _text_page),
        ("图片层", _image_page),
        ("视频", _video_page),
        ("转场", _trans_page),
        ("轨道", _track_page),
    )

    failures = []
    kf_note = {}
    try:
        host.win.show()
        for page, loader in pages:
            expected, factory_state, missed = _prepare(panel, host, loader)
            for title, text, key, exp, got in missed:
                failures.append((page, title, "%s [%s]" % (text, key), exp, got))
            rows = _row_buttons(panel)
            assert rows, "页面 %s 没有任何可见的行 ↺" % page
            for sec, row, btn in rows:
                title, text, key = _label(sec, row)
                if id(row) not in expected:
                    failures.append((page, title, text, "snapshot", "missing"))
                    continue
                if _read_row(row) == expected[id(row)]:
                    continue
                before = _state(loader(which="object"))
                if key == "vid.pos":
                    kf_note["before"] = path_enabled(video.clips[0], "x")
                n_hist = len(host.history_mgr.history)
                btn.click()
                QApplication.processEvents()
                got = _read_row(row)
                if got != expected[id(row)]:
                    failures.append((page, title, "%s [%s]" % (text, key), expected[id(row)], got))
                bad = _diff_matches_factory(before, _state(loader(which="object")), factory_state)
                if bad:
                    failures.append((page, title, "%s data" % text, bad[:6], "not factory"))
                if len(host.history_mgr.history) != n_hist + 1 and host.history_mgr.history:
                    # still one new entry even when the window recorded it
                    if len(host.history_mgr.history) - n_hist != 1:
                        failures.append((
                            page, title, "%s undo" % text,
                            "1 undo", len(host.history_mgr.history) - n_hist))
                if key == "vid.pos":
                    clip = video.clips[0]
                    kf_note["after_enabled"] = path_enabled(clip, "x")
                    kf_note["after_x"] = float(getattr(clip, "x", 999))
                    host.undo()
                    restored = None
                    for tr in host.tasks[0]["timeline"].tracks:
                        if getattr(tr, "clips", None):
                            restored = tr.clips[0]
                            break
                    kf_note["undo_enabled"] = path_enabled(restored, "x") if restored is not None else None
                    kf_note["undo_x"] = float(getattr(restored, "x", 999) or 0)
                    host.resume_live()
                    # undo() stepped the history pointer back. Put it on the
                    # tail so the next reset appends instead of replacing.
                    mgr = host.history_mgr
                    if mgr.history:
                        mgr.current_index = len(mgr.history) - 1
        if not kf_note.get("before"):
            failures.append(("视频", "变换", "位置", "had x keyframes", kf_note))
        if kf_note.get("after_enabled") is not False or abs(kf_note.get("after_x", 9) - 0.0) > 1e-3:
            failures.append(("视频", "变换", "位置关键帧", "cleared + x=0", kf_note))
        if kf_note.get("undo_enabled") is not True:
            failures.append(("视频", "变换", "位置撤销", "keyframes return", kf_note))
    finally:
        panel.close()
        host.win.close()
    assert not failures, "\n".join(repr(f) for f in failures[:40])


def _fake_source():
    from cutora.model.timeline_model import MediaSource
    # Avoid ffprobe: build the fields MediaSource would set.
    src = MediaSource.__new__(MediaSource)
    src.id = "src"
    src.path = ""
    src.has_video = True
    src.has_audio = False
    src.duration = 2.0
    src.alpha = False
    src.preview_path = None
    src.alpha_mode = "straight"
    return src


def clip_props():
    from cutora.model.factory_defaults import track_defaults
    return track_defaults()["props"]


def test_q84_section_reset_restores_all_rows(tmp_path):
    """One「重置本段」restores that section; one undo brings every row back."""
    _qapp()
    from PySide6.QtWidgets import QApplication
    from cutora.model.factory_defaults import overlay_defaults, transition_defaults
    from cutora.model.timeline_model import Clip, TransitionItem
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.widgets.inspector import InspectorRow

    load_embedded_fonts()
    panel = PropertyPanel()
    task, video, image_tr, text_tr = _task()
    host = _Host(task, panel)
    _bind(host)
    png = str(tmp_path / "fit.png")
    _png(png, 80, 40)
    failures = []

    def _loaders():
        def _cur():
            return host.win.tasks[0]

        def _tr(kind):
            return next(t for t in _cur()["timeline"].tracks if t.type == kind)

        def text(factory=True, which=None, reload_only=False):
            cur = _cur()
            if which == "object":
                return cur["text_overlays"][0]
            if not reload_only:
                ov = overlay_defaults("text", canvas_w=320, canvas_h=180, end=4.0)
                ov["content"] = "段重置不能改内容"
                ov["_track_id"] = _tr("text").id
                cur["text_overlays"] = [ov]
            panel.load_data("overlay", cur["text_overlays"][0])

        def image(factory=True, which=None, reload_only=False):
            cur = _cur()
            if which == "object":
                return cur["image_overlays"][0]
            if not reload_only:
                ov = overlay_defaults(
                    "image", canvas_w=320, canvas_h=180, image_wh=(80, 40),
                    path=png, end=4.0, track_id=_tr("image").id, ov_id="ov_q84b")
                cur["image_overlays"] = [ov]
            panel.load_data("overlay", cur["image_overlays"][0])

        def video_page(factory=True, which=None, reload_only=False):
            tr = _tr("video")
            if which == "object":
                return tr.clips[0]
            if not reload_only:
                tr.clips = [Clip("src", 0.0, 2.0, 0.2)]
            panel.load_data("video_clip", (tr, tr.clips[0]))

        def trans(factory=True, which=None, reload_only=False):
            tr = _tr("video")
            if which == "object":
                return tr.transitions[0]
            if not reload_only:
                tr.transitions = [TransitionItem(
                    0.4, transition_defaults("wipe_left")["duration"], "wipe_left")]
            panel.load_data("transition", (tr, tr.transitions[0]))

        def track(factory=True, which=None, reload_only=False):
            tr = _tr("video")
            cur = _cur()
            if which == "object":
                return tr
            if not reload_only:
                from cutora.render.grade import identity_grade
                from cutora.render.split_layout import default_split
                tr.props = dict(clip_props())
                tr.grade = identity_grade()
                cur["grade"] = identity_grade()
                cur["split"] = default_split()
            panel.load_data("track", tr)

        return (
            ("文本层", text),
            ("图片层", image),
            ("视频", video_page),
            ("转场", trans),
            ("轨道", track),
        )

    try:
        host.win.show()
        for page, loader in _loaders():
            expected, _factory, missed = _prepare(panel, host, loader)
            if missed:
                for title, text, key, _exp, got in missed:
                    failures.append((page, title, "mutate %s" % text, key, got))
                continue
            for sec, btn in _section_buttons(panel):
                title = sec.section_title
                rows = []
                for child in sec.findChildren(InspectorRow):
                    if not child.reset_btn.isVisible():
                        continue
                    key = (title, child.full_label_text() or getattr(child, "reset_key", ""), getattr(child, "reset_key", None) or "")
                    # match expected by title+label+key; labels can repeat (色相)
                    rows.append(child)
                dirty = {id(r): _read_row(r) for r in rows}
                content = ""
                edit = getattr(panel, "ov_content", None)
                if edit is not None and page == "文本层":
                    content = edit.toPlainText()
                mgr = host.history_mgr
                if mgr.history:
                    mgr.current_index = len(mgr.history) - 1
                n_hist = len(mgr.history)
                btn.click()
                QApplication.processEvents()
                added = len(host.history_mgr.history) - n_hist
                if added != 1:
                    failures.append((page, title, "undo count", 1, added))
                for row in rows:
                    lab = row.full_label_text() or getattr(row, "reset_key", "")
                    exp = expected.get(id(row))
                    got = _read_row(row)
                    if exp is None:
                        failures.append((page, title, lab, "expected snapshot", got))
                        continue
                    if got != exp:
                        failures.append((page, title, lab, exp, got))
                if page == "文本层" and edit is not None and edit.toPlainText() != content:
                    failures.append((page, title, "内容", content, edit.toPlainText()))
                if added != 1:
                    continue
                host.undo()
                loader(reload_only=True)
                _expand(panel)
                QApplication.processEvents()
                # After undo the task copy is the pre-click object. Reload and
                # compare to the dirty snapshot taken on the previous objects'
                # widgets — those values were saved into the task.
                for row in sec.findChildren(InspectorRow):
                    if id(row) not in dirty:
                        continue
                    # row objects were reparented? load_data does not rebuild rows.
                    got = _read_row(row)
                    if got != dirty[id(row)]:
                        lab = row.full_label_text() or getattr(row, "reset_key", "")
                        # 「四边一起调」is a local editing flag, not project data.
                        if str(getattr(row, "reset_key", "") or "").endswith("fade_sync"):
                            continue
                        failures.append((page, title, "undo %s" % lab, dirty[id(row)], got))
                # re-dirty for the next section from a clean factory again
                _prepare(panel, host, loader)
    finally:
        panel.close()
        host.win.close()
    assert not failures, "\n".join(repr(f) for f in failures[:40])


def test_q84_no_dead_reset(caplog):
    """Clicking any visible ↺ must not log 'inspector reset with no handler'."""
    import logging
    _qapp()
    from PySide6.QtWidgets import QApplication, QToolButton
    from cutora.model.factory_defaults import overlay_defaults, transition_defaults
    from cutora.model.timeline_model import Clip, TransitionItem
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.property import PropertyPanel

    load_embedded_fonts()
    panel = PropertyPanel()
    task, video, image_tr, text_tr = _task()
    host = _Host(task, panel)
    _bind(host)
    caplog.set_level(logging.WARNING)
    warnings = []
    try:
        host.win.show()
        setups = []
        ov = overlay_defaults("text", canvas_w=320, canvas_h=180, end=4.0)
        ov["_track_id"] = text_tr.id
        task["text_overlays"] = [ov]
        setups.append(lambda: panel.load_data("overlay", ov))
        img = overlay_defaults(
            "image", canvas_w=320, canvas_h=180, image_wh=(80, 40),
            path="x.png", track_id=image_tr.id)
        task["image_overlays"] = [img]
        setups.append(lambda: panel.load_data("overlay", img))
        video.clips = [Clip("src", 0.0, 1.0, 0.0)]
        setups.append(lambda: panel.load_data("video_clip", (video, video.clips[0])))
        video.transitions = [TransitionItem(0.0, transition_defaults("wipe_left")["duration"], "wipe_left")]
        setups.append(lambda: panel.load_data("transition", (video, video.transitions[0])))
        setups.append(lambda: panel.load_data("track", video))
        for setup in setups:
            n = len(caplog.records)
            setup()
            _expand(panel)
            QApplication.processEvents()
            for btn in panel.findChildren(QToolButton):
                if (btn.text() or "") != "↺":
                    continue
                if not btn.isVisible() or not btn.isEnabled():
                    continue
                btn.click()
                QApplication.processEvents()
            for rec in caplog.records[n:]:
                if "inspector reset with no handler" in rec.getMessage():
                    warnings.append(rec.getMessage())
    finally:
        panel.close()
        host.win.close()
    assert not warnings, warnings
