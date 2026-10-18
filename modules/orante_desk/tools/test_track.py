# -*- coding: utf-8 -*-
"""验收规格 07：配图轨 plan/apply 确定性、扩图留弃、被拒补图、路由。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-track-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, config, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import REGISTRY, ACCOUNT_BOUND, load_all_services  # noqa: E402
from orante.models import Manifest, IMG_STATES, sha1_file  # noqa: E402
from orante.services import track as trackmod  # noqa: E402
from orante.services import arrange, expand  # noqa: E402

db.init()
load_all_services()

SEG_TEXT = "Stay for 7 seconds and type Amen. God bless you."


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "Jtest"

    def log(self, msg: str) -> None:
        print(f"  {msg}")
        self.lines.append(msg)

    def check(self) -> None:
        if self.cancelled.is_set():
            from orante.scheduler import JobCancelled
            raise JobCancelled()


class FakeGemini:
    def __init__(self, api_key=None, model=None):
        if api_key is not None and str(api_key).strip() == "":
            raise OranteError("还没填 Gemini API Key", "设置 → Gemini")
        self.api_key = api_key or "fake"

    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        text = str(prompt or "")
        name = Path(image_path).name
        if json_mode and "is_broll" in text:
            is_broll = "broll" in name.lower()
            return json.dumps({
                "is_broll": is_broll,
                "reason": "大全景" if is_broll else "有明确主角",
                "shot_size": "远景" if is_broll else "中景",
                "people": 20 if is_broll else 1,
                "crowd": is_broll,
            }, ensure_ascii=False)
        if "只改变拍摄距离和角度" in text or "换景别" in text or "指定景别" in text:
            return "同一个人物，同样的服装、发型、姿态和表情，只改变拍摄距离和角度：近景侧面。背景延续原图环境。"
        return "描述语"


class FakeEngine:
    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.n = 0

    def connect(self):
        pass

    def close(self):
        pass

    def gen_images(self, prompt, ref=None, n=4, dest_dir=None, **kw):
        dest = Path(dest_dir or tempfile.mkdtemp())
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for _ in range(n):
            self.n += 1
            p = dest / f"gen-{self.n:04d}.jpg"
            _jpeg(p, (30 + self.n * 7, 80, 50))
            out.append(p)
        return out


def _jpeg(path: Path, color=(40, 80, 60)) -> None:
    from PIL import Image as PILImage
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", (320, 560), color).save(path, "JPEG", quality=80)


def _ensure_account():
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()))
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A002",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A002", "t-grok", "grok", 9397, "A002", "", "", "9:16", 4, 1, "{}", db.now()))


def _make_manifest(n_seg=30, n_img=5, engine="flow", account="A001",
                   tid="T1007", extra_images=None) -> tuple[str, Path, Manifest]:
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-track-"))
    (task_dir / "images").mkdir()
    images = []
    for i in range(1, n_img + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _jpeg(fp, (40 + i * 20, 80, 60))
        images.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
            "role": "主体", "status": "ok", "desc": f"描述{p}", "voice": "",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": str(fp),
        })
    if extra_images:
        images.extend(extra_images)
    segs = []
    for i in range(1, n_seg + 1):
        segs.append({
            "s": i, "text": f"{SEG_TEXT} #{i}", "duration": 6,
            "image": "", "role": "主体", "shot": "", "prompt_final": "old-prompt",
            "status": "planned", "gen_id": None, "takes": [], "error": None,
        })
    m = Manifest.new(tid, "配图轨测", engine, account, "long", SEG_TEXT)
    m.images = images
    m.segments = segs
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(task_dir), "described", engine, account, db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "配图轨测", engine, account, "long", SEG_TEXT, str(task_dir / "images"),
             "", "described", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_jobs_registered() -> None:
    for k in ("arrange",):
        if k not in REGISTRY:
            raise SystemExit(f"FAIL 没注册 {k}，现有 {sorted(REGISTRY)}")
        if k in ACCOUNT_BOUND:
            raise SystemExit(f"FAIL {k} 不该占账号")
    for k in ("expand", "refill"):
        if k not in REGISTRY:
            raise SystemExit(f"FAIL 没注册 {k}")
        if k not in ACCOUNT_BOUND:
            raise SystemExit(f"FAIL {k} 应该占账号")
    if "arrange_preview" in REGISTRY:
        raise SystemExit("FAIL arrange_preview 不该是 job")
    if "pending_review" not in IMG_STATES:
        raise SystemExit(f"FAIL IMG_STATES 缺 pending_review：{IMG_STATES}")
    print("OK jobs：arrange 不占账号；expand/refill 占账号；pending_review 在 IMG_STATES")


def test_plan_deterministic() -> None:
    tid, d, m = _make_manifest(30, 5, tid="T1007")
    pins = {"1": "p01", "30": "p01"}
    pools = [{"from": 10, "to": 20, "images": ["p02", "p03"]}]
    a = trackmod.plan(m.segments, m.images, pins=pins, hold=3, jitter=1,
                      unit="seg", pools=pools, seed=42)
    b = trackmod.plan(m.segments, m.images, pins=pins, hold=3, jitter=1,
                      unit="seg", pools=pools, seed=42)
    pa = [r["p_new"] for r in a["rows"]]
    pb = [r["p_new"] for r in b["rows"]]
    if pa != pb:
        raise SystemExit(f"FAIL 同 seed 两次 plan 不一致\n{pa}\n{pb}")
    if len(a["rows"]) != 30:
        raise SystemExit(f"FAIL 行数 {len(a['rows'])}")
    if any(not r["p_new"] for r in a["rows"]):
        raise SystemExit(f"FAIL 有段没分到图：{[r['s'] for r in a['rows'] if not r['p_new']]}")
    if a["rows"][0]["p_new"] != "p01" or a["rows"][-1]["p_new"] != "p01":
        raise SystemExit(f"FAIL 钉区不是 p01：s1={a['rows'][0]['p_new']} s30={a['rows'][-1]['p_new']}")
    mid = [r for r in a["rows"] if 10 <= r["s"] <= 20]
    bad = [r["s"] for r in mid if r["p_new"] not in ("p02", "p03")]
    if bad:
        raise SystemExit(f"FAIL 限定区用了别的图：{[(r['s'], r['p_new']) for r in mid]}")
    print(f"OK plan 两次一致 seed=42：s1/s30=p01，s10-s20 只用 p02/p03，换图 {a['summary']['switch_after']} 次")
    return tid, d, m, a


def test_apply_fills(tid=None, d=None, m=None, planned=None) -> None:
    if tid is None:
        tid, d, m, planned = test_plan_deterministic()
    trackmod.apply(m, planned["rows"])
    m.save(d)
    m2 = Manifest.load(d)
    missing = [sg["s"] for sg in m2.segments if not sg.get("image")]
    if missing:
        raise SystemExit(f"FAIL apply 后没图的段：{missing}")
    if m2.seg(1)["image"] != "p01" or m2.seg(30)["image"] != "p01":
        raise SystemExit(f"FAIL 钉区 apply 后 {m2.seg(1)['image']} / {m2.seg(30)['image']}")
    for sg in m2.segments:
        if sg["status"] != "planned":
            raise SystemExit(f"FAIL s{sg['s']} status={sg['status']}")
        if sg.get("prompt_final"):
            raise SystemExit(f"FAIL s{sg['s']} prompt_final 没清空")
    print("OK apply 后 30 段都有 image，status=planned，prompt_final 已清空")


def test_apply_locked() -> None:
    tid, d, m = _make_manifest(4, 3, tid="T1008")
    res = trackmod.plan(m.segments, m.images, pins={"1": "p01"}, hold=2, jitter=0,
                        unit="seg", seed=1)
    m.segments[1]["status"] = "submitted"
    m.segments[1]["image"] = "p02"
    m.segments[1]["prompt_final"] = "keep-me"
    # 让 rows 试图改 s2
    for r in res["rows"]:
        if r["s"] == 2:
            r["p_new"] = "p03"
            r["changed"] = True
    try:
        trackmod.apply(m, res["rows"])
    except OranteError as e:
        if "s002" not in e.message and "s2" not in e.message:
            raise SystemExit(f"FAIL 锁段报错没点名：{e.message}")
        print(f"OK submitted 段不许改：{e.message}")
    else:
        raise SystemExit("FAIL submitted 段 apply 应该抛 OranteError")


def test_pending_not_in_pool() -> None:
    imgs = [
        {"p": "p01", "role": "主体", "status": "ok"},
        {"p": "p02", "role": "主体", "status": "pending_review"},
        {"p": "p03", "role": "主体", "status": "blocked"},
        {"p": "p04", "role": "空镜", "status": "ok"},
    ]
    pool = trackmod.subject_pool(imgs)
    if pool != ["p01"]:
        raise SystemExit(f"FAIL subject_pool 应该只有 p01，实际 {pool}")
    print("OK pending_review / blocked / 空镜 不进配图轨")


def test_arrange_job() -> None:
    tid, d, m = _make_manifest(8, 4, tid="T1009", engine="grok", account="A002")
    job = FakeJob(tid)
    track = {
        "pins": {"1": "p01", "8": "p01"},
        "pools": [{"from": 3, "to": 5, "images": ["p02", "p03"]}],
        "hold": 2, "jitter": 0, "unit": "seg", "seed": 7,
    }
    arrange.arrange(job, track=track)
    m2 = Manifest.load(d)
    if taskmod.get(tid)["status"] != "arranged":
        raise SystemExit(f"FAIL 状态 {taskmod.get(tid)['status']}")
    if m2.seg(1)["image"] != "p01" or m2.seg(8)["image"] != "p01":
        raise SystemExit("FAIL grok 任务钉区没写上")
    if not all(sg.get("image") for sg in m2.segments):
        raise SystemExit("FAIL grok 任务有段没图")
    if m2.track.get("seed") != 7:
        raise SystemExit(f"FAIL track.seed {m2.track.get('seed')}")
    prev = arrange.preview(tid, track)
    if len(prev["rows"]) != 8 or "summary" not in prev:
        raise SystemExit(f"FAIL preview：{prev.keys()}")
    print("OK arrange job（Grok 任务）status=arranged，preview 返回 rows/summary")
    return tid, d


def test_keep_discard(tid: str | None = None, d: Path | None = None) -> None:
    if tid is None:
        tid, d = test_arrange_job()
    m = Manifest.load(d)
    p = "p99"
    cand = d / "images" / "_候选" / "p99.jpg"
    _jpeg(cand, (10, 10, 90))
    m.images.append({
        "p": p, "file": "images/_候选/p99.jpg", "sha1": sha1_file(cand),
        "role": "主体", "status": "pending_review", "desc": "", "voice": "",
        "eye": "闭眼", "scene": "常规", "measure": {}, "origin": "expand", "src": "",
    })
    m.save(d)
    im = expand.keep_image(tid, p)
    if im["status"] != "ok":
        raise SystemExit(f"FAIL keep status {im['status']}")
    if im["file"].replace("\\", "/") != "images/p99.jpg":
        raise SystemExit(f"FAIL keep 路径 {im['file']}")
    if not (d / "images" / "p99.jpg").is_file():
        raise SystemExit("FAIL keep 后 images/p99.jpg 不在")
    if (d / "images" / "_候选" / "p99.jpg").is_file():
        raise SystemExit("FAIL keep 后 _候选 还留着")
    print("OK 留：pending_review → ok，文件挪到 images/")

    # 再放一张弃
    p2 = "p98"
    cand2 = d / "images" / "_候选" / "p98.jpg"
    _jpeg(cand2, (90, 10, 10))
    m = Manifest.load(d)
    m.images.append({
        "p": p2, "file": "images/_候选/p98.jpg", "sha1": sha1_file(cand2),
        "role": "主体", "status": "pending_review", "desc": "", "voice": "",
        "eye": "闭眼", "scene": "常规", "measure": {}, "origin": "expand", "src": "",
    })
    m.save(d)
    expand.discard_image(tid, p2)
    m2 = Manifest.load(d)
    if m2.image(p2):
        raise SystemExit("FAIL 弃了还在 manifest")
    if not (d / "images" / "_弃用" / "p98.jpg").is_file():
        raise SystemExit("FAIL 弃了文件没进 _弃用")
    print("OK 弃：记录删除，文件进 images/_弃用/")


def test_expand_and_refill() -> None:
    tid, d, m = _make_manifest(4, 2, tid="T1010")
    # 2 张主体，目标 4，扩出 pending_review
    config.set("expand.min_images", 4)
    config.set("expand.per_round", 2)
    config.set("expand.flip_replacements", True)
    job = FakeJob(tid, account_id="A001")
    with patch("orante.services.expand.Gemini", FakeGemini), \
         patch("orante.services.expand.ai.client", lambda job, **kw: FakeGemini()), \
         patch("orante.services.expand.make_engine", lambda acc, log: FakeEngine(acc, log)):
        res = expand.expand(job, target=4)
    m2 = Manifest.load(d)
    pending = [im for im in m2.images if im.get("status") == "pending_review"]
    if len(pending) < 2:
        raise SystemExit(f"FAIL expand 待审太少：images={m2.images} res={res}")
    for im in pending:
        rel = im["file"].replace("\\", "/")
        if not rel.startswith("images/_候选/"):
            raise SystemExit(f"FAIL 待审路径 {rel}")
        if not (d / im["file"]).is_file():
            raise SystemExit(f"FAIL 待审文件不在 {im['file']}")
    if trackmod.subject_pool(m2.images) != ["p01", "p02"]:
        raise SystemExit(f"FAIL 待审进了配图池 {trackmod.subject_pool(m2.images)}")
    print(f"OK expand 新增 {len(pending)} 张 pending_review，不进配图轨")

    # refill：把 p02 标 blocked，段也 blocked，补完指向不变
    m2.image("p02")["status"] = "blocked"
    for sg in m2.segments:
        if sg.get("image") == "p02" or not sg.get("image"):
            sg["image"] = "p02"
            sg["status"] = "blocked"
            sg["prompt_final"] = "blocked-prompt"
    # 至少留 p01 当参考
    m2.save(d)
    old_sha = m2.image("p02")["sha1"]
    job2 = FakeJob(tid, account_id="A001")
    with patch("orante.services.expand.Gemini", FakeGemini), \
         patch("orante.services.expand.ai.client", lambda job, **kw: FakeGemini()), \
         patch("orante.services.expand.make_engine", lambda acc, log: FakeEngine(acc, log)):
        r2 = expand.refill(job2)
    m3 = Manifest.load(d)
    im2 = m3.image("p02")
    if im2["status"] != "ok" or im2.get("origin") != "replace":
        raise SystemExit(f"FAIL refill 后 p02 {im2}")
    if im2["sha1"] == old_sha:
        raise SystemExit("FAIL refill 没换 sha1")
    if not list((d / "images" / "_replaced").glob("p02-*")):
        raise SystemExit("FAIL 旧图没进 _replaced")
    for sg in m3.segments:
        if sg.get("image") == "p02":
            if sg["status"] != "planned":
                raise SystemExit(f"FAIL 被拒段没打回 planned：s{sg['s']} {sg['status']}")
            if sg.get("prompt_final"):
                raise SystemExit("FAIL 被拒段 prompt_final 没清空")
            # 指向不动
            if sg["image"] != "p02":
                raise SystemExit("FAIL 配图轨指向变了")
    if not r2.get("replaced"):
        raise SystemExit(f"FAIL refill 返回 {r2}")
    print("OK refill：p02 覆盖、origin=replace、段仍指向 p02 且 planned")


def test_http_routes() -> None:
    from orante import server
    paths = {getattr(r, "path", "") for r in server.app.routes}
    need = (
        "/api/tasks/{tid}/track",
        "/api/tasks/{tid}/track/preview",
        "/api/tasks/{tid}/track/apply",
        "/api/tasks/{tid}/image/{p}/keep",
        "/api/tasks/{tid}/image/{p}/discard",
    )
    for p in need:
        if p not in paths:
            raise SystemExit(f"FAIL 缺路由 {p}，现有 {sorted(x for x in paths if 'track' in x or 'keep' in x or 'discard' in x)}")
    print("OK 五条 track/keep/discard 路由都挂在 FastAPI 上")


def test_js() -> None:
    import subprocess
    js = ROOT / "ui" / "js" / "panels" / "track.js"
    txt = js.read_text(encoding="utf-8")
    if "只对 Flow" in txt or "state.kind !== 'flow'" in txt or "engine !== 'flow'" in txt:
        raise SystemExit("FAIL 前端还在拦 Grok 任务")
    if ".pn" not in txt and "cellP" not in txt:
        raise SystemExit("FAIL 色条格子没有编号")
    if "slice(0, 90)" in txt:
        raise SystemExit("FAIL track.js 文案列还在 slice(0, 90) 硬截断")
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL node --check track.js\n{r.stderr}")
    print("OK 前端：Grok 能进配图轨；色条有编号；文案列不再 slice(0, 90)；node --check 通过")


def main() -> None:
    print(f"TEST_DATA={TEST_DATA}")
    test_jobs_registered()
    tid, d, m, planned = test_plan_deterministic()
    test_apply_fills(tid, d, m, planned)
    test_apply_locked()
    test_pending_not_in_pool()
    tid2, d2 = test_arrange_job()
    test_keep_discard(tid2, d2)
    test_expand_and_refill()
    test_http_routes()
    test_js()
    print("\n全部通过")


if __name__ == "__main__":
    main()
