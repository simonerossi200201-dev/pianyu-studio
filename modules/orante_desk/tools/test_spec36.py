# -*- coding: utf-8 -*-
"""规格 36：角色分配、姿态剥取、锐度挑选、描述语同次写 pose。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-36-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante.services import tails  # noqa: E402
from orante.services.describe import describe_one  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import lens  # noqa: E402


class FakeJob:
    def __init__(self):
        self.lines = []
        self.cancelled = threading.Event()
        self.id = "J36"
        self.task_id = "T0000"

    def log(self, msg):
        self.lines.append(msg)

    def check(self):
        pass


def test_assign_roles_odd():
    segs = [{"s": i, "role": "主体", "image": f"p{i:02d}"} for i in range(1, 12)]
    tails.assign_roles(segs)
    roles = [s["role"] for s in segs]
    assert roles == ["anchor", "bridge", "anchor", "bridge", "anchor",
                     "bridge", "anchor", "bridge", "anchor", "bridge", "anchor"], roles
    assert segs[0].get("img_role") == "主体"
    assert segs[1]["bridge"]["from_seg"] == 1
    assert segs[1]["bridge"]["to_image"] == "p03"
    assert segs[-1]["role"] == "anchor"
    print("  odd 11 段 A B A B … A  OK")


def test_assign_roles_even():
    segs = [{"s": i, "role": "主体", "image": f"p{i:02d}"} for i in range(1, 5)]
    tails.assign_roles(segs)
    roles = [s["role"] for s in segs]
    # 偶数：末尾排成 bridge, anchor → A B B A
    assert roles == ["anchor", "bridge", "bridge", "anchor"], roles
    print("  even 4 段 A B B A  OK")


def test_assign_roles_two():
    segs = [{"s": 1, "role": "主体"}, {"s": 2, "role": "主体"}]
    tails.assign_roles(segs)
    assert [s["role"] for s in segs] == ["anchor", "anchor"]
    print("  2 段全是 anchor  OK")


def test_peel_pose():
    raw = "这是一位讲道的人【睁眼|舞台】\n【音色】白人|女\nPOSE_JSON={\"head\":\"略偏左\",\"eyes\":\"看听众\",\"hands\":\"右手抬胸\",\"body\":\"站立\",\"expr\":\"恳切\",\"text\":\"右手抬胸看向听众\"}"
    pose, rest = tails.peel_pose(raw)
    assert pose["head"] == "略偏左"
    assert pose["text"] == "右手抬胸看向听众"
    assert "POSE_JSON" not in rest
    assert "讲道" in rest
    print("  peel_pose  OK")


def test_pick_sharpest():
    cands = [
        {"k": 1, "path": "a", "sharp": 10},
        {"k": 3, "path": "b", "sharp": 40},
        {"k": 5, "path": "c", "sharp": 80},
        {"k": 8, "path": "d", "sharp": 50},
        {"k": 10, "path": "e", "sharp": 200},  # 范围外，再锐也不取
    ]
    p = tails.pick_sharpest(cands, 3, 8)
    assert p["k"] == 5, p
    print("  pick_sharpest 取倒数 5（范围内最锐） OK")


def test_laplacian_ranks_sharp_over_blur():
    from PIL import Image, ImageFilter, ImageDraw
    d = Path(tempfile.mkdtemp(prefix="sharp-"))
    sharp = d / "sharp.jpg"
    blur = d / "blur.jpg"
    im = Image.new("RGB", (240, 320), (30, 30, 30))
    dr = ImageDraw.Draw(im)
    for i in range(0, 320, 8):
        dr.line((0, i, 239, i), fill=(220, 220, 220))
        dr.line((i % 240, 0, i % 240, 319), fill=(200, 200, 200))
    im.save(sharp, "JPEG", quality=95)
    im.filter(ImageFilter.GaussianBlur(8)).save(blur, "JPEG", quality=60)
    s = tails.laplacian_var(sharp)
    b = tails.laplacian_var(blur)
    assert s > b * 2, (s, b)
    print(f"  laplacian 锐 {s:.1f} > 糊 {b:.1f}  OK")


def test_describe_one_pose_same_call():
    """描述语那一次 ask_image 顺带带回 pose，不另开调用。"""
    from PIL import Image as PILImage
    d = Path(tempfile.mkdtemp(prefix="desc36-"))
    (d / "images").mkdir()
    img = d / "images" / "p01.jpg"
    PILImage.new("RGB", (64, 96), (80, 40, 40)).save(img, "JPEG")
    m = Manifest.new("T0000", "t", "grok", "A001", "long", "hello")
    im = {"p": "p01", "file": "images/p01.jpg", "sha1": "a" * 40,
          "role": "主体", "status": "ok", "desc": "", "voice": "",
          "eye": "闭眼", "scene": "常规", "measure": {}, "origin": "download", "src": ""}
    m.images = [im]
    n_img = {"n": 0}

    class G:
        def ask_image(self, prompt, image_path, json_mode=False, **kw):
            n_img["n"] += 1
            text = str(prompt or "")
            if "摄影指导" in text or "room_left" in text:
                return json.dumps({
                    "shot_size": "中景", "facing": "正面", "room_left": "中",
                    "room_right": "中", "headroom": "中", "depth": "中",
                    "people": 1, "crowd": False, "seated": False, "level": "平视",
                }, ensure_ascii=False)
            assert "POSE_JSON" in text, "描述语调用必须附带姿态追问"
            return (
                "这是一位讲道的人【睁眼|舞台】\n【音色】白人|女|中年|清亮|美国口音\n"
                'POSE_JSON={"head":"略偏左","eyes":"看听众","hands":"右手抬胸",'
                '"body":"站立","expr":"恳切","text":"右手抬胸看向听众"}'
            )

    job = FakeJob()
    with patch.object(lens, "measure", wraps=lens.measure):
        describe_one(job, m, d, im, gemini=G(), save=False)
    assert im.get("desc"), im
    assert im.get("pose", {}).get("head") == "略偏左", im.get("pose")
    # 1 次描述语（含 pose）+ 1 次测量 = 2，和加 pose 之前一样
    assert n_img["n"] == 2, n_img
    print("  describe_one 2 次调用写出 pose  OK")


def main():
    print("spec 36")
    test_assign_roles_odd()
    test_assign_roles_even()
    test_assign_roles_two()
    test_peel_pose()
    test_pick_sharpest()
    test_laplacian_ranks_sharp_over_blur()
    test_describe_one_pose_same_call()
    print("ALL OK")


if __name__ == "__main__":
    main()
