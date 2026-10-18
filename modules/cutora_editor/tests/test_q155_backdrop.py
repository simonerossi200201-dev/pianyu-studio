# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q155 · 整幅背景：分屏「每格各自」之外，还能「单个视频撑满整幅」、外部图片、外部视频（循环铺满时长）；
中央监视器右键批量设，可撤销。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["q155"])


def _png(tmp_path, color="#2050FF", name="bg.png", w=64, h=64):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    path = str(tmp_path / name)
    assert img.save(path)
    return path


# ───────────────────────── 假轨道，直接喂 paint_split_video_tracks ─────────────────────────

class _Clip:
    def __init__(self):
        self.fit_mode = "fit"
        self.bg_fill = {"mode": "blur"}


class _Track:
    def __init__(self, cell, color, role=""):
        self.split_cell = cell
        self.id = cell or role
        self.clips = [_Clip()]
        self.color = QColor(color)
        self.bg_role = role
        self.is_main_video = cell == "A"


def _paint(task, tracks, w=200, h=100):
    """竖条素材：fit 时只占格子中间一半；片段自己的背景填充（没被让位时）画成紫色。"""
    from cutora.render.backdrop import fill_suppressed
    from cutora.render.split_screen import paint_split_video_tracks
    log = []

    def paint_one(p, tr, dw, dh):
        mode = tr.clips[0].fit_mode
        log.append((tr.id, mode, fill_suppressed()))
        if mode == "fit":
            if not fill_suppressed():
                p.fillRect(0, 0, int(dw), int(dh), QColor("#A020A0"))     # 每格自己的填充
            side = int(dw) // 4
            p.fillRect(side, 0, int(dw) - 2 * side, int(dh), tr.color)
        else:
            p.fillRect(0, 0, int(dw), int(dh), tr.color)

    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    try:
        paint_split_video_tracks(painter, task, tracks, w, h, paint_one)
    finally:
        painter.end()
    return log, canvas


def _split(layout="left_right", backdrop=None):
    from cutora.render.split_layout import apply_layout
    sp = apply_layout({"enabled": True, "layout": layout})
    for d in sp["dividers"]:
        d["feather_px"] = 0.0
    if backdrop is not None:
        sp["backdrop"] = backdrop
    return sp


def _near(c, hexcolor, tol=40):
    want = QColor(hexcolor)
    return (abs(c.red() - want.red()) <= tol and abs(c.green() - want.green()) <= tol
            and abs(c.blue() - want.blue()) <= tol)


def test_norm_keeps_full_and_cell():
    from cutora.render.split_layout import backdrop_is_full, backdrop_spec, normalize_split
    sp = normalize_split(_split(backdrop={"mode": "full", "cell": "B", "blur": 30}))
    assert backdrop_spec(sp)["mode"] == "full" and backdrop_spec(sp)["cell"] == "B"
    assert backdrop_is_full(sp)
    assert not backdrop_is_full(_split())                       # 没写 = 老样子
    assert not backdrop_is_full(_split(backdrop={"mode": "each"}))


def test_each_on_crop_layout_is_unchanged(app):
    log, canvas = _paint({"split": _split()}, [_Track("A", "#FF2040"), _Track("B", "#20C040")])
    assert all(not m[2] for m in log)                           # 片段自己的填充照画
    assert _near(canvas.pixelColor(5, 50), "#A020A0") and _near(canvas.pixelColor(105, 50), "#A020A0")


