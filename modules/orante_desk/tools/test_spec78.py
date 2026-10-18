# -*- coding: utf-8 -*-
"""规格 78：描述语以用户改的为准，自动写不许盖回去。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec78-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import describe, expand  # noqa: E402
from orante.services import images as imgmod  # noqa: E402

db.init()

MEASURE = {
    "shot_size": "中景", "facing": "正面", "room_left": "中", "room_right": "中",
    "headroom": "中", "depth": "中", "people": 1, "crowd": False,
    "seated": False, "level": "平视",
}


class FakeJob:
    def __init__(self, task_id="T7801"):
        self.task_id = task_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J78"
        self.progress = {}

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        pass

    def set_progress(self, **kw) -> None:
        self.progress.update(kw)


class CountingGemini:
    def __init__(self):
        self.n = 0

    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        self.n += 1
        p = Path(image_path).stem
        return (
            f"自动描述{p}【闭眼|常规】\n"
            "【音色】白人|女|中年|清亮|美国口音\n"
            'POSE_JSON={"head":"正面","eyes":"看听众","hands":"自然",'
            '"body":"站立","expr":"平静","text":"正面站立"}'
        )


def _jpeg(path: Path, color=(40, 80, 120), size=(48, 64)):
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, color).save(path, "JPEG", quality=90)


def _ins_acc(aid="Aflow"):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "t-flow", "flow", 9378, aid, "", "", "9:16", 5, 1, "{}", db.now()))


def _make(tid="T7801", n=3, descs=None):
    _ins_acc()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-78-"))
    (task_dir / "images").mkdir()
    recs = []
    for i in range(1, n + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _jpeg(fp, (20 + i * 10, 40, 80))
        recs.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
            "role": "主体", "status": "ok",
            "desc": (descs or {}).get(p, ""), "voice": "",
            "eye": "闭眼", "scene": "常规",
            "measure": {}, "origin": "local", "src": str(fp),
        })
    m = Manifest.new(tid, "规格78", "flow", "Aflow", "short", "hello")
    m.images = recs
    m.segments = [{
        "s": 1, "text": "hi", "duration": 6, "image": "p01",
        "role": "主体", "shot": "", "prompt_final": "", "status": "planned",
        "gen_id": None, "takes": [], "error": None,
    }]
    m.save(task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "规格78", "flow", "Aflow", "short", "hello", str(task_dir / "images"),
         "", "prepared", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_backups_exist():
    for rel in (
        "ui/js/panels/gemini.js.bak-0920userwins",
        "ui/css/app.css.bak-0920userwins",
        "orante/i18n/zh.json.bak-0920userwins",
        "orante/services/describe.py.bak-0920userwins",
        "orante/services/images.py.bak-0920userwins",
        "orante/services/expand.py.bak-0920userwins",
    ):
        assert (ROOT / rel).is_file(), rel


def test_patch_by_user_sets_manual():
    im = {"p": "p01", "desc": "旧", "voice": ""}
    imgmod.patch(im, {"desc": "手改"}, by="user")
    assert im["desc"] == "手改"
    assert im.get("desc_manual") is True

    im2 = {"p": "p02", "desc": "旧", "voice": ""}
    imgmod.patch(im2, {"desc": "自动写的"})
    assert im2["desc"] == "自动写的"
    assert not im2.get("desc_manual")

    tid, d, m = _make("T78patch", n=1)
    r = describe.update_image(tid, "p01", {"desc": "界面手改", "by": "user"})
    assert r.get("desc_manual") is True
    m2 = Manifest.load(d)
    assert m2.image("p01").get("desc_manual") is True

    r2 = describe.update_image(tid, "p01", {"desc": "不是手改"})
    assert not r2.get("desc_manual") or r2.get("desc_manual") is True
    # 不带 by 不许把已有的手改清掉，也不新置。上面已经是 true，应仍为 true。
    m3 = Manifest.load(d)
    assert m3.image("p01").get("desc_manual") is True


def test_describe_one_skips_manual_no_gemini():
    tid, d, m = _make("T78one", n=1, descs={"p01": "我手写的"})
    im = m.image("p01")
    im["desc_manual"] = True
    g = CountingGemini()
    job = FakeJob(tid)
    meas = describe.describe_one(job, m, d, im, gemini=g, save=False)
    assert g.n == 0
    assert im["desc"] == "我手写的"
    assert any("跳过" in ln for ln in job.lines)
    assert "p01" in "".join(job.lines)
    assert meas == (im.get("measure") or {})


def test_batch_skips_one_manual_names_it():
    tid, d, m = _make("T78batch", n=3, descs={"p02": "手改p02"})
    m.image("p02")["desc_manual"] = True
    m.save(d)
    g = CountingGemini()
    job = FakeJob(tid)
    with patch("orante.services.describe.ai.client", lambda job, **kw: g), \
         patch("orante.services.describe.aikeys.list_all",
               lambda enabled_only=True: [{"id": "k1"}]), \
         patch("orante.services.describe.ai.workers", lambda n: 2), \
         patch("orante.services.describe.ai.calls", lambda: 0), \
         patch("orante.services.describe.lens.measure", lambda *a, **k: dict(MEASURE)), \
         patch("orante.services.describe.broll.judge",
               lambda *a, **k: {"is_broll": False, "reason": ""}):
        res = describe.describe(job)
    assert res["described"] == 2
    assert res.get("skipped_manual") == ["p02"]
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] == "手改p02"
    assert m2.image("p02").get("desc_manual") is True
    assert (m2.image("p01").get("desc") or "").strip()
    assert (m2.image("p03").get("desc") or "").strip()
    assert g.n == 2
    blob = "\n".join(job.lines)
    assert "跳过" in blob and "p02" in blob


def test_force_rewrites_manual():
    tid, d, m = _make("T78force", n=3, descs={"p02": "手改p02"})
    m.image("p02")["desc_manual"] = True
    m.save(d)
    g = CountingGemini()
    job = FakeJob(tid)
    with patch("orante.services.describe.ai.client", lambda job, **kw: g), \
         patch("orante.services.describe.aikeys.list_all",
               lambda enabled_only=True: [{"id": "k1"}]), \
         patch("orante.services.describe.ai.workers", lambda n: 3), \
         patch("orante.services.describe.ai.calls", lambda: 0), \
         patch("orante.services.describe.lens.measure", lambda *a, **k: dict(MEASURE)), \
         patch("orante.services.describe.broll.judge",
               lambda *a, **k: {"is_broll": False, "reason": ""}):
        res = describe.describe(job, force=True)
    assert res["described"] == 3
    assert res.get("skipped_manual") == []
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] != "手改p02"
    assert not m2.image("p02").get("desc_manual")
    assert g.n == 3


def test_copy_desc_overrides_manual_and_marks_target():
    tid, d, m = _make("T78copy", n=3, descs={"p01": "源描述", "p02": "手改目标", "p03": "另一张"})
    m.image("p02")["desc_manual"] = True
    m.save(d)
    r = imgmod.copy_desc(tid, "p01", ["p02", "p03"], ["desc"])
    assert r["n"] == 2
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] == "源描述"
    assert m2.image("p02").get("desc_manual") is True
    assert m2.image("p03")["desc"] == "源描述"
    assert m2.image("p03").get("desc_manual") is True
    assert m2.image("p01")["desc"] == "源描述"
    olds = {c["p"]: c for c in r["changed"]}
    assert olds["p02"]["desc_old"] == "手改目标"
    assert olds["p02"].get("desc_manual_old") is True


def test_apply_new_file_keeps_manual_desc_and_marks_stale():
    tid, d, m = _make("T78swap", n=1, descs={"p01": "我手写的别删"})
    im = m.image("p01")
    im["desc_manual"] = True
    m.save(d)
    pick = d / "images" / "_pick.jpg"
    _jpeg(pick, (200, 10, 10))
    job = FakeJob(tid)
    n_auto = {"n": 0}

    def _count(*a, **k):
        n_auto["n"] += 1
        return False

    with patch("orante.services.expand._auto_describe", _count), \
         patch("orante.services.expand.taskmod.emit_seg_changed"), \
         patch("orante.services.expand.taskmod.remember_counts", return_value={}):
        expand._apply_new_file(
            job, m, d, im, "p01", pick,
            auto=False, ref_p="p01", pick_vi=1, pick_prompt="x")
    assert im["desc"] == "我手写的别删"
    assert im.get("desc_stale") is True
    assert im.get("desc_manual") is True
    assert n_auto["n"] == 0
    assert any("手改" in ln for ln in job.lines)


def test_apply_new_file_clears_non_manual():
    tid, d, m = _make("T78swap2", n=1, descs={"p01": "自动的可以清"})
    im = m.image("p01")
    pick = d / "images" / "_pick2.jpg"
    _jpeg(pick, (10, 200, 10))
    job = FakeJob(tid)
    n_auto = {"n": 0}

    def _count(*a, **k):
        n_auto["n"] += 1
        return True

    with patch("orante.services.expand._auto_describe", _count), \
         patch("orante.services.expand.taskmod.emit_seg_changed"), \
         patch("orante.services.expand.taskmod.remember_counts", return_value={}):
        expand._apply_new_file(
            job, m, d, im, "p01", pick,
            auto=False, ref_p="p01", pick_vi=1, pick_prompt="x")
    assert im["desc"] == ""
    assert not im.get("desc_stale")
    assert n_auto["n"] == 1


def test_restore_auto_clears_manual_flag():
    tid, d, m = _make("T78restore", n=1, descs={"p01": "手改"})
    describe.update_image(tid, "p01", {"desc": "手改", "by": "user"})
    m1 = Manifest.load(d)
    assert m1.image("p01").get("desc_manual") is True
    r = describe.update_image(tid, "p01", {"desc_manual": False})
    assert not r.get("desc_manual")
    m2 = Manifest.load(d)
    assert not m2.image("p01").get("desc_manual")
    assert m2.image("p01")["desc"] == "手改"


def test_upscale_path_does_not_clear_desc():
    body = (ROOT / "orante" / "services" / "expand.py").read_text(encoding="utf-8")
    fn = body.split("def _apply_upscale_file", 1)[1].split("\ndef ", 1)[0]
    assert 'im["desc"] = ""' not in fn
    assert "im['desc'] = ''" not in fn
    assert "_auto_describe" not in fn


def test_gemini_js_has_draft_binding():
    js = (ROOT / "ui" / "js" / "panels" / "gemini.js").read_text(encoding="utf-8")
    assert "window.GeminiDescDrafts" in js
    assert 'draftOf(im,\'desc\')' in js or 'draftOf(im,"desc")' in js
    assert "desc_manual" in js
    assert "gemini.ctx.restore_auto" in js
    assert "forceManual" in js
    assert "orante.desc.draft." in js
