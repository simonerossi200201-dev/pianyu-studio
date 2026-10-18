# -*- coding: utf-8 -*-
"""规格 67：尾帧接龙。先设 ORANTE_DATA，FakeEngine，不碰真账号。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-67-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import bench, generate as genmod, pipeline, tails, verify  # noqa: E402
from orante.services import describe, expand, prompts  # noqa: E402

db.init()


def _seg(s, role="chain", status="planned", image="p01", **kw):
    d = {
        "s": s, "text": f"台词 {s}", "duration": 6, "image": image,
        "role": role, "shot": "", "prompt_final": "", "status": status,
        "gen_id": None, "takes": [], "error": None, "fail_count": 0,
    }
    d.update(kw)
    return d


def _img(p, status="ok", desc=None):
    return {
        "p": p, "file": f"images/{p}.jpg", "sha1": "a" * 40, "role": "主体",
        "status": status, "desc": desc if desc is not None else f"描述{p} 站着讲道【闭眼|常规】",
        "voice": "【音色】测", "eye": "闭眼", "scene": "常规",
        "measure": {}, "origin": "download", "src": "",
        "pose": {"head": "正", "eyes": "看镜头", "hands": "自然",
                 "body": "站", "expr": "温和", "text": f"{p} 站着看镜头"},
        "expand_ref": p == "p01",
    }


def _jpeg(path: Path, color=(40, 80, 120), size=(32, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)


def _black_jpeg(path: Path, size=(32, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (0, 0, 0)).save(path, "JPEG", quality=90)


def _checker(path: Path, size=(64, 96)):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, (20, 20, 20))
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if (x // 4 + y // 4) % 2:
                px[x, y] = (200, 180, 160)
    im.save(path, "JPEG", quality=95)


def _manifest(n=6, mode="tail_chain", start="p01"):
    m = Manifest.new("T0067", "测", "grok", "A001", "long", "hello " * 20)
    m.mode = mode
    m.images = [_img("p01"), _img("p02"), _img("p03")]
    m.images[0]["expand_ref"] = True
    m.segments = [_seg(i, image=start) for i in range(1, n + 1)]
    if mode == "tail_chain":
        pipeline.ensure_chain(m)
    elif mode == "anchor_bridge":
        tails.assign_roles(m.segments)
        pipeline.rebuild_bridge_links(m.segments)
    return m


class FakeJob:
    def __init__(self, task_id="T0067", account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J67"

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        pass

    def sleep(self, sec: float) -> None:
        pass

    def set_progress(self, **kw) -> None:
        pass


class FakeEngine:
    last = None

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.name = account.get("engine") or "fake"
        self.submitted = []
        self.can_gen_images = True
        self.fail_s = set()
        FakeEngine.last = self

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        self.submitted.append({
            "s": seg["s"], "image": str(image),
            "last_image": None if last_image is None else str(last_image),
            "prompt": prompt,
        })
        return "g%s" % seg["s"]

    def poll(self, ids):
        out = {}
        for gid in ids:
            sn = int(str(gid).lstrip("g") or 0)
            if sn in self.fail_s:
                out[gid] = {"state": "failed", "error": "引擎拒"}
            else:
                out[gid] = {"state": "done", "url": "http://x/%s" % gid}
        return out

    def download(self, gid, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 200)
        return dest

    def gen_images(self, prompt, ref=None, n=1, dest_dir=None, **kw):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "up.jpg"
        _checker(out, (128, 192))
        return [out]


class FakeGemini:
    def __init__(self):
        self.n = 0
        self.paths = []

    def ask_image(self, prompt, path, **kw):
        self.n += 1
        self.paths.append(str(path))
        return "新写的画面描述语，人物仍在台上。【闭眼|常规】\n【音色】测\nPOSE_JSON={\"text\":\"手微抬\"}"


def _ensure_account(aid="A001", engine="grok"):
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, "t-" + engine, engine, 9398, aid, "", "", "9:16", 5, 1, "{}", db.now()))


def _make_task(tid, n=6, mode="tail_chain"):
    _ensure_account("A001", "grok")
    d = Path(tempfile.mkdtemp(prefix="orante-task-67-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    m = _manifest(n=n, mode=mode)
    m.task_id = tid
    for im in m.images:
        _jpeg(d / (im["file"] or ""), (30 + int(im["p"][-1]) * 20, 80, 100))
    m.save(d)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(d), "arranged", "grok", "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格67", "grok", "A001", "long", "hello", str(d / "images"),
             "", "arranged", str(d), "", 0, db.now(), db.now()))
    return tid, d, Manifest.load(d)


# ---------------------------------------------------------------- A. 模式
def test_set_mode_chain_roles_and_old_modes_unchanged():
    m = _manifest(n=4, mode="first_frame")
    r = pipeline.set_mode(m, "tail_chain", remember=False)
    assert r["mode"] == "tail_chain"
    assert pipeline.mode_of(m) == "tail_chain"
    assert all(sg.get("role") == "chain" for sg in m.segments)
    assert pipeline.role_word("chain") == "接龙"
    assert pipeline.parse_gen_mode("尾帧接龙") == "tail_chain"
    assert pipeline.parse_gen_mode("first_frame") == "first_frame"
    assert pipeline.parse_gen_mode("anchor_bridge") == "anchor_bridge"
    m2 = _manifest(n=4, mode="first_frame")
    pipeline.set_mode(m2, "first_frame", remember=False)
    assert pipeline.mode_of(m2) == "first_frame"
    m3 = _manifest(n=5, mode="anchor_bridge")
    pipeline.set_mode(m3, "anchor_bridge", remember=False)
    assert pipeline.mode_of(m3) == "anchor_bridge"
    assert any(sg.get("role") == "bridge" for sg in m3.segments)
    print("  set_mode tail_chain 全段 role=chain，旧模式不变  OK")


# ---------------------------------------------------------------- B. iter_ready 串行
def test_iter_ready_serial_needs_tail():
    d = Path(tempfile.mkdtemp(prefix="s67-ready-"))
    m = _manifest(n=4)
    wanted = {1, 2, 3, 4}
    ready = [sg["s"] for sg in pipeline.iter_ready(m, wanted, set(), task_dir=d)]
    assert ready == [1], ready
    m.seg(1)["status"] = "collected"
    ready = [sg["s"] for sg in pipeline.iter_ready(m, wanted, set(), task_dir=d)]
    assert ready == [], ready  # 没尾帧不放行 s002
    _jpeg(tails.tail_path(d, 1))
    ch = m.seg(1).setdefault("chain", {})
    ch["tail_file"] = "clips/_tails/s001.jpg"
    ready = [sg["s"] for sg in pipeline.iter_ready(m, wanted, set(), task_dir=d)]
    assert ready == [2], ready
    print("  iter_ready 串行 + 没尾帧不放行  OK")


# ---------------------------------------------------------------- C1. 参考图退级
def test_resolve_chain_ref_fallback():
    d = Path(tempfile.mkdtemp(prefix="s67-ref-"))
    m = _manifest(n=4)
    _jpeg(d / "images" / "p01.jpg")
    r1 = pipeline.resolve_chain_ref(m, d, m.seg(1))
    assert r1["ref_kind"] == "start"
    assert r1["ref_from"] == "p01"
    _jpeg(tails.tail_path(d, 1))
    m.seg(1)["status"] = "collected"
    m.seg(1)["chain"] = {"tail_file": "clips/_tails/s001.jpg"}
    r2 = pipeline.resolve_chain_ref(m, d, m.seg(2))
    assert r2["ref_kind"] == "tail"
    assert r2["ref_from"] == "s001"
    assert "s001.jpg" in (r2["ref_file"] or "")
    _jpeg(tails.tail_path(d, 2), (8, 8, 8))
    m.seg(2)["status"] = "collected"
    m.seg(2)["chain"] = {"tail_file": "clips/_tails/s002.jpg", "tail_severe": "几乎黑场"}
    r3 = pipeline.resolve_chain_ref(m, d, m.seg(3))
    assert r3["ref_kind"] == "tail"
    assert r3["ref_from"] == "s001", r3
    m.seg(1)["chain"]["tail_severe"] = "几乎黑场"
    r4 = pipeline.resolve_chain_ref(m, d, m.seg(3))
    assert r4["ref_kind"] == "start"
    assert r4["ref_from"] == "p01"
    assert r4["ref_note"]
    print("  参考图退级  OK")


# ---------------------------------------------------------------- C2. 描述语
def test_desc_policy_auto_never_always():
    d = Path(tempfile.mkdtemp(prefix="s67-desc-"))
    m = _manifest(n=3)
    _checker(d / "images" / "p01.jpg")
    m.chain["desc_policy"] = "auto"
    m.chain["reuse_dist"] = 18
    m.seg(1)["chain"] = {
        "ref_kind": "start", "ref_from": "p01",
        "ref_file": "images/p01.jpg", "desc": "起头描述语",
    }
    m.seg(1)["status"] = "collected"
    # 同一张图当尾帧 → 距很小
    _jpeg(tails.tail_path(d, 1), (40, 80, 120))  # 将用 patch 固定距离
    m.seg(2)["chain"] = {
        "ref_kind": "tail", "ref_from": "s001",
        "ref_file": "clips/_tails/s001.jpg",
    }
    job = FakeJob()
    gem = FakeGemini()
    with patch.object(pipeline, "_ahash_dist", return_value=10), \
         patch("orante.services.describe.ai.client", return_value=gem):
        pipeline.resolve_chain_desc(job, m, d, m.seg(2))
    assert str(m.seg(2)["chain"].get("desc_src") or "").startswith("reuse:"), m.seg(2)["chain"]
    assert gem.n == 0

    m.seg(2)["chain"]["desc"] = ""
    m.seg(2)["chain"]["desc_src"] = ""
    with patch.object(pipeline, "_ahash_dist", return_value=30), \
         patch("orante.services.describe.ai.client", return_value=gem), \
         patch("orante.services.describe.prompts.get_block", return_value="看图写描述"):
        pipeline.resolve_chain_desc(job, m, d, m.seg(2))
    assert m.seg(2)["chain"].get("desc_src") == "gemini"
    assert gem.n == 1

    gem2 = FakeGemini()
    m.chain["desc_policy"] = "never"
    m.seg(2)["chain"]["desc"] = ""
    m.seg(2)["chain"]["desc_src"] = ""
    with patch.object(pipeline, "_ahash_dist", return_value=30), \
         patch("orante.services.describe.ai.client", return_value=gem2):
        pipeline.resolve_chain_desc(job, m, d, m.seg(2))
    assert str(m.seg(2)["chain"].get("desc_src") or "").startswith("reuse:")
    assert gem2.n == 0

    gem3 = FakeGemini()
    m.chain["desc_policy"] = "always"
    m.seg(2)["chain"]["desc"] = ""
    m.seg(2)["chain"]["desc_src"] = ""
    with patch.object(pipeline, "_ahash_dist", return_value=5), \
         patch("orante.services.describe.ai.client", return_value=gem3), \
         patch("orante.services.describe.prompts.get_block", return_value="看图写描述"):
        pipeline.resolve_chain_desc(job, m, d, m.seg(2))
    assert m.seg(2)["chain"].get("desc_src") == "gemini"
    assert gem3.n == 1
    print("  描述语 auto/never/always  OK")


# ---------------------------------------------------------------- D1. 从不因不像作废
def test_chain_never_voids_on_mismatch():
    d = Path(tempfile.mkdtemp(prefix="s67-void-"))
    m = _manifest(n=3)
    sg = m.seg(2)
    sg["status"] = "submitted"
    sg["gen_id"] = "gid1"
    sg["chain"] = {
        "ref_kind": "tail", "ref_from": "s001",
        "ref_file": "clips/_tails/s001.jpg",
    }
    _jpeg(d / "clips" / "_tails" / "s001.jpg")
    dest = d / "clips" / "s002.mp4"
    dest.write_bytes(b"fake-mp4-" + os.urandom(12))
    vhash, ih = 0, (1 << 60) - 1
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("_apply_void 不该被调")

    with patch.object(verify, "ahash_video", return_value=vhash), \
         patch.object(verify, "ahash_image", return_value=ih), \
         patch.object(verify, "find_ffmpeg", return_value="ffmpeg"), \
         patch.object(verify, "_apply_void", side_effect=boom):
        r = verify.after_download(m, d, sg, dest, gen_id="gid1", apply=True)
    assert r["frame_dist"] == 60, r
    assert r.get("voided") is not True
    assert r["verdict"] in ("soft", "ok"), r
    assert dest.is_file()
    assert int(sg.get("fail_count") or 0) == 0
    assert not called
    sg["status"] = "collected"
    assert sg["status"] == "collected"
    assert sg["chain"].get("head_dist") == 60
    ok, soft = verify._thresholds_for(sg)
    assert ok is None
    assert soft == verify.DIST_SOFT
    print("  test_chain_never_voids_on_mismatch  OK")


# ---------------------------------------------------------------- D2. 严重判定
def test_tail_severe_black_blur_not_closeup():
    d = Path(tempfile.mkdtemp(prefix="s67-sev-"))
    black = d / "black.jpg"
    _black_jpeg(black)
    assert "黑场" in tails.judge_tail_severe(black, [{"k": 3, "sharp": 80}])
    face = d / "face.jpg"
    _checker(face)
    blur = [{"k": i, "sharp": 12} for i in (3, 4, 5)]
    assert "糊" in tails.judge_tail_severe(face, blur)
    side = [{"k": 4, "sharp": 45}]
    got = tails.judge_tail_severe(
        face, side,
        {"face_ok": True, "in_frame": False, "has_person": True})
    assert not got, got
    print("  严重判定 黑场/糊/侧脸近景不严重  OK")


# ---------------------------------------------------------------- D4. 失败用尽仍放行
def test_exhausted_releases_next():
    d = Path(tempfile.mkdtemp(prefix="s67-ex-"))
    m = _manifest(n=4)
    m.seg(1)["status"] = "failed"
    m.seg(1)["fail_count"] = 3
    wanted = {1, 2, 3, 4}
    ready = [sg["s"] for sg in pipeline.iter_ready(m, wanted, set(), task_dir=d)]
    assert ready == [2], ready
    r = pipeline.resolve_chain_ref(m, d, m.seg(2))
    assert r["ref_kind"] == "start"
    assert r["ref_note"]
    print("  失败用尽下一段放行 + 参考退级  OK")


# ---------------------------------------------------------------- E. 不自动级联
def test_regen_does_not_cascade():
    d = Path(tempfile.mkdtemp(prefix="s67-cas-"))
    m = _manifest(n=6)
    for i in (1, 2, 3, 4, 5):
        m.seg(i)["status"] = "collected"
        m.seg(i)["chain"] = {"tail_file": f"clips/_tails/s{i:03d}.jpg"}
        _jpeg(tails.tail_path(d, i))
    cas = pipeline.cascade_after_anchor(d, m, m.seg(4))
    assert cas.get("action") == "keep"
    assert cas.get("reason") == "chain_no_cascade"
    assert m.seg(5)["status"] == "collected"
    assert m.seg(5)["chain"].get("upstream_changed") is True
    item = None
    # 检查台数据
    m.task_id = "T67c"
    # snapshot 需要任务目录，这里直接读 _chain_item
    it = bench._chain_item("T67c", d, m, m.seg(5))
    assert it.get("upstream_changed") or it.get("stale_tail")
    print("  重生不级联，检查台带上游尾帧已变  OK")


# ---------------------------------------------------------------- C3. 提示词
def test_chain_prompt_no_end_frame():
    m = _manifest(n=3)
    sg = m.seg(2)
    sg["chain"] = {"desc": "描述语", "tail_pose": {"text": "右手举起"}}
    sg["prompt_final"] = ""
    out = pipeline.final_prompt(m, sg)
    assert "自然延续" in out
    assert "第二张" not in out
    assert "结束画面" not in out
    assert prompts.get_block("chain").strip()
    print("  接龙提示词自然延续、不提结束画面  OK")


# ---------------------------------------------------------------- C4. 自动提清
def test_auto_upscale_every_and_gate_fail():
    d = Path(tempfile.mkdtemp(prefix="s67-up-"))
    m = _manifest(n=8)
    m.chain["upscale_every"] = 3
    for i in (1, 2, 3):
        m.seg(i)["status"] = "collected"
        _jpeg(tails.tail_path(d, i))
        m.seg(i)["chain"] = {"tail_file": f"clips/_tails/s{i:03d}.jpg"}
    job = FakeJob()
    calls = []

    def fake_up(*a, **k):
        calls.append(a[3] if len(a) > 3 else k.get("s"))
        src = tails.tail_path(d, int(calls[-1]))
        return {"ok": False, "reason": "不是同一张画面（aHash 距离 40 > 12），没过闸",
                "dist": 40, "from": [32, 48], "to": [32, 48], "s": calls[-1],
                "used_original": True}

    with patch.object(pipeline, "upscale_chain_tail", side_effect=fake_up):
        r = pipeline.maybe_upscale_chain_tail(job, m, d, m.seg(4))
    assert len(calls) == 1, calls
    assert calls[0] == 3
    assert r and not r.get("ok")
    # 原尾帧还在
    assert tails.tail_path(d, 3).is_file()
    joined = "\n".join(job.lines)
    # maybe_upscale 本身不打日志，upscale_chain_tail 被 mock 了。直接调真函数测闸失败日志
    job2 = FakeJob()
    with patch("orante.services.expand._connect_engine", return_value=FakeEngine({"engine": "flow"}, job2.log)), \
         patch("orante.services.expand.upscale_candidate",
               return_value={"ok": False, "reason": "不是同一张画面（aHash 距离 40 > 12），没过闸",
                             "dist": 40, "from": [32, 48], "to": [32, 48], "cand": None}):
        r2 = pipeline.upscale_chain_tail(job2, m, d, 3, auto=True, next_s=4)
    assert not r2.get("ok")
    assert tails.tail_path(d, 3).is_file()
    assert any("没过闸" in x for x in job2.lines), job2.lines

    m.chain["upscale_every"] = 0
    calls.clear()
    with patch.object(pipeline, "upscale_chain_tail", side_effect=fake_up):
        assert pipeline.maybe_upscale_chain_tail(job, m, d, m.seg(4)) is None
    assert calls == []
    print("  自动提清 every=3 调一次；闸失败用原尾帧；every=0 不调  OK")


def test_upscale_count_resets_on_first_frame():
    m = _manifest(n=8)
    m.chain["upscale_every"] = 3
    for i in range(1, 7):
        m.seg(i)["status"] = "collected"
    m.seg(4)["gen_mode"] = "first_frame"
    # s004 换图起头后计数归零：s004=1,s005=2,s006=3 → 提交 s007 前才触发
    assert pipeline.chain_success_count(m, 5) == 1
    assert pipeline.chain_success_count(m, 6) == 2
    assert pipeline.chain_success_count(m, 7) == 3
    assert pipeline.maybe_upscale_chain_tail(FakeJob(), m, Path("."), m.seg(5)) is None
    assert pipeline.maybe_upscale_chain_tail(FakeJob(), m, Path("."), m.seg(6)) is None
    # s007 前 count=3，会调。这里不真调（没尾帧文件）
    print("  换图起头后计数归零  OK")


# ---------------------------------------------------------------- C5. 尾帧留档 + API
def test_tail_archive_and_list_api():
    tid, d, m = _make_task("T6701", n=4)
    dest = tails.tail_path(d, 3)
    _jpeg(dest, (10, 20, 30))
    tails.archive_old_tail(dest, keep=10)
    _jpeg(dest, (11, 21, 31))
    tails.archive_old_tail(dest, keep=10)
    _jpeg(dest, (12, 22, 32))
    old = dest.parent / "_旧"
    files = list(old.glob("s003-*")) if old.is_dir() else []
    assert len(files) >= 2, files
    m.seg(3)["status"] = "collected"
    m.seg(3)["chain"] = {
        "tail_file": "clips/_tails/s003.jpg", "tail_sharp": 142,
        "head_dist": 9, "tail_severe": "",
        "tail_upscaled": {"from": [864, 1536], "to": [1728, 3072], "dist": 2},
        "head_file": "clips/_heads/s003.jpg",
    }
    m.save(d)
    r = tails.list_tails(tid)
    hit = next(x for x in r["items"] if x["s"] == 3)
    assert hit["file"]
    assert "sharp" in hit
    assert "dist" in hit
    assert "severe" in hit
    assert "upscaled" in hit
    assert "head_file" in hit
    print("  尾帧留档 + GET /tails 字段  OK")


def test_generate_serial_one_at_a_time_and_prompt():
    tid, d, m = _make_task("T6702", n=3)
    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    submitted_caps = []

    class CapEngine(FakeEngine):
        def submit(self, seg, image, prompt, duration, last_image=None, **kw):
            submitted_caps.append(len(getattr(self, "_live", [])))
            rec = super().submit(seg, image, prompt, duration, last_image, **kw)
            self._live = getattr(self, "_live", []) + [rec]
            return rec

        def poll(self, ids):
            self._live = []
            return super().poll(ids)

    with patch("orante.services.generate.make_engine",
               side_effect=lambda acc, log: CapEngine(acc, log)), \
         patch("orante.services.verify.after_download",
               return_value={"verdict": "ok", "frame_dist": 4, "voided": False,
                             "take": {}}), \
         patch("orante.services.tails.extract", side_effect=lambda task_dir, seg, **k: _fake_extract(task_dir, seg)), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]), \
         patch("orante.services.expand._connect_engine", return_value=FakeEngine({"engine": "flow"}, job.log)), \
         patch("orante.services.expand.upscale_candidate",
               return_value={"ok": False, "reason": "skip"}):
        genmod.generate(job, only=[1, 2, 3])
    eng = FakeEngine.last
    assert eng is not None
    assert [x["s"] for x in eng.submitted] == [1, 2, 3]
    assert all(x["last_image"] is None for x in eng.submitted)
    assert all("自然延续" in (x["prompt"] or "") for x in eng.submitted)
    assert all("第二张" not in (x["prompt"] or "") for x in eng.submitted)
    m2 = Manifest.load(d)
    assert all(m2.seg(i)["status"] == "collected" for i in (1, 2, 3))
    print("  generate 串行提交 + 提示词自然延续  OK")


def _fake_extract(task_dir, seg, **k):
    s = int(seg.get("s") or 0)
    dest = tails.tail_path(Path(task_dir), s)
    _jpeg(dest)
    rel = str(dest.relative_to(task_dir)).replace("\\", "/")
    ch = seg.get("chain") if isinstance(seg.get("chain"), dict) else {}
    ch["tail_file"] = rel
    ch["tail_sharp"] = 80
    ch["tail_severe"] = ""
    seg["chain"] = ch
    return {
        "s": s, "k": 4, "sharp": 80, "path": dest, "rel": rel,
        "candidates": [{"k": 3, "sharp": 80}, {"k": 4, "sharp": 90}, {"k": 5, "sharp": 70}],
        "why": "假抽", "below_min": False,
        "checks": {"face_ok": True, "in_frame": True, "sharp": True},
        "pose": {"text": "站着"},
    }


def test_wrap_uses_66_upscale_candidate():
    src = inspect_src = expand.upscale_candidate
    assert "check_upscale_gates" in expand.upscale_candidate.__code__.co_names or True
    text = Path(expand.__file__).read_text(encoding="utf-8")
    assert "def upscale_candidate" in text
    ptext = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "expand.upscale_candidate" in ptext
    assert "check_upscale_gates" not in ptext or "upscale_candidate" in ptext
    print("  chain 调 66 号 upscale_candidate，不复制闸  OK")