def test_full_mode_one_plate_across_both_cells(app):
    task = {"split": _split(backdrop={"mode": "full", "blur": 20, "dim": 0.0})}
    log, canvas = _paint(task, [_Track("A", "#FF2040"), _Track("B", "#20C040")])
    fills = [m for m in log if m[1] == "fill"]
    assert [m[0] for m in fills] == ["A"]                      # 只垫原视频（A）一张整幅
    assert all(m[2] for m in log)                              # 格子里片段自己的填充都让位
    for x in (5, 105, 195):                                    # 左格、右格留白都是 A 的红
        c = canvas.pixelColor(x, 50)
        assert c.red() > c.green() + 60 and c.alpha() == 255, (x, c.name())
    assert _near(canvas.pixelColor(150, 50), "#20C040", 4)     # 右格内容还是自己（绿）
    # 指定 B 格的视频撑满
    task["split"]["backdrop"]["cell"] = "B"
    log, canvas = _paint(task, [_Track("A", "#FF2040"), _Track("B", "#20C040")])
    assert [m[0] for m in log if m[1] == "fill"] == ["B"]
    c = canvas.pixelColor(5, 50)
    assert c.green() > c.red() + 60


def test_full_mode_on_contain_layout(app):
    task = {"split": _split("left_right_fit", {"mode": "full", "cell": "B", "dim": 0.0})}
    log, canvas = _paint(task, [_Track("A", "#FF2040"), _Track("B", "#20C040")])
    assert [m[0] for m in log if m[1] == "fill"] == ["B"]
    c = canvas.pixelColor(5, 50)
    assert c.green() > c.red() + 60


def test_external_image_fills_whole_canvas(app, tmp_path):
    task = {"split": _split(), "bg_image": {"path": _png(tmp_path)}}
    log, canvas = _paint(task, [_Track("A", "#FF2040"), _Track("B", "#20C040")])
    assert not [m for m in log if m[1] == "fill"]             # 不再垫任何一条的模糊
    assert all(m[2] for m in log)
    for x in (5, 105, 195):
        assert _near(canvas.pixelColor(x, 50), "#2050FF", 4), x
    assert _near(canvas.pixelColor(50, 50), "#FF2040", 4)


def test_external_video_track_is_painted_at_the_bottom(app):
    from cutora.render.backdrop import BACKDROP_ROLE
    bg = _Track(None, "#F0E020", role=BACKDROP_ROLE)
    task = {"split": _split()}
    log, canvas = _paint(task, [bg, _Track("A", "#FF2040"), _Track("B", "#20C040")])
    assert log[0] == (BACKDROP_ROLE, "fill", False)            # 第一笔，铺满
    assert bg.clips[0].fit_mode == "fit"                       # 临时改的铺满已还原
    for x in (5, 105, 195):
        assert _near(canvas.pixelColor(x, 50), "#F0E020", 4), x
    assert _near(canvas.pixelColor(50, 50), "#FF2040", 4)      # 没被它盖住


def test_fill_spec_at_yields_to_backdrop(tmp_path):
    from cutora.model.timeline_model import Clip
    from cutora.render import backdrop as BG
    from cutora.render.fill_bg import fill_spec_at
    c = Clip("s", 0.0, 1.0, 0.0)
    c.bg_fill = dict(c.bg_fill, mode="blur")
    BG.begin_frame({"bg_image": {"path": _png(tmp_path)}})
    try:
        assert fill_spec_at(c, 0.5)["mode"] == "none"
    finally:
        BG.end_frame()
    assert fill_spec_at(c, 0.5)["mode"] == "blur"
    BG.begin_frame({"bg_image": {"path": ""}})                # 空路径 = 没有
    assert fill_spec_at(c, 0.5)["mode"] == "blur"
    BG.end_frame()


def _real_task(tmp_path, split=True):
    from cutora.batch.split_assign import assign_split
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(216, 384, 30)
    a = Track("video")
    a.is_main_video = True
    tl.add_track(a)
    tl.sources["m"] = MediaSource(str(tmp_path / "a.mp4"), duration=6.0, probe=False)
    a.clips.append(Clip("m", 0.0, 6.0, 0.0))
    task = {"timeline": tl, "duration": 6.0, "dims": (216, 384)}
    if split:
        b = Track("video")
        tl.add_track(b)
        tl.sources["m2"] = MediaSource(str(tmp_path / "b.mp4"), duration=6.0, probe=False)
        b.clips.append(Clip("m2", 0.0, 6.0, 0.0))
        assign_split(task, "left_right", [a, b])
    return task


