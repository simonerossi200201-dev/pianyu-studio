# -*- coding: utf-8 -*-
"""规格 80：换一张图，不要把已经收好的片子也拉下水。先设 ORANTE_DATA。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec80-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from orante import db, tasks as taskmod  # noqa: E402
from orante.engines.base import BlockedImage  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import expand, generate as genmod, review  # noqa: E402

db.init()
load_all_services()

SEG = "Stay for 7 seconds and type Amen."
REPL_DIR = expand.REPL_DIR
UPSCALE_DIR = expand.UPSCALE_DIR


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J80"
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


class FakeGemini:
    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        if json_mode and "is_broll" in str(prompt):
            return json.dumps({"is_broll": False, "reason": "有主角",
                               "shot_size": "中景", "people": 1, "crowd": False})
        if json_mode:
            return json.dumps({"env": False, "facing": "镜头",
                               "shot_size": "中景", "people": 1, "desc": "a speaker"})
        return "换一个角度"


def _jpeg(path: Path, size=(80, 120), color=(40, 80, 60)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)


def _checker(path: Path, size=(80, 80), cell=8, a=(20, 40, 80), b=(200, 180, 40)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, a)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if ((x // cell) + (y // cell)) % 2:
                px[x, y] = b
    im.save(path, "JPEG", quality=90)


def _ins_acc(aid="A001", name="t-flow", engine="flow", port=9480):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, name, engine, port, aid, "", "", "9:16", 5, 1, "{}", db.now()))


def _acc():
    return {
        "id": "A001", "name": "t-flow", "engine": "flow", "port": 9480,
        "project_url": "https://flow.google.com/project/"
                       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "model": "Veo 3.1 - Lite [Lower Priority]", "aspect": "9:16",
        "max_concurrent": 5,
    }


def _put_task(tid, d, engine="flow", account_id="A001"):
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(d), "arranged", engine, account_id, db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格80", engine, account_id, "long", SEG, str(d / "images"),
             "", "arranged", str(d), "", 0, db.now(), db.now()))


def _seg(s, p, st, prompt="old", error=None):
    tk = []
    return {
        "s": s, "text": f"{SEG} #{s}", "duration": 6,
        "image": p, "role": "主体", "shot": "",
        "prompt_final": prompt, "status": st, "gen_id": None,
        "takes": tk, "error": error,
    }


def _img_rec(d, p, status="ok", color=(40, 80, 60)):
    fp = d / "images" / f"{p}.jpg"
    _jpeg(fp, (80, 120), color)
    return {
        "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
        "role": "主体", "status": status, "desc": f"描述{p}", "voice": "",
        "eye": "闭眼", "scene": "常规", "measure": {},
        "origin": "local", "src": "",
    }


def _make_p07_task(tid):
    """p07 被 5 段引用：s003/s005/s008 collected，s011 blocked，s013 planned。"""
    _ins_acc()
    d = Path(tempfile.mkdtemp(prefix=f"orante-t80-{tid}-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    images = [
        _img_rec(d, "p01", color=(20, 40, 80)),
        _img_rec(d, "p07", color=(180, 40, 40)),
    ]
    segs = [
        _seg(3, "p07", "collected", "prompt-s003"),
        _seg(5, "p07", "collected", "prompt-s005"),
        _seg(8, "p07", "collected", "prompt-s008"),
        _seg(11, "p07", "blocked", "prompt-s011", error="图被拒"),
        _seg(13, "p07", "planned", "prompt-s013"),
    ]
    for sg in segs:
        if sg["status"] == "collected":
            clip = d / "clips" / f"s{sg['s']:03d}.mp4"
            clip.write_bytes(b"\x00" * 800)
            sg["takes"] = [{"file": f"clips/s{sg['s']:03d}.mp4", "at": "t"}]
    m = Manifest.new(tid, "规格80", "flow", "A001", "long", SEG)
    m.images = images
    m.segments = segs
    m.save(d)
    _put_task(tid, d)
    return d, Manifest.load(d)


def _keep_desc(job, m, d, im, gemini=None):
    if not str(im.get("desc") or "").strip():
        im["desc"] = "speaker at podium"
    return True


def _apply(job, m, d, p="p07"):
    pick = d / "images" / "_pick.jpg"
    _jpeg(pick, (90, 130), (10, 180, 40))
    im = m.image(p)
    with patch("orante.services.expand._auto_describe", _keep_desc), \
         patch("orante.services.expand.taskmod.emit_seg_changed"), \
         patch("orante.services.expand.taskmod.remember_counts", return_value={}):
        return expand._apply_new_file(
            job, m, d, im, p, pick,
            auto=True, ref_p="p01", pick_vi=1, pick_prompt="x")


# ---- A/B · _apply_new_file 不再打回 collected --------------------------------

def test_apply_new_file_keeps_collected():
    d, m = _make_p07_task("T8001")
    job = FakeJob("T8001", "A001")
    r = _apply(job, m, d, "p07")
    assert r["ok"]
    assert r["reset"] == [11], r
    assert r["kept"] == [3, 5, 8], r
    m2 = Manifest.load(d)
    for sn, prompt in ((3, "prompt-s003"), (5, "prompt-s005"), (8, "prompt-s008")):
        sg = m2.seg(sn)
        assert sg["status"] == "collected", sg
        assert sg.get("prompt_final") == prompt, sg
        mark = sg.get("image_replaced") or {}
        assert mark.get("p") == "p07", mark
        assert mark.get("at"), mark
        assert str(mark.get("old_file") or "").replace("\\", "/").startswith("images/_replaced/"), mark
    sg11 = m2.seg(11)
    assert sg11["status"] == "planned", sg11
    assert not sg11.get("error"), sg11
    assert m2.seg(13)["status"] == "planned"
    blob = "\n".join(job.lines)
    assert "已收片，保留不动" in blob, job.lines
    assert "打回重做" in blob, job.lines
    assert "段打回 planned" not in blob
    print("  _apply_new_file 保留 collected、blocked 打回  OK")


def test_submitted_clears_prompt_keeps_status():
    d, m = _make_p07_task("T8002")
    m.segments.append(_seg(9, "p07", "submitted", "prompt-s009"))
    m.save(d)
    job = FakeJob("T8002", "A001")
    r = _apply(job, m, d, "p07")
    m2 = Manifest.load(d)
    sg = m2.seg(9)
    assert sg["status"] == "submitted", sg
    assert not sg.get("prompt_final"), sg
    assert 9 not in (r.get("reset") or [])
    assert 9 not in (r.get("kept") or [])
    print("  submitted 状态不动、prompt_final 清空  OK")


def test_reset_kept_only_then_clears_mark():
    d, m = _make_p07_task("T8003")
    job = FakeJob("T8003", "A001")
    _apply(job, m, d, "p07")
    rr = expand.reset_image_refs("T8003", "p07", only_kept=True)
    assert set(rr["reset"]) == {3, 5, 8}, rr
    m2 = Manifest.load(d)
    for sn in (3, 5, 8):
        sg = m2.seg(sn)
        assert sg["status"] == "planned", sg
        assert not sg.get("image_replaced"), sg
    assert m2.seg(13)["status"] == "planned"
    print("  这 3 段也按新图重做  OK")


def test_collect_clears_image_replaced():
    _ins_acc()
    d = Path(tempfile.mkdtemp(prefix="orante-t80-col-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    images = [_img_rec(d, "p01")]
    segs = [_seg(1, "p01", "planned", "p1")]
    segs[0]["image_replaced"] = {
        "p": "p01", "at": "2026-09-20T12:00:00", "old_file": "images/_replaced/p01-x.jpg"}
    m = Manifest.new("T8004", "规格80", "flow", "A001", "long", SEG)
    m.images = images
    m.segments = segs
    m.save(d)
    _put_task("T8004", d)

    class Eng:
        can_gen_images = True
        name = "flow"

        def __init__(self):
            self.submitted = []

        def connect(self):
            pass

        def close(self):
            pass

        def capacity(self):
            return 5

        def submit(self, seg, image, prompt, duration, last_image=None, **kw):
            self.submitted.append(int(seg["s"]))
            return f"g{seg['s']}"

        def poll(self, ids):
            return {gid: {"state": "done"} for gid in ids}

        def download(self, gid, dest: Path):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\x00" * 800)
            return dest

    def _ok_verify(mm, dd, seg, dest, **kw):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            dest.write_bytes(b"\x00" * 800)
        return {
            "take": {"file": str(dest.relative_to(dd)).replace("\\", "/"),
                     "gen_id": seg.get("gen_id") or "", "at": "t", "verdict": "ok"},
            "verdict": "ok", "voided": False,
        }

    job = FakeJob("T8004", "A001")
    eng = Eng()
    with patch("orante.services.generate.make_engine", lambda acc, log: eng), \
         patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.verify.after_download", _ok_verify), \
         patch("orante.services.generate._enqueue_relay", lambda *a, **k: None):
        genmod.generate(job, chain=False)
    m2 = Manifest.load(d)
    sg = m2.seg(1)
    assert sg["status"] == "collected", sg
    assert not sg.get("image_replaced"), sg
    print("  重新收片后 image_replaced 消失  OK")


# ---- E · 回归：手动换图 / 提升画质 / _block_image -----------------------------

def test_review_set_image_unchanged():
    _ins_acc()
    d = Path(tempfile.mkdtemp(prefix="orante-t80-swap-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    images = [_img_rec(d, "p01", color=(10, 10, 10)),
              _img_rec(d, "p02", color=(200, 10, 10))]
    segs = [
        _seg(1, "p01", "collected", "手写 ALPHA"),
        _seg(2, "p01", "collected", "prompt-s002"),
    ]
    segs[0]["prompt_locked"] = True
    segs[0]["takes"] = [{"file": "clips/s001.mp4", "at": "t"}]
    segs[1]["takes"] = [{"file": "clips/s002.mp4", "at": "t"}]
    (d / "clips" / "s001.mp4").write_bytes(b"\x00" * 100)
    (d / "clips" / "s002.mp4").write_bytes(b"\x00" * 100)
    m = Manifest.new("T8005", "规格80", "flow", "A001", "long", SEG)
    m.images = images
    m.segments = segs
    m.save(d)
    _put_task("T8005", d)
    r = review.set_image("T8005", 1, "p02")
    assert r["image"] == "p02"
    assert r.get("kept_locked") is True
    assert r.get("marked") is True
    m2 = Manifest.load(d)
    assert m2.seg(1)["image"] == "p02"
    assert m2.seg(1).get("prompt_final") == "手写 ALPHA"
    assert m2.seg(1).get("mark") is True
    assert m2.seg(2)["status"] == "collected"
    assert m2.seg(2)["image"] == "p01"
    src = (ROOT / "orante" / "services" / "review.py").read_text(encoding="utf-8")
    fn = src[src.find("def set_image"): src.find("\ndef _is_finished")]
    assert "_reset_refs" not in fn
    print("  review.set_image 行为不变  OK")


def test_upscale_does_not_reset_collected():
    _ins_acc()
    d = Path(tempfile.mkdtemp(prefix="orante-t80-up-"))
    (d / "images").mkdir()
    fp = d / "images" / "p03.jpg"
    _checker(fp, (80, 80))
    images = [{
        "p": "p03", "file": "images/p03.jpg", "sha1": sha1_file(fp),
        "role": "主体", "status": "ok", "desc": "原描述语不要改", "voice": "v1",
        "eye": "闭眼", "scene": "常规",
        "measure": {"shot_size": "近景", "facing": "正面"},
        "origin": "local", "src": "p03.jpg", "pose": {}, "flipped": False,
    }]
    segs = [_seg(i, "p03", "collected", f"old-{i}") for i in (1, 2, 3)]
    m = Manifest.new("T8006", "规格80", "flow", "A001", "short", "hello")
    m.images = images
    m.segments = segs
    m.save(d)
    _put_task("T8006", d, account_id="A001")
    segs0 = [(sg["s"], sg["status"], sg["prompt_final"]) for sg in m.segments]

    def maker(out: Path):
        Image.open(fp).resize((160, 160), Image.NEAREST).save(out, "JPEG", quality=95)

    class Eng:
        can_gen_images = True
        name = "flow"

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

    job = FakeJob("T8006", "A001")
    with patch("orante.services.expand._connect_engine", return_value=Eng()), \
         patch("orante.services.tails.laplacian_var", return_value=50.0), \
         patch("orante.services.expand._face_gate", return_value=(True, "skip")), \
         patch("orante.services.expand._reset_refs", wraps=expand._reset_refs) as spy:
        r = expand.upscale(job, ps=["p03"])
        assert spy.call_count == 0
    assert r["ok"] and r["ok"][0]["p"] == "p03", r
    m2 = Manifest.load(d)
    segs1 = [(sg["s"], sg["status"], sg["prompt_final"]) for sg in m2.segments]
    assert segs1 == segs0
    rr = expand.reset_image_refs("T8006", "p03")
    assert len(rr["reset"]) == 3
    m3 = Manifest.load(d)
    assert all(sg["status"] == "planned" for sg in m3.segments)
    print("  66 提升画质 collected 不打回  OK")


def test_block_image_skips_collected_and_submitted():
    src = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    i = src.find("def _block_image")
    assert i > 0
    chunk = src[i: src.find("\ndef ", i + 1)]
    assert 'if sg.get("status") in ("collected", "submitted")' in chunk
    assert "continue" in chunk
    d, m = _make_p07_task("T8007")
    blocked = genmod._block_image(m, "p07", "图被拒")
    nums = {int(sg["s"]) for sg in blocked}
    assert 11 in nums
    assert 3 not in nums and 5 not in nums and 8 not in nums
    assert m.seg(3)["status"] == "collected"
    assert m.seg(13)["status"] == "blocked"
    print("  _block_image 跳过 collected/submitted  OK")


def test_requeue_only_planned():
    src = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    i = src.find("def requeue_planned")
    assert i > 0
    chunk = src[i: src.find("\n    def ", i + 1)]
    assert 'sg.get("status") != "planned"' in chunk
    print("  自动换图只把 planned 塞回队列  OK")


# ---- 端到端：11 段，最后一段图被拒，已收不倒退 --------------------------------

def _ok_verify(m, d, seg, dest, **kw):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        dest.write_bytes(b"\x00" * 800)
    return {
        "take": {
            "file": str(dest.relative_to(d)).replace("\\", "/"),
            "gen_id": seg.get("gen_id") or "",
            "at": "t",
            "verdict": "ok",
        },
        "verdict": "ok", "voided": False,
    }


class RejectOldShaEngine:
    """只拒换图之前那份 p07；新图放行。"""
    can_gen_images = True
    name = "flow"

    def __init__(self, old_sha):
        self.old_sha = old_sha
        self.submitted = []
        self.n = 0

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
        dest = Path(dest_dir or tempfile.mkdtemp())
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for _ in range(max(1, int(n or 1))):
            self.n += 1
            p = dest / f"gen-{self.n:04d}.jpg"
            _jpeg(p, (200, 360), (20 + self.n, 90, 70))
            out.append(p)
        return out

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        sha = sha1_file(Path(image)) if image and Path(image).is_file() else ""
        if Path(image).stem == "p07" and sha == self.old_sha:
            raise BlockedImage("Flow 拒收了 p07.jpg（中性图可传、这张图任何尺寸都不可）")
        self.submitted.append(int(seg["s"]))
        return f"g{seg['s']}"

    def poll(self, ids):
        return {gid: {"state": "done"} for gid in ids}

    def download(self, gid, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 60000)
        return dest


def test_e2e_last_seg_rejected_does_not_rewind_collected():
    _ins_acc()
    d = Path(tempfile.mkdtemp(prefix="orante-t80-e2e-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    images = [_img_rec(d, "p01", color=(20, 80, 40)),
              _img_rec(d, "p07", color=(180, 30, 30))]
    p07_users = {3, 5, 8, 11}
    segs = []
    for i in range(1, 12):
        p = "p07" if i in p07_users else "p01"
        st = "planned" if i == 11 else "collected"
        sg = _seg(i, p, st, f"prompt-s{i:03d}")
        if st == "collected":
            clip = d / "clips" / f"s{i:03d}.mp4"
            clip.write_bytes(b"\x00" * 800)
            sg["takes"] = [{"file": f"clips/s{i:03d}.mp4", "at": "t"}]
        segs.append(sg)
    m = Manifest.new("T8011", "规格80", "flow", "A001", "long", SEG)
    m.images = images
    m.segments = segs
    m.save(d)
    _put_task("T8011", d)
    n_col0 = sum(1 for sg in m.segments if sg["status"] == "collected")
    assert n_col0 == 10
    old_sha = m.image("p07")["sha1"]
    job = FakeJob("T8011", "A001")
    eng = RejectOldShaEngine(old_sha)
    with patch("orante.services.generate.make_engine", lambda acc, log: eng), \
         patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.check_stage", lambda *a, **k: None), \
         patch("orante.services.expand._auto_describe", _keep_desc), \
         patch("orante.services.expand._nearest_dup", lambda *a, **k: (None, 99)), \
         patch("orante.services.verify.after_download", _ok_verify), \
         patch("orante.services.generate._enqueue_relay", lambda *a, **k: None):
        genmod.generate(job, chain=False)
    m2 = Manifest.load(d)
    for sn in (3, 5, 8):
        sg = m2.seg(sn)
        assert sg["status"] == "collected", sg
        assert sg.get("prompt_final") == f"prompt-s{sn:03d}"
        assert sg.get("image_replaced"), sg
    assert m2.seg(11)["status"] == "collected", m2.seg(11)
    n_col1 = sum(1 for sg in m2.segments if sg["status"] == "collected")
    assert n_col1 == 11, n_col1
    assert eng.submitted == [11], eng.submitted
    blob = "\n".join(job.lines)
    assert "已收片，保留不动" in blob, job.lines[-12:]
    assert "打回重做" in blob, job.lines[-12:]
    print("  端到端：已收不倒退，只重提最后一段  OK")


# ---- 静态：前端角标 / toast / 接口 -------------------------------------------

def test_ui_and_route_hooks():
    track = (ROOT / "ui" / "js" / "panels" / "track.js").read_text(encoding="utf-8")
    rev = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    store = (ROOT / "ui" / "js" / "store.js").read_text(encoding="utf-8")
    server = (ROOT / "orante" / "server.py").read_text(encoding="utf-8")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    assert "sg.image_replaced" in track
    assert "track.old_img" in track
    assert "isReplaced" in rev and "redoReplaced" in rev
    assert "image.replaced" in store
    assert "only_kept" in store
    assert "only_kept" in server
    assert zh["track.refill_redo_kept"]
    assert zh["track.old_img_tip"] == "这一段的片子是用换掉之前那张图生成的，画面没问题"
    assert zh["review.old_img"] == "旧图"
    src = (ROOT / "orante" / "services" / "expand.py").read_text(encoding="utf-8")
    i = src.find("def _apply_upscale_file")
    chunk = src[i: src.find("\ndef _upscale_prompt")]
    assert "_reset_refs" not in chunk
    print("  静态：角标 / toast / reset-refs / 提升画质不打回  OK")


if __name__ == "__main__":
    test_apply_new_file_keeps_collected()
    test_submitted_clears_prompt_keeps_status()
    test_reset_kept_only_then_clears_mark()
    test_collect_clears_image_replaced()
    test_review_set_image_unchanged()
    test_upscale_does_not_reset_collected()
    test_block_image_skips_collected_and_submitted()
    test_requeue_only_planned()
    test_e2e_last_seg_rejected_does_not_rewind_collected()
    test_ui_and_route_hooks()
    print("OK spec80")
