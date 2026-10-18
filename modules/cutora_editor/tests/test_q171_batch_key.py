# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q171 · 中央监视器右键：批量蓝绿幕抠像（默认只抠外部加载的视频）/ 批量开关分屏 / 批量取消。"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q171"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _ff():
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    return ff


def _main_clip(tmp_path):
    out = str(tmp_path / "main.mp4")
    if not os.path.isfile(out):
        subprocess.run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=216x384:r=30:d=3",
                        "-pix_fmt", "yuv420p", out], check=True)
    return out


def _screen_clip(tmp_path, color="0x22B830", name="green"):
    """绿 / 蓝幕素材：纯色背景 + 中间一个「人」（带纹理的方块），带一点噪点像真拍的。"""
    out = str(tmp_path / ("screen_%s.mp4" % name))
    if not os.path.isfile(out):
        subprocess.run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=%s:s=216x384:r=30:d=3" % color, "-f", "lavfi", "-i",
                        "testsrc2=s=80x160:r=30:d=3", "-filter_complex",
                        "[0][1]overlay=68:112,noise=alls=6:allf=t", "-pix_fmt", "yuv420p", out], check=True)
    return out


def _task(main, ext=None, *, locked_ext=None, backdrop=None):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(216, 384, 30)
    tr = Track("video")
    tr.is_main_video = True
    tl.add_track(tr)
    tl.sources["m"] = MediaSource(main, duration=3.0, probe=False)
    tr.clips.append(Clip("m", 0.0, 3.0, 0.0))
    for i, (path, locked, role) in enumerate(((ext, False, ""), (locked_ext, True, ""), (backdrop, False, "backdrop"))):
        if not path:
            continue
        t2 = Track("video")
        t2.locked = locked
        t2.bg_role = role
        tl.add_track(t2)
        sid = "e%d" % i
        tl.sources[sid] = MediaSource(path, duration=3.0, probe=False)
        t2.clips.append(Clip(sid, 0.0, 3.0, 0.0))
    return {"timeline": tl, "video": main, "dims": (216, 384), "image_overlays": [], "text_overlays": []}


def _dialog(tasks):
    from cutora.ui.central_monitor import CentralMonitorDialog

    class _Host:
        pass
    host = _Host()
    host.tasks = tasks
    dlg = CentralMonitorDialog(host, tasks)
    dlg.resize(1200, 800)
    dlg.show()
    return dlg


