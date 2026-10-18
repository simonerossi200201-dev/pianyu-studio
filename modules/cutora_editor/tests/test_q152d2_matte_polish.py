# -*- coding: utf-8 -*-
"""Q152-D 第二批：显卡排队（AI 字幕 / 抠像 / 跟踪先到先得）、对象 保留 / 挖掉、缓存 mask 尺寸上限、
传播估时（实测速度）、低置信度帧跳转（面板按钮 + 时间线点黄段）。"""
import os
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_MATTE_BACKEND", "fake")
    monkeypatch.setenv("CUTORA_MATTE_CACHE", str(tmp_path / "matte_cache"))
    from cutora.core import gpu_gate
    from cutora.fx import matte_store as S
    from cutora.fx.layer_fx import clear_cache
    gpu_gate.reset_for_tests()
    S.invalidate()
    clear_cache()
    yield
    gpu_gate.reset_for_tests()


# ───────────────────────────── 显卡排队 ─────────────────────────────

def test_gpu_gate_fifo_wait_and_cancel():
    from cutora.core import gpu_gate as G
    t1 = G.acquire("AI 字幕")
    assert t1 and G.holder() == "AI 字幕" and G.busy()
    order = []
    waits = []

    def worker(name, delay):
        time.sleep(delay)
        with G.hold(name, on_wait=lambda who: waits.append((name, who))) as ok:
            assert ok
            order.append(name)
            time.sleep(0.05)

    a = threading.Thread(target=worker, args=("AI 抠像", 0.0))
    b = threading.Thread(target=worker, args=("AI 跟踪", 0.1))
    a.start()
    b.start()
    time.sleep(0.3)
    assert G.waiting() == ["AI 抠像", "AI 跟踪"] and order == []
    assert ("AI 抠像", "AI 字幕") in waits
    G.release(t1)
    a.join(3)
    b.join(3)
    assert order == ["AI 抠像", "AI 跟踪"] and not G.busy() and G.waiting() == []
    # 排队中取消：拿不到、号也撤掉
    t2 = G.acquire("A")
    ev = threading.Event()
    got = {}
    th = threading.Thread(target=lambda: got.setdefault("t", G.acquire("B", should_stop=ev.is_set)))
    th.start()
    time.sleep(0.2)
    assert G.waiting() == ["B"]
    ev.set()
    th.join(3)
    assert got["t"] == 0 and G.waiting() == [] and G.holder() == "A"
    G.release(t2)
    assert G.acquire("C", timeout=0.1) and G.holder() == "C"
    # 别人的票释放不掉
    G.release(12345)
    assert G.holder() == "C"


def test_matte_worker_queues_behind_asr(tmp_path):
    _app()
    from cutora.core import gpu_gate as G
    from cutora.fx import matte_store as S
    from cutora.ui.matte_worker import MatteWorker
    from test_q152d_ai_matte import _spec, _square_video
    vid = _square_video(str(tmp_path / "sq.mp4"), frames=6)
    sid = S.new_session_id()
    sp = _spec(sid, {"0": [{"obj": 1, "x": 50 / 320, "y": 0.5, "label": 1}]})
    t_asr = G.acquire("AI 字幕")
    w = MatteWorker("segment", {"spec": sp, "path": vid, "frame": 0})
    queued, done, failed = [], [], []
    w.queued.connect(queued.append)
    w.done.connect(done.append)
    w.failed.connect(failed.append)
    w.start()
    t0 = time.time()
    while not queued and time.time() - t0 < 5:
        QApplication.processEvents()
        time.sleep(0.02)
    assert queued == ["AI 字幕"] and not done
    assert S.coverage(sid) == {}
    G.release(t_asr)
    w.wait(10000)
    QApplication.processEvents()
    assert done and not failed and S.coverage(sid) == {0: 1.0}
    # 排队中取消 → failed「已取消」
    t_asr = G.acquire("AI 字幕")
    w2 = MatteWorker("segment", {"spec": sp, "path": vid, "frame": 0})
    w2.failed.connect(failed.append)
    w2.start()
    time.sleep(0.3)
    w2.cancel()
    w2.wait(5000)
    QApplication.processEvents()
    assert failed and "取消" in failed[-1]
    G.release(t_asr)


