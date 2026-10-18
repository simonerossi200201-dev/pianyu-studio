# -*- coding: utf-8 -*-
"""规格 79：死 post 尽快失败，别每次重启再等 15 分钟。

FakePage，不碰真账号。先设 ORANTE_DATA。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-79-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante.engines.grok import EMPTY_STOP, GrokEngine  # noqa: E402
from orante.services import generate as genmod  # noqa: E402
from orante import paths as _paths  # noqa: E402

_paths.add_legacy_to_sys_path()
import grok_pipe  # noqa: E402

ACCT = {
    "id": "A000", "name": "fake-grok", "engine": "grok",
    "port": 9999, "max_concurrent": 4, "profile": "",
}
POST = "24c71865-aaaa-bbbb-cccc-111111111111"
CONV = "7efea7c6-dddd-eeee-ffff-222222222222"
OWN = f"https://grok.com/imagine/post/{POST}?conversation={CONV}"
HOME = "https://grok.com/imagine"
TOAST = "Post not found. Redirecting to imagine home…"
PROMO = "https://data.x.ai/xxx.mp4"


class FakeCtx:
    def __init__(self, page):
        self.page = page
        self.created = 0

    def new_page(self):
        self.created += 1
        return self.page


class FakePage:
    def __init__(self, url="", videos=None, bounce=False, generating=False,
                 inner_text=""):
        self.url = url
        self.videos = list(videos or [])
        self.bounce = bounce
        self.generating = generating
        self.inner_text = inner_text or (TOAST if bounce else "")
        self.gotos: list[str] = []
        self.closed = False
        self._on = []

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = HOME if self.bounce else url
        return None

    def evaluate(self, js):
        s = js if isinstance(js, str) else str(js)
        if "getEntriesByType" in s:
            return []
        if "readyState" in s and "querySelectorAll('video')" in s:
            return [{"src": u, "currentSrc": u, "sources": [],
                     "readyState": 0, "duration": 0, "visible": True}
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
            return bool(self.generating)
        if "失败" in s or "failed" in s.lower():
            return ""
        if "innerText" in s:
            return self.inner_text
        return self.inner_text

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


def _eng(page=None, conv=CONV, gid=POST, s=1, attach=None):
    logs: list[str] = []
    eng = GrokEngine(ACCT, logs.append)
    if page is not None:
        eng._pages[gid] = page
    eng._conv[gid] = conv
    eng._rows[gid] = {
        "第几段": s,
        "会话Id": conv,
        "postId": gid,
    }
    eng._active.add(gid)
    eng._submitted_at[gid] = time.time() - 60
    if attach is not None:
        eng.attach_seg(gid, attach)
    return eng, logs


# ---------------------------------------------------------------- 洞 1：空详情页
def test_empty_detail_logs_then_fails():
    """0 video 不在生成：第 5/10/15 轮各一条，第 20 轮 failed，关页。"""
    page = FakePage(url=OWN)
    eng, logs = _eng(page)
    last = None
    for i in range(EMPTY_STOP):
        last = eng._poll_one(POST)
        if last.get("state") == "failed":
            assert i + 1 == EMPTY_STOP, f"第 {i + 1} 轮就失败了：{last}"
            break
        assert last["state"] == "pending", last
    assert last is not None
    assert last["state"] == "failed"
    assert "没有视频" in (last.get("error") or "")
    assert page.closed
    assert POST not in eng._active
    empty_logs = [x for x in logs if "详情页开着" in x and "0 个 video" in x]
    assert len(empty_logs) == 3, empty_logs
    assert "第 5 轮" in empty_logs[0]
    assert "第 10 轮" in empty_logs[1]
    assert "第 15 轮" in empty_logs[2]


def test_empty_rounds_reset_when_video_appears():
    """中途出现一个 video → _empty_rounds 归零，不再判失败。"""
    page = FakePage(url=OWN)
    eng, logs = _eng(page)
    for _ in range(12):
        r = eng._poll_one(POST)
        assert r["state"] == "pending"
    assert eng._empty_rounds.get(POST, 0) == 12
    page.videos = [PROMO]
    r = eng._poll_one(POST)
    assert r["state"] == "pending"
    assert eng._empty_rounds.get(POST, 0) == 0
    for _ in range(10):
        r = eng._poll_one(POST)
        assert r["state"] == "pending"
    assert "没有视频" not in (r.get("error") or "")
    assert not page.closed


# ---------------------------------------------------------------- 洞 2：_page_of 被踢计数
def test_page_of_bounce_counts():
    """第一次 _page_of 就被踢 → _bounced 为 1，日志格式和 _poll_one 一致。"""
    page = FakePage(url=HOME, bounce=True, inner_text=TOAST)
    eng, logs = _eng(page=None)
    eng._ctx = FakeCtx(page)
    pg = eng._page_of(POST)
    assert pg is page
    assert eng._bounced.get(POST) == 1
    joined = "\n".join(logs)
    assert "被 Grok 踢回首页（第 1 次）" in joined
    assert "地址" in joined
    assert HOME in joined or "imagine" in joined


def test_bounce_from_page_of_then_poll_one_fails():
    """一次来自 _page_of，一次来自 _poll_one → failed，error 含「找不到这个 post」。"""
    page = FakePage(url=HOME, bounce=True, inner_text=TOAST)
    eng, logs = _eng(page=None)
    eng._ctx = FakeCtx(page)
    r1 = eng._poll_one(POST)
    assert r1["state"] == "pending"
    assert eng._bounced.get(POST) == 1
    r2 = eng._poll_one(POST)
    assert r2["state"] == "failed"
    assert "找不到这个 post" in (r2.get("error") or "")
    assert page.closed
    joined = "\n".join(logs)
    assert "第 1 次" in joined
    assert "第 2 次" in joined


# ---------------------------------------------------------------- 洞 3：gen_health 落盘
def test_gen_health_persists_across_adopt():
    """被踢一次写入 seg；新引擎 adopt 后 _bounced 仍是 1；再踢立刻 failed。"""
    page = FakePage(url=HOME, bounce=True, inner_text=TOAST)
    seg = {"s": 1, "gen_id": POST, "gen_conv": CONV, "image": "p01.jpg"}
    eng, logs = _eng(page, attach=seg)
    r1 = eng._poll_one(POST)
    assert r1["state"] == "pending"
    assert seg.get("gen_health", {}).get("bounced") == 1
    assert "first_bad_at" in seg["gen_health"]

    page2 = FakePage(url=HOME, bounce=True, inner_text=TOAST)
    logs2: list[str] = []
    eng2 = GrokEngine(ACCT, logs2.append)
    eng2.adopt(POST, seg)
    assert eng2._bounced.get(POST) == 1, "adopt 不许把被踢计数清零"
    assert eng2._bounced.get(POST) != 0
    assert any("接手" in x and "已被踢 1 次" in x for x in logs2)
    eng2._pages[POST] = page2
    eng2._submitted_at[POST] = time.time() - 60
    r2 = eng2._poll_one(POST)
    assert r2["state"] == "failed"
    assert "找不到这个 post" in (r2.get("error") or "")


def test_gen_health_cleared_after_clip():
    """收到片子之后 gen_health 从 seg 里消失。"""
    page = FakePage(url=HOME, bounce=True, inner_text=TOAST)
    seg = {"s": 1, "gen_id": POST, "gen_conv": CONV}
    eng, _ = _eng(page, attach=seg)
    eng._poll_one(POST)
    assert "gen_health" in seg
    url = "https://assets.grok.com/users/u/generated/abc.mp4"
    got = eng._mark_ready(POST, url, "post页")
    assert got["state"] == "done"
    assert "gen_health" not in seg
    assert eng.health_of(POST) is None


# ---------------------------------------------------------------- 洞 4：续跑超时
def test_adopt_timeout_from_first_bad_at():
    """first_bad_at 是 8 分钟前 → 立刻超时，文案含「续跑接手」。"""
    first = (datetime.now() - timedelta(minutes=8)).strftime("%Y-%m-%dT%H:%M:%S")
    seg = {"s": 1, "status": "submitted",
           "gen_health": {"bounced": 1, "first_bad_at": first}}
    now = time.time()
    due, elapsed = genmod.gen_timed_out(
        seg, adopted=True, started_at=now, now=now)
    assert due is True
    assert elapsed >= 7 * 60
    genmod.apply_timeout(seg, adopted=True, elapsed=elapsed)
    err = seg.get("error") or ""
    assert "续跑接手" in err
    assert "8 分钟" in err


def test_adopt_timeout_five_minutes_without_first_bad():
    now = time.time()
    seg = {"s": 1}
    due_early, _ = genmod.gen_timed_out(
        seg, adopted=True, started_at=now - 4 * 60, now=now)
    due_late, _ = genmod.gen_timed_out(
        seg, adopted=True, started_at=now - 6 * 60, now=now)
    assert due_early is False
    assert due_late is True
    assert genmod.ADOPT_TIMEOUT == 5 * 60


def test_fresh_submit_still_fifteen_minutes():
    now = time.time()
    seg = {"s": 1}
    due_14, _ = genmod.gen_timed_out(
        seg, adopted=False, started_at=now - 14 * 60, now=now)
    due_16, _ = genmod.gen_timed_out(
        seg, adopted=False, started_at=now - 16 * 60, now=now)
    assert due_14 is False
    assert due_16 is True
    assert genmod.GEN_TIMEOUT == 15 * 60
    sg = {"s": 1}
    genmod.apply_timeout(sg)
    assert sg["error"] == "等了太久没出片"


# ---------------------------------------------------------------- 汇总行
def test_summary_every_five_unless_changed():
    prev, n = None, 0
    key = (1, 0, 9, 0, (1,), 0, 0)
    emits = []
    for i in range(1, 11):
        emit, prev, n = genmod.tick_summary(prev, key, n)
        if emit:
            emits.append(i)
    assert emits == [1, 5, 10], emits
    # 已收数字一变立刻打
    key2 = (1, 0, 10, 0, (1,), 0, 0)
    emit, prev, n = genmod.tick_summary(prev, key2, n)
    assert emit is True
    line = genmod.inflight_summary_line(
        {POST: {"s": 1, "gen_health": {}}},
        0, 9, {POST: time.time() - 4 * 60 - 12}, set(),
        now=time.time())
    assert "在生成 1 条" in line
    assert "s001" in line
    assert "已等" in line
    assert "待提交 0" in line
    assert "已收 9" in line


# ---------------------------------------------------------------- generate 接线：adopt 超时立刻失败
class _FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J79"
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


class _PendingEngine:
    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.name = account.get("engine") or "fake"
        self.can_gen_images = False

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def poll(self, ids):
        return {gid: {"state": "pending", "raw": "wait"} for gid in ids}

    def forget_url(self, gen_id, url=""):
        pass

    def adopt(self, gid, seg=None):
        pass

    def attach_seg(self, gid, seg=None):
        pass


def _jpeg(path: Path, tag=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9" + tag + os.urandom(8))


def test_generate_adopt_times_out_immediately():
    """generate 轮询：first_bad_at 8 分钟前的续跑段，第一轮就超时。"""
    from orante import db
    from orante.models import Manifest

    db.init()
    tid = "T0079"
    d = Path(tempfile.mkdtemp(prefix="orante-task-79-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    _jpeg(d / "images" / "p01.jpg", b"p01")
    m = Manifest.new(tid, "测79", "grok", "A001", "long", "hello " * 20)
    m.mode = "first_frame"
    m.images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "a" * 40, "role": "主体",
        "status": "ok", "desc": "描述", "voice": "【音色】测",
        "eye": "闭眼", "scene": "常规", "measure": {}, "origin": "download",
        "src": "", "pose": {},
    }]
    first = (datetime.now() - timedelta(minutes=8)).strftime("%Y-%m-%dT%H:%M:%S")
    m.segments = [{
        "s": 1, "text": "台词", "duration": 6, "image": "p01",
        "role": "anchor", "shot": "", "prompt_final": "hi",
        "status": "submitted", "gen_id": "gid-dead", "takes": [],
        "error": None,
        "gen_health": {"bounced": 1, "empty_rounds": 0, "first_bad_at": first},
    }]
    m.save(d)
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-grok", "grok", 9398, "A001", "", "", "9:16", 5, 1, "{}",
             db.now()))
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(d), "arranged", "grok", "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格79", "grok", "A001", "long", "hello", str(d / "images"),
             "", "arranged", str(d), "", 0, db.now(), db.now()))
    job = _FakeJob(tid, "A001")
    with patch("orante.services.generate.make_engine",
               side_effect=lambda acc, log: _PendingEngine(acc, log)), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]), \
         patch("orante.services.tails.after_collect", return_value=None):
        genmod.generate(job, only=[1])
    m2 = Manifest.load(d)
    sg = m2.seg(1)
    assert sg["status"] == "failed", sg
    err = sg.get("error") or ""
    assert "续跑接手" in err, err
    assert any("续跑接手" in x for x in job.lines)


def test_summary_line_throttled_in_generate():
    """内容不变时每 5 轮一条；已收变了会走 tick_summary 的立刻打。"""
    from orante import db
    from orante.models import Manifest

    db.init()
    tid = "T0079b"
    d = Path(tempfile.mkdtemp(prefix="orante-task-79b-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    _jpeg(d / "images" / "p01.jpg", b"p01")
    m = Manifest.new(tid, "测79b", "grok", "A001", "long", "hello " * 20)
    m.mode = "first_frame"
    m.images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "a" * 40, "role": "主体",
        "status": "ok", "desc": "描述", "voice": "【音色】测",
        "eye": "闭眼", "scene": "常规", "measure": {}, "origin": "download",
        "src": "", "pose": {},
    }]
    m.segments = [{
        "s": 1, "text": "台词", "duration": 6, "image": "p01",
        "role": "anchor", "shot": "", "prompt_final": "hi",
        "status": "submitted", "gen_id": "gid-wait", "takes": [],
        "error": None,
    }]
    m.save(d)
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-grok", "grok", 9398, "A001", "", "", "9:16", 5, 1, "{}",
             db.now()))
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=? WHERE id=?", (str(d), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格79b", "grok", "A001", "long", "hello", str(d / "images"),
             "", "arranged", str(d), "", 0, db.now(), db.now()))

    class StopJob2(_FakeJob):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.polls = 0

        def sleep(self, sec: float) -> None:
            self.sleeps += 1
            if sec >= genmod.POLL_EVERY:
                self.polls += 1
                if self.polls > 10:
                    raise RuntimeError("stop after 10 polls")

    job = StopJob2(tid, "A001")
    try:
        with patch("orante.services.generate.make_engine",
                   side_effect=lambda acc, log: _PendingEngine(acc, log)), \
             patch("orante.services.describe.missing_subject_ps", return_value=[]), \
             patch("orante.services.tails.after_collect", return_value=None):
            genmod.generate(job, only=[1])
    except RuntimeError as e:
        assert "stop after 10 polls" in str(e)
    lines = [x for x in job.lines if "在生成" in x]
    assert len(lines) == 3, lines  # 第 1/5/10 轮
    assert "s001" in lines[0]
    assert "已等" in lines[0]


def test_empty_stop_constant():
    assert EMPTY_STOP == 20
    assert grok_pipe.IS_GENERATING_JS
    assert "generating" in grok_pipe.IS_GENERATING_JS.lower()
