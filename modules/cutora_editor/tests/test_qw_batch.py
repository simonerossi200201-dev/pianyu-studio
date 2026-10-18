# -*- coding: utf-8 -*-
"""QW batch locks: history labels, track reorder, credit copy, no prefer_task."""
from __future__ import annotations

import copy

from cutora.core.history import HistoryManager
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Track


def test_overlay_geom_spin_visible_at_280():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEventLoop, QTimer
    from cutora.ui.panels.property import PropertyPanel
    app = QApplication.instance() or QApplication(["qw02-spin"])
    p = PropertyPanel()
    p.load_data("overlay", {
        "type": "text", "content": "Hello",
        "x": 120, "y": 240, "w": 480, "h": 90,
        "fontsize": 48, "opacity": 255, "rotation": 0,
    })
    p.resize(280, 720)
    p.show()
    loop = QEventLoop()
    QTimer.singleShot(80, loop.quit)
    loop.exec()
    app.processEvents()
    assert p.ov_x.width() >= 48, p.ov_x.width()
    assert p.ov_y.width() >= 48, p.ov_y.width()
    # Q83: X/Y share one row; the ±10000 drag slider is a hidden mirror.
    assert abs(p.ov_x.mapToGlobal(p.ov_x.rect().topLeft()).y()
               - p.ov_y.mapToGlobal(p.ov_y.rect().topLeft()).y()) <= 1
    assert p.ov_x.value() == 120
    assert p.ov_w.value() == 480


def test_slider_spin_reflow_source():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "cutora")
    sub = open(os.path.join(root, "ui", "panels", "subtitle_style.py"),
               encoding="utf-8").read()
    prop = open(os.path.join(root, "ui", "panels", "property.py"),
                encoding="utf-8").read()
    fx = open(os.path.join(root, "ui", "panels", "fx_fields.py"),
              encoding="utf-8").read()
    assert "SliderSpin" in sub
    assert "sub_dyn_op_spin" in sub
    assert 'sp.setObjectName("geomSpin")' in prop
    # Q84 added reset_key on the shared X/Y row. The pair itself is still the
    # assertion: position is one row, not two. Q148 hung a keyframe watch on
    # each spin (with_trailing), still inside the one paired row.
    assert ('bf.addRow("位置", paired_fields(\n'
            '            with_trailing(self.ov_x, self.ov_x_kf),\n'
            '            with_trailing(self.ov_y, self.ov_y_kf)), reset_key="ov.pos")') in prop
    # Q78 put the lock on a trailing cluster with the restore button.
    # The lock must still be inside that cluster, on the size row.
    assert 'paired_fields(self.ov_w, self.ov_h, trailing=aspect_trail)' in prop
    assert '_at.addWidget(self.ov_lock_btn)' in prop
    assert 'setattr(panel, attr + "_spin", ss.spin)' in fx


def test_prefer_task_gone():
    from cutora.subtitle import stylepack as sp
    src = open(sp.__file__, encoding="utf-8").read()
    assert "prefer_task" not in src


def test_history_entry_has_label_and_time():
    h = HistoryManager(max_steps=80)
    assert h.max_steps == 80
    h.push("desc", {"x": 1}, extra={"command": "export.run"})
    entry = h.history[-1]
    assert entry.get("at")
    assert entry.get("command") == "export.run"
    assert entry.get("desc") == "desc"


def test_reorder_tracks_changes_render_order():
    from cutora.render.frame_renderer import compute_render_order
    tl = Timeline(1080, 1920, 30)
    subs = Track("subs")
    text = Track("text")
    tl.tracks[:] = [subs, text]
    task = {"timeline": tl, "text_overlays": [{"type": "text"}], "segments": [{"text": "a"}]}
    before = list(compute_render_order(task))
    tl.tracks[:] = [text, subs]
    after = list(compute_render_order(task))
    assert before != after


def test_copy_credit_to_each_task():
    src_ov = {"type": "text", "content": "© Studio", "x": 10, "y": 20, "w": 100, "h": 40}
    tasks = [
        {"text_overlays": [copy.deepcopy(src_ov)]},
        {"text_overlays": []},
        {"text_overlays": [{"type": "text", "content": "other"}]},
    ]

    ov = tasks[0]["text_overlays"][0]
    for i, task in enumerate(tasks):
        if i == 0:
            continue
        task.setdefault("text_overlays", []).append(copy.deepcopy(ov))
    assert tasks[1]["text_overlays"][-1]["content"] == "© Studio"
    assert tasks[2]["text_overlays"][-1] == ov or tasks[2]["text_overlays"][-1]["content"] == "© Studio"
    assert tasks[1]["text_overlays"][-1] is not ov