def _wait(app, cond, timeout=10.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _tracks(task):
    return task["timeline"].tracks


@pytest.fixture(autouse=True)
def _iso_settings(monkeypatch, tmp_path):
    from cutora.batch import batch_key as BK
    monkeypatch.setattr(BK, "_settings_path", lambda: str(tmp_path / "batch_key.json"))


# ───────────────────────────── 数据层 ─────────────────────────────

def test_detect_screen_green_blue_and_none():
    from cutora.batch import batch_key as BK
    rng = np.random.default_rng(1)
    g = np.zeros((120, 80, 3), np.uint8)
    g[...] = (40, 190, 60)
    g = np.clip(g.astype(int) + rng.integers(-6, 7, g.shape), 0, 255).astype(np.uint8)
    g[40:90, 25:55] = (180, 140, 120)            # 人
    d = BK.detect_screen(g)
    assert d["kind"] == "green"
    r, gg, b = (int(d["color"][i:i + 2], 16) for i in (1, 3, 5))
    assert abs(r - 40) < 8 and abs(gg - 190) < 8 and abs(b - 60) < 8
    bl = np.zeros((120, 80, 3), np.uint8)
    bl[...] = (30, 60, 210)
    assert BK.detect_screen(bl)["kind"] == "blue"
    normal = rng.integers(0, 255, (120, 80, 3)).astype(np.uint8)
    assert BK.detect_screen(normal) is None


def test_grab_frame_small_and_detects_real_clip(tmp_path):
    from cutora.batch import batch_key as BK
    path = _screen_clip(tmp_path)
    img = BK.grab_frame(path, 1.0)
    assert img is not None and img.shape == (BK.GRAB_SIZE, BK.GRAB_SIZE, 3)
    d = BK.detect_screen(img)
    assert d and d["kind"] == "green"
    assert BK.grab_frame(str(tmp_path / "nope.mp4"), 0) is None


def test_target_clips_default_external_only(tmp_path):
    from cutora.batch import batch_key as BK
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    task = _task(main, scr, locked_ext=scr, backdrop=scr)
    tr_main, tr_ext, tr_locked, tr_bd = _tracks(task)
    assert BK.load_target() == "external"
    clips, st = BK.target_clips(task, "external")
    assert clips == [tr_ext.clips[0]] and st["locked"] == 1      # 背景轨不算，锁定的跳过并计数
    assert BK.target_clips(task, "library")[0] == [tr_main.clips[0]]
    assert set(map(id, BK.target_clips(task, "both")[0])) == {id(tr_main.clips[0]), id(tr_ext.clips[0])}
    BK.save_target("library")
    assert BK.load_target() == "library"
    BK.save_target("bogus")
    assert BK.load_target() == "library"


def test_ops_preset_patch_enable_remove_and_graph(tmp_path):
    from cutora.batch import batch_key as BK
    from cutora.fx import graph as G
    from cutora.fx import keyer as K
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    task = _task(main, scr)
    ext = _tracks(task)[1].clips[0]
    # 没抠像的片段：改溢色 / 重新开启 不凭空造一份
    assert BK.apply_keyer(task, "external", BK.op_patch({"despill_on": True}))["changed"] == 0
    assert BK.apply_keyer(task, "external", BK.op_enable(True))["changed"] == 0
    assert ext.keyer == {}
    r = BK.apply_keyer(task, "external", BK.op_preset("blue"))
    assert r["changed"] == 1 and ext.keyer["enabled"] and ext.keyer["color"] == K.PRESETS["blue"]["color"]
    assert _tracks(task)[0].clips[0].keyer == {}                  # 主视频没动
    BK.apply_keyer(task, "external", BK.op_patch({"despill_on": True, "despill_scope": "all"}))
    assert ext.keyer["despill_on"] and ext.keyer["despill_scope"] == "all"
    BK.apply_keyer(task, "external", BK.op_step("erode", 1))
    assert ext.keyer["erode"] == 1.0
    BK.apply_keyer(task, "external", BK.op_enable(False))
    assert ext.keyer["enabled"] is False and ext.keyer["color"] == K.PRESETS["blue"]["color"]
    BK.apply_keyer(task, "external", BK.op_enable(True))
    assert ext.keyer["enabled"] is True
    BK.apply_keyer(task, "external", BK.op_remove())
    assert ext.keyer == {}
    # 节点图：改图里的抠像节点；图里没有抠像节点 → 跳过并计数
    ext.fx_graph = G.empty_graph()
    r = BK.apply_keyer(task, "external", BK.op_preset("green"))
    assert r["graph_none"] == 1 and r["changed"] == 0
    G.add_node(ext.fx_graph, "keyer")
    r = BK.apply_keyer(task, "external", BK.op_preset("green", "#11AA22"))
    node = next(n for n in ext.fx_graph["nodes"] if n["type"] == "keyer")
    assert r["changed"] == 1 and node["params"]["keyer"]["color"] == "#11AA22" and node["params"]["keyer"]["enabled"]


def test_snapshot_fx_restores_only_fx(tmp_path):
    from cutora.batch import batch_key as BK
    from cutora.batch import monitor_model as mm
    task = _task(_main_clip(tmp_path), _screen_clip(tmp_path))
    ext = _tracks(task)[1].clips[0]
    ext.masks = [{"type": "rect", "enabled": True}]
    snap = mm.snapshot_fx(task)
    BK.apply_keyer(task, "external", BK.op_preset("green"))
    BK.set_masks_enabled(task, "external", False)
    ext.x = 33.0
    mm.restore(task, snap)
    assert ext.keyer == {} and ext.masks[0]["enabled"] is True
    assert ext.x == 33.0                                           # 别的属性不归它管


def test_corrupt_check_keeps_green_screen_and_black_intro_fast_path():
    from cutora.render.readers import _is_frame_corrupt
    rng = np.random.default_rng(2)
    bgr_green = np.zeros((384, 216, 3), np.uint8)
    bgr_green[...] = (60, 190, 40)                                  # BGR 绿幕
    noisy = np.clip(bgr_green.astype(int) + rng.integers(-4, 5, bgr_green.shape), 0, 255).astype(np.uint8)
    assert not _is_frame_corrupt(noisy)                            # 真拍的绿幕有噪点
    person = bgr_green.copy()
    person[150:250, 70:140] = rng.integers(0, 255, (100, 70, 3))
    assert not _is_frame_corrupt(person)                           # 干净绿幕 + 中间有人
    two = np.zeros((384, 216, 3), np.uint8)
    two[:, 108:] = (200, 200, 200)
    assert not _is_frame_corrupt(two)                              # 几块是不同的纯色
    flat_green = np.zeros((384, 216, 3), np.uint8)
    flat_green[...] = (28, 199, 30)
    assert not _is_frame_corrupt(flat_green)                       # 有颜色的整幅纯色（合成的绿幕 / 色卡）照旧算正常
    nv12 = np.zeros((384, 216, 3), np.uint8)
    nv12[...] = (0, 135, 0)
    assert _is_frame_corrupt(nv12)                                 # 经典硬解失败绿
    assert _is_frame_corrupt(np.zeros((384, 216, 3), np.uint8))    # 整幅黑


def test_open_capture_accepts_black_intro_without_pipe_fallback(tmp_path):
    """开头一秒黑场的片子：以前判成「解坏了」掉到最慢的 ffmpeg 管道；现在看 40% 处那帧，正常走 OpenCV。"""
    from cutora.render import readers as R
    out = str(tmp_path / "black_intro.mp4")
    subprocess.run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=216x384:r=30:d=4",
                    "-vf", "fade=in:st=1:d=0.5", "-pix_fmt", "yuv420p", out], check=True)
    cap = R._open_video_capture(out, hw=False)
    assert cap is not None and not isinstance(cap, R._FFmpegFrameReader)
    ok, frame = cap.read()
    assert ok and frame is not None
    cap.release()


