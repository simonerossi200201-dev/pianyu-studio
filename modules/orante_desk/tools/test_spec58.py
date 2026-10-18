# -*- coding: utf-8 -*-
"""规格 58：跑到全部出片、换参考图、借图兜底。先设 ORANTE_DATA。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-58-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, paths, tasks as taskmod  # noqa: E402
from orante.engines.base import BlockedImage  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import coverage, expand, generate as genmod, prompts  # noqa: E402

db.init()
load_all_services()
paths.ensure_dirs()

SEG = "Stay for 7 seconds and type Amen."


def _jpeg(path: Path, size=(640, 960), color=(40, 80, 60)) -> None:
    from PIL import Image as PILImage
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, color).save(path, "JPEG", quality=80)


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J58"
        self.progress = {"phase": "", "done": 0, "total": 0, "current": "", "items": []}
        self.args = {}

    def log(self, msg: str) -> None:
        self.lines.append(msg)

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


def _acc():
    return {
        "id": "A001", "name": "t-flow", "engine": "flow", "port": 9458,
        "project_url": "https://flow.google.com/project/"
                       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/collection/"
                       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "model": "Veo 3.1 - Lite [Lower Priority]", "aspect": "9:16",
        "max_concurrent": 5,
    }


def _ensure_account():
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-flow", "flow", 9458, "A001",
             "https://flow.google.com/project/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
             "", "9:16", 5, 1, "{}", db.now()))


def _ok_verify(m, d, seg, dest, **kw):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        dest.write_bytes(b"\x00" * 800)
    take = {
        "file": str(dest.relative_to(d)).replace("\\", "/"),
        "gen_id": seg.get("gen_id") or "",
        "at": "t",
        "verdict": "ok",
    }
    return {"take": take, "verdict": "ok", "voided": False}


class RejectStemEngine:
    """指定文件名主干永远被拒（submit）；生图正常。"""
    can_gen_images = True
    name = "flow"

    def __init__(self, account, log, blocked_stems=()):
        self.account = account
        self.log = log
        self.blocked = set(blocked_stems)
        self.n = 0
        self.submitted = []
        self.refs = []

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
        if ref:
            self.refs.append(Path(ref).stem)
        dest = Path(dest_dir or tempfile.mkdtemp())
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for _ in range(n):
            self.n += 1
            p = dest / f"gen-{self.n:04d}.jpg"
            _jpeg(p, (200, 360), (20 + self.n, 90, 70))
            out.append(p)
        return out

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        stem = Path(image).stem if image else ""
        if stem in self.blocked:
            raise BlockedImage(
                f"Flow 拒收了 {Path(image).name}（中性图可传、这张图任何尺寸都不可）")
        self.submitted.append(int(seg["s"]))
        return f"g{seg['s']}"

    def poll(self, ids):
        return {gid: {"state": "done"} for gid in ids}

    def download(self, gid, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 60000)
        return dest


def _make_task(tid, images, segs, *, rtc=True):
    """images: [(p, status, desc), ...]  segs: [(s, p, status), ...]"""
    _ensure_account()
    d = Path(tempfile.mkdtemp(prefix=f"orante-t58-{tid}-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    recs = []
    for i, spec in enumerate(images):
        p, status = spec[0], spec[1]
        desc = spec[2] if len(spec) > 2 else f"描述{p}"
        fp = d / "images" / f"{p}.jpg"
        _jpeg(fp, (320, 560), (40 + i * 12, 70, 50 + i * 8))
        recs.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
            "role": "主体", "status": status, "desc": desc, "voice": "",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": "",
        })
    out_segs = []
    for s, p, st in segs:
        tk = []
        if st == "collected":
            clip = d / "clips" / f"s{s:03d}.mp4"
            clip.write_bytes(b"\x00" * 800)
            tk = [{"file": f"clips/s{s:03d}.mp4", "at": "t"}]
        out_segs.append({
            "s": s, "text": f"{SEG} #{s}", "duration": 6,
            "image": p, "role": "主体", "shot": "", "prompt_final": f"P{s}",
            "status": st, "gen_id": None, "takes": tk, "error": None,
        })
    m = Manifest.new(tid, "58测", "flow", "A001", "long", SEG)
    m.images = recs
    m.segments = out_segs
    m.mode = "first_frame"
    m.save(d)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(d), "arranged", "flow", "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "58测", "flow", "A001", "long", SEG, str(d / "images"),
             "", "arranged", str(d), "", 0, db.now(), db.now()))
    db.run("UPDATE tasks SET run_to_completion=? WHERE id=?", (1 if rtc else 0, tid))
    return d, Manifest.load(d)


def _keep_desc(job, m, d, im, gemini=None):
    if not str(im.get("desc") or "").strip():
        im["desc"] = "speaker at podium"
    return True


def _run_gen(job, eng, **kw):
    with patch("orante.services.generate.make_engine", lambda acc, log: eng), \
         patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.check_stage", lambda *a, **k: None), \
         patch("orante.services.expand._auto_describe", _keep_desc), \
         patch("orante.services.expand._nearest_dup", lambda *a, **k: (None, 99)), \
         patch("orante.services.verify.after_download", _ok_verify), \
         patch("orante.services.generate._enqueue_relay", lambda *a, **k: None):
        return genmod.generate(job, chain=False, **kw)


# ---- 用例 -------------------------------------------------------------

def test_rtc_true_borrows_on_round_2():
    d, m = _make_task("T5801", [
        ("p01", "ok", "john at podium"),
        ("p03", "ok", "john close up"),
        ("p07", "ok", "john wide shot"),
    ], [
        (1, "p01", "collected"),
        (18, "p03", "collected"),
        (19, "p07", "planned"),
        (20, "p01", "collected"),
    ], rtc=True)
    job = FakeJob("T5801", "A001")
    eng = RejectStemEngine(_acc(), job.log, blocked_stems={"p07"})
    _run_gen(job, eng)
    m1 = Manifest.load(d)
    assert m1.seg(19)["status"] == "blocked", m1.seg(19)
    assert int(m1.image("p07").get("auto_refill") or 0) >= 3
    assert any("先放着" in x for x in job.lines), job.lines[-8:]

    job2 = FakeJob("T5801", "A001")
    eng2 = RejectStemEngine(_acc(), job2.log, blocked_stems={"p07"})
    _run_gen(job2, eng2, redo=True, rtc_round=1)
    m2 = Manifest.load(d)
    sg = m2.seg(19)
    assert sg["status"] == "collected", sg
    assert sg.get("image") != "p07"
    assert sg.get("borrowed") or (sg.get("takes") or [{}])[-1].get("borrowed")
    assert not any(x.get("status") == "blocked" for x in m2.segments)
    assert any("借用" in x for x in job2.lines), job2.lines
    print("OK rtc=True：第 1 轮 3 次失败，第 2 轮借图，0 段 blocked")


def test_rtc_false_stays_blocked():
    d, m = _make_task("T5802", [
        ("p01", "ok", "a"),
        ("p07", "ok", "b"),
    ], [
        (1, "p01", "collected"),
        (2, "p07", "planned"),
    ], rtc=False)
    job = FakeJob("T5802", "A001")
    eng = RejectStemEngine(_acc(), job.log, blocked_stems={"p07"})
    _run_gen(job, eng)
    m1 = Manifest.load(d)
    assert m1.seg(2)["status"] == "blocked"
    orig = m1.seg(2)["image"]
    job2 = FakeJob("T5802", "A001")
    _run_gen(job2, RejectStemEngine(_acc(), job2.log, blocked_stems={"p07"}),
             redo=True, rtc_round=1)
    m2 = Manifest.load(d)
    assert m2.seg(2)["status"] == "blocked"
    assert m2.seg(2)["image"] == orig
    assert not any("借用" in x for x in job.lines + job2.lines)
    print("OK rtc=False：停在 blocked，不借图")


def test_relay_stall_stops():
    d, m = _make_task("T5803", [
        ("p01", "ok", "a"),
        ("p02", "ok", "b"),
    ], [
        (1, "p01", "planned"),
        (2, "p02", "planned"),
    ], rtc=True)
    starts = []

    def rec_enqueue(job, relay_n):
        starts.append(int(relay_n))

    job = FakeJob("T5803", "A001")
    eng = RejectStemEngine(_acc(), job.log, blocked_stems={"p01", "p02"})
    with patch("orante.services.generate.make_engine", lambda acc, log: eng), \
         patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.check_stage", lambda *a, **k: None), \
         patch("orante.services.expand._auto_describe", _keep_desc), \
         patch("orante.services.expand._nearest_dup", lambda *a, **k: (None, 99)), \
         patch("orante.services.verify.after_download", _ok_verify), \
         patch("orante.services.generate._enqueue_relay", rec_enqueue):
        genmod.generate(job, chain=True, rtc_round=0)
        genmod.generate(job, redo=True, chain=True, rtc_round=1)
        genmod.generate(job, redo=True, chain=True, rtc_round=2)
    assert any("连续 2 轮没有进展" in x for x in job.lines), job.lines[-12:]
    assert len(starts) <= 5
    # 第 3 次（rtc_round=2）应停，不再排队
    assert starts == [1, 2], starts
    print("OK 全拒：连续 2 轮零进展就停，日志有那句话")


def test_decide_relay_budget():
    d0 = genmod.decide_relay(
        blocked_n=3, collected_n=0, prev_blocked=None, prev_collected=None,
        stalled=0, relay_n=0)
    assert d0["chain"] is True and d0["relay"] == 1
    d1 = genmod.decide_relay(
        blocked_n=3, collected_n=0, prev_blocked=3, prev_collected=0,
        stalled=0, relay_n=1)
    assert d1["chain"] is True and d1["stalled"] == 1
    d2 = genmod.decide_relay(
        blocked_n=3, collected_n=0, prev_blocked=3, prev_collected=0,
        stalled=1, relay_n=2)
    assert d2["chain"] is False and d2["stop"] == "stalled"
    assert "连续 2 轮没有进展" in d2["log"]
    dmax = genmod.decide_relay(
        blocked_n=1, collected_n=9, prev_blocked=2, prev_collected=8,
        stalled=0, relay_n=5)
    assert dmax["chain"] is False and dmax["stop"] == "max"
    print("OK decide_relay：2 轮零进展停、ROUND_MAX=5")


def test_refill_attempt_changes_ref_and_cov():
    # 2026-09-27 起：有描述语的图按自己的描述语补（见 test_spec96）；
    # 覆盖库这条路只留给没有描述语的图，所以这里 p07 的描述语留空。
    prompts.set_block("expand", coverage.default_lib())
    d, m = _make_task("T5804", [
        ("p01", "ok", "a"),
        ("p05", "ok", "b"),
        ("p07", "blocked", ""),
    ], [
        (1, "p01", "collected"),
        (2, "p05", "planned"),
        (3, "p07", "blocked"),
    ])
    # 第 1 次构图钉死，后面 attempt=3 要离它最远
    cam0 = (coverage.default_lib().get("cameras") or [{}])[0]
    act0 = (coverage.default_lib().get("actions") or [{}])[0]
    cov1 = coverage.cov_meta(cam0, act0)
    m.image("p07")["cov"] = dict(cov1)
    m.image("p07")["first_refill_cov"] = dict(cov1)
    m.save(d)
    job = FakeJob("T5804", "A001")
    eng = RejectStemEngine(_acc(), job.log)
    far_calls = []
    real_far = coverage.pick_farthest

    def wrap_far(*a, **k):
        r = real_far(*a, **k)
        far_calls.append(r)
        return r

    with patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.check_stage", lambda *a, **k: None), \
         patch("orante.services.expand._nearest_dup", lambda *a, **k: (None, 99)), \
         patch("orante.services.expand._auto_describe", lambda *a, **k: True), \
         patch("orante.services.coverage.pick_farthest", wrap_far):
        r1 = expand.refill_one(job, m, d, "p07", eng, FakeGemini(),
                               auto=True, attempt=1)
        ref1 = (r1 or {}).get("ref")
        r2 = expand.refill_one(job, Manifest.load(d), d, "p07", eng, FakeGemini(),
                               auto=True, attempt=2)
        ref2 = (r2 or {}).get("ref")
        m3 = Manifest.load(d)
        r3 = expand.refill_one(job, m3, d, "p07", eng, FakeGemini(),
                               auto=True, attempt=3)
    assert r1 and r1.get("ok"), r1
    assert r2 and r2.get("ok"), r2
    assert r3 and r3.get("ok"), r3
    assert ref1 != ref2, (ref1, ref2)
    assert any("换参考图" in x for x in job.lines), job.lines
    assert far_calls, "attempt=3 应走 pick_farthest"
    cov3 = (r3 or {}).get("cov") or {}
    want = (far_calls[-1][0].get("cov") or {}) if far_calls[-1] else {}
    assert cov3.get("camera") == want.get("camera"), (cov3, want)
    assert coverage.cov_distance(cov1, cov3) >= coverage.cov_distance(
        cov1, (r1.get("cov") or cov1))
    print("OK refill_one attempt=2 换参考，attempt=3 构图最远")


def test_borrow_spacing_and_files():
    d, m = _make_task("T5805", [
        ("p01", "ok", "alpha podium speech"),
        ("p03", "ok", "beta close"),
        ("p05", "ok", "alpha podium speech"),
        ("p06", "ok", "gamma other"),
        ("p07", "blocked", "alpha podium speech"),
    ], [
        (1, "p01", "collected"),
        (2, "p07", "blocked"),
        (3, "p03", "collected"),
        (4, "p05", "collected"),
        (5, "p05", "collected"),
        (6, "p06", "collected"),
    ])
    before = {p.name: p.read_bytes() for p in (d / "images").glob("*.jpg")}
    sg = m.seg(2)
    pick = genmod.borrow_image(m, d, sg)
    assert pick, "应借到一张图"
    assert pick not in ("p01", "p03", "p07"), pick  # 间隔：不和相邻同款，也不用自己
    # p05 用了 2 次、p06 用了 1 次；描述语 p05 更接近 → 但间隔优先后，
    # p05/p06 都合法，p05 文本更近应排前面。p06 用得更少。
    # 排序：(adj, -sim, n, p) → p05 sim 高、n=2；p06 sim 低、n=1
    # 本段 desc 是 alpha podium speech，p05 同文案，应优先 p05
    assert pick == "p05", pick
    assert sg.get("image") == "p05"
    assert sg.get("borrowed") is True
    after = {p.name: p.read_bytes() for p in (d / "images").glob("*.jpg")}
    assert before == after
    m.save(d)
    # 收片路径会把 borrowed 写进 take；这里先打在段上
    assert sg.get("borrowed")
    print("OK borrow_image：间隔排列、文件没动、段标了 borrowed")


def test_borrow_least_used_when_desc_tie():
    d, m = _make_task("T5806", [
        ("p01", "ok", "x"),
        ("p05", "ok", "same"),
        ("p06", "ok", "same"),
        ("p07", "blocked", "same"),
    ], [
        (1, "p01", "collected"),
        (2, "p07", "blocked"),
        (3, "p01", "collected"),
        (4, "p05", "collected"),
        (5, "p05", "collected"),
        (6, "p06", "collected"),
    ])
    pick = genmod.borrow_image(m, d, m.seg(2))
    # 相邻是 p01 / p01，避开。p05 用 2 次、p06 用 1 次，文案都 same → p06
    assert pick == "p06", pick
    print("OK borrow_image：文案打平就用得最少的")


def test_task_cap_not_bypassed_same_round():
    imgs = [(f"p{i:02d}", "ok", f"d{i}") for i in range(1, 7)]
    segs = [(i, f"p{i:02d}", "planned") for i in range(1, 7)]
    d, m = _make_task("T5807", imgs, segs, rtc=True)
    calls = []
    real = expand.refill_one

    def wrap(job, m, d, p, eng, gemini=None, **kw):
        calls.append(p)
        return real(job, m, d, p, eng, gemini, **kw)

    job = FakeJob("T5807", "A001")
    eng = RejectStemEngine(
        _acc(), job.log, blocked_stems={f"p{i:02d}" for i in range(1, 7)})
    with patch("orante.services.generate.make_engine", lambda acc, log: eng), \
         patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.check_stage", lambda *a, **k: None), \
         patch("orante.services.expand._auto_describe", _keep_desc), \
         patch("orante.services.expand._nearest_dup", lambda *a, **k: (None, 99)), \
         patch("orante.services.verify.after_download", _ok_verify), \
         patch("orante.services.generate._enqueue_relay", lambda *a, **k: None), \
         patch("orante.services.expand.refill_one", wrap):
        genmod.generate(job, chain=False, rtc_round=0)
    uniq = []
    for p in calls:
        if p not in uniq:
            uniq.append(p)
    assert len(uniq) <= genmod.MAX_AUTO_REFILL_TASK, uniq
    assert any("已停止自动换图以免毁掉图池" in x for x in job.lines), job.lines[-6:]
    print("OK MAX_AUTO_REFILL_TASK=4 同一轮不绕过")


def test_progress_line_and_warning_copy():
    src = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    assert "已接力" in src and "ROUND_MAX" in src
    assert "已停止自动换图以免毁掉图池" in src
    assert "确认是接口抽风就先歇十分钟" in src
    js = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    assert "g-block-badge" in js
    assert "gen.blocked_retry" in js
    assert "gen.blocked_borrow" in js
    assert "gen.blocked_swap" in js
    assert "gen.rtc" in js
    rv = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    assert "isBorrowed" in rv and "review.borrowed" in rv
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("gen.rtc", "gen.blocked_badge", "gen.blocked_retry",
              "gen.blocked_borrow", "gen.blocked_swap", "review.borrowed"):
        assert k in zh, k
    print("OK 文案 / 徽章 / 借图标记都在")


def test_bak():
    for rel in (
        "orante/services/generate.py.bak-0912complete",
        "orante/services/expand.py.bak-0912complete",
        "orante/services/coverage.py.bak-0912complete",
        "ui/js/panels/generate.js.bak-0912complete",
    ):
        assert (ROOT / rel).is_file(), rel
    print("OK bak-0912complete 在")


if __name__ == "__main__":
    test_rtc_true_borrows_on_round_2()
    test_rtc_false_stays_blocked()
    test_relay_stall_stops()
    test_decide_relay_budget()
    test_refill_attempt_changes_ref_and_cov()
    test_borrow_spacing_and_files()
    test_borrow_least_used_when_desc_tie()
    test_task_cap_not_bypassed_same_round()
    test_progress_line_and_warning_copy()
    test_bak()
    print("\n全部通过")


# ---------------- 2026-09-27：补图按每张图自己的描述语（用户要求） ----------------

class DescEngine(RejectStemEngine):
    """记下每次生图的提示词、参考图、画幅；reject_refs 里的参考图一上传就被拒。"""

    def __init__(self, account, log, reject_refs=()):
        super().__init__(account, log)
        self.reject_refs = set(reject_refs)
        self.calls = []

    def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
        if ref and Path(ref).stem in self.reject_refs:
            raise BlockedImage(f"Flow 拒收了 {Path(ref).name}：Failed to upload（连传 2 次都没收）")
        self.calls.append({"prompt": prompt, "ref": Path(ref).stem if ref else None,
                           "aspect": kw.get("aspect")})
        return super().gen_images(prompt, ref=ref, n=n, dest_dir=dest_dir, **kw)


BASEBALL = ("这是一群五位年轻20岁左右的美国白人男子基督徒，他们穿着深灰色短袖训练T恤和白色棒球裤，"
            "围成一个封闭的圆圈站立在草地上，身后停放着一辆黄色校车【空镜|群像】")


def _run_refill(d, p, eng, *, auto=False, attempt=1):
    job = FakeJob(d.name, "A001")
    with patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.check_stage", lambda *a, **k: None), \
         patch("orante.services.expand._nearest_dup", lambda *a, **k: (None, 99)), \
         patch("orante.services.expand._auto_describe", _keep_desc):
        r = expand.refill_one(job, Manifest.load(d), d, p, eng, FakeGemini(),
                              auto=auto, attempt=attempt)
    return r, job


def test_desc_core_strips_tags():
    assert expand._desc_core({"desc": BASEBALL}).endswith("黄色校车")
    assert "【" not in expand._desc_core({"desc": BASEBALL})
    assert expand._desc_core({"desc": ""}) == ""


def test_refill_uses_own_desc_not_suite_library():
    """有别的主体图能当参考：按 p07 自己的描述语、带参考图生成；绝不出现套装覆盖库那段讲道现场。"""
    prompts.set_block("expand", coverage.default_lib())
    d, m = _make_task("T5891", [("p01", "ok", "讲道人站在讲台后"), ("p07", "blocked", BASEBALL)],
                      [(1, "p01", "collected"), (2, "p07", "blocked")])
    eng = DescEngine(_acc(), lambda *a: None)
    r, job = _run_refill(d, "p07", eng)
    assert r and r.get("ok"), job.lines
    c = eng.calls[0]
    assert "五位年轻20岁左右的美国白人男子基督徒" in c["prompt"]
    assert "以上传的这张图为参考" in c["prompt"]
    assert "讲台" not in c["prompt"] and "讲道" not in c["prompt"]
    assert "【" not in c["prompt"]
    assert c["ref"] is not None
    assert any("按 p07 自己的描述语" in x for x in job.lines)


def test_refill_without_any_usable_ref_generates_from_desc_only():
    """单图任务、这张图自己传不上去（T0113 的情况）：不带参考图，只照描述语生成一张补上。"""
    d, m = _make_task("T5892", [("p01", "blocked", BASEBALL)], [(1, "p01", "blocked")])
    mm = Manifest.load(d)
    mm.image("p01")["block_reason"] = "图被拒：Flow 拒收了 p01.jpg：Failed to upload p01-e61f38cbec.（连传 2 次都没收）"
    mm.save(d)
    eng = DescEngine(_acc(), lambda *a: None, reject_refs={"p01"})
    r, job = _run_refill(d, "p01", eng)
    assert r and r.get("ok"), job.lines
    assert r.get("ref") == "描述语"
    assert len(eng.calls) == 1, "自己传不上去的图不再拿自己当参考白试"
    c = eng.calls[0]
    assert c["ref"] is None and c["aspect"] == "9:16"
    assert c["prompt"].startswith("生成一张真实自然的现场纪实摄影照片")
    assert "黄色校车" in c["prompt"]
    assert any("不带参考图" in x for x in job.lines)
    m2 = Manifest.load(d)
    assert m2.image("p01")["status"] == "ok"


def test_refill_self_ref_rejected_then_desc_only():
    """自己当参考被拒（被拒原因不是上传）：先试自己，被拒后退到只照描述语生成。"""
    d, m = _make_task("T5893", [("p01", "blocked", BASEBALL)], [(1, "p01", "blocked")])
    eng = DescEngine(_acc(), lambda *a: None, reject_refs={"p01"})
    r, job = _run_refill(d, "p01", eng)
    assert r and r.get("ok"), job.lines
    assert [c["ref"] for c in eng.calls] == [None]
    assert any("自己当参考也被拒" in x for x in job.lines)


def test_refill_no_desc_keeps_old_library_path():
    """没有描述语的图照旧走覆盖库（不改原有功能）。"""
    prompts.set_block("expand", coverage.default_lib())
    d, m = _make_task("T5894", [("p01", "ok", "a"), ("p07", "blocked", "")],
                      [(1, "p01", "collected"), (2, "p07", "blocked")])
    eng = DescEngine(_acc(), lambda *a: None)
    job = FakeJob(d.name, "A001")
    with patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.check_stage", lambda *a, **k: None), \
         patch("orante.services.expand._nearest_dup", lambda *a, **k: (None, 99)), \
         patch("orante.services.expand._auto_describe", lambda *a, **k: True):
        r = expand.refill_one(job, Manifest.load(d), d, "p07", eng, FakeGemini(), auto=True)
    assert r and r.get("ok"), job.lines
    assert "按描述语" not in "\n".join(job.lines)
    assert not any("以上传的这张图为参考，生成同一个现场" in c["prompt"] for c in eng.calls)


# ---------------- 2026-09-27：原图传不上 Flow —— 能消化就自动消化，消化不了就明说换引擎 ----------------

class RefuseOriginalEngine(RejectStemEngine):
    """只拒"原图"那一份内容（按 sha1）：原图一上传就被 Flow 拒（跟 T0113 一样），
    换成新生成的图就能正常交。can_make_images=False 时连补图都生不出来。"""

    def __init__(self, account, log, refused_sha, can_make_images=True):
        super().__init__(account, log)
        self.refused_sha = set(refused_sha)
        self.can_make = can_make_images

    def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
        if not self.can_make:
            return []
        return super().gen_images(prompt, ref=ref, n=n, dest_dir=dest_dir, **kw)

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        if sha1_file(Path(image)) in self.refused_sha:
            raise BlockedImage(f"Flow 拒收了 {Path(image).name}：Failed to upload x.jpg（连传 2 次都没收）")
        self.submitted.append(int(seg["s"]))
        return f"g{seg['s']}"


def test_flow_refused_image_undigestable_tells_user_to_switch_engine():
    d, m = _make_task("T5895", [("p01", "ok", BASEBALL)], [(1, "p01", "planned")], rtc=False)
    orig = Manifest.load(d).image("p01")["sha1"]
    job = FakeJob("T5895", "A001")
    eng = RefuseOriginalEngine(_acc(), job.log, {orig}, can_make_images=False)
    toasts = []
    real_emit = genmod.events.emit

    def spy(kind, **data):
        if kind == "toast":
            toasts.append(data)
        return real_emit(kind, **data)

    with patch("orante.services.generate.events.emit", spy):
        _run_gen(job, eng)
    m1 = Manifest.load(d)
    im = m1.image("p01")
    assert im["status"] == "blocked" and im.get("flow_upload_refused") is True
    assert m1.seg(1)["error"] == genmod.flow_refused_text("p01")
    assert "Grok" in m1.seg(1)["error"] and "网页手动上传也一样" in m1.seg(1)["error"]
    assert sum(1 for x in job.lines if "⛔" in x) == 1, "只说一次，不刷屏"
    assert [t["text"] for t in toasts if "Flow 不收" in t.get("text", "")] == ["p01 这张图 Flow 不收"]


def test_flow_refused_image_digested_by_desc_refill():
    """能消化就消化：原图传不上去，自动按描述语生成一张新图补上，接着出片；不打扰用户。"""
    d, m = _make_task("T5896", [("p01", "ok", BASEBALL)], [(1, "p01", "planned")], rtc=False)
    orig = Manifest.load(d).image("p01")["sha1"]
    job = FakeJob("T5896", "A001")
    eng = RefuseOriginalEngine(_acc(), job.log, {orig}, can_make_images=True)
    _run_gen(job, eng)
    m1 = Manifest.load(d)
    im = m1.image("p01")
    assert m1.seg(1)["status"] == "collected", (m1.seg(1), job.lines[-10:])
    assert im["status"] == "ok" and "flow_upload_refused" not in im
    assert im["sha1"] != orig
    assert not any("⛔" in x for x in job.lines)
    assert any("不带参考图" in x for x in job.lines), "单图任务：只照描述语生成，不用传不上去的原图"
    assert (d / "images" / "_replaced").is_dir(), "原图留底，不丢"
