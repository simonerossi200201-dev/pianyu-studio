# -*- coding: utf-8 -*-
"""规格 52：Grok 接片改单图起于上一段尾帧。先设 ORANTE_DATA。"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-52-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.engines.grok import GrokEngine  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import bench, generate as genmod, pipeline, tails, verify  # noqa: E402
from orante.services import prompts  # noqa: E402

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


def _manifest(engine="grok", n=5, mode="anchor_bridge"):
    m = Manifest.new("T0052", "测", engine, "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, 8)]
    segs = []
    for i in range(1, n + 1):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    pipeline.rebuild_bridge_links(m.segments)
    return m


def _ready_bridge(m, d: Path, s=2):
    src = m.seg(s - 1)
    src["status"] = "collected"
    src["bridge_ready"] = True
    src["takes"] = [{"file": f"clips/s{s - 1:03d}.mp4"}]
    sg = m.seg(s)
    sg["role"] = "bridge"
    sg["status"] = "planned"
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    br.update({
        "from_seg": s - 1,
        "to_image": m.seg(s + 1)["image"] if m.seg(s + 1) else "p03",
        "tail_frame": f"clips/_tails/s{s - 1:03d}.jpg",
        "tail_pose": {"text": "右手举起拿着话筒"},
        "tail_checks": {"face_ok": True, "in_frame": True, "sharp": True},
        "gap_score": 1, "gap_why": "小",
    })
    sg["bridge"] = br
    _jpeg(d / "clips" / "_tails" / f"s{s - 1:03d}.jpg", b"tail")
    for im in m.images:
        _jpeg(d / (im["file"] or ""), im["p"].encode())
    (d / "clips").mkdir(parents=True, exist_ok=True)
    return sg


# ---------------------------------------------------------------- A. stage / prompt
def test_grok_stage_single_and_prompt():
    d = Path(tempfile.mkdtemp(prefix="s52-stage-"))
    m = _manifest("grok")
    sg = _ready_bridge(m, d, 2)
    p1, p2, tmp = pipeline.stage_bridge_start(d, m, sg)
    assert p2 is None
    assert p1.name == "01_首帧_start.jpg"
    assert p1.is_file()
    names = sorted(x.name for x in tmp.iterdir() if x.is_file())
    assert names == ["01_首帧_start.jpg"], names
    sg["bridge_engine"] = "grok-single"
    body = "必须准确地说出以下英文：It comes from the Spirit of truth."
    out = pipeline.wrap_bridge_prompt(m, sg, body)
    assert "自然延续" in out
    assert "右手举起" in out
    assert "第二张" not in out
    assert "结束画面" not in out
    assert "02_尾帧_end.jpg" not in out
    assert "It comes from the Spirit of truth" in out
    assert prompts.get_block("bridge_single").strip()
    print("  Grok stage 单图 + 提示词自然延续  OK")


def test_flow_stage_dual_and_old_template():
    d = Path(tempfile.mkdtemp(prefix="s52-flow-"))
    m = _manifest("flow")
    sg = _ready_bridge(m, d, 2)
    p1, p2, tmp = pipeline.stage_bridge_images(d, m, sg)
    assert p1.name == "01_首帧_start.jpg"
    assert p2.name == "02_尾帧_end.jpg"
    names = sorted(x.name for x in tmp.iterdir() if x.is_file())
    assert names == ["01_首帧_start.jpg", "02_尾帧_end.jpg"], names
    sg["bridge_engine"] = "flow"
    body = "必须准确地说出以下英文：hello."
    out = pipeline.wrap_bridge_prompt(m, sg, body)
    assert "01_首帧_start.jpg" in out
    assert "02_尾帧_end.jpg" in out
    assert "结束画面" in out
    assert "自然延续" not in out
    print("  Flow stage 双图 + 旧模板  OK")


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "Jtest"

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        pass

    def sleep(self, sec: float) -> None:
        pass


class FakeEngine:
    last = None

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.name = account.get("engine") or "fake"
        self.submitted = []
        self.can_gen_images = False
        FakeEngine.last = self

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        staged = []
        parent = Path(image).parent
        if parent.is_dir():
            staged = sorted(x.name for x in parent.iterdir() if x.is_file())
        self.submitted.append({
            "s": seg["s"], "image": str(image),
            "last_image": None if last_image is None else str(last_image),
            "prompt": prompt, "staged": staged,
            "bridge_engine": seg.get("bridge_engine"),
        })
        return "g%s" % seg["s"]

    def poll(self, ids):
        return {gid: {"state": "done", "url": "http://x/%s" % gid} for gid in ids}

    def download(self, gid, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 200)
        return dest


def _ensure_account(aid="A001", engine="grok"):
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, "t-" + engine, engine, 9398, aid, "", "", "9:16", 5, 1, "{}", db.now()))
    else:
        db.run("UPDATE accounts SET engine=? WHERE id=?", (engine, aid))


def _make_task(tid, engine="grok", n=5):
    _ensure_account("A001", engine)
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-52-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
    m = _manifest(engine, n=n)
    m.task_id = tid
    sg = _ready_bridge(m, task_dir, 2)
    m.seg(1)["status"] = "collected"
    m.seg(1)["bridge_ready"] = True
    m.seg(3)["status"] = "planned"
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(task_dir), "arranged", engine, "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格52", engine, "A001", "long", "hello", str(task_dir / "images"),
             "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir), sg


def test_generate_grok_submit_single():
    tid, d, m, sg = _make_task("T5201", "grok")
    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    with patch("orante.services.generate.make_engine",
               side_effect=lambda acc, log: FakeEngine(acc, log)), \
         patch("orante.services.verify.after_download",
               return_value={"verdict": "ok", "frame_dist": 3, "voided": False}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.tails.extract_head", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.generate(job, only=[2])
    eng = FakeEngine.last
    assert eng is not None
    got = [x for x in eng.submitted if x["s"] == 2]
    assert got, eng.submitted
    rec = got[0]
    assert rec["last_image"] is None, rec
    assert rec["staged"] == ["01_首帧_start.jpg"], rec["staged"]
    assert "自然延续" in rec["prompt"]
    assert "第二张" not in rec["prompt"]
    assert "结束画面" not in rec["prompt"]
    assert rec["bridge_engine"] == "grok-single"
    joined = "\n".join(job.lines)
    assert "接片(Grok 单图)" in joined
    assert "首帧=s001 尾帧" in joined
    print("  generate Grok submit last_image=None  OK")


def test_generate_flow_submit_dual():
    tid, d, m, sg = _make_task("T5202", "flow")
    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    with patch("orante.services.generate.make_engine",
               side_effect=lambda acc, log: FakeEngine(acc, log)), \
         patch("orante.services.verify.after_download",
               return_value={"verdict": "ok", "frame_dist": 8, "voided": False}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.tails.extract_head", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.generate(job, only=[2])
    eng = FakeEngine.last
    assert eng is not None
    got = [x for x in eng.submitted if x["s"] == 2]
    assert got, eng.submitted
    rec = got[0]
    assert rec["last_image"] is not None, rec
    assert "02_尾帧_end.jpg" in rec["staged"]
    assert "结束画面" in rec["prompt"]
    assert rec["bridge_engine"] == "flow"
    print("  generate Flow submit 双图  OK")


# ---------------------------------------------------------------- B. after_download 分档
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
            m, d, sg, dest, asset_url="http://x/v", gen_id="gid1",
            log=logs.append, apply=apply), logs


def test_after_download_grok_single_ok_soft_wrong():
    d = Path(tempfile.mkdtemp(prefix="s52-ad-"))
    m = _manifest("grok")
    sg = _ready_bridge(m, d, 2)
    sg["bridge_engine"] = "grok-single"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    dest = d / "clips" / "s002.mp4"
    dest.write_bytes(b"fake-mp4-" + os.urandom(12))

    r, _ = _run_after(m, d, sg, dest, 8, apply=False)
    assert r["verdict"] == "ok", r
    assert r["frame_dist"] == 8

    r, _ = _run_after(m, d, sg, dest, 14, apply=False)
    assert r["verdict"] == "soft", r
    assert r["frame_dist"] == 14

    r, logs = _run_after(m, d, sg, dest, 20, apply=True)
    assert r["verdict"] == "wrong_clip", r
    assert r.get("voided") is True
    assert sg["status"] == "planned", sg["status"]
    assert not sg.get("gen_id")
    assert int(sg.get("fail_count") or 0) == 1
    assert int(sg.get("mismatch_count") or 0) == 1
    junk = d / "clips" / "_弃用"
    assert junk.is_dir() and any(junk.iterdir()), list(junk.iterdir()) if junk.is_dir() else []
    assert any("重新提交" in x for x in logs), logs
    assert not any("重新取地址" in x for x in logs), logs

    dest2 = d / "clips" / "s002b.mp4"
    dest2.write_bytes(b"fake-mp4-" + os.urandom(12))
    sg["status"] = "submitted"
    sg["gen_id"] = "gid2"
    r2, logs2 = _run_after(m, d, sg, dest2, 20, apply=True)
    assert r2["verdict"] == "wrong_clip"
    assert int(sg.get("fail_count") or 0) == 2
    assert int(sg.get("mismatch_count") or 0) == 2
    assert sg["status"] == "failed", sg["status"]
    assert "两次首帧都对不上" in (sg.get("error") or "")
    assert not sg.get("gen_id")
    assert not pipeline.can_enqueue(m, sg)
    print("  grok-single 8 ok / 14 soft / 20 planned / 二次 failed  OK")


def test_after_download_flow_dist_25_ok():
    d = Path(tempfile.mkdtemp(prefix="s52-flow25-"))
    m = _manifest("flow")
    sg = _ready_bridge(m, d, 2)
    sg["bridge_engine"] = "flow"
    sg["status"] = "submitted"
    dest = d / "clips" / "s002.mp4"
    dest.write_bytes(b"fake-mp4-" + os.urandom(12))
    r, _ = _run_after(m, d, sg, dest, 25, apply=False)
    assert r["verdict"] == "ok", r
    assert r["frame_dist"] == 25
    print("  Flow 双图距 25 → ok（30 红线）  OK")


def test_thresholds_for_is_only_source():
    src_a = inspect.getsource(verify.after_download)
    src_b = inspect.getsource(pipeline.score_collected_bridge)
    assert "_thresholds_for" in src_a
    assert "_thresholds_for" in src_b
    for src in (src_a, src_b):
        assert "DIST_OK" not in src
        assert "DIST_SOFT" not in src
        assert "BRIDGE_MATCH_DIST" not in src
    ok, soft = verify._thresholds_for({"role": "bridge", "bridge_engine": "grok-single"})
    assert (ok, soft) == (verify.DIST_OK, verify.DIST_SOFT)
    ok2, soft2 = verify._thresholds_for({"role": "bridge", "bridge_engine": "flow"})
    assert soft2 == pipeline.BRIDGE_MATCH_DIST
    print("  _thresholds_for 是唯一阈值来源  OK")


def test_non_bridge_wrong_clip_still_refetch():
    d = Path(tempfile.mkdtemp(prefix="s52-nb-"))
    m = _manifest("grok", mode="first_frame")
    sg = m.seg(1)
    sg["role"] = "anchor"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    _jpeg(d / "images" / "p01.jpg", b"p01")
    dest = d / "clips" / "s001.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"fake-mp4-" + os.urandom(12))
    r, logs = _run_after(m, d, sg, dest, 40, apply=True)
    assert r["verdict"] == "wrong_clip"
    assert sg["status"] == "submitted"
    assert sg.get("gen_id") == "gid1"
    assert (d / "clips" / "_错件").is_dir()
    assert any("重新取地址" in x for x in logs), logs
    print("  非接片 wrong_clip 仍重新取地址  OK")


# ---------------------------------------------------------------- 网络层 _rejected
class FakePage:
    def __init__(self, url="", videos=None, perf=None):
        self.url = url
        self.videos = list(videos or [])
        self.perf = list(perf or [])
        self.gotos = []
        self.closed = False
        self._on = []

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = url

    def evaluate(self, js):
        s = js if isinstance(js, str) else str(js)
        if "getEntriesByType" in s:
            return list(self.perf)
        if "readyState" in s and "querySelectorAll('video')" in s:
            return [{"src": u, "currentSrc": u, "sources": [],
                     "readyState": 4 if str(u).startswith("blob:") else 0,
                     "duration": 15 if str(u).startswith("blob:") else 0,
                     "visible": True}
                    for u in self.videos]
        if "looksLikePost" in s or ("startsWith('blob:')" in s and "uniq" in s):
            strong = [u for u in self.videos
                      if u and not str(u).startswith("blob:")]
            return {"strong": strong, "weak": []}
        if "querySelectorAll('video')" in s or "currentSrc" in s:
            strong = [u for u in self.videos
                      if u and not str(u).startswith("blob:")]
            return {"strong": strong, "weak": []}
        if "generating" in s.lower() or "正在生成" in s:
            return False
        if "失败" in s or "failed" in s.lower():
            return ""
        return ""

    def locator(self, sel):
        class L:
            def count(self_):
                return 0
        return L()

    def on(self, event, handler):
        self._on.append((event, handler))

    def wait_for_timeout(self, ms):
        return None

    def bring_to_front(self):
        return None

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def reload(self, **kwargs):
        return None


def test_netvid_rejected_returns_pending():
    POST = "b8901485-aaaa-bbbb-cccc-111111111111"
    CONV = "7efea7c6-dddd-eeee-ffff-222222222222"
    NET = "https://assets.grok.com/users/u/generated/abc"
    BLOB = "blob:https://grok.com/abc-def"
    OWN = f"https://grok.com/imagine/post/{POST}?conversation={CONV}"
    page = FakePage(url=OWN, videos=[BLOB], perf=[NET])
    logs = []
    eng = GrokEngine(
        {"id": "A000", "name": "fake", "engine": "grok",
         "port": 9999, "max_concurrent": 4, "profile": ""},
        logs.append)
    eng._pages[POST] = page
    eng._conv[POST] = CONV
    eng._rows[POST] = {"第几段": 10, "会话Id": CONV, "postId": POST}
    eng._active.add(POST)
    eng._submitted_at[POST] = time.time() - 60
    eng._netvid[POST] = [NET]
    eng._rejected[POST] = {NET}
    r = eng._poll_one(POST)
    assert r["state"] == "pending", r
    joined = "\n".join(logs)
    assert "出片" not in joined

    # 下载按钮那路 url 为空：forget_url 也要把网络层见过的地址作废
    logs2 = []
    eng2 = GrokEngine(
        {"id": "A000", "name": "fake", "engine": "grok",
         "port": 9999, "max_concurrent": 4, "profile": ""},
        logs2.append)
    eng2._pages[POST] = page
    eng2._conv[POST] = CONV
    eng2._rows[POST] = {"第几段": 10, "会话Id": CONV, "postId": POST}
    eng2._active.add(POST)
    eng2._submitted_at[POST] = time.time() - 60
    eng2._netvid[POST] = [NET]
    eng2.forget_url(POST, "")
    assert NET in (eng2._rejected.get(POST) or set())
    r2 = eng2._poll_one(POST)
    assert r2["state"] == "pending", r2
    print("  网络层 _rejected 地址 → pending  OK")


# ---------------------------------------------------------------- C. 尾帧门槛 / 检查台
def test_pick_sharpest_min_and_warn():
    cands = [
        {"k": 3, "path": "a", "sharp": 40.0},
        {"k": 4, "path": "b", "sharp": 55.0},
        {"k": 5, "path": "c", "sharp": 90.0},
        {"k": 6, "path": "d", "sharp": 70.0},
    ]
    picked = tails.pick_sharpest(cands)
    assert picked["k"] == 5
    assert not picked.get("below_min")
    low = [
        {"k": 3, "path": "a", "sharp": 20.0},
        {"k": 4, "path": "b", "sharp": 35.0},
        {"k": 5, "path": "c", "sharp": 50.0},
    ]
    picked2 = tails.pick_sharpest(low)
    assert picked2["k"] == 5
    assert picked2.get("below_min") is True
    print("  TAIL_MIN_SHARP 换候选 / 全不及格 below_min  OK")


def test_bench_item_engine_and_dist_red():
    m = _manifest("grok")
    d = Path(tempfile.mkdtemp(prefix="s52-bench-"))
    sg = _ready_bridge(m, d, 2)
    sg["bridge_engine"] = "grok-single"
    sg["bridge"]["mismatch_first_dist"] = 20
    sg["bridge"]["first_frame"] = "clips/_heads/s002.jpg"
    sg["fail_count"] = 2
    sg["mismatch_count"] = 2
    _jpeg(d / "clips" / "_heads" / "s002.jpg", b"head")
    it = bench._item("T0052", d, m, sg)
    assert it["engine_label"] == "Grok 单图"
    assert it["first_dist"] == 20
    assert it["dist_red"] is True
    assert it["fail_twice"] is True
    assert it["level"] == "red"
    sg2 = _ready_bridge(m, d, 4)
    sg2["bridge_engine"] = "flow"
    it2 = bench._item("T0052", d, m, sg2)
    assert it2["engine_label"] == "Flow 双图"
    print("  检查台 Grok 单图 / Flow 双图 + 距>16 红  OK")


def test_ui_copy():
    zh = (ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8")
    assert "主片锁身份，接片起于上一段尾帧" in zh
    assert "Flow 接片首尾双图，Grok 接片单图延长" in zh
    js = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    assert "gen.mode.anchor_tip" in js
    assert "it.engine_label" in js
    assert "it.first_dist" in js
    print("  界面文字  OK")


def test_cancel_mid_submit_does_not_mark_queue_failed():
    """09-28 J00762：点「停止」后浏览器关掉，排队的段一个个报 context closed，
    被记成「提交失败」。现在：停止时这一段原样留着（待做），任务按取消结束。"""
    import pytest
    from orante.errors import OranteError
    from orante.scheduler import JobCancelled
    tid, d, m, sg = _make_task("T5203", "flow")
    job = FakeJob(tid, "A001")
    job.cancelled = threading.Event()

    class ClosingEngine(FakeEngine):
        def submit(self, seg, image, prompt, duration, last_image=None, **kw):
            job.cancelled.set()
            raise OranteError("真人操作卡在「点生成」：BrowserContext.new_page: Target page, "
                              "context or browser has been closed")

    before = Manifest.load(d).seg(2)["status"]
    with patch("orante.services.generate.make_engine",
               side_effect=lambda acc, log: ClosingEngine(acc, log)), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.tails.extract_head", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        with pytest.raises(JobCancelled):
            genmod.generate(job, only=[2])
    m2 = Manifest.load(d)
    assert m2.seg(2)["status"] == before == "planned"
    assert not m2.seg(2).get("error")
    assert not any("提交失败" in ln for ln in job.lines), job.lines


def _mk_av(path: Path, audio=True):
    import subprocess
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
           "testsrc=size=320x180:rate=24:duration=1"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    return path


def test_strip_audio_removes_track_keeps_video():
    """不说话的任务：Flow 的「Return silent videos」挡不住声音，下载后统一去音轨。"""
    import pytest
    if not verify.find_ffmpeg():
        pytest.skip("没有 ffmpeg")
    d = Path(tempfile.mkdtemp(prefix="s52-audio-"))
    v = _mk_av(d / "a.mp4", audio=True)
    h0 = verify.ahash_video(v)
    assert verify.has_audio(v) is True
    assert verify.strip_audio(v) is True
    assert verify.has_audio(v) is False
    verify.clear_mem_cache()
    assert verify.ahash_video(v) == h0          # 画面没动
    assert verify.strip_audio(v) is False       # 已经无声：不动
    v2 = _mk_av(d / "b.mp4", audio=False)
    b0 = v2.read_bytes()
    assert verify.strip_audio(v2) is False and v2.read_bytes() == b0
    assert not list(d.glob("*.tmp.mp4"))


def test_generate_strips_audio_before_verify_for_silent_tasks():
    src = inspect.getsource(genmod.generate)
    i = src.index("verifymod.strip_audio(dest)")
    j = src.index("verifymod.after_download(")
    assert i < j and "if strip_silent:" in src[i - 400:i]


def test_verify_task_respects_human_ok():
    """人工认可的片（human_ok）复查时不再被判错件。"""
    d = Path(tempfile.mkdtemp(prefix="s52-hok-"))
    m = _manifest("flow", n=2, mode="first_frame")
    for im in m.images:
        _jpeg(d / im["file"], im["p"].encode())
    sg = m.seg(1)
    sg["status"] = "collected"
    clip = d / "clips" / "s001.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\x00" * 300)
    st = clip.stat()
    sg["takes"] = [{"file": "clips/s001.mp4", "bytes": st.st_size, "mtime": int(st.st_mtime),
                    "frame_ahash": verify.ahash_hex(0), "verdict": "ok", "human_ok": True}]
    with patch.object(verify, "ahash_image", return_value=(1 << 120) - 1), \
         patch.object(verify, "find_ffmpeg", return_value="ffmpeg"):
        res = verify.verify_task(m, d, only=[1])
    assert res[0]["verdict"] == "ok"
    assert sg["takes"][-1]["verdict"] == "ok"
    sg["takes"][-1].pop("human_ok")
    with patch.object(verify, "ahash_image", return_value=(1 << 120) - 1), \
         patch.object(verify, "find_ffmpeg", return_value="ffmpeg"):
        res = verify.verify_task(m, d, only=[1])
    assert res[0]["verdict"] == "wrong_clip"