# ───────────────────────────── 监视器右键 ─────────────────────────────

def _menu_texts(menu):
    out = []
    for a in menu.actions():
        out.append(a.text())
        if a.menu() is not None:
            out.extend("  " + t for t in _menu_texts(a.menu()))
    return out


def _find(menu, text):
    for a in menu.actions():
        if a.text() == text:
            return a
        if a.menu() is not None:
            hit = _find(a.menu(), text)
            if hit is not None:
                return hit
    return None


def test_key_menu_presets_target_external_and_one_undo(tmp_path):
    app = _app()
    from PySide6.QtWidgets import QMenu
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    tasks = [_task(main, scr) for _ in range(4)] + [_task(main)]
    dlg = _dialog(tasks)
    g = dlg.grid
    msgs = []
    g.status.connect(msgs.append)
    try:
        app.processEvents()
        g.selected = {0, 1, 2, 4}
        targets = g.selected_tiles()
        menu = QMenu(dlg)
        g._add_key_menu(menu, targets[0], "cell", None, targets, "（4 条）")
        texts = _menu_texts(menu)
        assert any(t.startswith("蓝绿幕抠像") for t in texts)
        for want in ("全自动抠像（每条自己拾色 + 自动阈值，去掉半透明灰）", "标准绿幕", "标准蓝幕", "手机绿幕（压缩严重）",
                     "复制这一条的抠像设置", "关闭抠像（保留参数）", "重新开启抠像", "移除抠像设置"):
            assert any(t.strip() == want for t in texts), want
        assert any("外部加载的视频（默认）" in t for t in texts)
        _find(menu, "标准绿幕").trigger()
        for i in (0, 1, 2):
            assert _tracks(tasks[i])[1].clips[0].keyer["enabled"] is True
            assert _tracks(tasks[i])[0].clips[0].keyer == {}       # 媒体库主视频不动
        assert _tracks(tasks[3])[1].clips[0].keyer == {}            # 没选中
        assert "1 条没有外部加载的视频" in msgs[-1]
        assert g.history.can_undo()
        g.undo()
        assert all(_tracks(tasks[i])[1].clips[0].keyer == {} for i in (0, 1, 2))
        # 改作用范围 → 主视频
        g.set_key_target("library")
        g.key_apply(targets, __import__("cutora.batch.batch_key", fromlist=["x"]).op_preset("blue"), "标准蓝幕")
        assert _tracks(tasks[4])[0].clips[0].keyer["enabled"] is True
        assert _tracks(tasks[0])[1].clips[0].keyer == {}
    finally:
        dlg.close()