def test_alignment_worker_uses_gate():
    import inspect
    from cutora.asr.align_worker import AlignmentWorker
    src = inspect.getsource(AlignmentWorker.run)
    assert "gpu_gate.acquire" in src and "gpu_gate.release" in src
    assert hasattr(AlignmentWorker, "queued")
    from cutora.batch import text_locate_asr
    assert "gpu_gate.acquire" in inspect.getsource(text_locate_asr)


# ───────────────────────────── 对象 保留 / 挖掉 ─────────────────────────────

def test_object_modes_combine():
    from cutora.fx import ai_matte as A
    from cutora.fx import matte_store as S
    sid = S.new_session_id()
    big = np.zeros((40, 40), np.uint8)
    big[5:35, 5:35] = 255
    small = np.zeros((40, 40), np.uint8)
    small[15:25, 15:25] = 255
    with S.Session(sid, fps=30.0, src_w=40, src_h=40) as sess:
        sess.write(0, 1, big, 1.0)
        sess.write(0, 2, small, 1.0)
        sess.write(1, 2, small, 1.0)              # 第 1 帧只有「挖掉」对象的结果
    sp = A.normalize({"session": sid, "objects": [{"id": 1}, {"id": 2, "mode": "subtract"}]})
    assert sp["objects"][1]["mode"] == "subtract" and A.new_object(sp)["mode"] == "add"
    a = A.combined_raw(sp, 0)
    assert a[8, 8] == 1.0 and a[20, 20] == 0.0 and a[1, 1] == 0.0
    assert A.combined_raw(sp, 1, tolerance=0) is None             # 保留对象这帧没结果 → 不套（不闪）
    only_cut = A.normalize({"session": sid, "objects": [{"id": 2, "mode": "subtract"}]})
    c = A.combined_raw(only_cut, 0)
    assert c[1, 1] == 1.0 and c[20, 20] == 0.0
    both_add = A.normalize({"session": sid, "objects": [{"id": 1}, {"id": 2}]})
    assert A.combined_raw(both_add, 0)[20, 20] == 1.0
    assert A.normalize({"objects": [{"id": 1, "mode": "weird"}]})["objects"][0]["mode"] == "add"
    assert A.signature(sp) != A.signature(both_add)
    # 渲染入口用的就是这个
    src = {"src_t": 0.0, "crop": (0, 0, 0, 0), "flip": (False, False), "order": "crop_flip"}
    al = A.alpha_for_frame({"ai_matte": sp}, src, 40, 40)
    assert al[8, 8] == 1.0 and al[20, 20] == 0.0


def test_object_mode_command():
    _app()
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(320, 180, 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, 2.0, 0.0)
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {"timeline": tl, "duration": 2.0, "text_overlays": [], "image_overlays": [], "segments": [], "style": {}}
    reset_bus_for_tests()
    win = _win_for_task(task)
    bus = get_bus()
    bus.bind_window(win)
    loc = {"target": "clip", "track_id": "v0", "clip_index": 0}
    bus.execute("ai.matte.session", dict(loc, op="create"), source="ui")
    bus.execute("ai.matte.object", dict(loc, op="add"), source="ui")
    bus.execute("ai.matte.set", dict(loc, patch={"objects": [{"id": 2, "mode": "subtract"}]}), source="ui")
    assert clip.ai_matte["objects"][1]["mode"] == "subtract"
    win.undo()
    assert win.tasks[0]["timeline"].tracks[0].clips[0].ai_matte["objects"][1]["mode"] == "add"


# ───────────────────────────── 缓存尺寸 / 估时 ─────────────────────────────

