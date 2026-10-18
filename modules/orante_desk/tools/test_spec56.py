# -*- coding: utf-8 -*-
"""规格 56：Flow 跨度闸事后复查 + 段级单图重拍。先设 ORANTE_DATA。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-56-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config, db  # noqa: E402
from orante.errors import OranteError  # noqa: E402
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


def _hx(bits: int) -> str:
    return verify.ahash_hex(bits)


def _manifest(n=5, mode="anchor_bridge", engine="flow"):
    m = Manifest.new("T0056", "测", engine, "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, 8)]
    segs = []
    for i in range(1, n + 1):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    pipeline.rebuild_bridge_links(m.segments)
    return m


def _ready_bridge(m, s=2, gap=5, checks=None, engine_tag=None):
    src = m.seg(s - 1)
    src["status"] = "collected"
    br = m.seg(s)
    br["role"] = "bridge"
    br["status"] = "planned"
    if engine_tag:
        br["bridge_engine"] = engine_tag
    bridge = br.get("bridge") if isinstance(br.get("bridge"), dict) else {}
    nxt = m.seg(s + 1)
    bridge.update({
        "from_seg": s - 1,
        "to_image": (nxt or {}).get("image") or "p03",
        "tail_frame": "clips/_tails/s%03d.jpg" % (s - 1),
        "tail_pose": {"text": "右手举起"},
        "tail_checks": checks or {"face_ok": True, "in_frame": True, "sharp": True},
        "gap_score": gap, "gap_why": "跨度测",
    })
    br["bridge"] = bridge
    if nxt and not nxt.get("image"):
        nxt["image"] = "p03"
    pipeline.refresh_bridge_ready(m, src, br)
    return src, br


def _ensure_account(aid="A001", engine="flow"):
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, "t-" + engine, engine, 9397, aid, "", "", "9:16", 5, 1, "{}", db.now()))
    else:
        db.run("UPDATE accounts SET engine=? WHERE id=?", (engine, aid))


def _make_task(tid, engine="flow", n=5, mode="anchor_bridge"):
    _ensure_account("A001", engine)
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-56-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
    m = _manifest(n, mode=mode, engine=engine)
    m.task_id = tid
    for im in m.images:
        _jpeg(task_dir / im["file"], im["p"].encode())
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(task_dir), "arranged", engine, "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格56", engine, "A001", "long", "hello", str(task_dir / "images"),
             "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_gate_mode():
    mf = _manifest(5, engine="flow")
    mg = _manifest(5, engine="grok")
    assert pipeline.gate_mode_of(mf, mf.seg(2)) == "advise"
    assert pipeline.gate_mode_of(mg, mg.seg(2)) == "block"
    sg = mg.seg(2)
    sg["bridge_engine"] = "grok-single"
    assert pipeline.gate_mode_of(mg, sg) == "block"
    print("  gate_mode Flow=advise / Grok=block  OK")


def test_flow_gap5_advise():
    m = _manifest(5, engine="flow")
    src, br = _ready_bridge(m, 2, gap=5)
    assert src.get("bridge_ready") is True
    assert pipeline.can_enqueue(m, br) is True
    why = pipeline.wait_reason(m, br)
    assert why is None, why
    assert bench.bench_level(br, m) == "yellow"
    print("  Flow 跨度 5：能进队、不提检查台、黄不是红  OK")


def test_grok_gap5_still_block():
    m = _manifest(5, engine="grok")
    src, br = _ready_bridge(m, 2, gap=5, engine_tag="grok-single")
    assert src.get("bridge_ready") is False
    assert pipeline.can_enqueue(m, br) is False
    why = pipeline.wait_reason(m, br)
    assert why and "检查台" in why["text"], why
    assert bench.bench_level(br, m) == "red"
    print("  Grok 单图接片跨度 5：仍 block、仍红  OK")


def test_flow_face_ok_false_still_blocks():
    m = _manifest(5, engine="flow")
    src, br = _ready_bridge(
        m, 2, gap=2,
        checks={"face_ok": False, "in_frame": True, "sharp": True})
    assert src.get("bridge_ready") is False
    assert pipeline.can_enqueue(m, br) is False
    assert bench.bench_level(br, m) == "red"
    print("  Flow face_ok=False：仍红、仍拦  OK")


def test_swap_all_red_no_stamp_on_stuck():
    tid, d, m = _make_task("T5601", engine="grok", n=5)
    _jpeg(d / "clips" / "_tails" / "s001.jpg", b"tail")
    _jpeg(d / "clips" / "_tails" / "s003.jpg", b"tail3")
    src2, b2 = _ready_bridge(m, 2, gap=5, engine_tag="grok-single")
    src4, b4 = _ready_bridge(m, 4, gap=5, engine_tag="grok-single")
    m.seg(3)["status"] = "planned"
    m.seg(5)["status"] = "collected"
    m.save(d)

    def fake_swap(task_dir, mm, sg, log=None, gemini=None, job=None):
        nxt = pipeline.next_of(mm, sg)
        if nxt and nxt.get("status") in ("collected", "submitted"):
            return None
        return {"from": "p03", "to": "p04", "gap_from": 5, "gap_to": 2,
                "tried": [], "changed": True}

    with patch.object(pipeline, "auto_swap_anchor", side_effect=fake_swap), \
            patch.object(bench, "_kick_generate", return_value=None):
        r = bench.swap_all_red(tid)
    m2 = Manifest.load(d)
    assert m2.seg(2)["bridge"].get("bench_acted") is True
    assert not m2.seg(4)["bridge"].get("bench_acted")
    assert r["swapped"] == 1, r
    assert r["stuck"] == 1, r
    assert "换成 1" in r["summary_text"] and "换不动 1" in r["summary_text"], r["summary_text"]
    print("  swap_all_red：换成 1、换不动 1，换不动不盖章  OK")


def test_fallback_single_and_toggle_back():
    tid, d, m = _make_task("T5602", engine="flow", n=4)
    src, br = _ready_bridge(m, 2, gap=2)
    br["status"] = "collected"
    br["gen_id"] = "gid-old"
    br["mismatch_count"] = 1
    clip = d / "clips" / "s002.mp4"
    clip.write_bytes(b"old-bridge-clip")
    br["takes"] = [{"file": "clips/s002.mp4"}]
    m.save(d)
    assert br["role"] == "bridge"
    assert m.seg(3)["role"] == "bridge"

    with patch.object(bench, "_kick_generate", return_value=None):
        r = bench.fallback_single(tid, 2)
    m2 = Manifest.load(d)
    sg = m2.seg(2)
    assert r["changed"] is True
    assert sg["role"] == "anchor"
    assert sg["status"] == "planned"
    assert sg.get("gen_id") in (None, "")
    assert "bridge" not in sg or not sg.get("bridge")
    assert int(sg.get("mismatch_count") or 0) == 0
    assert not clip.is_file()
    trash = d / "clips" / "_弃用"
    assert trash.is_dir()
    assert any(p.name.endswith("s002.mp4") for p in trash.iterdir()), list(trash.iterdir())
    nxt = m2.seg(3)
    assert nxt["role"] == "bridge"
    assert int((nxt.get("bridge") or {}).get("from_seg") or 0) == 2

    r2 = pipeline.toggle_role(m2, 2)
    assert r2["role"] == "bridge"
    assert m2.seg(2)["role"] == "bridge"
    print("  fallback_single：作废旧片、下一段 from_seg 指向它、能切回  OK")


def test_fallback_single_boundaries():
    tid, d, m = _make_task("T5603", engine="flow", n=5)
    last = m.segments[-1]["s"]
    with patch.object(bench, "_kick_generate", return_value=None):
        r_last = bench.fallback_single(tid, last)
        r_first = bench.set_seg_mode(tid, 1, "bridge")
    assert r_last["changed"] is False
    assert r_last["role"] == "anchor"
    assert r_first["changed"] is False
    assert r_first["role"] == "anchor"
    tr = pipeline.toggle_role(Manifest.load(d), last)
    assert tr["role"] == "anchor"
    assert "末段" in tr["hint"]
    print("  末段/第一段边界和 toggle_role 一致  OK")


def test_stale_tail_still_resets_in_advise():
    d = Path(tempfile.mkdtemp(prefix="stale56-"))
    m = _manifest(5, engine="flow")
    src = m.seg(1)
    src["status"] = "collected"
    src["bridge_ready"] = True
    br = m.seg(2)
    br["role"] = "bridge"
    br["status"] = "collected"
    clip = d / "clips" / "s002.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"old-bridge")
    br["takes"] = [{"file": "clips/s002.mp4", "frame_ahash": _hx((1 << 30) - 1)}]
    br["bridge"] = {
        "from_seg": 1, "to_image": "p03",
        "tail_frame": "clips/_tails/s001.jpg",
        "tail_checks": {"face_ok": True, "in_frame": True},
        "gap_score": 5, "gap_why": "大",
        "tail_ahash": _hx(0),
    }
    r = pipeline.cascade_after_anchor(d, m, src)
    assert r["action"] == "reset", r
    assert r.get("reason") == "stale_tail", r
    assert br["status"] == "planned"
    print("  advise 下尾帧指纹检查仍 reset  OK")


def test_set_seg_mode_idempotent():
    tid, d, m = _make_task("T5604", engine="flow", n=5)
    src, br = _ready_bridge(m, 2, gap=2)
    br["status"] = "collected"
    clip = d / "clips" / "s002.mp4"
    clip.write_bytes(b"clip-once")
    br["takes"] = [{"file": "clips/s002.mp4"}]
    m.save(d)
    with patch.object(bench, "_kick_generate", return_value=None):
        r1 = bench.set_seg_mode(tid, 2, "single")
        n1 = len(list((d / "clips" / "_弃用").iterdir())) if (d / "clips" / "_弃用").is_dir() else 0
        r2 = bench.set_seg_mode(tid, 2, "single")
        n2 = len(list((d / "clips" / "_弃用").iterdir())) if (d / "clips" / "_弃用").is_dir() else 0
    assert r1["changed"] is True
    assert r2["changed"] is False
    assert n2 == n1
    print("  set_seg_mode 两次幂等  OK")


def test_set_seg_mode_first_frame_errors():
    tid, d, m = _make_task("T5605", engine="flow", n=5, mode="first_frame")
    with patch.object(bench, "_kick_generate", return_value=None):
        try:
            bench.set_seg_mode(tid, 2, "single")
            raise AssertionError("逐段首帧任务不该能改段级方式")
        except OranteError as e:
            msg = str(e)
            assert "逐段首帧" in msg, msg
    print("  逐段首帧任务 set_seg_mode 报错  OK")


def test_bench_density_in_config():
    config.set("ui.bench.density", "compact")
    config.set("ui.bench.zoom", 80)
    assert config.get("ui.bench.density") == "compact"
    assert int(config.get("ui.bench.zoom")) == 80
    assert config.DEFAULTS.get("ui.bench.density") == "comfy"
    assert config.DEFAULTS.get("ui.bench.zoom") == 100
    print("  检查台密度/缩放进 config  OK")


def test_snapshot_sort_first_dist():
    tid, d, m = _make_task("T5606", engine="flow", n=7)
    _ready_bridge(m, 2, gap=5)
    _ready_bridge(m, 4, gap=4)
    _ready_bridge(m, 6, gap=2)
    m.seg(2)["bridge"]["mismatch_first_dist"] = 8
    m.seg(4)["bridge"]["mismatch_first_dist"] = 40
    m.seg(6)["bridge"]["mismatch_first_dist"] = 12
    m.save(d)
    snap = bench.snapshot(tid)
    ss = [it["s"] for it in snap["items"]]
    assert ss[0] == 4, ss
    assert ss[1] == 6, ss
    print("  检查台排序 首帧距 > 跨度 > 段号  OK")
