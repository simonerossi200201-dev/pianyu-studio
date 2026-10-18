# -*- coding: utf-8 -*-
"""规格 66：扩图换账号 + 右键提升画质。"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec66-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from orante import db, tasks as taskmod  # noqa: E402
from orante.engines.base import BlockedImage  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import REGISTRY, load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import expand, prompts  # noqa: E402
from orante.services.verify import hamming  # noqa: E402

db.init()
load_all_services()

UPSCALE_DIR = expand.UPSCALE_DIR
REPL_DIR = expand.REPL_DIR


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J66"
        self.progress = {"phase": "", "done": 0, "total": 0, "current": "", "items": []}

    def log(self, msg: str) -> None:
        self.lines.append(str(msg))

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


def _ins_acc(aid, name, engine, enabled=1, port=0):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run("UPDATE accounts SET name=?, engine=?, enabled=? WHERE id=?",
               (name, engine, enabled, aid))
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, name, engine, port or 9300, aid, "", "", "9:16", 5, enabled, "{}", db.now()))


def _checker(path: Path, size=(80, 80), cell=8, a=(20, 40, 80), b=(200, 180, 40)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, a)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if ((x // cell) + (y // cell)) % 2:
                px[x, y] = b
    im.save(path, "JPEG", quality=90)


def _solid(path: Path, size=(80, 80), color=(12, 90, 40)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)


def _stripes(path: Path, size=(160, 160), color_a=(10, 10, 10), color_b=(240, 240, 240)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color_a)
    dr = ImageDraw.Draw(im)
    for y in range(0, size[1], 6):
        dr.rectangle([0, y, size[0], y + 2], fill=color_b)
    im.save(path, "JPEG", quality=90)


def _ensure_accs():
    _ins_acc("Aflow", "TIK-L1", "flow", 1, 9311)
    _ins_acc("Agrok", "TIK-G-SIMONE", "grok", 1, 9312)
    _ins_acc("Aoff", "TIK-OFF", "grok", 0, 9313)


def _make_task(tid, *, engine="flow", account_id="Aflow", n_seg=3) -> tuple[str, Path, Manifest]:
    _ensure_accs()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-66-"))
    (task_dir / "images").mkdir()
    fp = task_dir / "images" / "p03.jpg"
    _checker(fp, (80, 80))
    images = [{
        "p": "p03", "file": "images/p03.jpg",
        "sha1": sha1_file(fp), "role": "主体", "status": "ok",
        "desc": "原描述语不要改", "voice": "v1",
        "eye": "闭眼", "scene": "常规",
        "measure": {"shot_size": "近景", "facing": "正面"},
        "origin": "local", "src": "p03.jpg", "pose": {},
        "flipped": False,
    }]
    segs = []
    for i in range(1, n_seg + 1):
        segs.append({
            "s": i, "text": f"seg {i}", "duration": 6,
            "image": "p03", "role": "主体", "shot": "",
            "prompt_final": f"old-{i}", "status": "collected", "gen_id": None,
            "takes": [], "error": None,
        })
    m = Manifest.new(tid, "规格66", engine, account_id, "short", "hello")
    m.images = images
    m.segments = segs
    m.save(task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "规格66", engine, account_id, "short", "hello", str(task_dir / "images"),
         "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def _engine(maker):
    class Eng:
        def gen_images(self, prompt, ref=None, n=1, dest_dir=None, **kw):
            dest_dir = Path(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            out = dest_dir / "up.jpg"
            maker(out)
            return [out]

        def connect(self):
            pass

        def close(self):
            pass

    return Eng()


def test_source_task_when_empty():
    tid, _d, _m = _make_task("T6601")
    st = expand.expand_account_state(tid)
    assert st["current"]["source"] == "task", st
    assert st["current"]["id"] == "Aflow", st
    ids = {a["id"] for a in st["options"]}
    engs = {a["engine"] for a in st["options"]}
    assert "Aflow" in ids and "Agrok" in ids
    assert "flow" in engs and "grok" in engs
    assert "Aoff" not in ids
    assert all(a.get("can_gen") for a in st["options"])
    with patch("orante.services.expand.can_gen_images", side_effect=lambda e: e == "flow"):
        st2 = expand.expand_account_state(tid)
    ids2 = {a["id"] for a in st2["options"]}
    assert "Aflow" in ids2
    assert "Agrok" not in ids2, "不能生图的引擎不应出现在 options"


def test_borrowed_source():
    """任务账号停用 → 系统借 Flow，source=borrowed，跟随项仍能选回去。"""
    tid, _d, _m = _make_task("T6603", account_id="Aoff", engine="grok")
    st = expand.expand_account_state(tid)
    assert st["current"]["source"] == "borrowed", st
    assert st["current"]["engine"] == "flow"
    assert st["task_account"]["id"] == "Aoff"


def test_pin_grok_then_clear():
    tid, _d, _m = _make_task("T6602")
    st = expand.set_expand_account(tid, "Agrok")
    t = taskmod.get(tid)
    assert t.get("expand_account_id") == "Agrok"
    assert st["current"]["source"] == "pinned"
    assert st["current"]["id"] == "Agrok"
    assert expand.resolve_account_id(t, persist=False) == "Agrok"
    st2 = expand.set_expand_account(tid, "")
    t2 = taskmod.get(tid)
    assert not t2.get("expand_account_id")
    assert st2["current"]["source"] == "task"
    assert st2["current"]["id"] == "Aflow"
    assert expand.resolve_account_id(t2, persist=False) == "Aflow"


def test_routes_exist():
    from orante.server import app
    paths_ = {getattr(r, "path", None) for r in app.routes}
    assert "/api/tasks/{tid}/expand-account" in paths_
    assert "/api/tasks/{tid}/image/{p}/reset-refs" in paths_
    assert "upscale" in REGISTRY


def test_make_engine_account_source():
    text = (ROOT / "orante" / "services" / "expand.py").read_text(encoding="utf-8")
    hits = [m.start() for m in re.finditer(r"make_engine\(", text)]
    assert hits, "expand.py 里没有 make_engine("
    for pos in hits:
        chunk = text[max(0, pos - 900):pos]
        assert "job.account_id or resolve_account_id" in chunk, (
            f"make_engine 账号不是 job.account_id or resolve_account_id，附近：{chunk[-200:]}")


def test_upscale_path_no_flip_or_describe():
    text = (ROOT / "orante" / "services" / "expand.py").read_text(encoding="utf-8")
    i = text.find("def _apply_upscale_file")
    j = text.find("\ndef refill_one", i)
    body = text[i:j]
    # 注释里提「翻转」可以，但不能调用这两个函数
    assert re.search(r"\b_flip_to\s*\(", body) is None
    assert re.search(r"\b_auto_describe\s*\(", body) is None
    assert "def upscale(" in body


def test_img_menu_off_upscale():
    js = (ROOT / "ui" / "js" / "panels" / "track.js").read_text(encoding="utf-8")
    m = re.search(r"function imgMenuOff\(im, key\) \{.*?\n      \}", js, re.S)
    assert m, "找不到 imgMenuOff"
    body = m.group(0)
    assert "upscale" in body
    assert "imgLimited(im)" in body


def test_gate1_dist30_no_replace():
    tid, d, m = _make_task("T6610")
    orig = d / "images" / "p03.jpg"
    orig_sha = sha1_file(orig)
    orig_bytes = orig.read_bytes()

    def maker(out: Path):
        _stripes(out, (200, 200))

    def fake_hash(fp):
        p = str(fp).replace("\\", "/")
        if "/_upscale/" in p or p.endswith("/up.jpg"):
            return (1 << 30) - 1
        return 0

    job = FakeJob(tid, "Aflow")
    with patch("orante.services.expand._connect_engine", return_value=_engine(maker)), \
         patch("orante.services.expand._img_hash", side_effect=fake_hash), \
         patch("orante.services.tails.laplacian_var", return_value=50.0), \
         patch("orante.services.expand._face_gate", return_value=(True, "skip")):
        r = expand.upscale(job, ps=["p03"])
    assert r["ok"] == []
    assert r["failed"] and r["failed"][0]["p"] == "p03"
    reason = r["failed"][0].get("reason") or ""
    assert "同一张" in reason or "距离" in reason, reason
    assert orig.read_bytes() == orig_bytes
    assert sha1_file(orig) == orig_sha
    cand = d / UPSCALE_DIR / "up.jpg"
    assert cand.is_file(), "候选应留在 images/_upscale/"
    assert not list((d / REPL_DIR).glob("p03-*")) if (d / REPL_DIR).exists() else True


def test_gate2_same_size_no_clearer():
    tid, d, m = _make_task("T6611")
    orig = d / "images" / "p03.jpg"
    orig_bytes = orig.read_bytes()

    def maker(out: Path):
        shutil.copy2(orig, out)

    job = FakeJob(tid, "Aflow")
    with patch("orante.services.expand._connect_engine", return_value=_engine(maker)), \
         patch("orante.services.tails.laplacian_var", return_value=50.0), \
         patch("orante.services.expand._face_gate", return_value=(True, "skip")):
        r = expand.upscale(job, ps=["p03"])
    assert r["ok"] == []
    reason = (r["failed"][0].get("reason") if r["failed"] else "") or ""
    assert "没有更清楚" in reason, reason
    assert orig.read_bytes() == orig_bytes
    assert any("没有更清楚" in ln for ln in job.lines), job.lines
    assert (d / UPSCALE_DIR / "up.jpg").is_file()


def test_all_pass_replace_no_flip_no_desc_no_reset():
    tid, d, m = _make_task("T6612", n_seg=3)
    orig = d / "images" / "p03.jpg"
    desc0 = dict(m.image("p03"))
    measure0 = dict(desc0.get("measure") or {})
    desc_text = desc0.get("desc")
    segs0 = [(sg["s"], sg["status"], sg["prompt_final"]) for sg in m.segments]

    def maker(out: Path):
        im = Image.open(orig)
        im.resize((160, 160), Image.NEAREST).save(out, "JPEG", quality=95)

    job = FakeJob(tid, "Aflow")
    with patch("orante.services.expand._connect_engine", return_value=_engine(maker)), \
         patch("orante.services.tails.laplacian_var", return_value=50.0), \
         patch("orante.services.expand._face_gate", return_value=(True, "人物闸跳过：测试")), \
         patch("orante.services.expand._reset_refs", wraps=expand._reset_refs) as spy:
        r = expand.upscale(job, ps=["p03"])
        assert spy.call_count == 0
    assert r["ok"] and r["ok"][0]["p"] == "p03", r
    assert r["failed"] == []
    m2 = Manifest.load(d)
    im = m2.image("p03")
    assert im["origin"] == "upscale"
    assert im.get("flipped") is not True
    assert im.get("desc") == desc_text
    assert dict(im.get("measure") or {}) == measure0
    new = d / "images" / "p03.jpg"
    with Image.open(new) as pic:
        assert pic.size == (160, 160)
    baks = list((d / REPL_DIR).glob("p03-*"))
    assert baks, "旧图应在 images/_replaced/"
    segs1 = [(sg["s"], sg["status"], sg["prompt_final"]) for sg in m2.segments]
    assert segs1 == segs0
    # 手动打回
    rr = expand.reset_image_refs(tid, "p03")
    assert len(rr["reset"]) == 3
    m3 = Manifest.load(d)
    assert all(sg["status"] == "planned" for sg in m3.segments)


def test_blocked_image_human_fail_no_leftover():
    tid, d, m = _make_task("T6613")
    orig = d / "images" / "p03.jpg"
    orig_bytes = orig.read_bytes()

    class Boom:
        def gen_images(self, *a, **k):
            dest = k.get("dest_dir")
            if dest:
                Path(dest).mkdir(parents=True, exist_ok=True)
                (Path(dest) / "half.jpg").write_bytes(b"xx")
            raise BlockedImage("知名人士")

        def connect(self):
            pass

        def close(self):
            pass

    job = FakeJob(tid, "Aflow")
    with patch("orante.services.expand._connect_engine", return_value=Boom()):
        try:
            expand.upscale(job, ps=["p03"])
            raise AssertionError("BlockedImage 应让 job 以人话失败")
        except OranteError as e:
            assert "拒" in e.message
    assert orig.read_bytes() == orig_bytes
    up = d / UPSCALE_DIR
    leftovers = list(up.glob("*")) if up.exists() else []
    assert leftovers == [], leftovers


def test_prompts_upscale_block():
    raw = prompts.get_block("upscale")
    assert "原样重绘" in raw
    assert "Photorealistic" in raw
    prompts.set_block("upscale", "自定义提升画质词")
    assert prompts.get_block("upscale") == "自定义提升画质词"
    prompts.reset_block("upscale")
    assert "原样重绘" in prompts.get_block("upscale")
    blocks = prompts.all_blocks()
    assert "upscale" in blocks
    assert "upscale" in prompts.EDITABLE


def test_ahash_distribution():
    """给报告贴实测分布，定稿 UPSCALE_SAME_DIST=12。"""
    tmp = Path(tempfile.mkdtemp(prefix="orante-ahash-66-"))
    a = tmp / "a.jpg"
    _checker(a, (80, 80))
    same = tmp / "same.jpg"
    shutil.copy2(a, same)
    big = tmp / "big.jpg"
    Image.open(a).resize((160, 160), Image.NEAREST).save(big, "JPEG", quality=95)
    flip = tmp / "flip.jpg"
    Image.open(a).transpose(Image.FLIP_LEFT_RIGHT).save(flip, "JPEG", quality=95)
    other = tmp / "other.jpg"
    _stripes(other, (80, 80))
    ha = expand._img_hash(a)
    rows = []
    for name, p in (("copy", same), ("2x", big), ("flip", flip), ("stripes", other)):
        hb = expand._img_hash(p)
        d = hamming(ha, hb) if ha is not None and hb is not None else None
        rows.append((name, d))
        print(f"  aHash {name}: {d}")
    by = dict(rows)
    assert by["copy"] == 0
    assert by["2x"] is not None and by["2x"] <= expand.UPSCALE_SAME_DIST
    assert by["stripes"] is not None and by["stripes"] > expand.UPSCALE_SAME_DIST
    # 镜像通常还很像，但提升画质禁止翻转就是怕画面反了；距离仅作记录


def test_assembled_prompt():
    p = expand._upscale_prompt()
    print("完整提示词：")
    print(p)
    assert "原样重绘" in p
    assert "Photorealistic" in p