def test_mask_dims_cap_and_render_upscale():
    from cutora.fx import ai_matte as A
    from cutora.fx import matte_jobs as J
    from cutora.fx import matte_store as S
    assert S.mask_dims(2160, 3840) == (1080, 1920) and S.mask_dims(1080, 1920) == (1080, 1920)
    assert S.mask_dims(3840, 2160) == (1920, 1080) and S.mask_dims(100, 50, max_edge=40) == (40, 20)
    a = np.zeros((2160, 3840), np.float32)
    a[:, :1920] = 1.0
    u = J._u8(a)
    assert u.shape == (1080, 1920) and u[500, 100] == 255 and u[500, 1800] == 0
    sid = S.new_session_id()
    with S.Session(sid, fps=30.0, src_w=3840, src_h=2160) as sess:
        sess.write(0, 1, u, 1.0)
    sp = A.normalize({"session": sid, "objects": [{"id": 1}]})
    # 4K 源裁掉左 1000 px：缓存是半分辨率，裁切量按比例换算
    src = {"src_t": 0.0, "crop": (1000, 0, 0, 0), "flip": (False, False), "order": "crop_flip"}
    out = A.alpha_for_frame({"ai_matte": sp}, src, 2840, 2160)
    assert out.shape == (2160, 2840)
    assert out[1000, 900] == pytest.approx(1.0) and out[1000, 940] == pytest.approx(0.0, abs=1e-6)


def test_estimate_and_speed_records():
    from cutora.fx import matte_jobs as J
    from cutora.fx import matte_store as S
    secs, measured = J.estimate_seconds(100, "matte-sam21-large", "cpu")
    assert secs == pytest.approx(400.0) and not measured
    assert J.estimate_seconds(100, "matte-sam21-large", "cuda:0")[0] == pytest.approx(8.0)
    S.record_speed("matte-sam21-large", "cpu", 2.0, 4)          # 样本太少不记
    assert S.measured_speed("matte-sam21-large", "cpu") is None
    S.record_speed("matte-sam21-large", "cpu", 2.0, 30)
    S.record_speed("matte-sam21-large", "cpu", 1.0, 30)
    assert S.measured_speed("matte-sam21-large", "cpu") == pytest.approx(1.5)
    secs, measured = J.estimate_seconds(100, "matte-sam21-large", "cpu")
    assert measured and secs == pytest.approx(150.0)
    assert J.format_duration_zh(20) == "20 秒" and J.format_duration_zh(150) == "3 分钟"
    assert J.format_duration_zh(3 * 3600 + 120) == "3 小时 2 分钟"


# ───────────────────────────── 低置信度跳转 ─────────────────────────────

def _low_session():
    from cutora.fx import matte_store as S
    sid = S.new_session_id()
    m = np.full((10, 10), 255, np.uint8)
    with S.Session(sid, fps=30.0, src_w=10, src_h=10) as sess:
        for fi in range(0, 30):
            sess.write(fi, 1, m, 0.2 if fi in (5, 6, 7, 20) else 0.9)
    return sid


def test_runs_and_neighbour_low_frames():
    from cutora.fx import ai_matte as A
    from cutora.model.timeline_model import Clip
    sid = _low_session()
    sp = A.normalize({"session": sid, "objects": [{"id": 1}]})
    assert A.low_frames(sp) == [5, 6, 7, 20]
    assert A.neighbour_low_frame(sp, 0, 1) == 5 and A.neighbour_low_frame(sp, 6, 1) == 20
    assert A.neighbour_low_frame(sp, 6, -1) == 5 and A.neighbour_low_frame(sp, 21, 1) is None
    assert A.neighbour_low_frame(sp, 5, -1) is None
    clip = Clip("s", 0.5, 1.5, 2.0)            # in 0.5 s，从时间线 2 s 开始
    clip.ai_matte = sp
    assert A.source_frame_to_timeline(clip, 15, 30.0) == pytest.approx(2.0)
    clip.speed = 2.0
    assert A.source_frame_to_timeline(clip, 21, 30.0) == pytest.approx(2.1)
    clip.speed = 1.0
    runs = A.coverage_runs(clip)
    assert [r[2] for r in runs] == [False, True, False, True, False]
    assert runs[1][0] == pytest.approx(2.0 + 5 / 30.0 - 0.5) and runs[1][1] == pytest.approx(2.0 + 8 / 30.0 - 0.5)


