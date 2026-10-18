# -*- coding: utf-8 -*-
"""规格 60：in_frame 误判近景不再卡死接片。先设 ORANTE_DATA。"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-60-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import bench, pipeline, review, tails  # noqa: E402

db.init()


def _load_fix():
    spec = importlib.util.spec_from_file_location(
        "fix_inframe", ROOT / "tools" / "fix_inframe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fix_inframe = _load_fix()


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


def _manifest(n=11, mode="anchor_bridge", engine="flow"):
    m = Manifest.new("T0060", "测", engine, "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, 8)]
    segs = []
    for i in range(1, n + 1):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    pipeline.rebuild_bridge_links(m.segments)
    return m


def _ready_bridge(m, s=2, gap=3, checks=None, engine_tag=None, ready=True):
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
    if ready:
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


def _make_task(tid, engine="flow", n=11, mode="anchor_bridge"):
    _ensure_account("A001", engine)
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-60-"))
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
            (tid, "规格60", engine, "A001", "long", "hello", str(task_dir / "images"),
             "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def _run_fix(*args):
    env = dict(os.environ)
    env["ORANTE_DATA"] = str(TEST_DATA)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fix_inframe.py"), *args],
        capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=str(ROOT), check=False)


def test_checks_ok_in_frame_no_longer_blocks():
    m = _manifest(5)
    src, br = _ready_bridge(
        m, 2, gap=2,
        checks={"face_ok": True, "in_frame": False, "sharp": True})
    assert pipeline._checks_ok(src, br) is True
    src2, br2 = _ready_bridge(
        m, 4, gap=2,
        checks={"face_ok": False, "in_frame": True, "sharp": True})
    assert pipeline._checks_ok(src2, br2) is False
    print("  _checks_ok：in_frame=False 不拦，face_ok=False 仍拦  OK")


def test_wait_reason_tells_the_truth():
    m = _manifest(5)
    src, br = _ready_bridge(
        m, 2, gap=2,
        checks={"face_ok": False, "in_frame": True, "sharp": True})
    assert src.get("bridge_ready") is False
    why = pipeline.wait_reason(m, br)
    assert why, why
    assert why.get("code") == "no_face", why
    assert "没拍到脸" in why["text"], why
    assert not re.search(r"等 s\d+ 收片", why["text"] or ""), why["text"]

    br["bridge"]["tail_frame"] = ""
    br["bridge"]["tail_checks"] = {"face_ok": True, "in_frame": True}
    pipeline.refresh_bridge_ready(m, src, br)
    why2 = pipeline.wait_reason(m, br)
    assert why2 and why2.get("code") == "tail_missing", why2
    assert "尾帧没抽出来" in why2["text"], why2
    assert not re.search(r"等 s\d+ 收片", why2["text"] or ""), why2["text"]

    src["status"] = "planned"
    src["bridge_ready"] = False
    why3 = pipeline.wait_reason(m, br)
    assert why3 and why3.get("code") == "prev_not_collected", why3
    assert why3["text"] == "等 s001 收片"
    print("  wait_reason：不撒谎 + code  OK")


def test_bench_level_in_frame_yellow():
    m = _manifest(5, engine="flow")
    src, br = _ready_bridge(
        m, 2, gap=2,
        checks={"face_ok": True, "in_frame": False, "sharp": True})
    assert bench.bench_level(br, m) == "yellow"
    assert bench.can_pass(br, m) is True
    print("  bench_level：in_frame=False → yellow，can_pass  OK")


def test_t0020_scene_enqueues():
    m = _manifest(11, engine="flow")
    src, br = _ready_bridge(
        m, 10, gap=3,
        checks={"face_ok": True, "in_frame": False, "sharp": True})
    assert src.get("status") == "collected"
    assert src.get("bridge_ready") is True, src
    assert pipeline.can_enqueue(m, br) is True
    why = pipeline.wait_reason(m, br)
    assert why is None, why
    wanted = {sg["s"] for sg in m.segments}
    ready = [sg["s"] for sg in pipeline.iter_ready(m, wanted, set())]
    assert 10 in ready, ready
    # 旧 flag 没刷新：in_frame 已放行但 bridge_ready 仍是 False
    src["bridge_ready"] = False
    assert pipeline.can_enqueue(m, br) is True
    assert src.get("bridge_ready") is True
    print("  T0020 场景：s010 in_frame=False 能进队  OK")


def test_review_payload_shows_wait():
    d = Path(tempfile.mkdtemp(prefix="rv60-"))
    m = _manifest(5)
    src, br = _ready_bridge(
        m, 2, gap=2,
        checks={"face_ok": False, "in_frame": True, "sharp": True})
    payload = review._seg_payload(d, m, br)
    assert "没拍到脸" in (payload.get("wait_why") or ""), payload
    assert payload.get("wait_code") == "no_face"
    print("  收片 payload 带真实 wait_why / wait_code  OK")


def test_pass_one_force_unblocks():
    tid, d, m = _make_task("T6001", engine="flow", n=5)
    src, br = _ready_bridge(
        m, 2, gap=2,
        checks={"face_ok": False, "in_frame": True, "sharp": True})
    m.save(d)
    assert pipeline.can_enqueue(m, br) is False
    with patch.object(bench, "_kick_generate", return_value=None):
        r = bench.pass_one(tid, 2, force=True)
    assert r["item"]["passed"] is True
    m2 = Manifest.load(d)
    assert m2.seg(2)["bridge"].get("passed") is True
    assert m2.seg(1).get("bridge_ready") is True
    assert pipeline.can_enqueue(m2, m2.seg(2)) is True
    print("  忽略检查 force 放行  OK")


def test_fix_inframe_dry_and_idempotent():
    tid, d, m = _make_task("T6002", engine="flow", n=5)
    src, br = _ready_bridge(
        m, 2, gap=2,
        checks={"face_ok": True, "in_frame": False, "sharp": True})
    # 已经带 note 的跳过
    src4, br4 = _ready_bridge(
        m, 4, gap=2,
        checks={"face_ok": True, "in_frame": False, "sharp": True,
                "in_frame_note": "already"})
    m.save(d)

    r1 = _run_fix("--dry-run")
    out1 = (r1.stdout or "") + (r1.stderr or "")
    assert r1.returncode == 0, out1
    assert "s002" in out1, out1
    assert "s004" not in out1, out1
    assert "dry-run" in out1 or "没写盘" in out1, out1
    m_mid = Manifest.load(d)
    assert m_mid.seg(2)["bridge"]["tail_checks"]["in_frame"] is False

    r2 = _run_fix("--yes")
    out2 = (r2.stdout or "") + (r2.stderr or "")
    assert r2.returncode == 0, out2
    assert "已改" in out2, out2
    m_yes = Manifest.load(d)
    chk = m_yes.seg(2)["bridge"]["tail_checks"]
    assert chk["in_frame"] is True
    assert chk.get("in_frame_note")
    assert m_yes.seg(4)["bridge"]["tail_checks"]["in_frame"] is False

    r3 = _run_fix("--dry-run")
    out3 = (r3.stdout or "") + (r3.stderr or "")
    assert r3.returncode == 0, out3
    assert "没有需要改的段" in out3, out3
    print("  fix_inframe：dry-run 列出、--yes 写、再跑 0 条  OK")


def test_tail_ask_distinguishes_closeup():
    assert "头部" in tails.TAIL_ASK
    assert "近景" in tails.TAIL_ASK
    assert "半个身子" not in tails.TAIL_ASK
    print("  TAIL_ASK 区分近景 / 出画  OK")


def test_ui_review_wait_buttons():
    rev = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    gen = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    zh = (ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    assert "review.wait.ignore" in rev
    assert "review.wait.retake" in rev
    assert "seg.wait_why" in rev
    assert "pendingBench" in rev
    assert "openBenchFor" in gen
    assert "pendingBench" in gen
    assert "忽略这条检查，直接拍" in zh
    assert "换一帧尾帧" in zh
    assert ".wait-why" in css
    print("  ⑤ 缺片卡片原因 + 两个按钮  OK")


def test_stamp_writes_code():
    m = _manifest(5)
    pipeline.stamp_waits(m)
    b = m.seg(2)
    assert b.get("wait_code") == "prev_not_collected", b
    assert b.get("wait_why") == "等 s001 收片"
    print("  stamp_waits 写 wait_code  OK")