def _studio_frames(screen=(40, 175, 70), grad=0.4, w=270, h=480, n=3, seed=0):
    """像真拍的绿 / 蓝幕：上亮下暗的不均匀光、地上一块影子、中间一个人（头 + 身体）、边缘溢色、噪点。返回 (帧列表, 人的真值)。"""
    import cv2
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    lum = 1.0 - grad * (yy / h) - 0.15 * np.abs(xx / w - 0.5)
    head = ((xx - w / 2) ** 2 / (w * 0.16) ** 2 + (yy - h * 0.32) ** 2 / (h * 0.12) ** 2) < 1
    body = (np.abs(xx - w / 2) < w * 0.3) & (yy > h * 0.45)
    gt = head | body
    shadow = ((xx - w * 0.62) ** 2 / (w * 0.3) ** 2 + (yy - h * 0.85) ** 2 / (h * 0.12) ** 2) < 1
    frames = []
    for _ in range(n):
        bg = np.array(screen, np.float32)[None, None, :] * lum[..., None]
        bg[shadow] *= 0.6
        fg = np.zeros_like(bg)
        fg[head] = (205, 160, 135)
        fg[body & ~head] = (60, 60, 75)
        mix = cv2.GaussianBlur(gt.astype(np.float32), (0, 0), 3)[..., None]
        f = fg * mix + bg * (1 - mix)
        f[gt] += np.array((0, 10, 0), np.float32)
        f += rng.normal(0, 4, f.shape)
        frames.append(np.clip(f, 0, 255).astype(np.uint8))
    return frames, gt