def test_timeline_click_yellow_run_seeks():
    _app()
    from cutora.fx import ai_matte as A
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.widget import TimelineWidget
    sid = _low_session()
    tl = Timeline(320, 180, 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, 1.0, 0.0)
    clip.ai_matte = A.normalize({"session": sid, "objects": [{"id": 1}]})
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {"timeline": tl, "duration": 1.0, "text_overlays": [], "image_overlays": [], "segments": [], "style": {}}
    w = TimelineWidget()
    w.resize(1200, 300)
    w.set_data(1000, task)
    w.pixels_per_second = 600.0
    w.show()
    QApplication.processEvents()
    rows = [r for r in w._track_positions().get("rows", []) if r[0] is tr]
    assert rows
    _t, row_y, _h = rows[0]
    x_low = (5.5 / 30.0) * 600.0
    assert w._hit_matte_band(x_low, row_y + 2) == pytest.approx(5 / 30.0)
    assert w._hit_matte_band((12 / 30.0) * 600.0, row_y + 2) is None        # 绿段不跳
    assert w._hit_matte_band(x_low, row_y + 20) is None                       # 不在条上
    seeks = []
    w.seek_request.connect(seeks.append)
    vx = x_low - float(getattr(w, "scroll_x", 0) or 0)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(vx, row_y + 2), QPointF(vx, row_y + 2),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert seeks and seeks[0] == pytest.approx(167, abs=1), seeks
    w.close()


def test_panel_low_jump_and_mode_combo(tmp_path):
    _app()
    from test_q152d_ai_matte import _square_video
    from test_q31_keyframes import _task_with_clip
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.fx import ai_matte as A
    from cutora.ui.panels.property import PropertyPanel
    media = _square_video(str(tmp_path / "sq.mp4"), frames=30)
    task, tr, clip = _task_with_clip(media, start_t=0.0, duration=1.0, canvas=(320, 180))
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    win._timeline_ms = 0
    seeks = []

    def _seek(ms, _w=win):
        seeks.append(ms)
        _w._timeline_ms = ms
    win.on_seek_request = _seek
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.show()
    try:
        sid = _low_session()
        clip.ai_matte = A.normalize({"session": sid, "objects": [{"id": 1}, {"id": 2, "mode": "subtract"}]})
        panel.load_data("video_clip", (tr, clip))
        ai = panel.vid_fx.ai
        assert ai.btn_low_next.isVisibleTo(ai) and ai.btn_low_next.isEnabled() and not ai.btn_low_prev.isEnabled()
        ai.btn_low_next.click()
        assert seeks and ai.current_frame() == 5
        ai.btn_low_next.click()
        assert ai.current_frame() == 20 and not ai.btn_low_next.isEnabled() and ai.btn_low_prev.isEnabled()
        ai.btn_low_prev.click()
        assert ai.current_frame() == 5
        # 对象模式下拉
        ai.obj_list.setCurrentRow(1)
        assert ai.obj_mode.currentText() == "subtract" and ai.obj_list.item(1).font().strikeOut()
        ai.obj_list.setCurrentRow(0)
        assert ai.obj_mode.currentText() == "add"
        ai.obj_mode.setCurrentText("subtract")
        assert clip.ai_matte["objects"][0]["mode"] == "subtract"
        # 队列提示
        ai._on_queued("AI 字幕")
        assert "排队" in ai.lbl_info.text() and "AI 字幕" in ai.lbl_info.text() and ai.btn_cancel.isVisibleTo(ai)
    finally:
        win.close()