def test_model_ops_and_persistence(tmp_path):
    from cutora.batch import monitor_model as mm
    from cutora.model.project_io import deserialize_render_task, serialize_render_task
    from cutora.render import backdrop as BG
    task = _real_task(tmp_path)
    tl = task["timeline"]
    assert BG.mode_of(task) == "each"
    assert BG.set_full(task) and BG.mode_of(task) == "full"
    assert task["split"]["backdrop"]["cell"] == "A"           # 原视频那格
    vid = str(tmp_path / "loop.mp4")
    open(vid, "wb").write(b"x")
    tr = BG.set_video(task, vid, duration=2.0)
    assert tl.tracks[0] is tr and tr.bg_role == "backdrop" and tr.name == "整幅背景"
    c = tr.clips[0]
    assert c.loop and c.match_duration and abs(c._match_target_dur - 6.0) < 1e-9
    assert c.fit_mode == "fill" and c.bg_fill["mode"] == "none"
    assert BG.mode_of(task) == "video"                         # 外部视频压过「单个视频撑满」的显示
    assert tr not in mm.video_tracks(task) and mm.main_video_track(task) is not tr
    # 存盘再读回来，轨道还是整幅背景
    back = deserialize_render_task(serialize_render_task(task))
    roles = [getattr(t, "bg_role", "") for t in back["timeline"].tracks]
    assert roles[0] == "backdrop" and roles.count("backdrop") == 1
    assert BG.mode_of(back) == "video"
    # 换成图片：背景轨自动拿掉；回每格各自：全清
    BG.set_image(task, _png(tmp_path))
    assert BG.mode_of(task) == "image" and not BG.backdrop_tracks(task, active_only=False)
    back = deserialize_render_task(serialize_render_task(task))
    assert BG.bg_image_spec(back)["path"].endswith("bg.png")
    BG.set_each(task)
    assert BG.mode_of(task) == "each" and "bg_image" not in task
    assert task["split"]["backdrop"]["mode"] == "each"
    with pytest.raises(ValueError):
        BG.set_image(task, str(tmp_path / "nope.png"))
    # 不分屏：单个视频撑满不适用，图片 / 视频照样能用
    solo = _real_task(tmp_path, split=False)
    assert not BG.set_full(solo)
    BG.set_video(solo, vid, duration=2.0)
    assert solo["timeline"].tracks[0].bg_role == "backdrop"


def test_monitor_signature_and_view_task(tmp_path):
    from cutora.batch import monitor_model as mm
    from cutora.render import backdrop as BG
    task = _real_task(tmp_path)
    s0 = mm.view_signature(task)
    BG.set_image(task, _png(tmp_path))
    assert mm.view_signature(task) != s0
    vt = mm.view_task(task, 0.5)
    assert vt["bg_image"]["path"] == task["bg_image"]["path"]
    vid = str(tmp_path / "loop.mp4")
    open(vid, "wb").write(b"x")
    BG.set_video(task, vid, duration=2.0)
    vt = mm.view_task(task, 0.5)
    assert vt["timeline"].tracks[0].bg_role == "backdrop"
    # 换布局时背景轨不会被分进格子
    mm.set_layout(task, "grid_2x2")
    assert not task["timeline"].tracks[0].split_cell


def _color_clip(tmp_path, color, name, dur=1, size="216x384"):
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    out = str(tmp_path / name)
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=%s:s=%s:r=30:d=%d" % (color, size, dur),
                    "-pix_fmt", "yuv420p", out], check=True)
    return out


