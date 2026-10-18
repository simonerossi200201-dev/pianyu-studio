# -*- coding: utf-8 -*-
"""规格 97：重生时临时换账号（比如 Flow 的任务改用 Grok 重生几段），时长按原片自动换档。先设 ORANTE_DATA。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-97-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from orante import db, jobs as jobmod  # noqa: E402
from orante.engines import caps  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import generate as genmod, pipeline, tails  # noqa: E402

db.init()


def _pin():
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    db.init()


# ---------------------------------------------------------------- 时长换档

def test_snap_duration_rules():
    assert [caps.snap_duration("grok", d) for d in (4, 6, 8)] == [6, 6, 10]
    assert [caps.snap_duration("flow", d) for d in (6, 10, 15)] == [6, 8, 8]
    assert caps.snap_duration("flow", 8) == 8 and caps.snap_duration("grok", 15) == 15
    with pytest.raises(ValueError):
        caps.snap_duration("sora", 8)


def test_submit_duration_follows_engine():
    class E:
        def __init__(self, n):
            self.name = n
    assert genmod._submit_duration({"duration": 8}, E("grok")) == 10
    assert genmod._submit_duration({"duration": 8}, E("flow")) == 8
    assert genmod._submit_duration({"duration": 10}, E("flow")) == 8
    assert genmod._submit_duration({"duration": 6}, E("grok")) == 6


# ---------------------------------------------------------------- 夹具

def _acc(aid, engine, enabled=1):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run("UPDATE accounts SET engine=?, enabled=? WHERE id=?", (engine, enabled, aid))
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "名" + aid, engine, 9300 + int(aid[-2:]), aid, "", "", "9:16", 4, enabled, "{}", db.now()))


def _img(p):
    return {
        "p": p, "file": f"images/{p}.jpg", "sha1": "a" * 40, "role": "主体",
        "status": "ok", "desc": f"描述{p}", "voice": "", "eye": "", "scene": "常规",
        "measure": {}, "origin": "download", "src": "",
    }


def _make_task(tid, *, engine="flow", account="A971", copy="", mode="first_frame",
               durs=(8, 6, 8)):
    _pin()
    _acc("A971", "flow")
    _acc("A972", "grok")
    _acc("A973", "grok", enabled=0)
    d = Path(tempfile.mkdtemp(prefix="orante-task-97-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    m = Manifest.new(tid, "规格97", engine, account, "long", copy)
    m.mode = mode
    m.images = [_img(f"p{i:02d}") for i in range(1, len(durs) + 1)]
    for im in m.images:
        (d / im["file"]).write_bytes(b"\xff\xd8\xff\xd9" + im["p"].encode())
    segs = []
    for i, du in enumerate(durs, 1):
        clip = d / "clips" / f"{tid}-s{i:03d}-p{i:02d}.mp4"
        clip.write_bytes(b"\x01" * (100 + i))
        segs.append({
            "s": i, "text": "", "duration": du, "image": f"p{i:02d}", "role": "anchor",
            "shot": "", "prompt_final": f"提示词{i}", "prompt_locked": True,
            "status": "collected", "gen_id": f"old{i}", "error": None,
            "takes": [{"file": "clips/" + clip.name, "gen_id": f"old{i}"}],
        })
    if mode == "anchor_bridge":
        tails.assign_roles(segs)
    m.segments = segs
    if mode == "anchor_bridge":
        pipeline.rebuild_bridge_links(m.segments)
    m.save(d)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, copy=?, updated=? WHERE id=?",
               (str(d), "collected", engine, account, copy, db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格97", engine, account, "long", copy, str(d / "images"),
             "", "collected", str(d), "", 0, db.now(), db.now()))
    return d


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J97"

    def log(self, msg):
        self.lines.append(msg)

    def check(self):
        pass

    def sleep(self, sec):
        pass


class FakeEngine:
    made = []

    def __init__(self, account, log):
        self.account = account
        self.name = account.get("engine")
        self.subs = []
        FakeEngine.made.append(self)

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 4

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        self.subs.append({"s": seg["s"], "duration": duration, "kw": kw,
                          "bridge_engine": seg.get("bridge_engine")})
        return "n%s" % seg["s"]

    def poll(self, ids):
        return {g: {"state": "done", "url": "http://x/" + g} for g in ids}

    def download(self, gid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x02" * 300)
        return dest


def _run_regen(tid, account_id, segs, strip=None):
    FakeEngine.made = []
    job = FakeJob(tid, account_id)
    strip_calls = strip if strip is not None else []
    with patch("orante.services.generate.make_engine",
               side_effect=lambda acc, log: FakeEngine(acc, log)), \
         patch("orante.services.generate.packable_manifest", return_value=False), \
         patch("orante.services.verify.after_download",
               side_effect=lambda m, d, seg, dest, **k: {
                   "verdict": "ok", "frame_dist": 2, "voided": False,
                   "take": {"file": "clips/" + Path(dest).name, "gen_id": k.get("gen_id")}}), \
         patch("orante.services.verify.strip_audio",
               side_effect=lambda p, *a, **k: strip_calls.append(Path(p).name) or False), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.tails.extract_head", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.regenerate(job, segs)
    return job


# ---------------------------------------------------------------- 重生换账号

def test_regen_with_grok_on_flow_task_uses_grok_and_maps_duration():
    d = _make_task("T9701")
    job = _run_regen("T9701", "A972", [1, 2])
    assert len(FakeEngine.made) == 1
    eng = FakeEngine.made[0]
    assert eng.account["id"] == "A972" and eng.name == "grok"
    got = {x["s"]: x["duration"] for x in eng.subs}
    assert got == {1: 10, 2: 6}                      # 8 → 10，6 → 6
    assert all("silent" not in x["kw"] for x in eng.subs)   # silent 只给 Flow
    m = Manifest.load(d)
    s1 = m.seg(1)
    assert s1["status"] == "collected"
    assert s1["duration"] == 8                       # 段本身的时长不改
    tk = s1["takes"][-1]
    assert tk["engine"] == "grok" and tk["account_id"] == "A972" and tk["duration"] == 10
    assert m.seg(3)["status"] == "collected" and m.seg(3)["takes"][-1]["gen_id"] == "old3"
    from orante import tasks as taskmod
    assert taskmod.get("T9701")["account_id"] == "A971"     # 任务账号不变
    joined = "\n".join(job.lines)
    assert "临时改用账号" in joined and "Grok 按 10 秒出" in joined


def test_regen_without_override_keeps_task_account_and_duration():
    _make_task("T9702")
    job = _run_regen("T9702", "A971", [1])
    eng = FakeEngine.made[0]
    assert eng.account["id"] == "A971"
    assert eng.subs[0]["duration"] == 8
    assert "临时改用账号" not in "\n".join(job.lines)


def test_regen_override_on_silent_flow_task_strips_audio():
    """不说话的 Flow 任务临时换 Grok 重生：片子也去音轨，跟任务里其它片一致。"""
    _make_task("T9703", copy="")
    calls = []
    _run_regen("T9703", "A972", [1], strip=calls)
    assert calls, "应该去音轨"


def test_regen_override_on_talking_task_keeps_audio():
    _make_task("T9704", copy="有台词的文案")
    calls = []
    _run_regen("T9704", "A972", [1], strip=calls)
    assert calls == []


def test_regen_override_does_not_relay():
    """临时换账号的这一轮不接力（没做成的留给任务自己的账号）。"""
    _make_task("T9705")
    seen = {}
    real = genmod.generate

    def spy(job, **kw):
        seen.update(kw)
        return real(job, **kw)
    with patch("orante.services.generate.generate", side_effect=spy):
        _run_regen("T9705", "A972", [1])
    assert seen.get("chain") is False and seen.get("acc_id") == "A972"


def test_regen_override_bridge_goes_grok_single_in_anchor_mode():
    d = _make_task("T9706", mode="anchor_bridge", durs=(8, 8, 8))
    m = Manifest.load(d)
    br = [sg for sg in m.segments if sg.get("role") == "bridge"]
    if not br:
        pytest.skip("锚点模式没排出接片")
    sn = br[0]["s"]
    # 只看 regenerate 给接片定的路，不真跑出片
    with patch("orante.services.generate.generate", return_value={"submitted": 0}):
        _run_regen("T9706", "A972", [sn])
    sg = Manifest.load(d).seg(sn)
    want = "grok-single" if pipeline.GROK_BRIDGE_MODE == "single_tail" else "grok"
    assert sg["bridge_engine"] == want


# ---------------------------------------------------------------- jobs.start 把关

class _Sched:
    def __init__(self, active=None):
        self.active = active or []
        self.sub = []

    def list(self, active_only=True):
        return list(self.active)

    def submit(self, kind, fn, task_id=None, account_id=None, **args):
        self.sub.append({"kind": kind, "task_id": task_id, "account_id": account_id, "args": args})

        class J:
            pass
        j = J()
        j.account_id = account_id
        return j


def test_jobs_start_regen_with_other_account():
    _make_task("T9707")
    s = _Sched()
    with patch.object(jobmod, "scheduler", s):
        j = jobmod.start("regenerate", "T9707", segs=[1], account_id="A972")
    assert j.account_id == "A972"
    assert s.sub[0]["args"] == {"segs": [1]}


def test_jobs_start_rejects_disabled_account():
    _make_task("T9708")
    with patch.object(jobmod, "scheduler", _Sched()):
        with pytest.raises(OranteError) as ei:
            jobmod.start("regenerate", "T9708", segs=[1], account_id="A973")
    assert "停用" in ei.value.message


def test_jobs_start_rejects_two_accounts_on_same_task():
    _make_task("T9709")
    busy = _Sched([{"task_id": "T9709", "kind": "generate", "account_id": "A971", "status": "running"}])
    with patch.object(jobmod, "scheduler", busy):
        with pytest.raises(OranteError) as ei:
            jobmod.start("regenerate", "T9709", segs=[1], account_id="A972")
    assert "正在用" in ei.value.message
    # 同一个账号照旧排队
    with patch.object(jobmod, "scheduler", busy):
        jobmod.start("regenerate", "T9709", segs=[1])
    assert busy.sub and busy.sub[-1]["account_id"] == "A971"
    # 反过来：临时换 Grok 的重生在跑，任务自己的账号也不能同时开生成
    busy2 = _Sched([{"task_id": "T9709", "kind": "regenerate", "account_id": "A972", "status": "running"}])
    with patch.object(jobmod, "scheduler", busy2):
        with pytest.raises(OranteError):
            jobmod.start("generate", "T9709")


def test_ui_wiring():
    js = (ROOT / "ui" / "js" / "parts" / "regen-acc.js").read_text(encoding="utf-8")
    assert "window.RegenAccPick" in js and "snap(" in js
    idx = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert idx.index("parts/regen-acc.js") < idx.index("panels/generate.js")
    rv = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    assert "regen-acc-pick" in rv and "args.account_id = regenAcc.value" in rv
    gj = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    assert "regen-acc-pick" in gj and "args.account_id = redoAcc.value" in gj
    import json
    for lang in ("zh", "en"):
        d = json.loads((ROOT / "orante" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        for k in ("regen.acc.title", "regen.acc.same", "regen.acc.dur", "regen.acc.dur_part",
                  "regen.acc.dur_same", "regen.acc.hint"):
            assert k in d, (lang, k)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
