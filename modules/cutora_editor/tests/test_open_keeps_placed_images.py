"""打开工程时不许删掉用户放在图片轨上的图片。

2026-09-21：Q27 的迁移 `_strip_overlay_elements` 对 image_overlays 也删 kind=="image"
的元素。批量加载、资产库、拖放插进来的图片全是这种结构，于是每次打开工程图片就被清空，
时间线上只剩空条；用户一保存，硬盘上的路径也没了。存盘本身是完整的，毁在打开的那一刻。
"""
from __future__ import annotations

import copy

from cutora.subtitle.text_layer import migrate_open_project


def _image_overlay(path):
    # 与 batch/script_insert_image.make_overlay、assets/user.build_image_overlay 同形
    return {"type": "image", "start": 0.0, "end": 5.0, "_track_id": "T1",
            "elements": [{"kind": "image", "path": path, "x": 0, "y": 0,
                          "w": 1080, "h": 1920, "opacity": 1.0}]}


def test_open_keeps_batch_loaded_images():
    data = {"tasks": [{"image_overlays": [_image_overlay(r"D:\\pics\\a.png"),
                                          _image_overlay(r"D:\\pics\\b.png")]}]}
    before = copy.deepcopy(data)
    n, _msg = migrate_open_project(data)
    assert n == 0, "打开工程时删掉了 %d 个用户放的图片" % n
    assert data == before


def test_open_still_strips_legacy_text_image_elements():
    """Q27 的本意保留：文本层里那个已废弃的「图片元素」功能仍然被清掉。"""
    data = {"tasks": [{"text_overlays": [{"type": "text", "text": "hi",
        "elements": [{"kind": "image", "path": r"D:\\x.png"}]}]}]}
    n, _msg = migrate_open_project(data)
    assert n == 1
    assert data["tasks"][0]["text_overlays"][0]["elements"] == []


def test_open_keeps_stickers_everywhere():
    data = {"tasks": [{"image_overlays": [{"elements": [
        {"kind": "image", "sticker_id": "heart", "path": ""}]}]}]}
    n, _msg = migrate_open_project(data)
    assert n == 0
