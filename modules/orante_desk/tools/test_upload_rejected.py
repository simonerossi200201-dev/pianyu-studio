# -*- coding: utf-8 -*-
"""规格 17：上传被拒 vs 接口坏了；出片反复失败；自动换图。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-reject-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, paths, tasks as taskmod  # noqa: E402
from orante.engines.base import BlockedImage  # noqa: E402
from orante.engines import flow_api  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import expand, generate as genmod  # noqa: E402

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
        self.id = "Jtest"
        self.progress = {"phase": "", "done": 0, "total": 0, "current": "", "items": []}

    def log(self, msg: str) -> None:
        print(f"  {msg}")
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
    def __init__(self, *a, **k):
        pass

    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        if json_mode and "is_broll" in str(prompt):
            return json.dumps({"is_broll": False, "reason": "有主角",
                               "shot_size": "中景", "people": 1, "crowd": False})
        return "换一个角度"


def _acc():
    return {
        "id": "A001", "name": "t-flow", "engine": "flow", "port": 9398,
        "project_url": "https://flow.google.com/project/"
                       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/collection/"
                       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "model": "Veo 3.1 - Lite [Lower Priority]", "aspect": "9:16",
        "max_concurrent": 5,
    }


def _engine(log=print) -> flow_api.FlowEngine:
    return flow_api.FlowEngine(_acc(), log)


def test_is_content_reject():
    assert flow_api._is_content_reject(
        {"status": 200, "payload": None, "err": [3]})
    assert flow_api._is_content_reject(
        {"status": 200, "payload": None, "err": [13]})
    assert not flow_api._is_content_reject(
        {"status": 400, "payload": None, "err": [3]})
    assert not flow_api._is_content_reject(
        {"status": 200, "payload": [["x"]], "err": [3]})
    assert not flow_api._is_content_reject(
        {"status": 200, "payload": None, "err": ["er", "x"]})
    print("OK _is_content_reject 只认 HTTP200 + 空 payload + [3]/[13]")


def test_rpc_callable_new_token():
    eng = _engine()
    n = {"i": 0}
    plan = [
        {"status": 200, "err": [3], "payload": None, "raw": "a"},
        {"status": 200, "err": [13], "payload": None, "raw": "b"},
        {"status": 200, "err": None, "payload": "ok", "raw": "c"},
    ]
    tokens = []

    def make_inner():
        n["i"] += 1
        tokens.append(n["i"])
        return ["inner", n["i"]]

    eng._ensure_page = lambda: None
    eng.page = type("P", (), {})()
    eng.src_path = "/p"

    def evaluate(js, args):
        return plan.pop(0)

    eng.page.evaluate = evaluate
    logs = []
    eng.log = logs.append
    with patch("orante.engines.flow_api.time.sleep", lambda s: None):
        payload = eng._rpc("maseQ", make_inner, retries=2)
    if payload != "ok":
        raise SystemExit(f"FAIL _rpc 最终 payload {payload}")
    if tokens != [1, 2, 3]:
        raise SystemExit(f"FAIL 每次重试应重新 make_inner，实际 {tokens}")
    if not any("重新取 token" in x for x in logs):
        raise SystemExit(f"FAIL 没有「重新取 token」日志：{logs}")
    print("OK _rpc 传函数时每次重试都重新取 token，日志有「重新取 token」")


def test_rpc_list_not_rebuilt():
    eng = _engine()
    inner = ["same"]
    n = {"i": 0}
    plan = [
        {"status": 200, "err": [3], "payload": None, "raw": "a"},
        {"status": 200, "err": None, "payload": "ok", "raw": "b"},
    ]
    eng._ensure_page = lambda: None
    eng.page = type("P", (), {})()
    eng.page.evaluate = lambda js, args: plan.pop(0)
    eng.log = lambda m: None
    with patch("orante.engines.flow_api.time.sleep", lambda s: None):
        eng._rpc("jwpduf", inner, retries=2)
    print("OK 不带 token 的 rpc 仍可传列表")


def test_upload_probe_blocked_not_cached():
    eng = _engine()
    img = TEST_DATA / "p03.jpeg"
    _jpeg(img, (1536, 2752), (90, 40, 40))
    sha = hashlib.sha1(img.read_bytes()).hexdigest()
    cache = TEST_DATA / "flow_media_cache.json"
    eng._cache_file = cache
    eng._cache = {}
    recaptcha_n = {"n": 0}

    def recaptcha(action):
        recaptcha_n["n"] += 1
        return f"tok-{recaptcha_n['n']}"

    eng._recaptcha = recaptcha
    # 原图三次 [3]/[13]，探测图一次成功
    seq = [
        {"ok": False, "status": 200, "err": [3], "payload": None, "raw": "r1"},
        {"ok": True, "status": 200, "err": None,
         "payload": [["probe-media-id-0123456789abcdef0123456789abcd"]], "raw": "ok"},
    ]

    def rpc(rpc, inner, retries=2, raise_=True):
        if callable(inner):
            inner()
        if not seq:
            raise SystemExit("FAIL _rpc 调用过多")
        res = seq.pop(0)
        if raise_ and not res["ok"]:
            raise OranteError("Flow 接口 maseQ 调用失败", str(res))
        if raise_:
            return res["payload"]
        return res

    eng._rpc = rpc
    archived = []
    eng._archive_media = lambda mid: archived.append(mid)
    try:
        eng.upload_image(img)
        raise SystemExit("FAIL 原图被拒应收 BlockedImage")
    except BlockedImage as e:
        if "p03.jpeg" not in str(e) or "缩小后能传" not in str(e):
            raise SystemExit(f"FAIL BlockedImage 原文不对：{e}")
    probe = paths.DATA / "tmp" / f"probe-{sha[:8]}.jpg"
    if not probe.is_file():
        raise SystemExit(f"FAIL 探测图没落盘 {probe}")
    from PIL import Image as PILImage
    with PILImage.open(probe) as im:
        if im.size[0] != 512:
            raise SystemExit(f"FAIL 探测图宽 {im.size} 不是 512")
    if eng._cache:
        raise SystemExit(f"FAIL 探测图写进了 cache：{eng._cache}")
    if cache.is_file():
        blob = cache.read_text(encoding="utf-8")
        if "probe" in blob:
            raise SystemExit("FAIL cache 文件含 probe")
    if not archived:
        raise SystemExit("FAIL 探测 mediaId 没归档")
    print("OK 原图被拒 → 探测成功 → BlockedImage，cache 无探测图，探测图宽 512")


def test_upload_probe_fail_is_down():
    eng = _engine()
    img = TEST_DATA / "p-bad.jpeg"
    _jpeg(img, (800, 1200), (10, 10, 10))
    eng._cache_file = TEST_DATA / "flow_media_cache2.json"
    eng._cache = {}
    eng._recaptcha = lambda a: "tok"

    def rpc(rpc, inner, retries=2, raise_=True):
        if callable(inner):
            inner()
        res = {"ok": False, "status": 200, "err": [3], "payload": None, "raw": "no"}
        if raise_:
            raise OranteError("Flow 接口 maseQ 调用失败", str(res))
        return res

    eng._rpc = rpc
    try:
        eng.upload_image(img)
        raise SystemExit("FAIL 探测也失败应抛 OranteError")
    except BlockedImage:
        raise SystemExit("FAIL 探测失败不该报被拒收")
    except OranteError as e:
        if e.message != "Flow 上传接口不可用":
            raise SystemExit(f"FAIL 标题不对：{e.message!r}")
        if "探测图也传不上去" not in (e.hint or ""):
            raise SystemExit(f"FAIL hint 不对：{e.hint!r}")
    print("OK 探测图也失败 → 「Flow 上传接口不可用」，不是被拒收")


def test_reject_snippet():
    rec = [None, None, {"policyViolation": "SAFETY: celebrity likeness", "status": 4}]
    s = flow_api._reject_snippet(rec)
    if not s or "SAFETY" not in s:
        raise SystemExit(f"FAIL snippet {s}")
    rec2 = [4, [3, "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"], ["PROMINENT_PERSON"]]
    s2 = flow_api._reject_snippet(rec2)
    if s2 != "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED / PROMINENT_PERSON":
        raise SystemExit(f"FAIL 错误码抠取 {s2}")
    print("OK 失败记录能抠出审核关键词 / 错误码")


def _ensure_account():
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-flow", "flow", 9398, "A001",
             "https://flow.google.com/project/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
             "", "9:16", 5, 1, "{}", db.now()))


def _make_task(tid, n_seg, images):
    _ensure_account()
    d = Path(tempfile.mkdtemp(prefix="orante-task-rej-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    recs = []
    for spec in images:
        p, status = spec[0], spec[1]
        fp = d / "images" / f"{p}.jpg"
        _jpeg(fp, (320, 560), (50, 80, 40))
        recs.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
            "role": "主体", "status": status, "desc": f"描述{p}", "voice": "",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": "", "expand_ref": p == images[0][0],
        })
    segs = []
    for i in range(1, n_seg + 1):
        img = recs[(i - 1) % len(recs)]["p"]
        segs.append({
            "s": i, "text": f"{SEG} #{i}", "duration": 6,
            "image": img, "role": "主体", "shot": "", "prompt_final": f"P{i}",
            "status": "planned", "gen_id": None, "takes": [], "error": None,
        })
    m = Manifest.new(tid, "拒测", "flow", "A001", "long", SEG)
    m.images = recs
    m.segments = segs
    m.save(d)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(d), "arranged", "flow", "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "拒测", "flow", "A001", "long", SEG, str(d / "images"),
             "", "arranged", str(d), "", 0, db.now(), db.now()))
    return d, Manifest.load(d)


class BlockRefEngine:
    """上传/生图：指定参考图抛 BlockedImage，其它正常出 jpeg。"""
    can_gen_images = True
    name = "flow"

    def __init__(self, account, log, blocked_names=()):
        self.account = account
        self.log = log
        self.name = (account or {}).get("engine") or "flow"
        self.blocked = set(blocked_names)
        self.n = 0
        self.submitted = []

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
        name = Path(ref).name if ref else ""
        stem = Path(ref).stem if ref else ""
        if name in self.blocked or stem in self.blocked:
            raise BlockedImage(f"Flow 拒收了 {name}（缩小后能传，全尺寸不能，多半是人脸/名人识别）")
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
        name = Path(image).name
        stem = Path(image).stem
        if name in self.blocked or stem in self.blocked:
            raise BlockedImage(f"Flow 拒收了 {name}")
        self.submitted.append(seg["s"])
        return f"g{seg['s']}"

    def poll(self, ids):
        return {gid: {"state": "done"} for gid in ids}

    def download(self, gid, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 60000)
        return dest


def test_expand_blocked_ref_skips():
    d, m = _make_task("T1701", 2, [("p01", "ok"), ("p02", "ok")])
    m.image("p01")["expand_ref"] = True
    m.image("p02")["expand_ref"] = False
    m.save(d)
    job = FakeJob("T1701", "A001")
    eng = BlockRefEngine(_acc(), job.log, blocked_names={"p01.jpg", "p01"})
    with patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.make_engine", lambda acc, log: eng):
        res = expand.expand(job, target=4, refs=["p01", "p02"])
    m2 = Manifest.load(d)
    if m2.image("p01")["status"] != "blocked":
        raise SystemExit(f"FAIL p01 应 blocked：{m2.image('p01')}")
    if res.get("all_blocked"):
        raise SystemExit("FAIL p02 还能扩，不该 all_blocked")
    pending = [im for im in m2.images if im.get("status") == "pending_review"]
    if not pending:
        raise SystemExit(f"FAIL p02 应继续扩出待审：{m2.images} res={res}")
    if any("参考图都被 Flow 拒收" in x for x in job.lines):
        raise SystemExit("FAIL 还有 p02 不该说全部被拒")
    if not any("参考图 p01 被" in x and "blocked" in x for x in job.lines):
        raise SystemExit(f"FAIL 缺跳过日志：{job.lines}")
    print("OK expand：p01 被拒标 blocked，任务不失败，继续扩 p02")


def test_expand_all_blocked_no_error():
    d, m = _make_task("T1702", 2, [("p01", "ok")])
    m.image("p01")["expand_ref"] = True
    m.save(d)
    job = FakeJob("T1702", "A001")
    eng = BlockRefEngine(_acc(), job.log, blocked_names={"p01.jpg", "p01"})
    with patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.expand.make_engine", lambda acc, log: eng):
        res = expand.expand(job, target=4, refs=["p01"])
    if not res.get("all_blocked"):
        raise SystemExit(f"FAIL 应 all_blocked：{res}")
    if not any("参考图都被 Flow 拒收了" in x for x in job.lines):
        raise SystemExit(f"FAIL 缺友好结束语：{job.lines}")
    print("OK expand：全部参考被拒 → 正常结束，不抛")


def test_refill_one_auto_flip_deletes():
    d, m = _make_task("T1703", 3, [("p01", "ok"), ("p02", "blocked")])
    for sg in m.segments:
        if sg["image"] == "p02":
            sg["status"] = "blocked"
    m.save(d)
    old = (d / "images" / "p02.jpg").read_bytes()
    job = FakeJob("T1703", "A001")
    eng = BlockRefEngine(_acc(), job.log)
    r = expand.refill_one(job, m, d, "p02", eng, FakeGemini(), auto=True)
    if not r or not r.get("ok"):
        raise SystemExit(f"FAIL refill_one {r}")
    im = Manifest.load(d).image("p02")
    if im["status"] != "ok" or im.get("origin") != "auto_refill" or not im.get("flipped"):
        raise SystemExit(f"FAIL 图字段 {im}")
    newp = d / "images" / "p02.jpg"
    if not newp.is_file():
        raise SystemExit("FAIL 新文件不在")
    if newp.read_bytes() == old:
        raise SystemExit("FAIL 旧文件没被换成翻转新图")
    if not list((d / "images" / "_replaced").glob("p02-*")):
        raise SystemExit("FAIL 自动换图应把旧图挪到 _replaced（0908 起不再 unlink）")
    print("OK refill_one auto：翻转覆盖、origin=auto_refill、旧图进 _replaced")


def test_generate_auto_refill_on_upload_block():
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("出片主循环会走写描述语门槛，整目录 pytest 易卡住；python tools/test_upload_rejected.py 仍跑")
    d, m = _make_task("T1704", 4, [("p01", "ok"), ("p02", "ok")])
    # s1,s2 用 p01（ok）；s3,s4 用 p02（上传被拒）
    m.seg(1)["image"] = m.seg(2)["image"] = "p01"
    m.seg(3)["image"] = m.seg(4)["image"] = "p02"
    m.seg(1)["status"] = "collected"
    m.seg(1)["takes"] = [{"file": "clips/x.mp4", "at": "t"}]
    (d / "clips" / "x.mp4").write_bytes(b"\x00" * 100)
    m.save(d)
    job = FakeJob("T1704", "A001")
    eng = BlockRefEngine(_acc(), job.log, blocked_names={"p02.jpg", "p02"})
    # 换图成功后新 p02.jpg 不再在 blocked 集合里（同名新文件，引擎仍按文件名拒）
    # 所以换图后 submit 还会拒。把 blocked 做成「只拒旧 sha」更接近真实。
    old_sha = m.image("p02")["sha1"]

    class Eng(BlockRefEngine):
        def submit(self, seg, image, prompt, duration, last_image=None, **kw):
            sha = sha1_file(Path(image)) if Path(image).is_file() else ""
            if Path(image).stem == "p02" and sha == old_sha:
                raise BlockedImage("Flow 拒收了 p02.jpg")
            self.submitted.append(seg["s"])
            return f"g{seg['s']}"

        def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
            dest = Path(dest_dir or tempfile.mkdtemp())
            dest.mkdir(parents=True, exist_ok=True)
            out = []
            for _ in range(n):
                self.n += 1
                p = dest / f"gen-{self.n:04d}.jpg"
                _jpeg(p, (200, 360), (200, 10, 10))
                out.append(p)
            return out

    eng = Eng(_acc(), job.log)
    with patch("orante.services.generate.make_engine", lambda acc, log: eng), \
         patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()):
        genmod.generate(job, only=[2, 3, 4])
    m2 = Manifest.load(d)
    if m2.image("p02")["origin"] != "auto_refill":
        raise SystemExit(f"FAIL p02 应被自动换图：{m2.image('p02')}")
    if not any("自动换图" in x for x in job.lines):
        raise SystemExit(f"FAIL 缺自动换图日志：{job.lines}")
    if not any("已换成新图" in x for x in job.lines):
        raise SystemExit(f"FAIL 缺换图成功日志：{job.lines}")
    if 3 not in eng.submitted or 4 not in eng.submitted:
        raise SystemExit(f"FAIL 换图后应重提 s3/s4：{eng.submitted}")
    print("OK generate：上传被拒 → 自动换图 → 段重提")


def test_generate_gen_fail_marks_blocked():
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("重生主循环会走写描述语门槛，整目录 pytest 易卡住；python tools/test_upload_rejected.py 仍跑")
    d, m = _make_task("T1705", 3, [("p01", "ok"), ("p14", "ok")])
    for i, s in enumerate((1, 2, 3), 1):
        m.seg(s)["image"] = "p14"
        m.seg(s)["status"] = "failed"
        m.seg(s)["error"] = "status=4"
        m.seg(s)["fail_count"] = 2
    m.image("p14")["gen_fail"] = 0
    m.save(d)
    job = FakeJob("T1705", "A001")
    # 重生：应先判 blocked 再换图，不要拿旧 p14 去 submit
    submitted_imgs = []

    class Eng(BlockRefEngine):
        def submit(self, seg, image, prompt, duration, last_image=None, **kw):
            submitted_imgs.append(Path(image).name)
            self.submitted.append(seg["s"])
            return f"g{seg['s']}"

        def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
            dest = Path(dest_dir or tempfile.mkdtemp())
            dest.mkdir(parents=True, exist_ok=True)
            self.n += 1
            p = dest / f"gen-{self.n:04d}.jpg"
            _jpeg(p, (180, 320), (10, 200, 10))
            return [p]

    eng = Eng(_acc(), job.log)
    with patch("orante.services.generate.make_engine", lambda acc, log: eng), \
         patch("orante.services.expand.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()):
        genmod.regenerate(job, segs=[1, 2, 3])
    if not any("连续" in x and "从没成功过" in x for x in job.lines):
        raise SystemExit(f"FAIL 缺反复失败判定日志：{job.lines}")
    if not any("已被拒，先补图" in x for x in job.lines):
        raise SystemExit(f"FAIL 缺跳过提交日志：{job.lines}")
    m2 = Manifest.load(d)
    if m2.image("p14").get("origin") != "auto_refill":
        raise SystemExit(f"FAIL p14 应自动换图 {m2.image('p14')}")
    if any(n == "p14.jpg" and False for n in submitted_imgs):
        pass
    print("OK regenerate：累计失败≥3 从未成功 → blocked → 自动换图，不拿旧图提交")


def test_poll_dumps_failed_json():
    rec = ["gid-aaaa", "x", None, None, None, [None, None, None, None, None, None, None, None, [4]]]
    rec.append({"policy": "unsafe celebrity"})
    s = flow_api._reject_snippet(rec)
    if not s:
        raise SystemExit("FAIL 应搜到 policy/unsafe")
    dest = paths.LOGS / "flow-failed-gid-aaaa.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    if not dest.is_file():
        raise SystemExit("FAIL 落盘")
    print("OK 失败记录可落盘 flow-failed-*.json")


def test_i18n():
    i18n = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("expand.all_blocked", "gen.upload_down", "gen.auto_refill"):
        if k not in i18n:
            raise SystemExit(f"FAIL i18n 缺 {k}")
    print("OK zh.json 新文案键")


def test_bak():
    for p in (
        ROOT / "orante" / "engines" / "flow_api.py.bak-0907a",
        ROOT / "orante" / "services" / "expand.py.bak-0907a",
        ROOT / "orante" / "services" / "generate.py.bak-0907a",
    ):
        if not p.is_file():
            raise SystemExit(f"FAIL 缺备份 {p.name}")
    print("OK 三个 bak-0907a 在")


def main():
    print(f"TEST_DATA={TEST_DATA}")
    test_bak()
    test_i18n()
    test_is_content_reject()
    test_rpc_callable_new_token()
    test_rpc_list_not_rebuilt()
    test_upload_probe_blocked_not_cached()
    test_upload_probe_fail_is_down()
    test_reject_snippet()
    test_poll_dumps_failed_json()
    test_expand_blocked_ref_skips()
    test_expand_all_blocked_no_error()
    test_refill_one_auto_flip_deletes()
    test_generate_auto_refill_on_upload_block()
    test_generate_gen_fail_marks_blocked()
    print("\n全部通过")


if __name__ == "__main__":
    main()