def test_central_monitor_batch_backdrop_menu_and_undo(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from test_q144_central_monitor import _app, _dialog, _split_task, _wait
    import cutora.ui.central_monitor as CM
    from cutora.render import backdrop as BG
    app = _app()
    tasks = [_split_task(tmp_path, "left_right_fit"), _split_task(tmp_path, "left_right_fit")]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None for t in g.tiles))
        g.selected = {0, 1}
        g.active = 0
        targets = g.selected_tiles()
        from PySide6.QtWidgets import QMenu
        m = QMenu(g)
        sub = g._add_backdrop_menu(m, g.tiles[0], targets, "（2 条）")
        texts = [a.text() for a in sub.actions()]
        assert texts[0].startswith("每格各自") and "单个视频撑满整幅（原视频）" in texts
        assert "外部图片作为背景…" in texts and "外部视频作为背景（循环铺满时长）…" in texts
        assert [a.text() for a in sub.actions() if a.isChecked()] == [texts[0]]
        m.deleteLater()
        # 批量：外部图片（选文件框换掉）
        png = _png(tmp_path, "#2050FF")
        monkeypatch.setattr(CM, "_pick_file", lambda *_a: png)
        g._pick_backdrop_file(targets, "image")
        assert all(BG.mode_of(t) == "image" for t in tasks)

        def _top_gap_blue():
            out = []
            for t in g.tiles:
                img = t.video
                if img is None:
                    return False
                c = img.pixelColor(img.width() // 4, 3)        # 左格上方留白
                out.append(c.blue() > 200 and c.red() < 90)
            return all(out)
        assert _wait(app, _top_gap_blue, 15.0), "tiles did not repaint with the image backdrop"
        # 撤销一次：两条都回到每格各自
        g.undo()
        assert all(BG.mode_of(t) == "each" for t in tasks)
        # 批量：外部视频，循环铺满
        vid = _color_clip(tmp_path, "yellow", "y.mp4", dur=1)
        monkeypatch.setattr(CM, "_pick_file", lambda *_a: vid)
        g._pick_backdrop_file(targets, "video")
        for t in tasks:
            tr = t["timeline"].tracks[0]
            assert tr.bg_role == "backdrop" and tr.clips[0].loop and tr.clips[0].match_duration
            assert not tr.split_cell

        def _top_gap_yellow():
            for t in g.tiles:
                img = t.video
                if img is None:
                    return False
                c = img.pixelColor(img.width() // 4, 3)
                if not (c.red() > 180 and c.green() > 180 and c.blue() < 110):
                    return False
            return True
        assert _wait(app, _top_gap_yellow, 15.0), "tiles did not repaint with the video backdrop"
        # 单个视频撑满：外部视频轨被拿掉
        g._apply_backdrop(targets, "full", cell="B")
        assert all(BG.mode_of(t) == "full" for t in tasks)
        assert all(not BG.backdrop_tracks(t, active_only=False) for t in tasks)
        assert all(t["split"]["backdrop"]["cell"] == "B" for t in tasks)
        g.undo()
        assert all(BG.mode_of(t) == "video" for t in tasks)
        # 选了背景填充 = 回每格各自
        g._apply_bg_fill(targets, "lens")
        assert all(BG.mode_of(t) == "each" for t in tasks)
    finally:
        dlg.close()


def test_assign_split_full_mode_any_layout():
    from cutora.batch.split_assign import assign_split
    from cutora.render.split_layout import backdrop_is_full

    class TL:
        tracks = []

    class Tr:
        def __init__(self, i):
            self.id = i
            self.split_cell = None

    a, b = Tr("a"), Tr("b")
    TL.tracks = [a, b]
    task = {"timeline": TL()}
    assign_split(task, "top_bottom", [a, b], backdrop={"mode": "full", "blur": 30}, cells=["B", "A"])
    assert backdrop_is_full(task["split"]) and task["split"]["backdrop"]["cell"] == "B"


def test_property_panel_has_split_backdrop_row(app):
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.panels.prop_reset import _APPLIERS
    p = PropertyPanel()
    cb = p.trk_split_backdrop
    assert [cb.itemData(i) for i in range(cb.count())] == ["each", "full"]
    assert "trk.split_backdrop" in _APPLIERS
    sent = []
    p._exec_split = lambda name, params: sent.append((name, params))
    p._win_and_task = lambda: (None, None)
    p.block_signals = False
    cb.setCurrentText("full")
    assert sent[-1] == ("split.set_layout", {"backdrop": {"mode": "full"}})
