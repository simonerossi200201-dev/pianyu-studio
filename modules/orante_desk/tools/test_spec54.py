# -*- coding: utf-8 -*-
"""规格 54：每个任务自己选生成方式。先设 ORANTE_DATA。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-54-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import pipeline, prepare, tails  # noqa: E402

db.init()


class FakeJob:
    def __init__(self, task_id):
        self.task_id = task_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J54"

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        pass


def _seg(s, role="anchor", status="planned", image="", **kw):
    d = {
        "s": s, "text": f"台词 {s}", "duration": 6, "image": image or f"p{s:02d}",
        "role": role, "shot": "", "prompt_final": "", "status": status,
        "gen_id": None, "takes": [], "error": None,
    }
    d.update(kw)
    return d


def _acc(aid, name, engine, port):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, name, engine, port, aid, "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _ensure_accs():
    _acc("A54g", "g54", "grok", 9411)
    _acc("A54f", "f54", "flow", 9412)


def _insert_task(tid, engine, gen_mode="", name="测"):
    _ensure_accs()
    aid = "A54g" if engine == "grok" else "A54f"
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,gen_mode,status,dir,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, name, engine, aid, "long", "hello world and more words here", "src",
         "", gen_mode, "draft", None, 0, db.now(), db.now()),
    )


def _fake_fetch(job, t, d):
    return [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "a" * 40, "role": "主体",
        "status": "ok", "desc": "", "voice": "", "eye": "", "scene": "",
        "measure": {}, "origin": "download", "src": "",
    }]


def _fake_screen(job, d, images):
    return images


def _fake_split(job, t, m):
    return {"rule": {}}, [_seg(1, image="", role="anchor")]


def _do_prepare(tid):
    batch = TEST_DATA / "batch"
    batch.mkdir(exist_ok=True)
    job = FakeJob(tid)
    with patch("orante.services.images.fetch", _fake_fetch), \
         patch("orante.services.broll.screen", _fake_screen), \
         patch("orante.services.split.split_task", _fake_split):
        prepare.prepare(job, batch_dir=str(batch), batch_id="B54")
    t = taskmod.get(tid)
    return Manifest.load(Path(t["dir"]))


def test_default_mode_for_engine():
    assert pipeline.default_mode_for_engine("grok") == "first_frame"
    assert pipeline.default_mode_for_engine("flow") == "anchor_bridge"
    assert pipeline.default_mode_for_engine("GROK") == "first_frame"
    assert pipeline.resolve_gen_mode({"engine": "grok", "gen_mode": ""}) == "first_frame"
    assert pipeline.resolve_gen_mode({"engine": "flow", "gen_mode": None}) == "anchor_bridge"
    assert pipeline.resolve_gen_mode({"engine": "grok", "gen_mode": "anchor_bridge"}) == "anchor_bridge"
    assert pipeline.parse_gen_mode("跟随引擎") == ""
    assert pipeline.parse_gen_mode("逐段首帧") == "first_frame"


def test_prepare_follow_engine():
    _insert_task("T5401", "grok", gen_mode="")
    _insert_task("T5402", "flow", gen_mode="")
    mg = _do_prepare("T5401")
    mf = _do_prepare("T5402")
    assert mg.mode == "first_frame", mg.mode
    assert mf.mode == "anchor_bridge", mf.mode


def test_prepare_ignores_global_last_used():
    pipeline.remember_mode("anchor_bridge")
    _insert_task("T5403", "grok", gen_mode="")
    m = _do_prepare("T5403")
    assert m.mode == "first_frame", m.mode
    assert pipeline.last_used_mode() == "anchor_bridge"


def test_explicit_mode_survives_account_swap():
    _insert_task("T5404", "grok", gen_mode="anchor_bridge")
    m = _do_prepare("T5404")
    assert m.mode == "anchor_bridge"
    t = taskmod.get("T5404")
    res = taskmod.upsert_rows([{
        "id": "T5404",
        "name": t["name"],
        "copy": t["copy"],
        "image_source": t["image_source"],
        "type": t["type"],
        "account": "f54",
    }])
    assert "T5404" in res["saved"], res
    t2 = taskmod.get("T5404")
    assert t2["engine"] == "flow"
    assert pipeline.parse_gen_mode(t2.get("gen_mode")) == "anchor_bridge"
    m2 = Manifest.load(Path(t2["dir"]))
    assert m2.mode == "anchor_bridge"


def test_follow_mode_tracks_account_swap():
    _insert_task("T5405", "grok", gen_mode="")
    m = _do_prepare("T5405")
    assert m.mode == "first_frame"
    t = taskmod.get("T5405")
    res = taskmod.upsert_rows([{
        "id": "T5405",
        "name": t["name"],
        "copy": t["copy"],
        "image_source": t["image_source"],
        "type": t["type"],
        "account": "f54",
    }])
    assert "T5405" in res["saved"], res
    t2 = taskmod.get("T5405")
    assert t2["engine"] == "flow"
    assert pipeline.parse_gen_mode(t2.get("gen_mode")) == ""
    m2 = Manifest.load(Path(t2["dir"]))
    assert m2.mode == "anchor_bridge"


def test_set_mode_hint_and_enqueue():
    m = Manifest.new("T5412", "测", "grok", "A54g", "long", "hello " * 20)
    m.mode = "anchor_bridge"
    segs = []
    for i in range(1, 13):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    pipeline.rebuild_bridge_links(m.segments)
    anchors = [sg for sg in segs if sg["role"] == "anchor"]
    bridges = [sg for sg in segs if sg["role"] == "bridge"]
    assert len(anchors) == 6 and len(bridges) == 6, (len(anchors), len(bridges))
    for sg in anchors:
        sg["status"] = "collected"
    for sg in bridges:
        sg["status"] = "planned"
        sg["wait_on"] = 1
        sg["wait_why"] = "等 s001 收片"
    before = [sg["status"] for sg in segs]
    r = pipeline.set_mode(m, "first_frame")
    after = [sg["status"] for sg in segs]
    assert after == before, (before, after)
    for sg in bridges:
        assert pipeline.can_enqueue(m, sg), sg
    hint = r.get("hint") or ""
    assert "6 段单图首帧" in hint and "保留" in hint, hint
    assert "6 段首尾帧接片" in hint and "配图" in hint, hint


def test_set_mode_back_to_anchor_hint():
    m = Manifest.new("T5413", "测", "flow", "A54f", "long", "hello " * 20)
    m.mode = "first_frame"
    segs = []
    for i in range(1, 13):
        segs.append(_seg(i, image=f"p{((i - 1) % 7) + 1:02d}"))
    tails.assign_roles(segs)
    m.segments = segs
    r = pipeline.set_mode(m, "anchor_bridge")
    hint = r.get("hint") or ""
    assert "6 段" in hint and "接片" in hint, hint
    assert r["mode"] == "anchor_bridge"


def test_running_mode_change_blocked():
    _insert_task("T5417", "flow", gen_mode="")
    m = _do_prepare("T5417")
    assert m.mode == "anchor_bridge"
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,finished,log,error)"
        " VALUES(?,?,?,?,?,?,?,?)",
        ("J5417", "T5417", "generate", "running", db.now(), None, "", ""),
    )
    assert taskmod.task_is_running("T5417")
    with pytest.raises(OranteError, match="T5417 正在跑"):
        taskmod.apply_gen_mode("T5417", "first_frame")
    m2 = Manifest.load(Path(taskmod.get("T5417")["dir"]))
    assert m2.mode == "anchor_bridge"


def test_preview_does_not_change():
    _insert_task("T5418", "flow", gen_mode="")
    _do_prepare("T5418")
    r = taskmod.apply_gen_mode("T5418", "first_frame", preview=True)
    assert r.get("preview") is True
    assert "单图首帧" in (r.get("hint") or "") or "首尾帧接片" in (r.get("hint") or "")
    m2 = Manifest.load(Path(taskmod.get("T5418")["dir"]))
    assert m2.mode == "anchor_bridge"


def test_gen_mode_column_exists():
    cols = {r["name"] for r in db.conn().execute("PRAGMA table_info(tasks)").fetchall()}
    assert "gen_mode" in cols
