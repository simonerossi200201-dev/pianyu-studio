# -*- coding: utf-8 -*-
"""规格 61：无限下载循环——Flow forget_url、非接片作废上限、sha 降档、超时兜底。先设 ORANTE_DATA。"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-61-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db  # noqa: E402
from orante.engines.base import Engine  # noqa: E402
from orante.engines.flow_api import FlowEngine  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import generate as genmod, tails, verify  # noqa: E402

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


def _manifest(engine="flow", n=3, mode="first_frame"):
    m = Manifest.new("T0061", "测", engine, "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, 8)]
    segs = []
    for i in range(1, n + 1):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    return m


def _ahash_pair(dist: int):
    """vhash=0，配图哈希低 dist 位置 1 → 汉明距 = dist。"""
    return 0, (1 << dist) - 1 if dist else 0


def _run_after(m, d, sg, dest, dist, apply=True):
    vhash, ih = _ahash_pair(dist)
    logs = []

    def fake_img(path, ffmpeg=""):
        return ih

    with patch.object(verify, "ahash_video", return_value=vhash), \
         patch.object(verify, "ahash_image", side_effect=fake_img), \
         patch.object(verify, "find_ffmpeg", return_value="ffmpeg"):
        return verify.after_download(
            m, d, sg, dest, asset_url="http://x/v", gen_id=sg.get("gen_id") or "gid1",
            log=logs.append, apply=apply), logs


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J61"
        self.sleeps = 0

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        if self.sleeps > 80:
            raise RuntimeError("test loop too long")

    def sleep(self, sec: float) -> None:
        self.sleeps += 1
        if self.sleeps > 80:
            raise RuntimeError("test loop too long")


class FakeEngine:
    last = None

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.name = account.get("engine") or "fake"
        self.submitted = []
        self.can_gen_images = False
        self.forgotten = []
        FakeEngine.last = self

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        self.submitted.append({"s": seg["s"]})
        return "g%s" % seg["s"]

    def poll(self, ids):
        return {gid: {"state": "done", "url": "http://x/%s" % gid} for gid in ids}

    def download(self, gid, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 200)
        return dest

    def forget_url(self, gen_id, url=""):
        self.forgotten.append((gen_id, url))


def _ensure_account(aid="A001", engine="flow"):
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, "t-" + engine, engine, 9398, aid, "", "", "9:16", 5, 1, "{}", db.now()))
    else:
        db.run("UPDATE accounts SET engine=? WHERE id=?", (engine, aid))


def _make_task(tid, engine="flow", n=3, mode="first_frame"):
    _ensure_account("A001", engine)
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-61-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
    m = _manifest(engine, n=n, mode=mode)
    m.task_id = tid
    for im in m.images:
        _jpeg(task_dir / (im["file"] or ""), im["p"].encode())
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(task_dir), "arranged", engine, "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格61", engine, "A001", "long", "hello", str(task_dir / "images"),
             "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    m2 = Manifest.load(task_dir)
    return tid, task_dir, m2, m2.seg(1)


def _run_gen(job, only, engine_factory, **extra_patches):
    ad = extra_patches.get(
        "after_download", {"verdict": "ok", "frame_dist": 3, "voided": False})
    ad_kw = {"return_value": ad} if not callable(ad) else {"side_effect": ad}
    with patch("orante.services.generate.make_engine", side_effect=engine_factory), \
         patch("orante.services.verify.after_download", **ad_kw), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.tails.extract_head", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        return genmod.generate(job, only=only)


FLOW_ACC = {
    "id": "A001", "name": "t-flow", "engine": "flow", "port": 9222,
    "project_url": (
        "https://flow.google.com/project/"
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/collection/"
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    ),
}


def _flow_rec(gid, *urls, st=3):
    rec = [gid, None, None, None, None, [None] * 8 + [[st]]]
    rec.extend(urls)
    return rec


# ---------------------------------------------------------------- A. forget_url
def test_flow_forget_url_skips_rejected():
    logs = []
    eng = FlowEngine(FLOW_ACC, logs.append)
    gid = "gen-abc-61"
    url_a = "https://flow-content.google/video/aaa"
    url_b = "https://flow-content.google/video/bbb"
    rec = _flow_rec(gid, url_a, url_b)
    payload = [None, None, [rec]]

    with patch.object(eng, "_rpc", return_value=payload):
        r1 = eng.poll([gid])
        assert r1[gid]["state"] == "done", r1
        assert r1[gid]["url"] == url_a, r1
        eng.forget_url(gid, url_a)
        assert url_a in eng._rejected[gid]
        r2 = eng.poll([gid])
        assert r2[gid]["state"] == "done", r2
        assert r2[gid]["url"] == url_b, r2
        eng.forget_url(gid, url_b)
        r3 = eng.poll([gid])
        assert r3[gid]["state"] == "pending", r3
        assert r3[gid].get("raw") == "rejected-url"
        assert not r3[gid].get("url")
    print("  FlowEngine.forget_url 作废后 poll 不再返回该地址  OK")


def test_engine_base_has_forget_url_and_generate_calls_directly():
    assert "forget_url" in Engine.__dict__, Engine.__dict__.keys()
    assert callable(Engine.forget_url)
    src = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    assert 'getattr(eng, "forget_url"' not in src
    assert "eng.forget_url(" in src
    print("  Engine 基类有 forget_url；generate 不再 getattr 探测  OK")


# ---------------------------------------------------------------- B. 非接片 wrongclip_count
def test_non_bridge_wrong_clip_count_tiers():
    verify.clear_void_streak()
    d = Path(tempfile.mkdtemp(prefix="s61-wc-"))
    m = _manifest("flow", n=1, mode="first_frame")
    sg = m.seg(1)
    sg["role"] = "anchor"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    _jpeg(d / "images" / "p01.jpg", b"p01")
    (d / "clips").mkdir(parents=True, exist_ok=True)

    dest1 = d / "clips" / "s001a.mp4"
    dest1.write_bytes(b"fake-a-" + os.urandom(16))
    r1, logs1 = _run_after(m, d, sg, dest1, 185, apply=True)
    assert r1["verdict"] == "wrong_clip"
    assert int(sg.get("wrongclip_count") or 0) == 1
    assert sg["status"] == "submitted", sg["status"]
    assert sg.get("gen_id") == "gid1"
    assert any("重新取地址" in x for x in logs1), logs1

    dest2 = d / "clips" / "s001b.mp4"
    dest2.write_bytes(b"fake-b-" + os.urandom(16))
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    r2, _ = _run_after(m, d, sg, dest2, 185, apply=True)
    assert r2["verdict"] == "wrong_clip"
    assert int(sg.get("wrongclip_count") or 0) == 2
    assert sg["status"] == "planned", sg["status"]
    assert not sg.get("gen_id")

    dest3 = d / "clips" / "s001c.mp4"
    dest3.write_bytes(b"fake-c-" + os.urandom(16))
    sg["status"] = "submitted"
    sg["gen_id"] = "gid2"
    r3, _ = _run_after(m, d, sg, dest3, 185, apply=True)
    assert int(sg.get("wrongclip_count") or 0) == 3
    assert sg["status"] == "planned", sg["status"]
    assert not sg.get("gen_id")

    dest4 = d / "clips" / "s001d.mp4"
    dest4.write_bytes(b"fake-d-" + os.urandom(16))
    sg["status"] = "submitted"
    sg["gen_id"] = "gid3"
    r4, _ = _run_after(m, d, sg, dest4, 185, apply=True)
    assert int(sg.get("wrongclip_count") or 0) == 4
    assert sg["status"] == "failed", sg["status"]
    assert "首帧一直对不上配图" in (sg.get("error") or "")
    assert "距 185" in (sg.get("error") or "")
    print("  非接片 wrong_clip 1→submitted / 2→planned / 4→failed  OK")


# ---------------------------------------------------------------- C. sha 命中立刻降档
def test_same_sha_skips_submitted():
    verify.clear_void_streak()
    d = Path(tempfile.mkdtemp(prefix="s61-sha-"))
    m = _manifest("flow", n=1, mode="first_frame")
    sg = m.seg(1)
    sg["role"] = "anchor"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    _jpeg(d / "images" / "p01.jpg", b"p01")
    (d / "clips").mkdir(parents=True, exist_ok=True)

    data = b"same-clip-bytes-" + os.urandom(24)
    digest = hashlib.sha256(data).hexdigest()
    sg["voided_sha"] = [digest]

    dest = d / "clips" / "s001.mp4"
    dest.write_bytes(data)
    r, logs = _run_after(m, d, sg, dest, 185, apply=True)
    assert r["verdict"] == "wrong_clip"
    assert r.get("same_file") is True
    assert sg["status"] != "submitted", sg["status"]
    assert sg["status"] == "planned", sg["status"]
    assert not sg.get("gen_id")
    assert any("又下到同一个文件" in x for x in logs), logs
    assert any(digest[:8] in x for x in logs), logs
    print("  sha 命中 voided_sha → 不走 submitted，直接降档  OK")


def test_same_file_second_download_logs_and_fails():
    """第一次记下 sha，第二次同一文件立刻跳过重新取地址。"""
    verify.clear_void_streak()
    d = Path(tempfile.mkdtemp(prefix="s61-sha2-"))
    m = _manifest("flow", n=1, mode="first_frame")
    sg = m.seg(1)
    sg["role"] = "anchor"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    _jpeg(d / "images" / "p01.jpg", b"p01")
    (d / "clips").mkdir(parents=True, exist_ok=True)
    data = b"loop-file-" + os.urandom(24)

    dest1 = d / "clips" / "s001-1.mp4"
    dest1.write_bytes(data)
    r1, _ = _run_after(m, d, sg, dest1, 185, apply=True)
    assert sg["status"] == "submitted"
    assert sg.get("voided_sha")

    dest2 = d / "clips" / "s001-2.mp4"
    dest2.write_bytes(data)
    r2, logs2 = _run_after(m, d, sg, dest2, 185, apply=True)
    assert r2.get("same_file") is True
    assert sg["status"] != "submitted", sg["status"]
    assert any("又下到同一个文件" in x for x in logs2), logs2
    print("  同一个文件第二次 → 降档并点名  OK")


# ---------------------------------------------------------------- D. 6 次底 + 超时
def test_void_six_times_unconditional_fail():
    tid, d, m, sg = _make_task("T6106", "flow", n=1, mode="first_frame")
    sg["status"] = "submitted"
    sg["gen_id"] = "gid-loop"
    m.save(d)
    job = FakeJob(tid, "A001")

    def always_void(*_a, **_k):
        return {"voided": True, "verdict": "wrong_clip", "frame_dist": 185, "take": {}}

    _run_gen(job, [1], lambda acc, log: FakeEngine(acc, log), after_download=always_void)
    m2 = Manifest.load(d)
    sg2 = m2.seg(1)
    assert sg2["status"] == "failed", sg2
    assert "6 次" in (sg2.get("error") or ""), sg2.get("error")
    joined = "\n".join(job.lines)
    assert "本轮作废已 6 次" in joined, job.lines
    eng = FakeEngine.last
    assert eng is not None
    assert len(eng.forgotten) >= 6
    print("  作废 6 次 → 无条件 failed  OK")


def test_void_path_respects_gen_timeout():
    tid, d, m, sg = _make_task("T6107", "flow", n=1, mode="first_frame")
    sg["status"] = "submitted"
    sg["gen_id"] = "gid-old"
    m.save(d)
    clock = {"t": 1_000_000.0}

    class TJob(FakeJob):
        def sleep(self, sec: float) -> None:
            self.sleeps += 1
            if sec >= genmod.POLL_EVERY:
                clock["t"] += genmod.GEN_TIMEOUT + 5
            if self.sleeps > 20:
                raise RuntimeError("test loop too long")

    class PendingEngine(FakeEngine):
        def poll(self, ids):
            return {gid: {"state": "done", "url": "http://x/%s" % gid} for gid in ids}

        def download(self, gid, dest: Path):
            raise AssertionError("超时后不该再下载")

    job = TJob(tid, "A001")
    with patch("orante.services.generate.time.time", side_effect=lambda: clock["t"]):
        _run_gen(job, [1], lambda acc, log: PendingEngine(acc, log),
                 after_download=lambda *_a, **_k: (_ for _ in ()).throw(
                     AssertionError("超时后不该再校验")))
    m2 = Manifest.load(d)
    sg2 = m2.seg(1)
    assert sg2["status"] == "failed", sg2
    assert "等了太久" in (sg2.get("error") or ""), sg2.get("error")
    print("  作废路径也会被 GEN_TIMEOUT 判掉  OK")


# ---------------------------------------------------------------- E. 日志合并
def test_void_logs_coalesce():
    verify.clear_void_streak()
    d = Path(tempfile.mkdtemp(prefix="s61-log-"))
    m = _manifest("flow", n=1, mode="first_frame")
    sg = m.seg(1)
    sg["role"] = "anchor"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    _jpeg(d / "images" / "p01.jpg", b"p01")
    (d / "clips").mkdir(parents=True, exist_ok=True)

    all_logs = []
    for i in range(5):
        dest = d / "clips" / f"s001-{i}.mp4"
        dest.write_bytes(b"uniq-%d-" % i + os.urandom(16))
        sg["status"] = "submitted"
        sg["gen_id"] = "gid1"
        _, logs = _run_after(m, d, sg, dest, 185, apply=True)
        all_logs.extend(logs)

    full = [x for x in all_logs if "已作废，重新取地址" in x]
    assert len(full) == 1, all_logs
    counted = [x for x in all_logs if "×" in x and "次" in x]
    assert counted, all_logs
    assert any("× 5 次" in x for x in counted), all_logs
    assert len(full) + len(counted) == 5, all_logs
    print("  连续 5 次作废：1 条完整行 + 计数行  OK")


def test_wrongclip_cleared_on_collect():
    tid, d, m, sg = _make_task("T6108", "flow", n=1, mode="first_frame")
    sg["status"] = "submitted"
    sg["gen_id"] = "gid-ok"
    sg["wrongclip_count"] = 3
    sg["voided_sha"] = ["abcd"]
    m.save(d)
    job = FakeJob(tid, "A001")
    _run_gen(job, [1], lambda acc, log: FakeEngine(acc, log))
    m2 = Manifest.load(d)
    sg2 = m2.seg(1)
    assert sg2["status"] == "collected", sg2
    assert int(sg2.get("wrongclip_count") or 0) == 0
    assert not sg2.get("voided_sha")
    print("  收片成功清 wrongclip_count  OK")
