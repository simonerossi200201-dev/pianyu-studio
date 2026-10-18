# -*- coding: utf-8 -*-
"""规格 53：接片失败计数记错账。先设 ORANTE_DATA。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-53-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.engines.grok import GrokEngine, NET_MIN_BYTES  # noqa: E402
from orante.errors import NeedRedownload, OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import bench, generate as genmod, pipeline, tails, verify  # noqa: E402

from orante import paths as _paths  # noqa: E402
_paths.add_legacy_to_sys_path()
import grok_pipe  # noqa: E402

db.init()

POST = "b8901485-aaaa-bbbb-cccc-111111111111"
CONV = "7efea7c6-dddd-eeee-ffff-222222222222"
OWN = f"https://grok.com/imagine/post/{POST}?conversation={CONV}"
NET = "https://assets.grok.com/users/u/generated/abc"
BLOB = "blob:https://grok.com/abc-def"


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
    m = Manifest.new("T0053", "测", engine, "A001", "long", "hello " * 20)
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


def _ahash_pair(dist: int):
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


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "Jtest"
        self.sleeps = 0

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        if self.sleeps > 40:
            raise RuntimeError("test loop too long")

    def sleep(self, sec: float) -> None:
        self.sleeps += 1
        if self.sleeps > 40:
            raise RuntimeError("test loop too long")


class FakeEngine:
    last = None

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.name = account.get("engine") or "fake"
        self.submitted = []
        self.downloads = 0
        self.can_gen_images = False
        FakeEngine.last = self

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        self.submitted.append({
            "s": seg["s"], "image": str(image),
            "last_image": None if last_image is None else str(last_image),
            "prompt": prompt, "bridge_engine": seg.get("bridge_engine"),
        })
        return "g%s" % seg["s"]

    def poll(self, ids):
        return {gid: {"state": "done", "url": "http://x/%s" % gid} for gid in ids}

    def download(self, gid, dest: Path):
        self.downloads += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 200)
        return dest


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


def _eng(logs=None):
    logs = logs if logs is not None else []
    eng = GrokEngine(
        {"id": "A000", "name": "fake", "engine": "grok",
         "port": 9999, "max_concurrent": 4, "profile": ""},
        logs.append)
    return eng, logs


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
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-53-"))
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
            (tid, "规格53", engine, "A001", "long", "hello", str(task_dir / "images"),
             "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    m2 = Manifest.load(task_dir)
    return tid, task_dir, m2, m2.seg(int(sg["s"]))


def _run_gen(job, only, engine_factory, **extra_patches):
    kw = dict(
        make=("orante.services.generate.make_engine", engine_factory),
        ad=("orante.services.verify.after_download",
            extra_patches.get("after_download",
                              {"verdict": "ok", "frame_dist": 3, "voided": False})),
        tails=("orante.services.tails.after_collect", None),
        head=("orante.services.tails.extract_head", None),
        desc=("orante.services.describe.missing_subject_ps", []),
    )
    ctxs = [
        patch(kw["make"][0], side_effect=kw["make"][1]),
        patch(kw["ad"][0], return_value=kw["ad"][1]),
        patch(kw["tails"][0], return_value=kw["tails"][1]),
        patch(kw["head"][0], return_value=kw["head"][1]),
        patch(kw["desc"][0], return_value=kw["desc"][1]),
    ]
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        return genmod.generate(job, only=only)


# ---------------------------------------------------------------- A. mismatch_count 闸
def test_wrong_clip_twice_blocks_wait_reason():
    d = Path(tempfile.mkdtemp(prefix="s53-mis-"))
    m = _manifest("grok")
    sg = _ready_bridge(m, d, 2)
    sg["bridge_engine"] = "grok-single"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    dest = d / "clips" / "s002.mp4"
    dest.write_bytes(b"fake-mp4-" + os.urandom(12))
    _run_after(m, d, sg, dest, 20, apply=True)
    assert int(sg.get("mismatch_count") or 0) == 1
    assert pipeline.can_enqueue(m, sg)

    dest2 = d / "clips" / "s002b.mp4"
    dest2.write_bytes(b"fake-mp4-" + os.urandom(12))
    sg["status"] = "submitted"
    sg["gen_id"] = "gid2"
    _run_after(m, d, sg, dest2, 20, apply=True)
    assert int(sg.get("mismatch_count") or 0) == 2
    why = pipeline.wait_reason(m, sg)
    assert why and "两次首帧都对不上" in why["text"], why
    assert "检查台可重试" in why["text"]
    assert not pipeline.can_enqueue(m, sg)
    print("  wrong_clip ×2 → mismatch_count=2 挡住  OK")


def test_one_mismatch_plus_download_fail_still_enqueues():
    """s010 现场：一次对不上 + 一次下载失败 ≠ 两次对不上。"""
    d = Path(tempfile.mkdtemp(prefix="s53-s010-"))
    m = _manifest("grok")
    sg = _ready_bridge(m, d, 2)
    sg["bridge_engine"] = "grok-single"
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    dest = d / "clips" / "s002.mp4"
    dest.write_bytes(b"fake-mp4-" + os.urandom(12))
    _run_after(m, d, sg, dest, 87, apply=True)
    assert int(sg.get("mismatch_count") or 0) == 1
    # 下载失败走 generate 的 OranteError 分支：fail_count 加、mismatch 不加
    sg["status"] = "failed"
    sg["error"] = genmod._clip_dl_error("下载失败：HTTP 200 / 99176 字节 / 不是合法 mp4")
    sg["fail_count"] = int(sg.get("fail_count") or 0) + 1
    assert int(sg.get("fail_count") or 0) == 2
    assert int(sg.get("mismatch_count") or 0) == 1
    assert pipeline.can_enqueue(m, sg), pipeline.wait_reason(m, sg)
    print("  一次对不上 + 一次下载失败 → 能进队  OK")


# ---------------------------------------------------------------- B. 换路清账
def test_settle_clears_old_grok_mismatch_idempotent():
    d = Path(tempfile.mkdtemp(prefix="s53-settle-"))
    m = _manifest("grok")
    sg = _ready_bridge(m, d, 2)
    sg["status"] = "failed"
    sg["bridge_engine"] = "grok"
    sg["mismatch_count"] = 2
    sg["fail_count"] = 2
    logs = []
    touched = pipeline.settle_bridge_paths(m, log=logs.append)
    assert 2 in touched
    assert sg["mismatch_count"] == 0
    assert sg["bridge_engine"] == "grok-single"
    assert any("双图→单图" in x and "清零" in x for x in logs), logs

    logs2 = []
    sg["mismatch_count"] = 1
    touched2 = pipeline.settle_bridge_paths(m, log=logs2.append)
    assert touched2 == []
    assert sg["mismatch_count"] == 1
    assert logs2 == []
    print("  settle 双图→单图清零 / 再跑不清  OK")


def test_migrate_acct_53_idempotent():
    d = Path(tempfile.mkdtemp(prefix="s53-mig-"))
    m = _manifest("grok")
    sg = _ready_bridge(m, d, 2)
    sg["bridge_engine"] = "grok"
    sg["mismatch_count"] = 2
    assert pipeline.GROK_BRIDGE_MODE == "single_tail"
    assert pipeline.migrate_bridge_acct_53(m) is True
    assert sg["mismatch_count"] == 0
    assert sg["bridge"]["acct_migrated_53"] is True
    sg["mismatch_count"] = 9
    assert pipeline.migrate_bridge_acct_53(m) is False
    assert sg["mismatch_count"] == 9
    print("  migrate_53 幂等  OK")


def test_generate_settles_old_grok_before_wait_reason():
    tid, d, m, sg = _make_task("T5301", "grok")
    sg["status"] = "failed"
    sg["bridge_engine"] = "grok"
    sg["mismatch_count"] = 2
    sg["fail_count"] = 2
    sg["error"] = "接片两次都对不上"
    m.save(d)
    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    _run_gen(job, [2], lambda acc, log: FakeEngine(acc, log))
    joined = "\n".join(job.lines)
    assert "双图→单图" in joined, joined
    assert "接片(Grok 单图)" in joined, joined
    assert "两次首帧都对不上" not in joined
    m2 = Manifest.load(d)
    assert m2.seg(2)["bridge_engine"] == "grok-single"
    print("  generate 组队前换路清账  OK")


# ---------------------------------------------------------------- C. 网络层体积闸
def test_net_candidates_skip_99kb():
    logs = []
    eng, logs = _eng(logs)
    page = FakePage(url=OWN, videos=[BLOB],
                    perf=[{"url": NET, "size": 99 * 1024}])
    eng._pages[POST] = page
    eng._conv[POST] = CONV
    eng._rows[POST] = {"第几段": 10, "会话Id": CONV, "postId": POST}
    eng._netvid[POST] = [NET]
    eng._netsize[POST] = {NET: 99 * 1024}
    cands = eng._net_candidates(POST, page, tag="s010")
    assert NET not in cands
    assert cands == []
    joined = "\n".join(logs)
    assert "只有 99 KB，跳过" in joined, joined
    assert "s010" in joined
    assert NET[:60] in joined
    assert 99 * 1024 < NET_MIN_BYTES
    print("  99 KB 网络层候选跳过  OK")


def test_attach_netvid_reads_content_length():
    logs = []
    eng, logs = _eng(logs)
    page = FakePage(url=OWN)
    eng._attach_netvid(page, POST)
    assert page._on
    ev, handler = page._on[0]
    assert ev == "response"

    class Resp:
        url = NET
        headers = {"Content-Type": "video/mp4", "Content-Length": str(99 * 1024)}

    handler(Resp())
    assert NET in (eng._netvid.get(POST) or [])
    assert eng._netsize[POST][NET] == 99 * 1024
    print("  response Content-Length 记体积  OK")


def _tiny_ftyp(n=99176):
    body = bytearray(n)
    body[4:8] = b"ftyp"
    return bytes(body)


class _R:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status
        self.ok = 200 <= status < 300

    def body(self):
        return self._body


class _Ctx:
    def __init__(self, body):
        self.body = body

        class Req:
            def __init__(self, outer):
                self.outer = outer

            def get(self, url, timeout=None):
                return _R(self.outer.body)

        self.request = Req(self)


def test_is_real_mp4_fail_need_redownload_and_reject():
    assert not grok_pipe.is_real_mp4(_tiny_ftyp(99176))
    dest = Path(tempfile.mkdtemp(prefix="s53-dl-")) / "x.mp4"
    eng, logs = _eng()
    eng._ctx = _Ctx(_tiny_ftyp(99176))
    eng._urls[POST] = NET
    eng._unique[POST] = True
    eng._rows[POST] = {"postId": POST, "视频URL": NET, "unique_strong": True,
                       "第几段": 10}
    try:
        eng.download(POST, dest)
        raise AssertionError("should NeedRedownload")
    except NeedRedownload as e:
        assert "不是合法 mp4" in e.message
    except OranteError:
        raise AssertionError("is_real_mp4 不过不该 OranteError")
    assert NET in (eng._rejected.get(POST) or set())
    assert not eng._urls.get(POST)
    print("  is_real_mp4 不过 → NeedRedownload + _rejected  OK")


def test_need_redownload_keeps_submitted_then_five_fails():
    tid, d, m, sg = _make_task("T5302", "grok")

    class OnceThumb(FakeEngine):
        def download(self, gid, dest: Path):
            self.downloads += 1
            if self.downloads == 1:
                raise NeedRedownload("HTTP 200 / 99176 字节 / 不是合法 mp4")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\x00" * 200)
            return dest

    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    _run_gen(job, [2], lambda acc, log: OnceThumb(acc, log))
    joined = "\n".join(job.lines)
    assert "下载地址过不了闸，重新取" in joined, joined
    m2 = Manifest.load(d)
    assert m2.seg(2)["status"] == "collected", m2.seg(2)
    assert int(m2.seg(2).get("thumb_count") or 0) == 0
    assert m2.seg(2)["status"] != "failed"
    print("  一次不合格地址 → 段不失败，随后收片  OK")


def test_five_bad_urls_fail_with_thumb_copy():
    tid, d, m, sg = _make_task("T5303", "grok")

    class AlwaysThumb(FakeEngine):
        def download(self, gid, dest: Path):
            self.downloads += 1
            raise NeedRedownload("HTTP 200 / 99176 字节 / 不是合法 mp4")

    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    _run_gen(job, [2], lambda acc, log: AlwaysThumb(acc, log))
    joined = "\n".join(job.lines)
    assert "网络层一直只给到缩略图/预览" in joined, joined
    m2 = Manifest.load(d)
    sg2 = m2.seg(2)
    assert sg2["status"] == "failed"
    assert sg2.get("error") == "网络层一直只给到缩略图/预览"
    assert int(sg2.get("thumb_count") or 0) >= pipeline.THUMB_STOP
    assert int(sg2.get("mismatch_count") or 0) == 0
    why = pipeline.wait_reason(m2, sg2)
    assert why and "下载一直不合格" in why["text"], why
    print("  连续 5 次不合格 → failed 点名缩略图  OK")


# ---------------------------------------------------------------- 超时 / 双前缀
def test_timeout_three_strikes():
    d = Path(tempfile.mkdtemp(prefix="s53-to-"))
    m = _manifest("grok")
    sg = _ready_bridge(m, d, 2)
    sg["status"] = "failed"
    sg["bridge_engine"] = "grok-single"
    assert genmod.apply_timeout(sg) is False
    assert sg["error"] == "等了太久没出片"
    assert int(sg.get("timeout_count") or 0) == 1
    assert int(sg.get("mismatch_count") or 0) == 0
    assert pipeline.can_enqueue(m, sg), pipeline.wait_reason(m, sg)
    assert genmod.apply_timeout(sg) is False
    assert pipeline.can_enqueue(m, sg)
    assert genmod.apply_timeout(sg) is True
    assert sg["error"] == "等了太久没出片 ×3"
    why = pipeline.wait_reason(m, sg)
    assert why and "×3" in why["text"], why
    assert not pipeline.can_enqueue(m, sg)
    print("  超时两次能重试，第三次才停  OK")


def test_no_double_download_prefix():
    a = genmod._clip_dl_error("下载失败：HTTP 200 / 99176 字节 / 不是合法 mp4")
    b = genmod._clip_dl_error("HTTP 200 / 99176 字节 / 不是合法 mp4")
    assert a == "下载失败：HTTP 200 / 99176 字节 / 不是合法 mp4"
    assert b == "下载失败：HTTP 200 / 99176 字节 / 不是合法 mp4"
    assert "下载失败：下载失败：" not in a
    assert "下载失败：下载失败：" not in b
    src = (ROOT / "orante" / "engines" / "grok.py").read_text(encoding="utf-8")
    assert 'raise OranteError(f"下载失败：{e}"' not in src
    print("  错误串不出现双前缀  OK")


# ---------------------------------------------------------------- D. 检查台重试
def test_bench_retry_clears_and_enqueues():
    tid, d, m, sg = _make_task("T5304", "grok")
    sg["status"] = "failed"
    sg["bridge_engine"] = "grok-single"
    sg["mismatch_count"] = 2
    sg["timeout_count"] = 3
    sg["thumb_count"] = 5
    sg["error"] = "接片两次首帧都对不上（检查台可重试）"
    sg["gen_id"] = "dead"
    m.save(d)
    it = bench._item(tid, d, m, sg)
    assert it["fail_twice"] is True
    assert it["block_why"] == "两次首帧都对不上"
    assert it["can_retry"] is True
    with patch.object(bench, "_kick_generate", return_value=None):
        r = bench.retry_bridge(tid, 2)
    assert "重试接片" in (r.get("log") or "")
    item = r["item"]
    assert item["can_retry"] is False or item["status"] == "planned"
    m2 = Manifest.load(d)
    sg2 = m2.seg(2)
    assert sg2["status"] == "planned"
    assert sg2.get("error") is None
    assert sg2.get("gen_id") is None
    assert int(sg2.get("mismatch_count") or 0) == 0
    assert int(sg2.get("timeout_count") or 0) == 0
    assert int(sg2.get("thumb_count") or 0) == 0
    assert pipeline.can_enqueue(m2, sg2)
    print("  检查台重试接片清账进队  OK")


def test_ui_retry_copy():
    zh = (ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8")
    js = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    assert "重试接片" in zh
    assert "gen.bench.act_retry" in js
    assert "benchRetry" in js
    assert "it.block_why" in js
    assert "it.can_retry" in js
    py = (ROOT / "orante" / "services" / "bench.py").read_text(encoding="utf-8")
    assert "两次首帧都对不上" in py
    assert "下载一直不合格" in py
    assert "超时 3 次" in py
    print("  检查台文案  OK")
