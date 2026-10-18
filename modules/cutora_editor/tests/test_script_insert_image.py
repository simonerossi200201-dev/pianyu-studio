# -*- coding: utf-8 -*-
"""离线自测：批量插图的多平台副本（路径、配置、剥离覆层）。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from cutora.batch import script_insert_image as sii


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_prefixed_output_path():
    p = r"C:\out\Tik009_sub.mp4"
    assert sii.prefixed_output_path(p, "Youtube") == r"C:\out\Youtube_Tik009_sub.mp4"
    already = r"C:\out\Youtube_Tik009_sub.mp4"
    assert sii.prefixed_output_path(already, "Youtube") == already


def test_platform_dest_paths():
    copies = [
        {"prefix": "Youtube"},
        {"prefix": "Instagram"},
        {"prefix": "TikTok"},
    ]
    raw = r"C:\v\字幕成品\Tik009_sub.mp4"
    got = sii.platform_dest_paths(raw, copies)
    assert got == [
        r"C:\v\字幕成品\Youtube_Tik009_sub.mp4",
        r"C:\v\字幕成品\Instagram_Tik009_sub.mp4",
        r"C:\v\字幕成品\TikTok_Tik009_sub.mp4",
    ]
    # 已经带了第一份前缀也不该叠两次
    first = got[0]
    got2 = sii.platform_dest_paths(first, copies)
    assert got2 == got


def test_normalize_and_active_copies():
    td = tempfile.mkdtemp(prefix="platimg_")
    yt = os.path.join(td, "yt.png")
    ig = os.path.join(td, "ig.jpg")
    tk = os.path.join(td, "tk.webp")
    _touch(yt); _touch(ig); _touch(tk)

    cfg = sii.normalize_platform_copies(
        {"youtube": yt, "instagram": ig, "tiktok": tk},
        start_sec=60, fit="fill",
    )
    assert cfg["enabled"] is True
    assert cfg["render_mode"] == "once"
    assert [p["prefix"] for p in cfg["platforms"]] == ["Youtube", "Instagram", "TikTok"]

    task = {"platform_copies": cfg}
    copies = sii.active_copies(task)
    assert len(copies) == 3
    assert copies[0]["prefix"] == "Youtube"

    base = os.path.join(td, "Tik009_sub.mp4")
    resolved = sii.resolve_export_output_path(task, base)
    assert os.path.basename(resolved) == "Youtube_Tik009_sub.mp4"

    empty = sii.active_copies({"platform_copies": {"enabled": False, "platforms": cfg["platforms"]}})
    assert empty == []


def test_strip_insert_overlays():
    task = {
        "image_overlays": [
            {"id": "ov_insert_abc", "_script": "insert_image", "start": 60, "end": 100},
            {"id": "ov_other", "start": 0, "end": 10, "elements": [{"kind": "image", "path": "x.png"}]},
        ],
        "segments": [{"start": 0, "end": 1}],
        "platform_copies": {"enabled": True},
    }
    stripped = sii.task_without_insert_overlays(task)
    assert len(stripped["image_overlays"]) == 1
    assert stripped["image_overlays"][0]["id"] == "ov_other"
    assert stripped["platform_copies"]["enabled"] is True
    assert len(task["image_overlays"]) == 2  # 原 task 不动


def test_insert_uses_first_platform_image_not_default():
    td = tempfile.mkdtemp(prefix="platins_")
    default = os.path.join(td, "Bible_study.png")
    yt = os.path.join(td, "youtube_cover.png")
    ig = os.path.join(td, "ig_cover.png")
    _touch(default); _touch(yt); _touch(ig)
    task = {
        "video": os.path.join(td, "Tik009.mp4"),
        "duration": 200.0,
        "dims": (720, 1280),
        "image_overlays": [{
            "id": "ov_insert_old",
            "_script": "insert_image",
            "elements": [{"kind": "image", "path": default}],
        }],
    }
    sii.insert_into_tasks(
        [task],
        default,
        start_sec=60,
        platform_copies={"youtube": yt, "instagram": ig},
    )
    ovs = [ov for ov in task["image_overlays"] if sii.is_insert_overlay(ov)]
    assert len(ovs) == 1
    used = ovs[0]["elements"][0]["path"]
    assert os.path.normcase(used) == os.path.normcase(yt), used
    assert os.path.basename(used) != "Bible_study.png"
    assert task["platform_copies"]["render_mode"] == "once"


def test_insert_stores_each_render_mode():
    td = tempfile.mkdtemp(prefix="platmode2_")
    yt = os.path.join(td, "yt.png")
    ig = os.path.join(td, "ig.png")
    _touch(yt); _touch(ig)
    task = {
        "video": os.path.join(td, "Tik009.mp4"),
        "duration": 200.0,
        "dims": (720, 1280),
        "image_overlays": [],
    }
    sii.insert_into_tasks(
        [task], yt, start_sec=60,
        platform_copies={"youtube": yt, "instagram": ig, "render_mode": "each"},
    )
    assert task["platform_copies"]["render_mode"] == "each"
    assert sii.copies_render_mode(task) == "each"


def test_file_dialog_start():
    td = tempfile.mkdtemp(prefix="pickdir_")
    f = os.path.join(td, "a.png")
    _touch(f)
    assert sii.file_dialog_start(f, "") == f
    assert sii.file_dialog_start(td, "") == td
    missing = os.path.join(td, "nope", "x.png")
    assert sii.file_dialog_start(missing, "") == td
    other = tempfile.mkdtemp(prefix="defdir_")
    assert sii.file_dialog_start("", other) == other
    empty = sii.file_dialog_start("", "")
    assert empty in ("", sii.SUGGESTED_IMAGE_DIR)


def test_render_mode_and_overlay_swap():
    td = tempfile.mkdtemp(prefix="platmode_")
    yt = os.path.join(td, "yt.png")
    ig = os.path.join(td, "ig.png")
    _touch(yt); _touch(ig)
    cfg = sii.normalize_platform_copies(
        {"youtube": yt, "instagram": ig, "render_mode": "each"},
        start_sec=60, fit="fill",
    )
    assert cfg["render_mode"] == "each"
    task = {
        "video": os.path.join(td, "Tik009.mp4"),
        "duration": 200.0,
        "dims": (720, 1280),
        "image_overlays": [{
            "id": "ov_insert_abc",
            "_script": "insert_image",
            "start": 60,
            "end": 200,
            "w": 720,
            "h": 1280,
            "elements": [{"kind": "image", "path": yt}],
        }],
        "platform_copies": cfg,
    }
    assert sii.copies_render_mode(task) == "each"
    assert sii.copies_render_mode({"platform_copies": {"enabled": True}}) == "once"

    swapped = sii.task_with_platform_overlay(task, {"image": ig, "prefix": "Instagram"})
    assert "platform_copies" not in swapped
    ovs = [ov for ov in swapped["image_overlays"] if sii.is_insert_overlay(ov)]
    assert len(ovs) == 1
    used = ovs[0]["elements"][0]["path"]
    assert os.path.normcase(used) == os.path.normcase(ig)
    # 原 task 不能被改掉
    assert task.get("platform_copies") is cfg
    orig = task["image_overlays"][0]["elements"][0]["path"]
    assert os.path.normcase(orig) == os.path.normcase(yt)


def test_layout_fill_full_frame():
    x, y, w, h = sii.layout_element(1080, 1920, 1080, 1920, fit="fill", scale=1.0)
    assert abs(x) < 0.01 and abs(y) < 0.01
    assert abs(w - 1080) < 0.01 and abs(h - 1920) < 0.01


if __name__ == "__main__":
    test_prefixed_output_path()
    test_platform_dest_paths()
    test_normalize_and_active_copies()
    test_strip_insert_overlays()
    test_insert_uses_first_platform_image_not_default()
    test_insert_stores_each_render_mode()
    test_file_dialog_start()
    test_render_mode_and_overlay_swap()
    test_layout_fill_full_frame()
    print("ok")
