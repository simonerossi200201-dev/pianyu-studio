# -*- coding: utf-8 -*-
"""规格 73：批量完成预检/串行执行；礼花 UI 走 node test_spec73.js。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec73-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import review  # noqa: E402
from orante.scheduler import Job, scheduler  # noqa: E402

db.init()
load_all_services()


def test_backups_exist():
    for rel in (
        "ui/js/panels/review.js.bak-0920cele",
        "ui/css/app.css.bak-0920cele",
        "ui/js/panels/tasks.js.bak-0920cele",
        "orante/services/review.py.bak-0920cele",
        "orante/server.py.bak-0920cele",
        "orante/i18n/zh.json.bak-0920cele",
    ):
        assert (ROOT / rel).is_file(), rel


def _ensure_account():
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()))


def _put_clip(d: Path, tid: str, s: int) -> str:
    rel = f"clips/{tid}-s{s:03d}-p01.mp4"
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 240)
    return rel


def _make_task(tid: str, n: int = 4, missing=(), fresh=(), name: str = "") -> tuple:
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix=f"orante-task-73-{tid}-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
    img = task_dir / "images" / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9" + b"x" * 40)
    images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": sha1_file(img),
        "role": "主体", "status": "ok", "desc": "", "voice": "",
        "eye": "闭眼", "scene": "常规", "measure": {},
        "origin": "local", "src": str(img),
    }]
    miss = set(missing)
    fr = set(fresh)
    segs = []
    for i in range(1, n + 1):
        takes = []
        st = "collected"
        if i not in miss:
            takes = [{"file": _put_clip(task_dir, tid, i)}]
        else:
            st = "planned"
        sg = {
            "s": i, "text": f"Stay #{i}", "duration": 6,
            "image": "p01", "role": "主体", "shot": "",
            "prompt_final": f"FINAL-{i}",
            "status": st, "gen_id": None, "takes": takes, "error": None,
        }
        if i in fr:
            sg["fresh"] = "2026-09-20 12:00:00"
        segs.append(sg)
    m = Manifest.new(tid, name or tid, "flow", "A001", "long", "copy")
    m.images = images
    m.segments = segs
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, name=?, updated=? WHERE id=?",
               (str(task_dir), "collected", name or tid, db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, name or tid, "flow", "A001", "long", "copy", str(task_dir / "images"),
             "", "collected", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_i18n_keys():
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k, v in {
        "review.finish_tip_missing_ids": "还缺 {n} 段（{ids}）",
        "review.finish_tip_fresh": "有 {n} 段是刚重生的，还没复查",
        "review.finish_tip_job": "正在生成，先停下来",
        "review.finish_tip_ok": "{n} 段全收齐",
        "review.confetti_title_n": "完成 {n} 个任务",
        "tasks.finish_sel": "完成选中的 {n} 个任务",
        "tasks.finish_dlg.go": "完成这 {n} 个",
    }.items():
        assert zh.get(k) == v, (k, zh.get(k))


def test_preview_splits_three_tasks():
    ok_id, _, _ = _make_task("T7301", n=4, name="认真-全收齐")
    miss_id, _, _ = _make_task("T7302", n=4, missing=(2, 4), name="认真-缺片")
    fresh_id, _, _ = _make_task("T7303", n=4, fresh=(1, 3), name="认真-新片")

    prev = review.finish_preview([ok_id, miss_id, fresh_id])
    assert prev["n_ready"] == 1, prev
    assert prev["n_blocked"] == 2, prev
    assert prev["ready"][0]["tid"] == ok_id
    assert "全收齐" in prev["ready"][0]["reason"]

    by = {x["tid"]: x for x in prev["blocked"]}
    miss = by[miss_id]
    assert "还缺" in miss["reason"]
    assert "s002" in miss["reason"] and "s004" in miss["reason"]
    # 理由来自 finishBlockers 同一套键
    chk = review.finish_check(miss_id, apply=False)
    assert miss["reason"] == chk["reason"]
    assert miss["reason"] == review._finish_reason(chk["blockers"])

    fresh = by[fresh_id]
    assert "重生" in fresh["reason"] or "复查" in fresh["reason"]
    assert fresh["reason"] == review.finish_check(fresh_id, apply=False)["reason"]


def test_batch_only_finishes_ready():
    ok_id, _, _ = _make_task("T7311", n=3, name="可完成")
    miss_id, _, _ = _make_task("T7312", n=3, missing=(1, 2), name="缺片")
    st_before = taskmod.get(miss_id)["status"]
    res = review.finish_batch([ok_id, miss_id])
    assert res["n"] == 1
    assert res["celebrate"] is True
    assert [x["tid"] for x in res["done"]] == [ok_id]
    assert taskmod.get(ok_id)["status"] == "finished"
    assert taskmod.get(miss_id)["status"] == st_before


def test_active_job_blocked():
    tid, _, _ = _make_task("T7321", n=3, name="在跑")
    j = Job("J73live", tid, "generate", "A001", lambda job: None, {})
    j.status = "running"
    scheduler.jobs[j.id] = j
    try:
        chk = review.finish_check(tid, apply=False)
        assert chk["ok"] is False
        assert chk["blockers"]["job"] is True
        assert "正在生成" in chk["reason"]
        prev = review.finish_preview([tid])
        assert prev["n_ready"] == 0
        assert prev["n_blocked"] == 1
        assert "正在生成" in prev["blocked"][0]["reason"]
    finally:
        scheduler.jobs.pop(j.id, None)


def test_serial_second_error_third_still_runs():
    a, _, _ = _make_task("T7331", n=2, name="一")
    b, _, _ = _make_task("T7332", n=2, name="二")
    c, _, _ = _make_task("T7333", n=2, name="三")
    orig = review.taskmod.set_status
    n = {"i": 0}

    def boom(task_id, status, force=False):
        if status == "finished":
            n["i"] += 1
            if n["i"] == 2:
                raise RuntimeError("boom-2")
        return orig(task_id, status, force)

    with patch.object(review.taskmod, "set_status", side_effect=boom):
        res = review.finish_batch([a, b, c])
    assert n["i"] == 3
    done_ids = [x["tid"] for x in res["done"]]
    fail_ids = [x["tid"] for x in res["failed"]]
    assert a in done_ids
    assert c in done_ids
    assert b in fail_ids
    assert "boom-2" in (res["failed"][0].get("error") or "")
    assert taskmod.get(a)["status"] == "finished"
    assert taskmod.get(c)["status"] == "finished"
    assert taskmod.get(b)["status"] == "collected"


def test_zero_success_no_celebrate():
    miss_id, _, _ = _make_task("T7341", n=3, missing=(1,), name="全不行")
    res = review.finish_batch([miss_id])
    assert res["n"] == 0
    assert res["celebrate"] is False
    assert taskmod.get(miss_id)["status"] == "collected"


def test_batch_path_calls_finish_check():
    src = (ROOT / "orante" / "services" / "review.py").read_text(encoding="utf-8")
    i_prev = src.index("def finish_preview")
    i_batch = src.index("def finish_batch")
    i_end = src.index("def reveal_path")
    prev_body = src[i_prev:i_batch]
    batch_body = src[i_batch:i_end]
    assert "finish_check(" in prev_body
    assert "finish_check(" in batch_body
    # 预检/批量自己没有另写缺片/标记判定
    assert "isMissing" not in prev_body
    assert "seg.get(\"mark\")" not in prev_body
    assert "seg.get(\"mark\")" not in batch_body


def test_routes_and_menu():
    from orante import server as servermod
    paths = {getattr(r, "path", "") for r in servermod.app.routes}
    assert "/api/tasks/finish-preview" in paths
    assert "/api/tasks/finish-batch" in paths
    js = (ROOT / "ui" / "js" / "panels" / "tasks.js").read_text(encoding="utf-8")
    assert "openFinishSel" in js
    assert "tasks.finish_sel" in js
    assert "finish-preview" in js
    rev = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    assert "CELEBRATE_MS = 4800" in rev
    assert "PARTICLE_CAP = 1100" in rev
    assert "quadraticCurveTo" in rev
    assert "window.ReviewCelebrate" in rev


def test_node_confetti():
    script = ROOT / "tools" / "test_spec73.js"
    r = subprocess.run(
        ["node", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    out = r.stdout or ""
    assert "spec73 confetti ok" in out
