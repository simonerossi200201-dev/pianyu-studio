# -*- coding: utf-8 -*-
"""规格 68：换图描述卡片。先设 ORANTE_DATA，FakeEngine + FakeGemini。"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec68-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.engines.base import BlockedImage  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import REGISTRY, load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import desc_cards, expand, pipeline, preset  # noqa: E402

db.init()
load_all_services()


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J68"
        self.progress = {"phase": "", "done": 0, "total": 0, "current": "", "items": []}

    def log(self, msg: str) -> None:
        self.lines.append(str(msg))

    def check(self) -> None:
        pass

    def sleep(self, sec: float) -> None:
        pass

    def set_progress(self, **kw) -> None:
        item = kw.pop("item", None)
        for k, v in kw.items():
            self.progress[k] = v
        if item is not None:
            self.progress.setdefault("items", []).append(item)


class FakeEngine:
    last = None

    def __init__(self, account=None, log=None, *, block=False):
        self.account = account or {}
        self.log = log
        self.block = block
        self.calls = []
        FakeEngine.last = self

    def connect(self):
        pass

    def close(self):
        pass

    def gen_images(self, prompt, ref=None, n=1, dest_dir=None, **kw):
        self.calls.append({"prompt": prompt, "ref": str(ref) if ref else None,
                           "dest_dir": str(dest_dir) if dest_dir else None, "kw": dict(kw)})
        if self.block:
            dest_dir = Path(dest_dir or ".")
            dest_dir.mkdir(parents=True, exist_ok=True)
            leftover = dest_dir / "blocked.jpg"
            Image.new("RGB", (16, 16), (9, 9, 9)).save(leftover, "JPEG")
            raise BlockedImage("平台拒收")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "card.jpg"
        Image.new("RGB", (64, 96), (180, 40, 40)).save(out, "JPEG", quality=90)
        return [out]


class FakeGemini:
    def ask_image(self, prompt, path, **kw):
        return "一位三十多岁东亚女性，齐肩黑发，米色西装。【闭眼|常规】\n【音色】测"


def _ins_acc(aid="Aflow", name="TIK-L1", engine="flow"):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, name, engine, 9388, aid, "", "", "9:16", 5, 1, "{}", db.now()))


def _jpeg(path: Path, color=(40, 80, 120), size=(48, 64)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)


def _sha_map(folder: Path) -> dict[str, str]:
    out = {}
    if not folder.is_dir():
        return out
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        if rel.startswith("_"):
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        out[rel] = h
    return out


def _make_task(tid, *, n_seg=3, mode="first_frame", with_tail=False):
    _ins_acc()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-68-"))
    (task_dir / "images").mkdir()
    fp = task_dir / "images" / "p03.jpg"
    _jpeg(fp, (20, 90, 40))
    images = [{
        "p": "p03", "file": "images/p03.jpg",
        "sha1": sha1_file(fp), "role": "主体", "status": "ok",
        "desc": "一位三十多岁的东亚女性，留着齐肩黑发，穿着米色西装外套站在讲台上【闭眼|常规】",
        "voice": "v1", "eye": "闭眼", "scene": "常规",
        "measure": {}, "origin": "local", "src": "p03.jpg", "pose": {},
        "expand_ref": True,
    }]
    segs = []
    for i in range(1, n_seg + 1):
        segs.append({
            "s": i, "text": f"seg {i}", "duration": 6,
            "image": "p03", "role": "主体", "shot": "",
            "prompt_final": f"old-{i}", "status": "collected", "gen_id": None,
            "takes": [], "error": None,
        })
    m = Manifest.new(tid, "规格68", "flow", "Aflow", "short", "hello")
    m.images = images
    m.segments = segs
    m.mode = mode
    if mode == "tail_chain":
        pipeline.ensure_chain(m)
        for sg in m.segments:
            if int(sg["s"]) >= 2:
                sg["image"] = "p03"
                sg["chain"] = {
                    "ref_kind": "tail",
                    "ref_from": "s%03d" % (int(sg["s"]) - 1),
                    "ref_file": "clips/_tails/s%03d.jpg" % (int(sg["s"]) - 1),
                    "desc": "接龙描述语，同一位东亚女性米色西装",
                }
        if with_tail or True:
            tails = task_dir / "clips" / "_tails"
            tails.mkdir(parents=True, exist_ok=True)
            for i in range(1, n_seg):
                _jpeg(tails / ("s%03d.jpg" % i), (90, 20, 20))
    m.save(task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "规格68", "flow", "Aflow", "short", "hello", str(task_dir / "images"),
         "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_seed_factory_on_empty():
    db.run("DELETE FROM desc_cards")
    n = desc_cards.seed_if_empty()
    assert n == 4
    rows = desc_cards.list_all()
    assert [r["name"] for r in rows] == ["正脸角度", "侧脸角度", "人物近景", "人物中景"]
    assert all(r["builtin"] == 1 for r in rows)
    assert [r["id"] for r in rows] == ["C001", "C002", "C003", "C004"]
    assert "正对镜头" in rows[0]["prompt"]
    assert "四十五到六十度" in rows[1]["prompt"]


def test_seed_does_not_overwrite():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    desc_cards.update("C001", prompt="用户改过的正脸")
    desc_cards.delete("C002")
    n = desc_cards.seed_if_empty()
    assert n == 0
    rows = {r["id"]: r for r in desc_cards.list_all()}
    assert rows["C001"]["prompt"] == "用户改过的正脸"
    assert "C002" not in rows


def test_restore_factory_keeps_user():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    user = desc_cards.create("仰拍", "从下往上拍")
    desc_cards.delete("C003")
    desc_cards.update("C001", prompt="改过")
    rows = desc_cards.restore_factory()
    by_id = {r["id"]: r for r in rows}
    assert by_id["C001"]["prompt"].startswith("正面机位")
    assert "C003" in by_id
    assert any(r["id"] == user["id"] and r["name"] == "仰拍" for r in rows)


def test_crud_copy_reorder_delete():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    c = desc_cards.create("仰拍", "低机位仰拍")
    assert c["id"].startswith("C")
    assert c["builtin"] == 0
    cp = desc_cards.copy(c["id"])
    assert cp["name"] == "仰拍 副本"
    assert cp["prompt"] == "低机位仰拍"
    assert cp["id"] != c["id"]
    ids = [r["id"] for r in desc_cards.list_all()]
    # 把仰拍挪到最前
    new_ids = [c["id"]] + [i for i in ids if i != c["id"]]
    ordered = desc_cards.reorder(new_ids)
    assert ordered[0]["id"] == c["id"]
    desc_cards.move(c["id"], 1)
    gone = desc_cards.delete(c["id"])
    assert gone["id"] == c["id"]
    left = {r["id"] for r in desc_cards.list_all()}
    assert c["id"] not in left
    assert cp["id"] in left


def test_card_swap_adds_image_and_kicks_regen():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    tid, d, m = _make_task("T6801")
    before = _sha_map(d / "images")
    n_before = len(m.images)
    eng = FakeEngine()
    started = []

    def _start(kind, task_id=None, **args):
        started.append({"kind": kind, "task_id": task_id, "args": args})
        class J:
            id = "Jregen"
        return J()

    job = FakeJob(tid, account_id="Aflow")
    with patch("orante.services.expand.make_engine", return_value=eng), \
         patch("orante.jobs.start", side_effect=_start), \
         patch("orante.services.ai.client", return_value=FakeGemini()):
        r = desc_cards.card_swap(job, s=1, card_id="C001", auto_redo=True)
    m2 = Manifest.load(d)
    assert len(m2.images) == n_before + 1
    new = [im for im in m2.images if str(im.get("origin") or "").startswith("card:")]
    assert len(new) == 1
    assert new[0]["origin"] == "card:C001"
    assert new[0]["status"] == "ok"
    assert new[0]["card_name"] == "正脸角度"
    after = _sha_map(d / "images")
    for rel, h in before.items():
        assert after.get(rel) == h, f"旧文件 {rel} 被改了"
    sg = m2.seg(1)
    assert sg["image"] == new[0]["p"]
    assert sg["status"] == "planned"
    assert started and started[0]["kind"] == "regenerate"
    assert started[0]["args"].get("segs") == [1]
    assert r["p"] == new[0]["p"]
    cards_dir = d / "images" / "_cards"
    leftovers = [p for p in cards_dir.glob("*") if p.is_file()] if cards_dir.is_dir() else []
    assert not leftovers, leftovers


def test_frontal_bypasses_no_frontal():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    tid, d, m = _make_task("T6802")
    eng = FakeEngine()
    job = FakeJob(tid, account_id="Aflow")
    with patch("orante.services.expand.make_engine", return_value=eng), \
         patch("orante.jobs.start", return_value=type("J", (), {"id": "x"})()), \
         patch("orante.services.ai.client", return_value=FakeGemini()):
        desc_cards.card_swap(job, s=1, card_id="C001", auto_redo=False)
    assert eng.calls, "FakeEngine 没被调用"
    call = eng.calls[-1]
    assert "正对镜头" in call["prompt"]
    assert call["kw"].get("no_frontal") is False
    assert desc_cards.LAST_GEN["no_frontal"] is False
    # 普通扩图仍走 no_frontal=True
    assert expand._no_frontal() is True
    with patch("orante.services.coverage.pick", return_value=[]) as pk:
        expand._coverage_picks(1, Manifest.load(d), False)
        assert pk.called
        assert pk.call_args.kwargs.get("no_frontal") is True


def test_chain_s005_uses_tail_ref():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    tid, d, m = _make_task("T6803", n_seg=6, mode="tail_chain")
    sg = m.seg(5)
    assert sg["chain"]["ref_file"].endswith("s004.jpg")
    eng = FakeEngine()
    job = FakeJob(tid, account_id="Aflow")
    with patch("orante.services.expand.make_engine", return_value=eng), \
         patch("orante.jobs.start", return_value=type("J", (), {"id": "x"})()), \
         patch("orante.services.ai.client", return_value=FakeGemini()):
        desc_cards.card_swap(job, s=5, card_id="C004", auto_redo=False)
    assert eng.calls
    ref = Path(eng.calls[-1]["ref"])
    assert ref.name == "s004.jpg"
    m2 = Manifest.load(d)
    sg2 = m2.seg(5)
    assert sg2.get("gen_mode") == pipeline.MODE_FIRST
    assert pipeline.is_chain_restart(sg2)


def test_blocked_image_no_pool_no_leftover():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    tid, d, m = _make_task("T6804")
    n_before = len(m.images)
    eng = FakeEngine(block=True)
    job = FakeJob(tid, account_id="Aflow")
    with patch("orante.services.expand.make_engine", return_value=eng), \
         patch("orante.jobs.start") as st:
        try:
            desc_cards.card_swap(job, s=1, card_id="C001", auto_redo=True)
            raise AssertionError("BlockedImage 应该让 job 失败")
        except OranteError as e:
            assert "拒" in str(e)
        st.assert_not_called()
    m2 = Manifest.load(d)
    assert len(m2.images) == n_before
    assert not any(str(im.get("origin") or "").startswith("card:") for im in m2.images)
    cards_dir = d / "images" / "_cards"
    leftovers = [p for p in cards_dir.glob("*") if p.is_file()] if cards_dir.is_dir() else []
    assert not leftovers, leftovers


def test_preset_roundtrip_desc_cards():
    db.run("DELETE FROM desc_cards")
    desc_cards.seed_if_empty()
    desc_cards.update("C001", prompt="导出自定义正脸")
    extra = desc_cards.create("仰拍", "从下往上")
    dest = TEST_DATA / "cards.orpreset"
    r = preset.export(["desc_cards"], False, "pw-68-ok", str(dest))
    assert dest.is_file()
    counts = (r.get("meta") or {}).get("counts") or {}
    assert counts.get("desc_cards") >= 5
    desc_cards.update("C001", prompt="本机改了")
    desc_cards.delete(extra["id"])
    preset.do_import(str(dest), "pw-68-ok", "merge_preset", ["desc_cards"])
    rows = {r["name"]: r for r in desc_cards.list_all()}
    assert rows["正脸角度"]["prompt"] == "导出自定义正脸"
    assert "仰拍" in rows
    assert rows["仰拍"]["prompt"] == "从下往上"


def test_card_swap_registered():
    assert "card_swap" in REGISTRY
    from orante.server import app
    paths_ = {getattr(r, "path", None) for r in app.routes}
    assert "/api/desc-cards" in paths_
    assert "/api/desc-cards/{cid}" in paths_


def test_full_prompts_for_report():
    """给报告贴一次四张卡片实际发出去的完整提示词。"""
    ident = "一位三十多岁的东亚女性，留着齐肩黑发，穿着米色西装外套站在讲台上"
    out = {}
    for c in desc_cards.FACTORY:
        out[c["name"]] = desc_cards.build_card_prompt(c["prompt"], ident, no_frontal=False)
        assert c["prompt"].splitlines()[0] in out[c["name"]]
        assert ident in out[c["name"]]
    desc_cards.LAST_GEN["report_prompts"] = out
    # 正脸必须含「正对镜头」，且拼装不走 coverage.pick
    assert "正对镜头" in out["正脸角度"]
    with patch("orante.services.coverage.pick") as pk:
        desc_cards.build_card_prompt(desc_cards.FACTORY[0]["prompt"], ident, no_frontal=False)
        pk.assert_not_called()
