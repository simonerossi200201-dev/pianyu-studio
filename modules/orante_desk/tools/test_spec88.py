# -*- coding: utf-8 -*-
"""0923 两处改动：
  ① 准备阶段不再叫 Gemini 判空镜（图全部收下当主体）。
  ② ① 任务表的「时长」有文案也能手选：按这一档的字/词上限拆段，每段都是这个秒数。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-88-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orante" / "legacy"))

from orante import db, tasks as taskmod  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import split as splitmod, duration_edit  # noqa: E402
import flow_shots  # noqa: E402
from orante.legacy import flow_split  # noqa: E402

db.init()

WS = re.compile(r"\s+")


class FakeJob:
    def __init__(self, task_id):
        self.task_id = task_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J88"

    def log(self, msg):
        self.lines.append(str(msg))

    def check(self):
        pass


def _jpeg(fp: Path, size=(64, 64)):
    fp.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (40, 80, 120)).save(fp, "JPEG")


def _acc(aid, engine):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "t88-" + engine, engine, 9700 + (sum(ord(c) for c in aid) % 200),
         aid, "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _task(tid, *, copy="", engine="flow", duration_pref="", n_img=2, status="draft"):
    aid = "A88" + engine
    _acc(aid, engine)
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    d = Path(tempfile.mkdtemp(prefix="orante-task-88-"))
    recs = []
    for i in range(1, n_img + 1):
        p = f"p{i:02d}"
        fp = d / "images" / f"{p}.jpg"
        _jpeg(fp)
        recs.append({"p": p, "file": f"images/{p}.jpg", "role": "主体", "status": "ok",
                     "desc": "", "voice": "", "eye": "闭眼", "scene": "常规",
                     "measure": {}, "origin": "local", "src": str(fp)})
    m = Manifest.new(tid, tid, engine, aid, "short", copy)
    m.images = recs
    m.save(d)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, tid, engine, aid, "short", copy, str(d / "images"),
         str(duration_pref or ""), status, str(d), "", 0, db.now(), db.now()),
    )
    return tid, d, Manifest.load(d)


def _split(tid, d):
    t = taskmod.get(tid)
    m = Manifest.load(d)
    job = FakeJob(tid)
    rule, segs = splitmod.split_task(job, t, m)
    return rule, segs, job


CN = "今天天气不错，我们出去走走，顺便把这段文案拆开看看效果。" * 6
EN = ("Hello world, this is a longer flow copy with many words to force multi "
      "segment split behavior across several clauses, commas, and periods. ") * 4


# ---------- ② 有文案也能手选时长 ----------

def test_pref_parse():
    assert splitmod.copy_duration_pref({"duration_pref": ""}, "flow") is None
    assert splitmod.copy_duration_pref({"duration_pref": "自动"}, "grok") is None
    assert splitmod.copy_duration_pref({"duration_pref": "6"}, "flow") == 6
    assert splitmod.copy_duration_pref({"duration_pref": "15"}, "grok") == 15
    with pytest.raises(splitmod.DurationError):
        splitmod.copy_duration_pref({"duration_pref": "15"}, "flow")
    with pytest.raises(splitmod.DurationError):
        splitmod.copy_duration_pref({"duration_pref": "abc"}, "grok")


def test_grok_copy_pref_6_all_six_and_short_enough():
    tid, d, _ = _task("T8801", copy=CN, engine="grok", duration_pref="6")
    rule, segs, job = _split(tid, d)
    assert rule["rule"]["duration_pref"] == 6
    cap = int(splitmod._rule_for("grok")["dur_6s_max_chars"])
    assert segs and all(int(s["duration"]) == 6 for s in segs)
    assert all(len(s["text"]) <= cap for s in segs)
    # 文案一个字都不能丢
    assert WS.sub("", "".join(s["text"] for s in segs)) == WS.sub("", CN)


def test_flow_copy_pref_4_all_four():
    tid, d, _ = _task("T8802", copy=EN, engine="flow", duration_pref="4")
    rule, segs, job = _split(tid, d)
    assert rule["rule"]["duration_pref"] == 4
    assert segs and all(int(s["duration"]) == 4 for s in segs)
    assert WS.sub("", "".join(s["text"] for s in segs)) == WS.sub("", EN)
    # Flow 只在标点处断句：断不开的长从句照样超量，但要有一行说清楚
    cap = int(splitmod._rule_for("flow")["dur_4s_max_words"])
    if any(flow_split.word_count(s["text"]) > cap for s in segs):
        assert any("可能念不完" in ln for ln in job.lines)


def test_flow_copy_pref_4_splits_shorter_than_auto():
    """能断开的地方就该按 4 秒档断：段数不少于自动档。"""
    tid, d, _ = _task("T8802b", copy=EN, engine="flow", duration_pref="4")
    _, segs, _ = _split(tid, d)
    auto = splitmod.preview(EN, "flow")
    assert len(segs) >= len(auto)


def test_no_pref_still_auto():
    tid, d, _ = _task("T8803", copy=CN, engine="grok", duration_pref="")
    rule, segs, _ = _split(tid, d)
    assert "duration_pref" not in rule["rule"]
    auto = splitmod.preview(CN, "grok")
    assert [s["duration"] for s in segs] == [s["duration"] for s in auto]
    assert [s["text"] for s in segs] == [s["text"] for s in auto]


def test_pref_out_of_range_falls_back_to_auto():
    """换账号留下的旧值（Flow 上的 15 秒）不能把有文案的任务卡死：警告一行，按语速自动。"""
    tid, d, _ = _task("T8804", copy=EN, engine="flow", duration_pref="15")
    rule, segs, job = _split(tid, d)
    assert "duration_pref" not in rule["rule"]
    assert [s["duration"] for s in segs] == [s["duration"] for s in splitmod.preview(EN, "flow")]
    assert any("按语速自动定时长" in ln for ln in job.lines)


def test_auto_duration_follows_pref():
    t = {"duration_pref": "6", "engine": "flow"}
    assert duration_edit.auto_duration({"text": "one two three four five six seven eight nine"},
                                       "flow", t) == 6
    t2 = {"duration_pref": "", "engine": "flow"}
    assert duration_edit.auto_duration({"text": "one two three"}, "flow", t2) == 4


def test_batch_set_duration_pref_covers_copy_rows():
    tid, d, _ = _task("T8805", copy=CN, engine="grok", duration_pref="")
    r = duration_edit.set_tasks_duration_pref([tid], {"grok": 10})
    assert tid in r["updated"]
    assert (taskmod.get(tid).get("duration_pref") or "") == "10"


# ---------- ① 准备阶段不判空镜 ----------

def test_prepare_never_calls_gemini_broll():
    from orante.services import prepare as prepmod, images as imagesmod, broll as brollmod
    tid, d, _ = _task("T8806", copy="", engine="flow", duration_pref="8", n_img=2)
    batch = Path(tempfile.mkdtemp(prefix="orante-batch-88-"))

    def fake_fetch(job, t, task_dir):
        recs = []
        for i in (1, 2):
            p = f"p{i:02d}"
            fp = Path(task_dir) / "images" / f"{p}.jpg"
            _jpeg(fp)
            recs.append({"p": p, "file": f"images/{p}.jpg", "role": "主体",
                         "status": "ok", "desc": "", "voice": "", "eye": "闭眼",
                         "scene": "常规", "measure": {}, "origin": "local", "src": str(fp)})
        return recs

    def boom(*a, **k):
        raise AssertionError("准备阶段不该再判空镜")

    job = FakeJob(tid)
    with patch.object(imagesmod, "fetch", fake_fetch), patch.object(brollmod, "screen", boom):
        out = prepmod.prepare(job, batch_dir=str(batch))
    assert out["subjects"] == 2 and out["brolls"] == 0
    m = Manifest.load(Path(out["dir"]))
    assert [im["role"] for im in m.images] == ["主体", "主体"]
    assert [im["p"] for im in m.images] == ["p01", "p02"]
    assert len(m.segments) == 2 and all(int(s["duration"]) == 8 for s in m.segments)


# ---------- 切套装不再拦生成：开跑时把提示词定稿 ----------

def test_prompt_snapshot_survives_suite_switch():
    from orante.services import generate as genmod
    tid, d, m = _task("T8807", copy="", engine="flow", duration_pref="8", n_img=2)
    m = Manifest.load(d)
    m.segments = [{"s": 1, "text": "", "duration": 8, "image": "p01", "status": "planned",
                   "prompt_final": "", "takes": [], "gen_id": None},
                  {"s": 2, "text": "", "duration": 8, "image": "p02", "status": "planned",
                   "prompt_final": "", "takes": [], "gen_id": None}]
    job = FakeJob(tid)
    with patch("orante.services.prompts.build_final_prompt",
               lambda mm, sg: "套装A：" + (sg.get("image") or "")):
        n = genmod.snapshot_prompts(job, m, m.segments)
    assert n == 2
    assert [sg["prompt_final"] for sg in m.segments] == ["套装A：p01", "套装A：p02"]
    # 另一个窗口换了套装：已经定稿的段照旧用定稿那份
    with patch("orante.services.prompts.build_final_prompt",
               lambda mm, sg: "套装B：" + (sg.get("image") or "")):
        assert genmod._final_prompt(m, m.segments[1]) == "套装A：p02"
        # 但图换了（补图 / 借图）就要按新图重拼
        m.segments[1]["image"] = "p01"
        assert genmod._final_prompt(m, m.segments[1]) == "套装B：p01"


def test_prompt_snapshot_skips_locked_and_chain():
    from orante.services import generate as genmod
    tid, d, m = _task("T8808", copy="", engine="flow", duration_pref="8", n_img=1)
    m = Manifest.load(d)
    segs = [{"s": 1, "text": "", "duration": 8, "image": "p01", "status": "planned",
             "prompt_final": "手写的", "prompt_locked": True, "takes": [], "gen_id": None}]
    m.segments = segs
    job = FakeJob(tid)
    with patch("orante.services.prompts.build_final_prompt", lambda mm, sg: "不该用到"):
        assert genmod.snapshot_prompts(job, m, segs) == 0
    assert segs[0]["prompt_final"] == "手写的"