def _studio_clip(tmp_path, name, screen, grad=0.4):
    out = str(tmp_path / ("studio_%s.mp4" % name))
    if os.path.isfile(out):
        return out
    frames, _gt = _studio_frames(screen, grad, n=30)
    p = subprocess.Popen([_ff(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "270x480",
                          "-r", "30", "-i", "-", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", out],
                         stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    return out


def _bg_gray(frames, gt, k):
    """背景里还剩多少「没抠干净的灰」（alpha > 2% 的像素占比）。"""
    import cv2
    from cutora.fx import keyer as K
    bgm = cv2.erode((~gt).astype(np.uint8), np.ones((7, 7))) > 0
    vals = []
    for f in frames:
        a, _o = K.matte_and_despill(f.astype(np.float32) / 255.0, K.normalize(dict(k, enabled=True)))
        vals.append(float((a[bgm] > 0.02).mean()))
    return float(np.mean(vals))


def test_tune_frames_removes_gray_where_preset_leaves_it():
    """Luca 截图里的问题：标准预设抠完背景是一片灰（半透明）。全自动精调后背景干净、人是实的。"""
    from cutora.batch import batch_key as BK
    from cutora.fx import keyer as K
    for screen, kind in (((40, 175, 70), "green"), ((90, 160, 90), "green"), ((30, 80, 200), "blue")):
        frames, gt = _studio_frames(screen, 0.45)
        preset_gray = _bg_gray(frames, gt, K.PRESETS[kind])
        res = BK.tune_frames(frames)
        assert res and res["kind"] == kind
        tuned_gray = _bg_gray(frames, gt, res["params"])
        assert preset_gray > 0.3, (screen, preset_gray)             # 预设：背景大片灰
        assert tuned_gray < 0.01, (screen, tuned_gray)              # 精调：背景干净
        assert res["quality"]["clean"], res
        assert res["params"]["black"] > 0.0 or res["params"]["white"] < 1.0   # 阈值往两边收了
    # 每条按自己的屏幕色拾色：两种不同的绿，拾出来的颜色不一样
    a = BK.tune_frames(_studio_frames((40, 175, 70))[0])["params"]["color"]
    b = BK.tune_frames(_studio_frames((90, 160, 90))[0])["params"]["color"]
    assert a != b


def test_tune_frames_is_fast_and_rejects_non_screen():
    from cutora.batch import batch_key as BK
    frames, _gt = _studio_frames()
    t0 = time.perf_counter()
    BK.tune_frames(frames)
    assert time.perf_counter() - t0 < 2.0
    rng = np.random.default_rng(3)
    assert BK.tune_frames([rng.integers(0, 255, (480, 270, 3)).astype(np.uint8)]) is None
    assert BK.tune_frames([]) is None


def test_ppm_and_threshold_ops(tmp_path):
    from cutora.batch import batch_key as BK
    img = np.arange(6 * 4 * 3, dtype=np.uint8).reshape(4, 6, 3)
    data = b"P6\n# c\n6 4\n255\n" + img.tobytes()
    assert np.array_equal(BK._parse_ppm(data), img)
    assert BK._parse_ppm(b"junk") is None
    f = BK.grab_frame_fit(_studio_clip(tmp_path, "g", (40, 175, 70)), 0.5, 200)
    assert f is not None and max(f.shape[:2]) == 200 and f.shape[0] > f.shape[1]   # 竖版比例保持
    cur = {"enabled": True, "black": 0.1, "white": 0.9}
    out = BK.op_threshold(0.1, -0.1)(dict(cur))
    assert out["black"] == 0.2 and out["white"] == 0.8
    out = BK.op_threshold(0.6, -0.6)(dict(cur))                    # 收过头：不会交叉
    assert out["white"] > out["black"]
    assert BK.op_threshold(0.1, 0)({"enabled": False}) is None


def test_key_auto_tunes_each_clip_in_background_one_undo(tmp_path, monkeypatch):
    app = _app()
    from cutora.batch import batch_key as BK
    main = _main_clip(tmp_path)
    g1 = _studio_clip(tmp_path, "g1", (40, 175, 70))
    g2 = _studio_clip(tmp_path, "g2", (90, 160, 90), 0.5)
    bl = _studio_clip(tmp_path, "bl", (30, 80, 200))
    tasks = [_task(main, g1), _task(main, g1), _task(main, g2), _task(main, bl), _task(main, main)]
    calls = []
    real = BK.grab_frame_fit

    def spy(path, t, side=BK.TUNE_SIDE):
        calls.append(path)
        return real(path, t, side)

    monkeypatch.setattr(BK, "grab_frame_fit", spy)
    dlg = _dialog(tasks)
    g = dlg.grid
    msgs = []
    g.status.connect(msgs.append)
    try:
        app.processEvents()
        g.select_all()
        undo_before = len(g.history._undo)
        w = g.key_auto(g.selected_tiles())
        assert w is not None and w.isRunning()
        assert _wait(app, lambda: g._key_state()["worker"] is None, 60)
        # 同一素材同一段只算一次（g1 用了两次），每个算 3 帧
        assert sorted(set(calls)) == sorted({g1, g2, bl, main}) and len(calls) == 4 * 3
        ks = [_tracks(tasks[i])[1].clips[0].keyer for i in range(4)]
        assert all(k["enabled"] for k in ks)
        assert ks[0] == ks[1]
        assert ks[0]["color"] != ks[2]["color"]                         # 每条自己拾色
        assert int(ks[3]["color"][5:7], 16) > int(ks[3]["color"][3:5], 16)   # 蓝幕
        assert all(k["black"] > 0.0 or k["white"] < 1.0 for k in ks)
        assert _tracks(tasks[4])[1].clips[0].keyer == {}               # 普通画面认不出 → 不动
        assert all(_tracks(t)[0].clips[0].keyer == {} for t in tasks)   # 媒体库主视频不动
        assert len(g.history._undo) == undo_before + 1                  # 一步撤销
        assert any("全自动抠像完成" in m and "没认出" in m for m in msgs)
        assert any("全自动抠像 1 / 4" in m for m in msgs)               # 一条一条报进度
        calls.clear()
        g.undo()
        assert all(_tracks(tasks[i])[1].clips[0].keyer == {} for i in range(4))
        g.key_auto(g.selected_tiles())                                  # 第二次全走记住的结果，不再抽帧
        app.processEvents()
        assert calls == [] and _tracks(tasks[0])[1].clips[0].keyer["enabled"]
    finally:
        dlg.close()


def test_key_auto_stop_keeps_done_and_close_mid_run(tmp_path, monkeypatch):
    app = _app()
    from cutora.batch import batch_key as BK
    main = _main_clip(tmp_path)
    clips = [_studio_clip(tmp_path, "s%d" % i, (40 + i * 5, 175, 70)) for i in range(4)]
    tasks = [_task(main, c) for c in clips]
    real = BK.grab_frame_fit
    monkeypatch.setattr(BK, "grab_frame_fit", lambda p, t, side=BK.TUNE_SIDE: (time.sleep(0.15), real(p, t, side))[1])
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g.select_all()
        g.key_auto(g.selected_tiles())
        assert _wait(app, lambda: any(_tracks(t)[1].clips[0].keyer for t in tasks), 30)
        g._key_stop_auto()
        assert _wait(app, lambda: g._key_state()["worker"] is None, 30)
        done = sum(1 for t in tasks if _tracks(t)[1].clips[0].keyer)
        assert 1 <= done < 4                                            # 停了：做好的保留、没做的不动
        assert g.history.can_undo()
        g.key_auto(g.selected_tiles())
        app.processEvents()
    finally:
        dlg.close()                                                     # 跑到一半关窗：不卡、不崩


def test_levels_slider_drag_and_keys():
    _app()
    from PySide6.QtCore import QPointF, Qt, QEvent
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication
    from cutora.ui.widgets.key_levels import LevelsSlider, alpha_histogram
    sl = LevelsSlider(0.0, 1.0)
    sl.resize(320, 90)
    sl.set_histogram(alpha_histogram(np.r_[np.zeros(500), np.ones(400), np.full(100, 0.5)]))
    sl.show()
    got = []
    sl.released.connect(lambda b, w: got.append((b, w)))

    def ev(t, x):
        return QMouseEvent(t, QPointF(x, 50), QPointF(x, 50), Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton if t != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(sl, ev(QEvent.Type.MouseButtonPress, sl._x_of(0.0)))
    QApplication.sendEvent(sl, ev(QEvent.Type.MouseMove, sl._x_of(0.3)))
    QApplication.sendEvent(sl, ev(QEvent.Type.MouseButtonRelease, sl._x_of(0.3)))
    assert got and abs(got[-1][0] - 0.3) < 0.02 and got[-1][1] == 1.0
    QApplication.sendEvent(sl, ev(QEvent.Type.MouseButtonPress, sl._x_of(1.0)))
    QApplication.sendEvent(sl, ev(QEvent.Type.MouseMove, sl._x_of(0.1)))     # 右把手拖过左把手：不交叉
    QApplication.sendEvent(sl, ev(QEvent.Type.MouseButtonRelease, sl._x_of(0.1)))
    assert got[-1][1] > got[-1][0]
    QApplication.sendEvent(sl, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier))
    assert got[-1][0] < 0.3
    sl.grab()                                                          # 画得出来
    sl.close()


def test_levels_in_menu_batch_rel_abs_and_preview_revert(tmp_path):
    app = _app()
    from PySide6.QtWidgets import QMenu
    from cutora.batch import batch_key as BK
    from cutora.ui.widgets.key_levels import KeyLevelsPanel
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    tasks = [_task(main, scr) for _ in range(3)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g.select_all()
        tiles = g.selected_tiles()
        g.key_apply(tiles, BK.op_preset("green"), "标准绿幕")
        _tracks(tasks[1])[1].clips[0].keyer["black"] = 0.2                 # 这条精调过、阈值不一样
        menu = QMenu(dlg)
        sub = g._add_key_menu(menu, tiles[0], "cell", None, tiles, "（3 条）")
        panels = [a.defaultWidget() for a in sub.actions() if hasattr(a, "defaultWidget") and a.defaultWidget()]
        panel = next(p for p in panels if isinstance(p, KeyLevelsPanel))
        from PySide6.QtWidgets import QLabel
        assert "选中的 3 条" in panel.findChild(QLabel, "keyLevelsTitle").text()
        # 拖动 = 只预览点中的那一条；关菜单不松手 → 还原
        panel.slider.moved.emit(0.3, 0.8)
        assert _tracks(tasks[0])[1].clips[0].keyer["black"] == 0.3
        assert _tracks(tasks[2])[1].clips[0].keyer["black"] == 0.0
        sub.aboutToHide.emit()
        assert _tracks(tasks[0])[1].clips[0].keyer["black"] == 0.0
        # 一起挪：各自保留差异
        undo_n = len(g.history._undo)
        g.key_levels_apply(tiles, 0.1, 0.9, "rel", 0.0, 1.0)
        assert [round(_tracks(t)[1].clips[0].keyer["black"], 3) for t in tasks] == [0.1, 0.3, 0.1]
        assert all(round(_tracks(t)[1].clips[0].keyer["white"], 3) == 0.9 for t in tasks)
        assert len(g.history._undo) == undo_n + 1
        # 统一成这个值
        g.key_levels_apply(tiles, 0.25, 0.75, "abs")
        assert all(_tracks(t)[1].clips[0].keyer["black"] == 0.25 for t in tasks)
        g.undo()
        assert [round(_tracks(t)[1].clips[0].keyer["black"], 3) for t in tasks] == [0.1, 0.3, 0.1]
        # 直方图后台读出来
        assert _wait(app, lambda: panel.slider.hist is not None, 20)
    finally:
        dlg.close()


def test_key_copy_paste_masks_ai_toggles(tmp_path):
    app = _app()
    from cutora.batch import batch_key as BK
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    tasks = [_task(main, scr) for _ in range(3)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        t0 = g.tiles[0]
        g.key_apply([t0], BK.op_preset("green", "#11CC22"), "标准绿幕")
        assert g.key_copy(t0)
        g.key_paste(g.tiles[1:])
        assert all(_tracks(t)[1].clips[0].keyer["color"] == "#11CC22" for t in tasks)
        ext = _tracks(tasks[2])[1].clips[0]
        ext.masks = [{"type": "rect", "enabled": True}]
        ext.ai_matte = {"enabled": True, "session": "x"}
        g.key_apply(g.tiles, None, "关闭遮罩", which="masks", on=False)
        g.key_apply(g.tiles, None, "关闭 AI 抠像", which="ai", on=False)
        assert ext.masks[0]["enabled"] is False and ext.ai_matte["enabled"] is False
        g.undo()
        assert ext.ai_matte["enabled"] is True
    finally:
        dlg.close()


def test_batch_key_is_fast_and_does_not_decode_offscreen(tmp_path):
    """60 条一次抠：只改数据 + 只作废画面；看不见的格子不进解码队列；界面线程毫秒级。"""
    app = _app()
    from cutora.batch import batch_key as BK
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    tasks = [_task(main, scr) for _ in range(60)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g._sched.stop()
        g._renderer.cancel_all()
        seen = []
        orig = g._renderer.request

        def spy(key, vt, t, gen, prio=1):
            seen.append(key)
            return orig(key, vt, t, gen, prio)

        g._renderer.request = spy
        for tile in g.tiles[10:]:
            tile.rect.moveTop(50000)
            tile.head.moveTop(50000)
        g.select_all()
        t0 = time.perf_counter()
        g.key_apply(g.selected_tiles(), BK.op_preset("green"), "标准绿幕")
        dt = time.perf_counter() - t0
        assert dt < 0.5, dt
        for _ in range(10):
            app.processEvents()
        off = {id(t) for t in g.tiles[10:]}
        assert not any(k in off for k in seen)
        assert all(_tracks(t)[1].clips[0].keyer["enabled"] for t in tasks)
    finally:
        dlg.close()


# ───────────────────────────── 分屏开关 / 批量取消 ─────────────────────────────

def test_cancel_and_restore_split_batch(tmp_path):
    app = _app()
    from PySide6.QtWidgets import QMenu
    from cutora.batch import monitor_model as mm
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    tasks = [_task(main, scr) for _ in range(3)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g.select_all()
        g._apply_layout(g.selected_tiles(), "left_right_fit")
        mm.set_divider_pos(tasks[0], "d1", 0.3)
        cells_before = [(tr.split_cell) for tr in _tracks(tasks[0])]
        menu = QMenu(dlg)
        g._add_split_quick(menu, g.selected_tiles(), "（3 条）")
        cancel = next(a for a in menu.actions() if a.text().startswith("取消分屏"))
        cancel.trigger()
        assert not any(mm.split_on(t) for t in tasks)
        menu = QMenu(dlg)
        g._add_split_quick(menu, g.selected_tiles(), "（3 条）")
        back = next(a for a in menu.actions() if a.text().startswith("恢复分屏"))
        back.trigger()
        assert all(mm.layout_name(t) == "left_right_fit" for t in tasks)
        assert [tr.split_cell for tr in _tracks(tasks[0])] == cells_before
        assert abs(next(d["pos"] for d in mm.divider_lines(tasks[0]) if d["id"] == "d1") - 0.3) < 1e-6
        g.undo()
        assert not any(mm.split_on(t) for t in tasks)
        # 分屏布局子菜单里也有「恢复关掉前的布局」
        menu = QMenu(dlg)
        g._add_layout_menus(menu)
        assert any("恢复关掉前的布局" in t for t in _menu_texts(menu))
    finally:
        dlg.close()


def test_batch_undo_menu_turns_things_off(tmp_path):
    app = _app()
    from PySide6.QtWidgets import QMenu
    from cutora.batch import batch_key as BK
    from cutora.batch import monitor_model as mm
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    tasks = [_task(main, scr) for _ in range(3)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g.select_all()
        tiles = g.selected_tiles()
        g._apply_layout(tiles, "top_bottom")
        g.key_apply(tiles, BK.op_preset("green"), "标准绿幕")
        menu = QMenu(dlg)
        sub = g._add_undo_menu(menu, tiles[0], tiles, "（3 条）")
        texts = [a.text() for a in sub.actions()]
        for want in ("取消分屏", "背景填充改回「无」", "关闭遮罩", "关闭 AI 抠像", "撤销上一步  Ctrl+Z"):
            assert want in texts, want
        _find(sub, "关闭蓝绿幕抠像（外部加载的视频）").trigger()
        assert all(_tracks(t)[1].clips[0].keyer["enabled"] is False for t in tasks)
        _find(sub, "取消分屏").trigger()
        assert not any(mm.split_on(t) for t in tasks)
    finally:
        dlg.close()


def test_full_context_menu_builds(tmp_path):
    """整份右键菜单（含抠像 / 分屏开关 / 批量取消）能建起来，不报错。"""
    app = _app()
    from PySide6.QtCore import QPointF, Qt, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QMenu
    main, scr = _main_clip(tmp_path), _screen_clip(tmp_path)
    tasks = [_task(main, scr) for _ in range(2)]
    dlg = _dialog(tasks)
    g = dlg.grid
    built = []

    def _grab_and_close():
        from PySide6.QtWidgets import QApplication
        pop = QApplication.activePopupWidget()
        if isinstance(pop, QMenu):
            built.append(_menu_texts(pop))
            pop.close()

    try:
        app.processEvents()
        tile = g.tiles[0]
        pos = QPointF(tile.rect.center())
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.RightButton,
                         Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, _grab_and_close)
        QTimer.singleShot(3000, lambda: [w.close() for w in [QApplication_active()] if w is not None])
        g._context_menu(ev)
        assert built
        texts = built[-1]
        assert any(t.startswith("蓝绿幕抠像") for t in texts)
        assert any(t.startswith("批量取消") for t in texts)
    finally:
        dlg.close()


def QApplication_active():
    from PySide6.QtWidgets import QApplication
    return QApplication.activePopupWidget()
