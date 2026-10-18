# -*- coding: utf-8 -*-
"""规格 47：扩图从编排解绑、编排门槛降到 prepared、出片前才查描述语。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec47-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file, sha256_file  # noqa: E402
from orante.services import arrange, describe, expand, generate, rescan  # noqa: E402
from orante.services import descqueue  # noqa: E402

db.init()
load_all_services()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


class FakeJob:
    def __init__(self, task_id, account_id=None):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J47"
        self.progress = {}

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        if self.cancelled.is_set():
            from orante.scheduler import JobCancelled
            raise JobCancelled()

    def set_progress(self, **kw) -> None:
        self.progress.update(kw)


class FakeGemini:
    def __init__(self, *a, **kw):
        self.n = 0

    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        self.n += 1
        p = Path(image_path).stem
        text = str(prompt or "")
        if json_mode and "is_broll" in text:
            return json.dumps({
                "is_broll": False, "reason": "有主角", "shot_size": "中景",
                "people": 1, "crowd": False,
            }, ensure_ascii=False)
        if json_mode and ("room_left" in text or "摄影指导" in text):
            return json.dumps({
                "shot_size": "中景", "facing": "正面", "people": 1,
            }, ensure_ascii=False)
        pose = (
            'POSE_JSON={"head":"正面","eyes":"看向听众","hands":"自然下垂",'
            '"body":"站立","expr":"平静","text":"正面站立看向听众"}'
        )
        return f"描述语{p}【闭眼|常规】\n{pose}"


def _jpeg(path: Path, color=(40, 80, 60)) -> None:
    from PIL import Image as PILImage
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", (320, 560), color).save(path, "JPEG", quality=80)


def _ensure_account():
    if db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()))


def _make(tid, n_img=3, n_seg=6, status="prepared", descs=None) -> tuple[str, Path, Manifest]:
    _ensure_account()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-47-"))
    (task_dir / "images").mkdir()
    images = []
    for i in range(1, n_img + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _jpeg(fp, (40 + i * 20, 80, 60))
        desc = ""
        if descs is not None and i - 1 < len(descs):
            desc = descs[i - 1]
        images.append({
            "p": p, "file": f"images/{p}.jpg",
            "sha1": sha1_file(fp), "sha256": sha256_file(fp),
            "role": "主体", "status": "ok", "desc": desc, "voice": "",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": fp.name, "pose": {},
        })
    segs = []
    for i in range(1, n_seg + 1):
        segs.append({
            "s": i, "text": f"seg {i}", "duration": 6,
            "image": "", "role": "主体", "shot": "",
            "prompt_final": "", "status": "planned", "gen_id": None,
            "takes": [], "error": None,
        })
    m = Manifest.new(tid, "规格47", "flow", "A001", "short", "hello")
    m.images = images
    m.segments = segs
    m.save(task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "规格47", "flow", "A001", "short", "hello", str(task_dir / "images"),
         "", status, str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_arrange_without_desc():
    print("== 没写描述语也能配图轨 ==")
    tid, d, m = _make("T4701", n_img=3, n_seg=8, status="prepared", descs=["", "", ""])
    if describe.missing_subject_ps(m) != ["p01", "p02", "p03"]:
        bad(f"缺描述语没点名：{describe.missing_subject_ps(m)}")
        return
    try:
        prev = arrange.preview(tid, {"hold": 2, "jitter": 0, "seed": 7})
    except OranteError as e:
        bad(f"预演被拦：{e.message}")
        return
    rows = prev.get("rows") or []
    if len(rows) != 8 or not all(r.get("p_new") for r in rows):
        bad(f"预演行不对：{rows[:3]}")
        return
    job = FakeJob(tid)
    try:
        arrange.arrange(job, {"hold": 2, "jitter": 0, "seed": 7})
    except OranteError as e:
        bad(f"应用被拦：{e.message}")
        return
    t = taskmod.get(tid)
    if t["status"] != "arranged":
        bad(f"应用后状态 {t['status']} 应为 arranged")
        return
    m2 = Manifest.load(d)
    if not all(sg.get("image") for sg in m2.segments):
        bad("应用后还有段没图")
        return
    ok("prepared 无描述语 → 预演 + 应用 → arranged")


def test_generate_blocks_missing_desc():
    print("== 出片前点名缺描述语的图 ==")
    tid, d, m = _make("T4702", n_img=3, n_seg=4, status="arranged",
                      descs=["有了", "", ""])
    for i, sg in enumerate(m.segments):
        sg["image"] = f"p{min(i + 1, 3):02d}"
    m.save(d)
    job = FakeJob(tid)
    try:
        generate.generate(job)
        bad("缺描述语居然没拦")
        return
    except OranteError as e:
        blob = (e.message or "") + (e.hint or "")
        if "p02" not in blob or "p03" not in blob:
            bad(f"没点名 p02/p03：{e.message} / {e.hint}")
            return
        if "p01" in (e.hint or ""):
            bad(f"不该点名已有描述语的 p01：{e.hint}")
            return
        if "不能出片" not in (e.message or ""):
            bad(f"原文不对：{e.message}")
            return
        ok(f"拦住并点名：{e.message} / {e.hint}")


def test_describe_skips_existing():
    print("== 写描述语跳过已有的 ==")
    tid, d, m = _make("T4703", n_img=3, n_seg=2, status="described",
                      descs=["旧描述语 p01【闭眼|常规】", "旧描述语 p02【闭眼|常规】", ""])
    fake = FakeGemini()
    job = FakeJob(tid)
    with patch("orante.services.describe.ai.client", lambda job, **kw: fake), \
         patch("orante.services.describe.aikeys.list_all", lambda enabled_only=True: [{"id": "k1"}]), \
         patch("orante.services.describe.ai.workers", lambda n: 1), \
         patch("orante.services.describe.ai.calls", lambda: fake.n):
        res = describe.describe(job)
    m2 = Manifest.load(d)
    if (m2.image("p01") or {}).get("desc") != "旧描述语 p01【闭眼|常规】":
        bad("已有描述语被重写了 p01")
        return
    if (m2.image("p02") or {}).get("desc") != "旧描述语 p02【闭眼|常规】":
        bad("已有描述语被重写了 p02")
        return
    if not str((m2.image("p03") or {}).get("desc") or "").strip():
        bad("缺的 p03 没写上")
        return
    if res.get("skipped") != 2 and res.get("described") != 1:
        # skipped 只在 todo 为空时返回；有 todo 时看 described=1
        if res.get("described") != 1:
            bad(f"返回不对：{res}")
            return
    # ask_image：描述语 + 测量，空镜判定若无 broll_check 再加一次。p03 至少 2 次，p01/p02 0 次
    if fake.n < 2:
        bad(f"调用次数太少：{fake.n}")
        return
    # 3 张全写大约 6–9 次；只写 1 张应明显少于
    if fake.n > 5:
        bad(f"已有的也被问了？调用 {fake.n} 次，日志 {job.lines}")
        return
    if not any("跳过" in ln for ln in job.lines):
        bad(f"日志没写跳过：{job.lines[:6]}")
        return
    t = taskmod.get(tid)
    if t["status"] != "described":
        bad(f"状态被拽走了：{t['status']}")
        return
    ok(f"只写缺的那张，调用 {fake.n} 次，状态仍 described")


def test_expand_rec_empty_and_status_hold():
    print("== 扩图新图 desc/pose 为空，状态不回退 ==")
    rec = expand._new_image_rec("p09", "images/_候选/p09.jpg", "主体", "pending_review", "expand")
    if rec.get("desc") != "":
        bad(f"新图 desc 不是空：{rec.get('desc')!r}")
        return
    if rec.get("pose") not in ({}, None):
        bad(f"新图 pose 不是空：{rec.get('pose')!r}")
        return
    tid, d, m = _make("T4704", n_img=2, n_seg=2, status="arranged",
                      descs=["有了", "也有了"])
    taskmod.set_status(tid, "described")  # 单调性应吞掉
    t = taskmod.get(tid)
    if t["status"] != "arranged":
        bad(f"arranged 被拽回 {t['status']}")
        return
    ok("新图 desc/pose 空；arranged ← described 被跳过")


def test_queue_includes_described_with_new():
    print("== 跨任务队列：已 described 但有新图缺 desc 也进队 ==")
    db.run("DELETE FROM tasks")
    _make("T4705", n_img=2, n_seg=1, status="described", descs=["有了", ""])
    items = descqueue.scan()
    ids = [t["id"] for t in items]
    if "T4705" not in ids:
        bad(f"described 缺 desc 没进队：{ids}")
        return
    ok("described + 缺 desc 进队列")


def test_rescan_keeps_pending():
    print("== 静默重扫留下 _候选 待审图 ==")
    tid, d, m = _make("T4706", n_img=2, n_seg=2, status="prepared", descs=["a", "b"])
    cand = d / "images" / "_候选"
    cand.mkdir(parents=True, exist_ok=True)
    fp = cand / "p09.jpg"
    _jpeg(fp, (10, 20, 30))
    m.images.append({
        "p": "p09", "file": "images/_候选/p09.jpg",
        "sha1": sha1_file(fp), "sha256": sha256_file(fp),
        "role": "主体", "status": "pending_review", "desc": "", "voice": "",
        "eye": "闭眼", "scene": "常规", "measure": {},
        "origin": "expand", "src": "p09.jpg", "pose": {},
    })
    m.save(d)
    plan = rescan.build_plan(d, Manifest.load(d))
    ps = {im.get("p") for im in plan["new_images"]}
    if "p09" not in ps:
        bad(f"待审图被当成已删除：{ps} deleted={plan.get('deleted')}")
        return
    rec = next(im for im in plan["new_images"] if im.get("p") == "p09")
    if rec.get("status") != "pending_review":
        bad(f"待审状态丢了：{rec.get('status')}")
        return
    if "images/_候选" not in (rec.get("file") or "").replace("\\", "/"):
        bad(f"待审路径变了：{rec.get('file')}")
        return
    ok("重扫保留 _候选/p09 待审")


def test_counts_desc_missing():
    print("== counts.desc_missing ==")
    tid, d, m = _make("T4707", n_img=3, n_seg=2, status="arranged",
                      descs=["x", "", ""])
    c = taskmod.decorate_counts(m)
    if c.get("desc_missing") != 2:
        bad(f"desc_missing={c.get('desc_missing')}")
        return
    if "p02" not in (c.get("desc_missing_ps") or []):
        bad(f"没点名：{c.get('desc_missing_ps')}")
        return
    ok("counts 带 desc_missing=2")


def test_frontend():
    print("== 前端门槛 / 扩图入口 / 节点灯 ==")
    trk = (ROOT / "ui" / "js" / "panels" / "track.js").read_text(encoding="utf-8")
    gem = (ROOT / "ui" / "js" / "panels" / "gemini.js").read_text(encoding="utf-8")
    gen = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    app = (ROOT / "ui" / "js" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    checks = [
        ("track 门槛 prepared", 'min-status="prepared"' in trk and "atLeastPrepared" in trk),
        ("track 提示条", "track.need_desc" in trk and "goDescribe" in trk),
        ("gemini 扩图按钮", "ExpandUi" in gem and "<expand-ui" in gem
         and "gemini.expand_btn" in (ROOT / "ui" / "js" / "parts" / "expand-ui.js").read_text(encoding="utf-8")),
        ("gemini 只补这 N 张", "gemini.need_desc.fill" in gem),
        ("generate 缺描述语置灰", "missingDescN" in gen and "gen.need_desc" in gen),
        ("generate 去补写", "goDescribe" in gen),
        ("③ 灯 prepared 就亮", "at(x, 'prepared')" in app and "track:" in app),
        ("灯悬停 tip", "lampTips" in app and "node.tip.need_desc" in app),
        ("expand-ui.js 接入", 'src="/ui/js/parts/expand-ui.js"' in html),
        ("i18n 去写描述语", zh.get("track.need_desc.go") == "去写描述语"),
        ("i18n 只补这 N 张", "只补这" in (zh.get("gemini.need_desc.fill") or "")),
        ("i18n 缺 N 张", "缺 {n} 张描述语" == zh.get("gen.need_desc")),
    ]
    for name, cond in checks:
        (ok if cond else bad)(name)


def main():
    print("规格 47")
    test_arrange_without_desc()
    test_generate_blocks_missing_desc()
    test_describe_skips_existing()
    test_expand_rec_empty_and_status_hold()
    test_queue_includes_described_with_new()
    test_rescan_keeps_pending()
    test_counts_desc_missing()
    test_frontend()
    print()
    if fail:
        print(f"FAIL {fail}")
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
