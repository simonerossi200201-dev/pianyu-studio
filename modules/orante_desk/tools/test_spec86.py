# -*- coding: utf-8 -*-
"""规格 86：简单任务（仅无文案）。测试前设 ORANTE_DATA。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-86-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orante" / "legacy"))

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import simple86, split as splitmod  # noqa: E402
import flow_shots  # noqa: E402

db.init()


class FakeJob:
    def __init__(self, task_id):
        self.task_id = task_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J86"

    def log(self, msg: str) -> None:
        self.lines.append(str(msg))

    def check(self) -> None:
        pass


def _jpeg(fp: Path, color=(40, 80, 120)):
    fp.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(fp, "JPEG")


def _sha1(fp: Path) -> str:
    import hashlib
    return hashlib.sha1(fp.read_bytes()).hexdigest()


def _ins_acc(aid="Aflow", engine="flow"):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "t-" + engine, engine, 9000 + (sum(ord(c) for c in aid) % 500), aid, "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _make(tid, *, n=1, copy="", descs=None, roles=None, engine="flow",
          duration_pref="", status="draft", account=None):
    aid = account or ("Aflow" if engine == "flow" else "Agrok")
    _ins_acc(aid, engine)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-86-"))
    (task_dir / "images").mkdir(exist_ok=True)
    recs = []
    for i in range(1, n + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _jpeg(fp, (20 + i * 10, 40, 80))
        role = (roles or {}).get(p, "主体")
        recs.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": _sha1(fp),
            "role": role, "status": "ok",
            "desc": (descs or {}).get(p, ""), "voice": "",
            "eye": "闭眼", "scene": "常规",
            "measure": {}, "origin": "local", "src": str(fp),
        })
    m = Manifest.new(tid, tid, engine, aid, "short", copy)
    m.images = recs
    m.save(task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, tid, engine, aid, "short", copy, str(task_dir / "images"),
         str(duration_pref or ""), status, str(task_dir), "", 0, db.now(), db.now()),
    )
    return tid, task_dir, Manifest.load(task_dir)


def _split_now(tid, task_dir):
    t = taskmod.get(tid)
    m = Manifest.load(task_dir)
    job = FakeJob(tid)
    rule, segs = splitmod.split_task(job, t, m)
    m.split = rule
    m.segments = segs
    m.save(task_dir)
    return rule, segs, job


def _arrange(tid, task_dir):
    t = taskmod.get(tid)
    m = Manifest.load(task_dir)
    job = FakeJob(tid)
    ok = simple86.auto_arrange_if_simple(job, t, m, task_dir)
    if ok:
        taskmod.set_status(tid, "arranged")
    return ok, Manifest.load(task_dir), job


def test_no_copy_one_image_duration_15_arranged():
    tid, d, m = _make("T8601", n=1, copy="", duration_pref="15",
                      engine="grok", account="Agrok")
    rule, segs, job = _split_now(tid, d)
    assert len(segs) == 1
    assert segs[0]["text"] == ""
    assert segs[0]["image"] == "p01"
    assert int(segs[0]["duration"]) == 15
    assert segs[0].get("image_locked") is True
    ok, m2, job2 = _arrange(tid, d)
    assert ok
    assert taskmod.get(tid)["status"] == "arranged"
    blob = "\n".join(job.lines + job2.lines)
    assert ("跳过编排" in blob) or ("自动排" in blob)


def test_no_copy_three_images_locked():
    tid, d, m = _make("T8602", n=3, copy="")
    rule, segs, job = _split_now(tid, d)
    assert [s["image"] for s in segs] == ["p01", "p02", "p03"]
    assert all(s.get("image_locked") for s in segs)


def test_no_copy_default_duration_flow_and_grok():
    tid_f, d_f, _ = _make("T8603f", n=1, copy="", duration_pref="", engine="flow")
    _, segs_f, _ = _split_now(tid_f, d_f)
    assert int(segs_f[0]["duration"]) == 8
    tid_g, d_g, _ = _make("T8603g", n=1, copy="", duration_pref="",
                          engine="grok", account="Agrok")
    _, segs_g, _ = _split_now(tid_g, d_g)
    assert int(segs_g[0]["duration"]) == 10


def test_with_copy_single_subject_not_auto_arranged():
    """有文案 + 单张主体图：与 86 前一致，不短路编排。"""
    tid, d, m = _make("T8604", n=1, copy="这是一段要说的台词，用来拆段。", status="prepared")
    rule, segs, job = _split_now(tid, d)
    assert segs and any((s.get("text") or "").strip() for s in segs)
    # 模拟准备后状态（prepare 在 auto_arrange 失败时保持 prepared）
    taskmod.set_status(tid, "prepared")
    ok, m2, job2 = _arrange(tid, d)
    assert ok is False
    assert taskmod.get(tid)["status"] == "prepared"
    assert not any(s.get("image_locked") for s in (m2.segments or []))
    tr = getattr(m2, "track", None) or {}
    assert not (isinstance(tr, dict) and tr.get("simple86"))
    assert simple86.is_simple(taskmod.get(tid), m2) is False


def test_with_copy_three_subjects_not_auto_arranged():
    tid, d, m = _make("T8605", n=3, copy="第一句台词。第二句台词。第三句台词。第四句台词。")
    _split_now(tid, d)
    ok, m2, job2 = _arrange(tid, d)
    assert ok is False


def test_broll_included_in_no_copy_split():
    """没文案：空镜也各成一段，全是空镜也能拆、也能自动排好。"""
    tid, d, m = _make("T8606", n=3, copy="",
                      roles={"p01": "主体", "p02": "空镜", "p03": "主体"})
    rule, segs, job = _split_now(tid, d)
    assert [s["image"] for s in segs] == ["p01", "p02", "p03"]
    ok, m2, _ = _arrange(tid, d)
    assert ok
    assert [s["image"] for s in m2.segments] == ["p01", "p02", "p03"]

    tid2, d2, _ = _make("T8606b", n=2, copy="",
                        roles={"p01": "空镜", "p02": "空镜"})
    _, segs2, _ = _split_now(tid2, d2)
    assert [s["image"] for s in segs2] == ["p01", "p02"]
    ok2, _, _ = _arrange(tid2, d2)
    assert ok2


def test_generate_blocks_no_copy_missing_desc():
    from orante.services import generate as genmod
    tid, d, m = _make("T8607", n=1, copy="", descs={"p01": ""})
    _split_now(tid, d)
    ok, m2, _ = _arrange(tid, d)
    assert ok
    bad = simple86.missing_desc_for_no_copy(taskmod.get(tid), m2)
    assert bad
    job = FakeJob(tid)
    with pytest.raises(OranteError) as ei:
        genmod.generate(job)
    full = str(ei.value) + " " + str(getattr(ei.value, "hint", "") or "")
    assert "描述语就是全部提示词" in full


def test_mute_tip_appended_when_no_copy_minimal():
    switches = {k: False for k in flow_shots.SWITCH_ALL_KEYS}
    switches["静音提示"] = True
    desc = "一位女士站在窗前。"
    tip = simple86.SILENT_DEFAULT
    out = flow_shots.build_prompt(
        desc, "", "闭眼", "常规", "", {"静音提示": tip, "silent": tip},
        switches=switches, no_copy=True,
    )
    assert out == desc + "\n" + tip


def test_mute_tip_off_equals_desc_only():
    switches = {k: False for k in flow_shots.SWITCH_ALL_KEYS}
    switches["静音提示"] = False
    desc = "一位女士站在窗前。"
    out = flow_shots.build_prompt(
        desc, "", "闭眼", "常规", "", {},
        switches=switches, no_copy=True,
    )
    assert out == desc


def test_with_copy_does_not_append_mute():
    switches = {k: False for k in flow_shots.SWITCH_ALL_KEYS}
    switches["静音提示"] = True
    desc = "一位女士站在窗前。"
    tip = simple86.SILENT_DEFAULT
    out = flow_shots.build_prompt(
        desc, "", "闭眼", "常规", "你好啊", {"静音提示": tip},
        switches=switches, no_copy=False,
    )
    assert tip not in out


def test_use_current_track_ok_and_rejects_missing():
    tid, d, m = _make("T8610", n=2, copy="有台词的任务用来测配图。", status="described")
    _split_now(tid, d)
    m = Manifest.load(d)
    for i, sg in enumerate(m.segments):
        sg["image"] = f"p{min(i + 1, 2):02d}"
    m.save(d)
    taskmod.set_status(tid, "described")
    with patch("orante.jobs.start", return_value=object()) as st:
        res = simple86.use_current_track(tid, start_generate=True)
    assert res["status"] == "arranged"
    assert res.get("started") is True
    assert taskmod.get(tid)["status"] == "arranged"
    st.assert_called()

    tid2, d2, m2 = _make("T8611", n=2, copy="另一段台词。", status="described")
    _split_now(tid2, d2)
    m2 = Manifest.load(d2)
    # 强制两段：一段有图一段没有
    m2.segments = [
        {"s": 1, "text": "a", "duration": 6, "image": "p01", "status": "planned"},
        {"s": 2, "text": "b", "duration": 6, "image": "", "status": "planned"},
    ]
    m2.save(d2)
    with pytest.raises(OranteError):
        simple86.use_current_track(tid2, start_generate=False)


def test_migrate_copy_to_desc_and_rejects():
    tid, d, m = _make("T8612", n=2, copy="原本文案放这里。",
                      descs={"p01": "", "p02": ""}, status="prepared")
    _split_now(tid, d)
    with pytest.raises(OranteError):
        simple86.migrate_copy_to_desc([tid], confirm=False)
    res = simple86.migrate_copy_to_desc([tid], confirm=True)
    assert tid in res["ok"]
    m2 = Manifest.load(d)
    assert not simple86.has_copy(taskmod.get(tid))
    for im in simple86.subject_images(m2):
        assert im.get("desc") == "原本文案放这里。"
        assert im.get("desc_manual") is True
    assert taskmod.get(tid)["status"] == "arranged"
    assert all(s.get("image_locked") for s in m2.segments)

    tid3, d3, m3 = _make("T8613", n=1, copy="有成片。", status="prepared")
    _split_now(tid3, d3)
    m3 = Manifest.load(d3)
    m3.segments[0]["takes"] = [{"id": "x"}]
    m3.save(d3)
    res3 = simple86.migrate_copy_to_desc([tid3], confirm=True)
    assert tid3 not in res3["ok"]
    assert any(x.get("id") == tid3 for x in res3.get("skipped", []))


def test_with_copy_split_still_works():
    tid, d, m = _make("T8614", n=2, copy="今天天气很好，我们一起出去走走吧。明天还要开会。")
    rule, segs, job = _split_now(tid, d)
    assert segs
    assert any((s.get("text") or "").strip() for s in segs)
    assert not any(s.get("image_locked") for s in segs)


def test_migrate_bad_duration_keeps_copy_0922():
    """0922：Flow 时长 15 迁移失败时，库里的文案不能被清掉；改成 8 后能重做。"""
    tid, d, m = _make("T8690", n=1, copy="迁移失败也不能丢的文案。", status="prepared")
    _split_now(tid, d)
    db.run("UPDATE tasks SET duration_pref=? WHERE id=?", ("15", tid))
    res = simple86.migrate_copy_to_desc([tid], confirm=True)
    assert tid not in res["ok"]
    err = [e for e in res["errors"] if e.get("id") == tid]
    assert err and "Flow" in err[0]["error"]
    assert taskmod.get(tid)["copy"] == "迁移失败也不能丢的文案。"
    assert taskmod.get(tid)["status"] == "prepared"
    db.run("UPDATE tasks SET duration_pref=? WHERE id=?", ("8", tid))
    res2 = simple86.migrate_copy_to_desc([tid], confirm=True)
    assert tid in res2["ok"]
    m2 = Manifest.load(d)
    assert [s["duration"] for s in m2.segments] == [8]


def test_migrate_recovers_from_manifest_copy_0922():
    """库里文案已被旧版清空、任务目录里还留着 → 仍能迁移。"""
    tid, d, m = _make("T8691", n=1, copy="只剩任务目录里有。", status="prepared")
    _split_now(tid, d)
    db.run("UPDATE tasks SET copy='' WHERE id=?", (tid,))
    res = simple86.migrate_copy_to_desc([tid], confirm=True)
    assert tid in res["ok"]
    m2 = Manifest.load(d)
    assert simple86.subject_images(m2)[0]["desc"] == "只剩任务目录里有。"


def test_classify_port_linger_waits_for_release_0922():
    from orante import main as mainmod
    calls = {"n": 0}

    def fake_free(port):
        calls["n"] += 1
        return calls["n"] > 4          # 前几次还占着，之后放掉

    with patch.object(mainmod, "_port_free", fake_free):
        kind = mainmod.classify_port(9999, retries=1, sleep=lambda s: None,
                                     fetch=lambda u: None, linger=15.0)
    assert kind == "free"
    with patch.object(mainmod, "_port_free", lambda p: False):
        kind2 = mainmod.classify_port(9999, retries=1, sleep=lambda s: None,
                                      fetch=lambda u: None, linger=0.0)
    assert kind2 == "other"
