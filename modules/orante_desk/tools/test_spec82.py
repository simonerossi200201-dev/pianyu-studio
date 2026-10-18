# -*- coding: utf-8 -*-
"""规格 82：Flow 项目页 404 说人话、账号限流不重试。先设 ORANTE_DATA。

FakePage / FakeRPC，不碰真账号。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-82-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from orante import accounts, config, db, paths  # noqa: E402
from orante.engines import flow_api  # noqa: E402
from orante.engines.base import BlockedImage  # noqa: E402
from orante.errors import FlowThrottled, OranteError  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.scheduler import Job, Scheduler, _finish_line  # noqa: E402
from orante.services import generate as genmod  # noqa: E402
from orante import tasks as taskmod  # noqa: E402

db.init()
load_all_services()
paths.ensure_dirs()

PID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CID = "ffffffff-1111-2222-3333-444444444444"
PROJECT_URL = f"https://flow.google.com/project/{PID}/collection/{CID}"
PROJECT_ONLY = f"https://flow.google.com/project/{PID}"
PAGE_404 = "https://flow.google.com/404?reason=project"
FLOW_HOME = "https://flow.google.com/"

ACC = {
    "id": "A082", "name": "TRE-1", "engine": "flow", "port": 9482,
    "project_url": PROJECT_URL,
    "model": "Veo 3.1 - Lite [Lower Priority]", "aspect": "9:16",
    "max_concurrent": 5, "extra": {},
}


def _jpeg(path: Path, size=(640, 960), color=(40, 80, 60)) -> None:
    from PIL import Image as PILImage
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, color).save(path, "JPEG", quality=80)


class FakeJob:
    def __init__(self, task_id="T0082", account_id="A082"):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "Jfake82"
        self.progress = {"phase": "", "done": 0, "total": 0, "current": "", "items": []}
        self.args = {}
        self.flow_throttle = None

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        if self.cancelled.is_set():
            from orante.scheduler import JobCancelled
            raise JobCancelled()

    def sleep(self, sec: float) -> None:
        pass

    def set_progress(self, **kw) -> None:
        item = kw.pop("item", None)
        for k, v in kw.items():
            self.progress[k] = v
        if item is not None:
            self.progress.setdefault("items", []).append(item)


class FakePage:
    def __init__(self, landing_map=None):
        # landing_map: collection / project / home → 落地 URL
        self.landing_map = dict(landing_map or {})
        self.url = "about:blank"
        self.gotos = []
        self.closed = False

    def goto(self, url, **kw):
        self.gotos.append(url)
        u = url or ""
        if "/collection/" in u:
            self.url = self.landing_map.get("collection", u)
        elif "/project/" in u:
            self.url = self.landing_map.get("project", u)
        elif "flow.google.com" in u:
            self.url = self.landing_map.get("home", u)
        else:
            self.url = u

    def evaluate(self, js, args=None):
        s = str(js)
        if "WIZ_global_data" in s:
            return {"SNlM0e": "xsrf-token", "cfb2h": "bl", "FdrFJe": "sid"}
        return None

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


class FakeCDP:
    def send(self, *a, **k):
        pass


class FakeCtx:
    def __init__(self, page):
        self._page = page
        self.pages = []

    def new_page(self):
        self.pages.append(self._page)
        return self._page

    def new_cdp_session(self, pg):
        return FakeCDP()


class FakeBr:
    def __init__(self, page):
        self.ctx = FakeCtx(page)


def _eng(logs=None, page=None):
    lines = logs if logs is not None else []
    acc = dict(ACC)
    acc["extra"] = {}
    eng = flow_api.FlowEngine(acc, lines.append)
    if page is not None:
        eng._br = FakeBr(page)
    return eng, lines


def _ensure_account(aid="A082", name="TRE-1", url=PROJECT_URL, extra=None):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run("DELETE FROM accounts WHERE id=?", (aid,))
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, name, "flow", 9480 + (sum(ord(c) for c in aid) % 60), aid,
         url, "Veo 3.1 - Lite [Lower Priority]", "9:16", 5, 1,
         db.j(extra or {}), db.now()))
    return accounts.get(aid)


# ---------------- 项目页 404 ----------------

def test_404_collection_rewrites_to_project():
    page = FakePage({
        "collection": PAGE_404,
        "project": PROJECT_ONLY,
        "home": FLOW_HOME,
    })
    logs = []
    eng, _ = _eng(logs, page)
    src = eng.src_path
    pg = eng._open_own_page()
    extra = (eng.account.get("extra") or {}).get("project_page") or {}
    assert extra.get("why") == "collection", extra
    assert extra.get("ok") is False
    assert pg.url == PROJECT_ONLY
    assert "/404" not in (pg.url or "")
    assert eng.src_path == src
    assert "/collection/" in eng.src_path
    blob = "\n".join(logs)
    assert "合集那一段失效" in blob
    assert "不影响出片" in blob


def test_404_both_goes_home_and_refetches_xsrf():
    page = FakePage({
        "collection": PAGE_404,
        "project": PAGE_404,
        "home": FLOW_HOME,
    })
    logs = []
    eng, _ = _eng(logs, page)
    src = eng.src_path
    n_xsrf = {"n": 0}
    orig = eng._wiz_of

    def wiz(pg):
        n_xsrf["n"] += 1
        return orig(pg)

    eng._wiz_of = wiz
    pg = eng._open_own_page()
    extra = (eng.account.get("extra") or {}).get("project_page") or {}
    assert extra.get("why") == "not_visible", extra
    assert extra.get("ok") is False
    assert "404" not in (pg.url or "")
    assert pg.url.rstrip("/") == FLOW_HOME.rstrip("/")
    assert eng.src_path == src
    assert n_xsrf["n"] >= 2, n_xsrf
    blob = "\n".join(logs)
    assert "看不到" in blob
    assert "不影响出片" in blob


def test_ok_landing_no_extra_nav_or_log():
    page = FakePage({"collection": PROJECT_URL, "project": PROJECT_ONLY, "home": FLOW_HOME})
    logs = []
    eng, _ = _eng(logs, page)
    pg = eng._open_own_page()
    extra = (eng.account.get("extra") or {}).get("project_page") or {}
    assert extra.get("ok") is True, extra
    assert pg.url == PROJECT_URL
    # 规格 96：Orante 自己的标签页导航时强制 ?hl=en（界面英文，真人操作的选择器才对得上）
    assert page.gotos == [flow_api.force_hl(PROJECT_URL, "en")]
    blob = "\n".join(logs)
    assert "打不开" not in blob
    assert "合集那一段" not in blob


def test_health_returns_project_page():
    _ensure_account()
    rec = {"ok": False, "why": "collection", "at": "2026-09-21T12:00:00"}
    with patch("orante.engines.chrome.port_alive", return_value=True), \
         patch("orante.engines.chrome.cdp_pages",
               return_value=[{"url": "https://flow.google.com/project/x"}]), \
         patch("orante.engines.flow_api.probe_account_project_page",
               return_value=rec):
        h = accounts.health("A082")
    assert h.get("project_page") == rec
    assert h.get("state") == "ready"


def test_use_project_url_strips_collection_only_on_click():
    _ensure_account()
    a = accounts.get("A082")
    assert "/collection/" in a["project_url"]
    # 没点就不改
    a2 = accounts.get("A082")
    assert a2["project_url"] == PROJECT_URL
    a3 = accounts.use_project_url("A082")
    assert a3["project_url"] == PROJECT_ONLY
    assert "/collection/" not in a3["project_url"]
    assert a3["name"] == "TRE-1"


# ---------------- 限流 ----------------

def _unusual_res():
    return {
        "status": 200,
        "payload": None,
        "err": [7, None, [["type.googleapis.com/google.rpc.ErrorInfo",
                           ["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]],
        "raw": "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
    }


def test_unusual_activity_no_retry():
    logs = []
    eng, _ = _eng(logs)
    n = {"i": 0}

    def evaluate(js, args):
        n["i"] += 1
        return _unusual_res()

    eng._ensure_page = lambda: None
    eng.page = type("P", (), {})()
    eng.page.evaluate = evaluate
    with patch("orante.engines.flow_api.time.sleep", lambda s: None):
        with pytest.raises(FlowThrottled) as ei:
            eng._rpc("eb1hJf", lambda: ["inner"], retries=2)
    assert n["i"] == 1, n["i"]
    blob = "\n".join(logs)
    # 限流由调用方打人话，_rpc 自己不打开发者那串
    assert "eb1hJf 失败" not in blob
    assert "type.googleapis.com" not in blob
    assert ei.value.flow_code == "PUBLIC_ERROR_UNUSUAL_ACTIVITY"


def test_timeout_still_retries():
    logs = []
    eng, _ = _eng(logs)
    n = {"i": 0}
    plan = [
        {"status": 200, "err": [7, None, [["x", ["PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT"]]]],
         "payload": None, "raw": ""},
        {"status": 200, "err": None, "payload": [None, 55, None, [["gid1234567890abcdef"]]],
         "raw": ""},
    ]

    def evaluate(js, args):
        n["i"] += 1
        return plan.pop(0)

    eng._ensure_page = lambda: None
    eng.page = type("P", (), {})()
    eng.page.evaluate = evaluate
    with patch("orante.engines.flow_api.time.sleep", lambda s: None):
        payload = eng._rpc("eb1hJf", lambda: ["inner"], retries=2)
    assert n["i"] == 2
    assert payload is not None
    blob = "\n".join(logs)
    assert "生成超时" in blob
    first = [ln for ln in logs if "生成超时" in ln][0]
    assert "eb1hJf" not in first
    assert "type.googleapis.com" not in first
    assert any("PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT" in ln and ln.startswith("        ")
               for ln in logs)


def test_unsafe_still_blocked_image():
    logs = []
    eng, _ = _eng(logs)
    eng._ensure_page = lambda: None

    def evaluate(js, args):
        return {
            "status": 200, "payload": None,
            "err": [7, None, [["x", ["PUBLIC_ERROR_UNSAFE_GENERATION"]]]],
            "raw": "",
        }

    eng.page = type("P", (), {})()
    eng.page.evaluate = evaluate
    with pytest.raises(BlockedImage):
        eng._rpc("eb1hJf", ["inner"], retries=2)


def test_mark_throttled_doubles_and_caps():
    _ensure_account()
    t0 = time.time()
    accounts.mark_throttled("A082", now=t0)
    a = accounts.get("A082")
    assert a["extra"]["throttle_minutes"] == 20
    assert abs(a["extra"]["throttled_until_ts"] - (t0 + 20 * 60)) < 2

    # 冷却刚结束（1 秒前），30 分钟窗口内再被限 → 40
    extra = dict(a["extra"])
    extra["throttled_until_ts"] = t0 - 1
    extra["throttled_until"] = datetime.fromtimestamp(t0 - 1).isoformat(timespec="seconds")
    accounts.update("A082", extra=extra)
    accounts.mark_throttled("A082", now=t0)
    a = accounts.get("A082")
    assert a["extra"]["throttle_minutes"] == 40

    extra = dict(a["extra"])
    extra["throttled_until_ts"] = t0 - 1
    extra["throttled_until"] = datetime.fromtimestamp(t0 - 1).isoformat(timespec="seconds")
    accounts.update("A082", extra=extra)
    accounts.mark_throttled("A082", now=t0)
    a = accounts.get("A082")
    assert a["extra"]["throttle_minutes"] == 80

    extra = dict(a["extra"])
    extra["throttled_until_ts"] = t0 - 1
    extra["throttled_until"] = datetime.fromtimestamp(t0 - 1).isoformat(timespec="seconds")
    accounts.update("A082", extra=extra)
    accounts.mark_throttled("A082", now=t0)
    a = accounts.get("A082")
    assert a["extra"]["throttle_minutes"] == 120  # 上限


def test_scheduler_skips_throttled_then_resumes():
    _ensure_account()
    t0 = time.time()
    accounts.mark_throttled("A082", now=t0)
    sched = Scheduler()
    ran = {"n": 0}

    def fn(job):
        ran["n"] += 1
        return {"submitted": 1, "failed": 0}

    job = Job("J00821", "T0082", "generate", "A082", fn, {})
    with sched.lock:
        sched.jobs[job.id] = job
        sched.queues["A082"].append(job)
    sched._pump()
    time.sleep(0.05)
    assert ran["n"] == 0
    assert job.status == "queued"

    # 到期
    extra = dict(accounts.get("A082")["extra"])
    extra["throttled_until_ts"] = t0 - 1
    extra["throttled_until"] = datetime.fromtimestamp(t0 - 1).isoformat(timespec="seconds")
    accounts.update("A082", extra=extra)
    sched._pump()
    time.sleep(0.15)
    assert ran["n"] == 1


class ThrottleEngine:
    name = "flow"
    can_gen_images = False

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.submits = []

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        self.submits.append(int(seg["s"]))
        raise FlowThrottled(
            self.account.get("name") or "TRE-1",
            code="PUBLIC_ERROR_UNUSUAL_ACTIVITY",
            err_n=7,
        )

    def poll(self, ids):
        return {}


def _ok_verify(m, d, seg, dest, **kw):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"\x00" * 800)
    return {
        "take": {"file": str(dest.relative_to(d)).replace("\\", "/"),
                 "gen_id": seg.get("gen_id") or "", "at": "t", "verdict": "ok"},
        "verdict": "ok", "voided": False,
    }


def _make_task(tid, n_segs=2):
    _ensure_account()
    d = Path(tempfile.mkdtemp(prefix=f"orante-t82-{tid}-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    fp = d / "images" / "p01.jpg"
    _jpeg(fp)
    recs = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": sha1_file(fp),
        "role": "主体", "status": "ok", "desc": "a speaker", "voice": "",
    }]
    m = Manifest.new(tid, tid, "flow", "A082", "short", "Stay for 7 seconds and type Amen.")
    m.images = recs
    m.segments = [{
        "s": i, "text": "Stay for 7 seconds and type Amen.",
        "duration": 8, "image": "p01", "status": "planned",
        "prompt_final": "Stay for 7 seconds and type Amen.",
        "role": "主体", "shot": "", "gen_id": None, "takes": [], "error": None,
    } for i in range(1, n_segs + 1)]
    m.mode = "first_frame"
    m.save(d)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, tid, "flow", "A082", "short", "hello", str(d / "images"),
         "", "arranged", str(d), "", 0, db.now(), db.now()))
    return d, m


def test_generate_throttle_skips_rest_and_keeps_planned():
    d, m = _make_task("T0082", n_segs=2)
    job = FakeJob("T0082")
    eng = ThrottleEngine(ACC, job.log)
    with patch("orante.services.generate.make_engine", return_value=eng):
        r = genmod.generate(job)
    assert eng.submits == [1], eng.submits
    m2 = Manifest.load(d)
    s1 = m2.seg(1)
    s2 = m2.seg(2)
    assert s1["status"] == "planned"
    assert "限流" in (s1.get("error") or "")
    assert s2["status"] == "planned"
    a = accounts.get("A082")
    until = a["extra"].get("throttled_until_ts") or 0
    assert until > time.time() + 19 * 60
    assert until < time.time() + 21 * 60
    blob = "\n".join(job.lines)
    first = [ln for ln in job.lines if "没提交成" in ln or "限流" in ln]
    assert first, blob
    assert "eb1hJf" not in first[0]
    assert "type.googleapis.com" not in first[0]
    raw_lines = [ln for ln in job.lines if ln.startswith("        [")]
    assert raw_lines and "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in raw_lines[0]
    assert r.get("throttled") is True
    assert r.get("failed") == 0


def test_accounts_network_throttle():
    """0924（规格 91）：两个账号先后受限不再全网暂停，要三个。

    两个太容易误判——各自超了一次跟网络没关系，却把所有 Flow 账号一起按住，
    而那会儿浏览器里明明还能生成。
    """
    _ensure_account("A082", "TRE-1")
    _ensure_account("A083", "TRE-2")
    _ensure_account("A084", "TRE-3")
    config.set("flow.last_throttle", None, touch_rev=False)
    config.set("flow.network_throttle", None, touch_rev=False)
    t0 = time.time()
    accounts.mark_throttled("A082", now=t0, reason={"code": "X", "rpc": "eb1hJf"})
    accounts.mark_throttled("A083", now=t0 + 60, reason={"code": "X", "rpc": "eb1hJf"})
    assert accounts.network_throttled() is False          # 两个还不算
    accounts.mark_throttled("A084", now=t0 + 120, reason={"code": "X", "rpc": "eb1hJf"})
    st = accounts.network_throttle_state()
    assert st.get("on") is True
    assert accounts.network_throttled() is True
    d, m = _make_task("T0083", n_segs=1)
    db.run("UPDATE tasks SET account_id=? WHERE id=?", ("A083", "T0083"))
    job = FakeJob("T0083", "A083")
    with patch("orante.services.describe.missing_subject_ps", return_value=[]):
        r = genmod.generate(job)
    assert r.get("network_throttled") is True
    assert r.get("submitted") == 0


def test_finish_line_not_green_on_fail():
    job = Job("J00822", "T0082", "generate", "A082", lambda j: None, {})
    job.result = {"submitted": 0, "failed": 2, "pending_throttled": 0}
    line = _finish_line(job)
    assert not line.startswith("✓")
    job.result = {"submitted": 0, "failed": 0, "pending_throttled": 2, "throttled": True}
    line = _finish_line(job)
    assert line.startswith("⚠")
    assert "限流" in line
    job.result = {"submitted": 3, "failed": 0, "collected": 3}
    assert _finish_line(job).startswith("✓")


def test_classify_human_unknown():
    info = flow_api.classify_flow_error("eb1hJf", {
        "status": 200,
        "err": [7, None, [["x", ["PUBLIC_ERROR_SOMETHING_NEW"]]]],
        "payload": None,
    })
    assert info["action"] == "unknown"
    assert "没见过" in info["plain"]
    assert "PUBLIC_ERROR_SOMETHING_NEW" in info["plain"]
    raw = flow_api.format_flow_raw(info)
    assert raw.startswith("        ")
    assert "eb1hJf" in raw
