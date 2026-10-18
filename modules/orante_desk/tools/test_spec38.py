# -*- coding: utf-8 -*-
"""规格 38：尾帧检查台 + 收片墙校验 + 默认模式仍是逐段首帧。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-38-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, config  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import tails, pipeline, bench, verify, review  # noqa: E402
from orante import tasks as taskmod  # noqa: E402

db.init()


def _seg(s, role="anchor", status="planned", image="", **kw):
    d = {
        "s": s, "text": f"台词 {s}", "duration": 6, "image": image or f"p{s:02d}",
        "role": role, "shot": "", "prompt_final": "", "status": status,
        "gen_id": None, "takes": [], "error": None,
    }
    d.update(kw)
    return d


def _img(p, status="ok", pose=None):
    return {
        "p": p, "file": f"images/{p}.jpg", "sha1": "a" * 40, "role": "主体",
        "status": status, "desc": f"描述{p}", "voice": "", "eye": "闭眼",
        "scene": "常规", "measure": {}, "origin": "download", "src": "",
        "pose": pose or {"head": "正", "eyes": "看镜头", "hands": "自然",
                         "body": "站", "expr": "温和", "text": f"{p} 站着看镜头"},
    }


def _manifest(n=5, mode="anchor_bridge", engine="flow"):
    m = Manifest.new("T0038", "测", engine, "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, 8)]
    segs = []
    for i in range(1, n + 1):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    pipeline.rebuild_bridge_links(m.segments)
    return m


def _jpeg(path: Path, tag=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9" + tag + os.urandom(8))


def _bridge(m, s=2, gap=4, passed=False, acted=False, checks=None):
    sg = m.seg(s)
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    br.update({
        "from_seg": s - 1, "to_image": m.seg(s + 1)["image"] if m.seg(s + 1) else "p03",
        "tail_frame": "clips/_tails/s%03d.jpg" % (s - 1),
        "tail_pose": {"text": "右手举起"},
        "tail_checks": checks or {"face_ok": True, "in_frame": True, "sharp": True},
        "gap_score": gap, "gap_why": "差", "auto_swapped_from": None,
    })
    if passed:
        br["passed"] = True
    if acted:
        br["bench_acted"] = True
    sg["bridge"] = br
    sg["role"] = "bridge"
    return sg


def test_default_mode_still_first_frame():
    m = Manifest.new("T1", "t", "flow", "A001", "short", "hi")
    assert m.mode == "first_frame", m.mode
    assert config.DEFAULTS.get("generate.mode") == "first_frame"
    print("  默认仍是逐段首帧  OK")


def test_levels():
    m = _manifest(5)
    sg = _bridge(m, 2, gap=2)
    assert bench.bench_level(sg, m) == "green"
    sg = _bridge(m, 2, gap=3)
    assert bench.bench_level(sg, m) == "yellow"
    # Flow 跨度 5 事后复查：黄，不是红
    sg = _bridge(m, 2, gap=5)
    assert bench.bench_level(sg, m) == "yellow"
    sg = _bridge(m, 2, gap=1, checks={"face_ok": False, "in_frame": True, "sharp": True})
    assert bench.bench_level(sg, m) == "red"
    sg = _bridge(m, 2, gap=5, passed=True)
    assert bench.bench_level(sg, m) == "green"
    mg = _manifest(5, engine="grok")
    sg = _bridge(mg, 2, gap=5)
    sg["bridge_engine"] = "grok-single"
    assert bench.bench_level(sg, mg) == "red"
    print("  红/黄/绿分档  OK")


def test_pass_rules():
    m = _manifest(5)
    yellow = _bridge(m, 2, gap=3)
    assert bench.can_pass(yellow, m) is True
    # Flow 跨度 5 已是黄，可直接过
    flow5 = _bridge(m, 2, gap=5)
    assert bench.can_pass(flow5, m) is True
    mg = _manifest(5, engine="grok")
    red = _bridge(mg, 2, gap=5)
    red["bridge_engine"] = "grok-single"
    assert bench.can_pass(red, mg) is False
    red2 = _bridge(mg, 2, gap=5, acted=True)
    red2["bridge_engine"] = "grok-single"
    assert bench.can_pass(red2, mg) is True
    print("  黄可过、红须先动作  OK")


def test_passed_forces_ready():
    m = _manifest(5, engine="grok")
    src = m.seg(1)
    src["status"] = "collected"
    br = _bridge(m, 2, gap=5)
    br["bridge_engine"] = "grok-single"
    ok = pipeline.refresh_bridge_ready(m, src, br)
    assert ok is False
    br["bridge"]["passed"] = True
    ok2 = pipeline.refresh_bridge_ready(m, src, br)
    assert ok2 is True
    assert src["bridge_ready"] is True
    print("  人工通过后强制 ready  OK")


def test_counts_banner():
    m = _manifest(7, engine="grok")
    b5 = _bridge(m, 2, gap=5)
    b5["bridge_engine"] = "grok-single"
    _bridge(m, 4, gap=3)
    _bridge(m, 6, gap=1)
    c = bench.counts_of(m)
    assert c["bench_red"] == 1, c
    assert c["bench_yellow"] == 1, c
    assert c["bench_green"] == 1, c
    dc = taskmod.decorate_counts(m)
    assert dc["bench_red"] == 1
    assert dc["bench_yellow"] == 1
    m2 = _manifest(5, mode="first_frame")
    c2 = bench.counts_of(m2)
    assert c2["bench_red"] == 0 and c2["bench_yellow"] == 0
    print("  counts 横幅红黄  OK")


def test_verify_bridge_compares_tail_not_image():
    d = Path(tempfile.mkdtemp(prefix="v38-"))
    m = _manifest(5)
    m.mode = "anchor_bridge"
    br = _bridge(m, 2, gap=2)
    br["status"] = "collected"
    br["takes"] = [{"file": "clips/s002.mp4"}]
    (d / "clips").mkdir(parents=True, exist_ok=True)
    (d / "clips" / "s002.mp4").write_bytes(b"fake-mp4-xxxx")
    _jpeg(d / "clips" / "_tails" / "s001.jpg", b"tail")
    _jpeg(d / "images" / "p02.jpg", b"img")

    TAIL, IMG, VID = 0x0, (1 << 40) - 1, 0x0

    def fake_img(path, ffmpeg=""):
        p = str(path).replace("\\", "/")
        if "_tails" in p or "s001.jpg" in p:
            return TAIL
        return IMG

    with patch.object(verify, "ahash_video", return_value=VID), \
         patch.object(verify, "ahash_image", side_effect=fake_img), \
         patch.object(verify, "find_ffmpeg", return_value="ffmpeg"):
        results = verify.verify_task(m, d, apply=False)
    by = {r["s"]: r for r in results}
    r = by.get(2)
    assert r, results
    assert r.get("ref_kind") == "tail", r
    assert r.get("verdict") == "ok", r
    assert (r.get("frame_dist") or 0) <= verify.DIST_OK
    iss = verify.issues_from_result({
        "code": verify.E_WRONG_CLIP, "frame_dist": 40,
        "ref_kind": "tail", "ref_label": "s001 尾帧",
    })
    assert any("尾帧" in (i.get("text") or "") for i in iss), iss
    assert not any("画质" in (i.get("text") or "") for i in iss)
    print("  接片 4b 对上一段尾帧  OK")


def test_verify_first_frame_mode_still_image():
    d = Path(tempfile.mkdtemp(prefix="v38b-"))
    m = _manifest(5, mode="first_frame")
    sg = m.seg(2)
    sg["role"] = "bridge"
    sg["status"] = "collected"
    sg["takes"] = [{"file": "clips/s002.mp4"}]
    sg["bridge"] = {
        "from_seg": 1, "to_image": "p03",
        "tail_frame": "clips/_tails/s001.jpg",
    }
    (d / "clips").mkdir(parents=True, exist_ok=True)
    (d / "clips" / "s002.mp4").write_bytes(b"fake-mp4-xxxx")
    _jpeg(d / "clips" / "_tails" / "s001.jpg", b"tail")
    _jpeg(d / "images" / "p02.jpg", b"img")

    TAIL, IMG, VID = 0x0, (1 << 40) - 1, 0x0

    def fake_img(path, ffmpeg=""):
        p = str(path).replace("\\", "/")
        if "_tails" in p:
            return TAIL
        return IMG

    with patch.object(verify, "ahash_video", return_value=VID), \
         patch.object(verify, "ahash_image", side_effect=fake_img), \
         patch.object(verify, "find_ffmpeg", return_value="ffmpeg"):
        results = verify.verify_task(m, d, apply=False)
    r = {x["s"]: x for x in results}.get(2)
    assert r, results
    assert r.get("ref_kind") == "image", r
    # 对配图，哈希差 40 → 拿错
    assert r.get("verdict") == "wrong_clip", r
    print("  逐段首帧模式仍对配图  OK")


def test_review_payload_bridge_fields():
    sg = {
        "s": 4, "role": "bridge",
        "bridge": {"from_seg": 3, "to_image": "p07", "tail_frame": "clips/_tails/s003.jpg"},
        "text": "hi", "image": "p04", "status": "collected", "takes": [],
        "prompt_final": "", "prompt_locked": False, "mark": False, "shot": "",
    }
    v = review._bridge_view(sg)
    assert v["from_seg"] == 3
    assert v["to_image"] == "p07"
    assert review._bridge_view({"s": 3, "role": "anchor"}) is None
    print("  收片墙 bridge 字段  OK")


def test_rescore_clears_passed():
    d = Path(tempfile.mkdtemp(prefix="rs38-"))
    m = _manifest(5)
    _jpeg(d / "images" / "p03.jpg", b"p03")
    _jpeg(d / "clips" / "_tails" / "s001.jpg", b"tail")
    br = _bridge(m, 2, gap=5, passed=True, acted=True)
    m.seg(3)["image"] = "p03"
    with patch.object(tails, "score_gap", return_value=(1, "小")):
        pipeline._rescore_bridge(d, m, br)
    assert br["bridge"].get("passed") in (None, False)
    assert br["bridge"]["gap_score"] == 1
    print("  重算跨度清掉旧放行  OK")


def test_extract_k_range():
    assert hasattr(tails, "extract_k")
    assert bench.TAIL_PICK_N == 15
    print("  手挑尾帧 1~15  OK")


def main():
    print("spec 38")
    test_default_mode_still_first_frame()
    test_levels()
    test_pass_rules()
    test_passed_forces_ready()
    test_counts_banner()
    test_verify_bridge_compares_tail_not_image()
    test_verify_first_frame_mode_still_image()
    test_review_payload_bridge_fields()
    test_rescore_clears_passed()
    test_extract_k_range()
    print("ALL OK")


if __name__ == "__main__":
    main()
