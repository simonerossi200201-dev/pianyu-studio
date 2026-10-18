# -*- coding: utf-8 -*-
"""规格 37：锚点+接片流水线。DAG、自动换锚点、重生联动、提示词、Flow 槽位、模式。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-37-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, config  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import tails, pipeline  # noqa: E402
from orante.engines import flow_api  # noqa: E402

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


def _manifest(n=9, mode="anchor_bridge"):
    m = Manifest.new("T0037", "测", "flow", "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, 8)]
    m.images.append(_img("p08"))
    m.images.append(_img("p09"))
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


def _touch_images(d: Path, m: Manifest):
    for im in m.images:
        _jpeg(d / im["file"], im["p"].encode())


def test_mode_default():
    m = Manifest.new("T1", "t", "flow", "A001", "short", "hi")
    assert m.mode == "first_frame", m.mode
    assert config.DEFAULTS.get("generate.mode") == "first_frame"
    print("  默认逐段首帧  OK")


def test_set_mode_and_spacing():
    m = _manifest(9, mode="first_frame")
    r = pipeline.set_mode(m, "anchor_bridge")
    assert r["mode"] == "anchor_bridge"
    assert m.track.get("no_adjacent_repeat") is True
    assert [sg["role"] for sg in m.segments][0] == "anchor"
    assert m.segments[-1]["role"] == "anchor"
    print("  切到锚点模式打开间隔排列  OK")


def test_dag_ready():
    m = _manifest(9)
    pipeline.stamp_waits(m)
    anchors = [sg for sg in m.segments if sg["role"] == "anchor"]
    bridges = [sg for sg in m.segments if sg["role"] == "bridge"]
    assert all(pipeline.can_enqueue(m, sg) for sg in anchors), "主片应直接能进队"
    assert not any(pipeline.can_enqueue(m, sg) for sg in bridges), "接片一开始不能进队"
    why = pipeline.wait_reason(m, bridges[0])
    assert why and "s001" in why["text"], why
    # s001 收了但还没 ready
    m.seg(1)["status"] = "collected"
    assert not pipeline.can_enqueue(m, m.seg(2))
    m.seg(1)["bridge_ready"] = True
    assert pipeline.can_enqueue(m, m.seg(2)), "s001 ready 后 s002 应能进队"
    # s004 还要等 s003
    assert not pipeline.can_enqueue(m, m.seg(4))
    print("  DAG：主片立刻、接片等上一段  OK")


def test_iter_ready_pipeline():
    m = _manifest(9)
    wanted = {sg["s"] for sg in m.segments}
    ready = [sg["s"] for sg in pipeline.iter_ready(m, wanted, set())]
    assert ready == [1, 3, 5, 7, 9], ready
    m.seg(1)["status"] = "collected"
    m.seg(1)["bridge_ready"] = True
    ready2 = [sg["s"] for sg in pipeline.iter_ready(m, wanted, {1, 3})]
    assert 2 in ready2, ready2
    assert 4 not in ready2
    print("  交错：s002 在 s003 还在忙时就能进队  OK")


def test_waiting_not_failed():
    m = _manifest(9)
    pipeline.stamp_waits(m)
    b = m.seg(2)
    assert b["status"] == "planned"
    assert b.get("wait_on") == 1
    assert "失败" not in (b.get("wait_why") or "")
    print("  等前置不算失败  OK")


def test_auto_swap_picks_lowest():
    d = Path(tempfile.mkdtemp(prefix="swap37-"))
    m = _manifest(5)
    _touch_images(d, m)
    m.mode = "anchor_bridge"
    # s002 接片，目标是 s003 的图
    br = m.seg(2)
    tails_dir = d / "clips" / "_tails"
    _jpeg(tails_dir / "s001.jpg", b"tail")
    br["bridge"] = {
        "from_seg": 1, "to_image": m.seg(3)["image"],
        "tail_frame": "clips/_tails/s001.jpg",
        "tail_pose": {"text": "手举起"}, "tail_checks": {"face_ok": True, "in_frame": True},
        "gap_score": 5, "gap_why": "差很大", "auto_swapped_from": None,
    }
    m.seg(1)["status"] = "collected"
    scores = {"p01": 5, "p02": 4, "p03": 5, "p04": 1, "p05": 3, "p06": 2, "p07": 4}

    def fake_gap(tail, dest, *a, **k):
        name = Path(dest).stem  # p04
        return scores.get(name, 5), "假"

    with patch.object(tails, "score_gap", side_effect=fake_gap):
        r = pipeline.auto_swap_anchor(d, m, br, log=lambda s: None)
    assert r and r["changed"], r
    tried = r["tried"]
    assert 1 <= len(tried) <= pipeline.MAX_SWAP_CANDIDATES, tried
    best = min((t for t in tried if t["gap"] is not None), key=lambda t: t["gap"])
    assert r["to"] == best["p"], r
    assert r["gap_to"] == best["gap"]
    assert r["gap_to"] < r["gap_from"]
    assert br["bridge"]["auto_swapped_from"] == "p03"
    pins = m.track.get("pins") or {}
    assert pins.get("3") == r["to"] or pins.get(3) == r["to"]
    assert m.seg(3)["image"] == r["to"]
    print(f"  自动换锚点 {r['from']} → {r['to']}（跨度 {r['gap_from']} → {r['gap_to']}） OK")


def test_auto_swap_max_four_and_blocked():
    d = Path(tempfile.mkdtemp(prefix="swap37b-"))
    m = _manifest(5)
    for im in m.images:
        if im["p"] not in ("p03", "p01"):
            im["status"] = "blocked"
    _touch_images(d, m)
    br = m.seg(2)
    _jpeg(d / "clips" / "_tails" / "s001.jpg", b"tail")
    br["bridge"] = {
        "from_seg": 1, "to_image": "p03",
        "tail_frame": "clips/_tails/s001.jpg",
        "tail_pose": {"text": "x"}, "tail_checks": {"face_ok": True, "in_frame": True},
        "gap_score": 5, "gap_why": "大",
    }
    m.seg(3)["image"] = "p03"
    called = []

    def fake_gap(tail, dest, *a, **k):
        called.append(Path(dest).stem)
        return 5, "仍大"

    with patch.object(tails, "score_gap", side_effect=fake_gap):
        r = pipeline.auto_swap_anchor(d, m, br)
    # 图池几乎全 blocked，最多试剩下的、且 ≤4
    assert len(called) <= pipeline.MAX_SWAP_CANDIDATES
    assert r is None or r.get("changed") is False
    print(f"  换不动（试了 {len(called)} 张，上限 4） OK")


def test_gap_gate():
    d = Path(tempfile.mkdtemp(prefix="gate37-"))
    m = _manifest(5)
    _touch_images(d, m)
    src = m.seg(1)
    src["status"] = "collected"
    br = m.seg(2)
    _jpeg(d / "clips" / "_tails" / "s001.jpg", b"tail")
    br["bridge"] = {
        "from_seg": 1, "to_image": m.seg(3)["image"],
        "tail_frame": "clips/_tails/s001.jpg",
        "tail_pose": {"text": "a"}, "tail_checks": {"face_ok": True, "in_frame": True},
        "gap_score": 2, "gap_why": "小",
    }
    g = pipeline.apply_gap_gate(d, m, src)
    assert g["ready"] is True, g
    assert src.get("bridge_ready") is True
    br["bridge"]["gap_score"] = 5
    with patch.object(pipeline, "auto_swap_anchor", return_value=None):
        g2 = pipeline.apply_gap_gate(d, m, src)
    # Flow 跨度闸是 advise：换不成仍 ready
    assert g2["ready"] is True
    m.engine = "grok"
    br["bridge_engine"] = "grok-single"
    with patch.object(pipeline, "auto_swap_anchor", return_value=None):
        g3 = pipeline.apply_gap_gate(d, m, src)
    assert g3["ready"] is False
    print("  跨度 ≤3 放行；Flow ≥4 仍 ready，Grok ≥4 换不成不 ready  OK")


def test_cascade_keep_and_reset():
    d = Path(tempfile.mkdtemp(prefix="cas37-"))
    m = _manifest(5)
    src = m.seg(1)
    src["status"] = "collected"
    src["bridge_ready"] = True
    br = m.seg(2)
    br["status"] = "collected"
    hx = "0" * 64
    br["bridge"] = {"gap_score": 2, "to_image": "p03", "tail_frame": "x",
                    "tail_ahash": hx}
    br["takes"] = [{"file": "clips/s002.mp4", "frame_ahash": hx}]
    r = pipeline.cascade_after_anchor(d, m, src)
    assert r["action"] == "keep", r
    br["bridge"]["gap_score"] = 4
    src["bridge_ready"] = False
    r2 = pipeline.cascade_after_anchor(d, m, src)
    assert r2["action"] == "reset", r2
    assert br["status"] == "planned"
    print("  重生联动：跨度 2 不动，跨度 4 打回  OK")


def test_toggle_role_last_forced():
    m = _manifest(5)
    last_s = m.segments[-1]["s"]
    r = pipeline.toggle_role(m, last_s)
    assert m.seg(last_s)["role"] == "anchor"
    assert "末段" in r["hint"]
    r2 = pipeline.toggle_role(m, 2)
    assert r2["role"] in ("anchor", "bridge")
    assert m.segments[-1]["role"] == "anchor"
    print("  切 role：末段强制主片  OK")


def test_bridge_prompt_has_three_lines_and_dialogue():
    m = _manifest(5)
    m.mode = "anchor_bridge"
    sg = m.seg(2)
    sg["text"] = "It comes from the Spirit of truth."
    sg["bridge"] = {
        "tail_pose": {"text": "右手举起"},
        "to_image": m.seg(3)["image"],
    }
    m.seg(3)["image"] = "p03"
    body = "【镜头】向前推。必须准确地说出以下英文：It comes from the Spirit of truth."
    out = pipeline.wrap_bridge_prompt(m, sg, body)
    assert "01_首帧_start.jpg" in out
    assert "02_尾帧_end.jpg" in out
    assert "不切镜" in out or "不跳剪" in out
    assert "右手举起" in out
    assert "It comes from the Spirit of truth" in out
    print("  接片提示词含过渡三句 + 台词  OK")


def test_flow_slot_2():
    eng = flow_api.FlowEngine.__new__(flow_api.FlowEngine)
    eng.model_name = "Veo 3.1 - Lite [Lower Priority]"
    eng.collection_id = "cid"
    eng.project_id = "pid"
    inner = eng._gen_inner("hello", "STARTIDxxxxxxxxxxxxSTARTID", [0.0, None, 1.0, 1], 6, "tok")
    slot = inner[0][0][4]
    assert slot[1].startswith("START"), slot
    assert slot[2] is None, slot
    inner2 = eng._gen_inner("hello", "STARTIDxxxxxxxxxxxxSTARTID", [0.0, None, 1.0, 1], 6, "tok",
                            last_media_id="ENDIDxxxxxxxxxxxxxxENDID")
    slot2 = inner2[0][0][4]
    assert slot2[2].startswith("END"), slot2
    print("  Flow 图片槽下标 2 写尾帧  OK")


def test_stage_bridge_filenames():
    d = Path(tempfile.mkdtemp(prefix="tmp37-"))
    m = _manifest(5)
    _touch_images(d, m)
    _jpeg(d / "clips" / "_tails" / "s001.jpg", b"tail")
    sg = m.seg(2)
    sg["bridge"] = {
        "tail_frame": "clips/_tails/s001.jpg",
        "to_image": m.seg(3)["image"],
    }
    p1, p2, tmp = pipeline.stage_bridge_images(d, m, sg)
    assert p1.name == "01_首帧_start.jpg", p1.name
    assert p2.name == "02_尾帧_end.jpg", p2.name
    assert p1.is_file() and p2.is_file()
    pipeline.cleanup_bridge_tmp(d, 2)
    assert not tmp.is_dir()
    print("  临时目录 01/02 文件名  OK")


def test_failed_anchor_does_not_fail_bridge():
    m = _manifest(5)
    m.seg(1)["status"] = "failed"
    m.seg(1)["error"] = "生成失败"
    pipeline.stamp_waits(m)
    b = m.seg(2)
    assert b["status"] == "planned"
    why = pipeline.wait_reason(m, b)
    assert why and "s001" in why["text"]
    print("  主片失败，接片仍 planned 等前置  OK")


def test_counts_include_waiting():
    from orante import tasks as taskmod
    m = _manifest(9)
    pipeline.stamp_waits(m)
    c = taskmod.decorate_counts(m)
    assert c["mode"] == "anchor_bridge"
    assert c["waiting"] >= 1
    assert "waiting" in c["cells"]
    assert len(c["roles"]) == 9
    print(f"  counts.waiting={c['waiting']} cells 含 waiting  OK")


def test_prompt_locked_untouched():
    m = _manifest(5)
    m.mode = "anchor_bridge"
    sg = m.seg(2)
    sg["prompt_locked"] = True
    sg["prompt_final"] = "手写提示词 KEEP"
    sg["bridge"] = {"tail_pose": {"text": "x"}, "to_image": "p03"}
    out = pipeline.final_prompt(m, sg)
    assert out == "手写提示词 KEEP"
    print("  prompt_locked 不重拼  OK")


def main():
    print("spec 37")
    test_mode_default()
    test_set_mode_and_spacing()
    test_dag_ready()
    test_iter_ready_pipeline()
    test_waiting_not_failed()
    test_auto_swap_picks_lowest()
    test_auto_swap_max_four_and_blocked()
    test_gap_gate()
    test_cascade_keep_and_reset()
    test_toggle_role_last_forced()
    test_bridge_prompt_has_three_lines_and_dialogue()
    test_flow_slot_2()
    test_stage_bridge_filenames()
    test_failed_anchor_does_not_fail_bridge()
    test_counts_include_waiting()
    test_prompt_locked_untouched()
    print("ALL OK")


if __name__ == "__main__":
    main()
