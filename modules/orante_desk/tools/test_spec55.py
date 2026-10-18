# -*- coding: utf-8 -*-
"""规格 55：重生主片后，下游接片不能留着用旧尾帧拍的那一条。先设 ORANTE_DATA。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-55-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import bench, pipeline, tails, verify  # noqa: E402

db.init()


def _seg(s, role="anchor", status="planned", image="", **kw):
    d = {
        "s": s, "text": f"台词 {s}", "duration": 6, "image": image or f"p{s:02d}",
        "role": role, "shot": "", "prompt_final": "", "status": status,
        "gen_id": None, "takes": [], "error": None,
    }
    d.update(kw)
    return d


def _img(p, status="ok"):
    return {
        "p": p, "file": f"images/{p}.jpg", "sha1": "a" * 40, "role": "主体",
        "status": status, "desc": f"描述{p}", "voice": "【音色】测",
        "eye": "闭眼", "scene": "常规", "measure": {}, "origin": "download", "src": "",
        "pose": {"head": "正", "eyes": "看镜头", "hands": "自然",
                 "body": "站", "expr": "温和", "text": f"{p} 站着看镜头"},
    }


def _jpeg(path: Path, tag=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9" + tag + os.urandom(8))


def _real_jpeg(path: Path, color=(40, 80, 120)):
    path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.new("RGB", (32, 32), color).save(path, "JPEG")


def _hx(bits: int) -> str:
    return verify.ahash_hex(bits)


def _manifest(n=5, mode="anchor_bridge", engine="flow"):
    m = Manifest.new("T0055", "测", engine, "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, 8)]
    segs = []
    for i in range(1, n + 1):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    pipeline.rebuild_bridge_links(m.segments)
    return m


def _collected_pair(m, d: Path, dist: int, gap=2, ready=True, tail_ahash=None):
    """s001 主片 collected，s002 接片 collected；dist = 尾帧指纹 vs 接片首帧。"""
    src = m.seg(1)
    src["status"] = "collected"
    src["bridge_ready"] = bool(ready)
    src["takes"] = [{"file": "clips/s001.mp4"}]
    br = m.seg(2)
    br["role"] = "bridge"
    br["status"] = "collected"
    tail_bits, take_bits = 0, (1 << dist) - 1 if dist else 0
    hx_tail = _hx(tail_bits) if tail_ahash is None else tail_ahash
    hx_take = _hx(take_bits)
    clip = d / "clips" / "s002.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"old-bridge-clip")
    br["takes"] = [{"file": "clips/s002.mp4", "frame_ahash": hx_take}]
    bridge = br.get("bridge") if isinstance(br.get("bridge"), dict) else {}
    bridge.update({
        "from_seg": 1,
        "to_image": (m.seg(3) or {}).get("image") or "p03",
        "tail_frame": "clips/_tails/s001.jpg",
        "tail_pose": {"text": "右手举起"},
        "tail_checks": {"face_ok": True, "in_frame": True, "sharp": True},
        "gap_score": gap, "gap_why": "小",
    })
    if hx_tail is not False:
        bridge["tail_ahash"] = hx_tail
    br["bridge"] = bridge
    return src, br


def test_keep_when_hash_almost_same():
    d = Path(tempfile.mkdtemp(prefix="keep55-"))
    m = _manifest(5)
    src, br = _collected_pair(m, d, dist=0, gap=2)
    logs = []
    r = pipeline.cascade_after_anchor(d, m, src, log=logs.append)
    assert r["action"] == "keep", r
    assert br["status"] == "collected"
    assert (d / "clips" / "s002.mp4").is_file()
    assert any("几乎没变" in x and "不动" in x for x in logs), logs
    print("  尾帧几乎没变（距 0）→ keep  OK")


def test_keep_at_dist_soft():
    d = Path(tempfile.mkdtemp(prefix="keep16-"))
    m = _manifest(5)
    src, br = _collected_pair(m, d, dist=16, gap=2)
    r = pipeline.cascade_after_anchor(d, m, src)
    assert r["action"] == "keep", r
    assert r.get("dist") == 16
    assert br["status"] == "collected"
    print("  距 16（= DIST_SOFT）→ keep  OK")


def test_reset_when_tail_changed():
    d = Path(tempfile.mkdtemp(prefix="stale55-"))
    m = _manifest(5)
    src, br = _collected_pair(m, d, dist=30, gap=2)
    logs = []
    r = pipeline.cascade_after_anchor(d, m, src, log=logs.append)
    assert r["action"] == "reset", r
    assert r.get("reason") == "stale_tail", r
    assert br["status"] == "planned"
    assert br.get("stale_from") == 1
    assert not (d / "clips" / "s002.mp4").is_file()
    trash = d / "clips" / "_弃用"
    assert trash.is_dir()
    assert any(p.name.endswith("s002.mp4") for p in trash.iterdir()), list(trash.iterdir())
    blob = "\n".join(logs)
    assert "重生 s001" in blob and "打回 s002" in blob and "尾帧已换" in blob, logs
    print("  尾帧换了（距 30）→ reset stale_tail，旧片进 _弃用  OK")


def test_missing_tail_ahash_resets():
    d = Path(tempfile.mkdtemp(prefix="miss55-"))
    m = _manifest(5)
    src, br = _collected_pair(m, d, dist=0, gap=2)
    br["bridge"].pop("tail_ahash", None)
    logs = []
    r = pipeline.cascade_after_anchor(d, m, src, log=logs.append)
    assert r["action"] == "reset", r
    assert r.get("reason") == "stale_tail", r
    assert br["status"] == "planned"
    assert any("算不出尾帧指纹" in x for x in logs), logs
    print("  缺 tail_ahash → reset，日志点名算不出  OK")


def test_gap_small_but_hash_far_still_resets():
    """本规格核心：跨度 ≤3 不再当免死金牌。"""
    d = Path(tempfile.mkdtemp(prefix="gap55-"))
    m = _manifest(5)
    src, br = _collected_pair(m, d, dist=30, gap=2)
    r = pipeline.cascade_after_anchor(d, m, src)
    assert r["action"] == "reset", r
    assert r.get("reason") == "stale_tail", r
    assert r.get("gap") == 2
    assert br["status"] == "planned"
    print("  跨度 2 但距 30 → 仍然 reset  OK")


def test_same_rule_for_grok():
    d = Path(tempfile.mkdtemp(prefix="grok55-"))
    m = _manifest(5, engine="grok")
    src, br = _collected_pair(m, d, dist=30, gap=1)
    r = pipeline.cascade_after_anchor(d, m, src)
    assert r["action"] == "reset", r
    assert r.get("reason") == "stale_tail"
    print("  Grok 接片同一套陈旧检查  OK")


def test_chain_resets_downstream_bridges():
    d = Path(tempfile.mkdtemp(prefix="chain55-"))
    m = _manifest(5)
    src, br2 = _collected_pair(m, d, dist=30, gap=2)
    # 人为构造 s001 主片 → s002 接片 → s003 接片
    s3 = m.seg(3)
    s3["role"] = "bridge"
    s3["status"] = "collected"
    clip3 = d / "clips" / "s003.mp4"
    clip3.write_bytes(b"old-bridge-3")
    s3["takes"] = [{"file": "clips/s003.mp4", "frame_ahash": _hx(0)}]
    s3["bridge"] = {
        "from_seg": 2, "to_image": "p04",
        "tail_frame": "clips/_tails/s002.jpg",
        "tail_ahash": _hx(0),
        "gap_score": 1, "gap_why": "小",
        "tail_checks": {"face_ok": True, "in_frame": True},
    }
    m.seg(4)["role"] = "anchor"
    logs = []
    r = pipeline.cascade_after_anchor(d, m, src, log=logs.append)
    assert r["action"] == "reset", r
    assert r.get("reason") == "stale_tail"
    assert br2["status"] == "planned"
    assert s3["status"] == "planned"
    assert 3 in (r.get("reset") or []), r
    assert not clip3.is_file()
    blob = "\n".join(logs)
    assert "重生 s001" in blob and "s002" in blob and "s003" in blob, logs
    print("  链式 s001→s002→s003 两段都打回  OK")


def test_chain_stops_at_anchor():
    d = Path(tempfile.mkdtemp(prefix="stop55-"))
    m = _manifest(5)
    src, br2 = _collected_pair(m, d, dist=30, gap=2)
    # s003 是主片，s004 是接片 collected —— 不该被 s001 的重生打回
    m.seg(3)["role"] = "anchor"
    m.seg(3)["status"] = "collected"
    s4 = m.seg(4)
    s4["role"] = "bridge"
    s4["status"] = "collected"
    s4["takes"] = [{"file": "clips/s004.mp4", "frame_ahash": _hx(99)}]
    s4["bridge"] = {"from_seg": 3, "gap_score": 1, "tail_ahash": _hx(0)}
    r = pipeline.cascade_after_anchor(d, m, src)
    assert br2["status"] == "planned"
    assert s4["status"] == "collected", "主片隔开的接片不该被打回"
    print("  链在下一个主片处停住  OK")


def test_fill_bridge_writes_tail_ahash():
    d = Path(tempfile.mkdtemp(prefix="fill55-"))
    m = _manifest(5)
    tail = d / "clips" / "_tails" / "s001.jpg"
    _real_jpeg(tail, (12, 34, 56))
    src = m.seg(1)
    src["status"] = "collected"
    br = m.seg(2)
    picked = {"k": 4, "sharp": 200.0, "below_min": False}
    inspect = {"face_ok": True, "in_frame": True, "pose": {"text": "站"}}
    tails._fill_bridge(m, d, br, src, picked, inspect)
    out = br["bridge"]
    assert out.get("tail_frame", "").replace("\\", "/").endswith("s001.jpg")
    hx = out.get("tail_ahash") or ""
    assert len(hx) == 64 and all(c in "0123456789abcdef" for c in hx), hx
    assert "T" in (out.get("tail_at") or "") or "-" in (out.get("tail_at") or "")
    print("  _fill_bridge 写 tail_ahash + tail_at  OK")


def test_archive_old_tail_keeps_three():
    d = Path(tempfile.mkdtemp(prefix="arch55-"))
    dest = tails.tail_path(d, 1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    old = dest.parent / "_旧"
    for i in range(4):
        dest.write_bytes(("old-%d" % i).encode() + b"-" * 8)
        moved = tails.archive_old_tail(dest)
        assert moved is not None
        assert not dest.exists()
        time.sleep(0.02)
    files = sorted(p.name for p in old.glob("s001-*") if p.is_file())
    assert len(files) == 3, files
    print("  覆盖尾帧旧图进 _旧/，超过 3 份删最旧  OK")


def test_extract_archives_before_overwrite():
    d = Path(tempfile.mkdtemp(prefix="ext55-"))
    dest = tails.tail_path(d, 1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"previous-tail-bytes")
    clip = d / "clips" / "s001.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"fake-mp4")
    picked_img = dest.parent / "_scan_s001" / "from_end_03.jpg"
    _real_jpeg(picked_img, (200, 10, 10))
    cands = [{"k": 3, "path": picked_img, "sharp": 220.0, "error": None}]
    seg = {"s": 1, "takes": [{"file": "clips/s001.mp4"}]}
    with patch.object(tails, "scan_tail_candidates", return_value=cands), \
         patch.object(tails, "clip_of", return_value=clip):
        info = tails.extract(d, seg, inspect=False)
    assert info["s"] == 1
    assert dest.is_file()
    olds = list((dest.parent / "_旧").glob("s001-*.jpg"))
    assert len(olds) == 1, olds
    assert olds[0].read_bytes().startswith(b"previous-tail-bytes")
    print("  extract 覆盖前旧图进 _旧/  OK")


def test_cascade_cost_preview():
    m = _manifest(5)
    m.seg(1)["role"] = "anchor"
    m.seg(1)["status"] = "collected"
    m.seg(2)["role"] = "bridge"
    m.seg(2)["status"] = "collected"
    m.seg(3)["role"] = "anchor"
    m.seg(3)["status"] = "collected"
    m.seg(4)["role"] = "bridge"
    m.seg(4)["status"] = "collected"
    assert pipeline.cascade_cost_of(m, m.seg(1)) == [2]
    assert pipeline.cascade_cost_of(m, m.seg(3)) == [4]
    print("  cascade_cost_of 只看到下一个主片之前的接片  OK")


def test_bench_stale_mark():
    d = Path(tempfile.mkdtemp(prefix="bench55-"))
    m = _manifest(5)
    sg = m.seg(2)
    sg["role"] = "bridge"
    sg["status"] = "planned"
    sg["stale_from"] = 1
    sg["bridge"] = {
        "from_seg": 1, "to_image": "p03", "gap_score": 2,
        "stale_tail": True,
        "tail_checks": {"face_ok": True, "in_frame": True, "sharp": True},
    }
    it = bench._item("T0055", d, m, sg)
    assert it["stale_from"] == 1
    assert it["stale_tail"] is True
    print("  检查台 item 带 stale_from / stale_tail  OK")
