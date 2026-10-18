# -*- coding: utf-8 -*-
"""规格 22：文案搜索归一、prompt_locked 写盘/重生/换图/配图轨。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-22-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import review, generate as genmod, track as trackmod  # noqa: E402
from orante import server as servermod  # noqa: E402

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
        pass

    def sleep(self, sec: float) -> None:
        pass


class FakeEngine:
    last = None

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.submitted = []
        FakeEngine.last = self

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 5

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        self.submitted.append({"s": seg["s"], "engine": self.account.get("engine"), "prompt": prompt})
        return "g%s" % seg["s"]

    def poll(self, ids):
        return {gid: {"state": "done"} for gid in ids}

    def download(self, gid, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 200)
        return dest


def _ensure_account():
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()))


def _make_task(tid="T2200", engine="flow", account="A001", n=3, status="collected"):
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-22-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
    images = []
    for i in range(1, 3):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        fp.write_bytes(b"\xff\xd8\xff\xd9" + b"x" * 80)
        images.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
            "role": "主体", "status": "ok", "desc": f"描述{p}", "voice": "【音色】测",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": str(fp),
        })
    segs = []
    for i in range(1, n + 1):
        segs.append({
            "s": i, "text": f"{SEG_TEXT} #{i}", "duration": 6,
            "image": "p01" if i % 2 else "p02", "role": "主体",
            "shot": "【镜头】静", "prompt_final": f"FINAL-{i}",
            "status": "collected" if status == "collected" else "planned",
            "gen_id": f"old{i}", "takes": [], "error": None,
        })
    m = Manifest.new(tid, "规格22", engine, account, "long", SEG_TEXT)
    m.images = images
    m.segments = segs
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(task_dir), status, engine, account, db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "规格22", engine, account, "long", SEG_TEXT, str(task_dir / "images"),
             "", status, str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)

T0013 = Path(r"D:\1_PROJECT\2026-09-07\B\T0013-李良v04\manifest.json")


def _js_search():
    node = r"""
    const fs = require('fs');
    const vm = require('vm');
    const ctx = { window: {}, console };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync('ui/js/parts/textsearch.js','utf8'), ctx);
    const TS = ctx.window.TextSearch;
    const segs = JSON.parse(process.argv[1]);
    const q1 = 'Nineveh';
    const h1 = TS.findS(segs, q1);
    const q2 = '(John 8:34–35)';
    const h2 = TS.findS(segs, q2);
    const q3 = '(John 8:34-35)';
    const h3 = TS.findS(segs, q3);
    console.log(JSON.stringify({h1, h2, h3, n: TS.normalize(q2)}));
    """
    return node


def test_search_t0013():
    if not T0013.is_file():
        raise SystemExit(f"FAIL 找不到 T0013 {T0013}")
    m = json.loads(T0013.read_text(encoding="utf-8"))
    segs = [{"s": sg["s"], "text": sg.get("text") or "", "prompt_final": sg.get("prompt_final") or ""}
            for sg in m["segments"]]
    r = subprocess.run(
        ["node", "-e", _js_search(), json.dumps(segs, ensure_ascii=False)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"FAIL node 搜索\n{r.stdout}\n{r.stderr}")
    data = json.loads(r.stdout.strip().splitlines()[-1])
    if data["h1"] != [3]:
        raise SystemExit(f"FAIL Nineveh 应命中 s003，实际 {data['h1']}")
    if 5 not in data["h2"]:
        raise SystemExit(f"FAIL 弯破折号 (John 8:34–35) 没命中 s005，实际 {data['h2']}")
    if 5 not in data["h3"]:
        raise SystemExit(f"FAIL 直连字符 John 8:34-35 也应命中，实际 {data['h3']}")
    print("OK 搜索：Nineveh → s003；弯破折号 (John 8:34–35) → s005")


def test_set_prompt_and_lock():
    tid, d, m = _make_task("T2201", n=3, status="collected")
    r = review.set_prompt(tid, 1, text="手写提示词 ALPHA")
    if not r.get("locked") or r.get("chars") != len("手写提示词 ALPHA"):
        raise SystemExit(f"FAIL set_prompt 返回 {r}")
    m2 = Manifest.load(d)
    if m2.seg(1).get("prompt_final") != "手写提示词 ALPHA" or not m2.seg(1).get("prompt_locked"):
        raise SystemExit(f"FAIL 没写进 manifest {m2.seg(1)}")
    body = review.get_review(tid)
    sg = next(x for x in body["segments"] if x["s"] == 1)
    if not sg.get("prompt_locked"):
        raise SystemExit("FAIL get_review 没带 prompt_locked")

    r2 = review.set_image(tid, 1, "p02")
    if not r2.get("kept_locked"):
        raise SystemExit(f"FAIL 换图应 kept_locked {r2}")
    m3 = Manifest.load(d)
    if m3.seg(1).get("prompt_final") != "手写提示词 ALPHA":
        raise SystemExit(f"FAIL 换图把手写提示词清掉了 {m3.seg(1).get('prompt_final')!r}")
    if m3.seg(1).get("image") != "p02":
        raise SystemExit(f"FAIL 换图没换上 {m3.seg(1).get('image')}")

    r3 = review.set_prompt(tid, 1, reset=True)
    if r3.get("locked") or r3.get("chars") != 0:
        raise SystemExit(f"FAIL reset 返回 {r3}")
    m4 = Manifest.load(d)
    if m4.seg(1).get("prompt_final") or m4.seg(1).get("prompt_locked"):
        raise SystemExit(f"FAIL 恢复默认没清 {m4.seg(1)}")

    taskmod.set_status(tid, "finished")
    try:
        review.set_prompt(tid, 2, text="不该写")
        raise SystemExit("FAIL 已完成任务不该能改提示词")
    except OranteError as e:
        if "完成" not in e.message:
            raise SystemExit(f"FAIL 拒绝文案不对 {e.message}")
    print("OK set_prompt 锁定/恢复默认/完成拒绝；换图 kept_locked")


def test_apply_keeps_locked():
    tid, d, m = _make_task("T2202", n=4, status="planned")
    for sg in m.segments:
        sg["status"] = "planned"
        sg["prompt_final"] = f"old-{sg['s']}"
    m.seg(2)["prompt_locked"] = True
    m.seg(2)["prompt_final"] = "KEEP-ME"
    m.save(d)
    res = trackmod.plan(m.segments, m.images, hold=2, jitter=0, unit="seg", seed=1)
    trackmod.apply(m, res["rows"])
    n_kept = int(getattr(m, "_n_prompt_locked", 0) or 0)
    if n_kept < 1:
        raise SystemExit(f"FAIL apply 没计锁定段 {n_kept}")
    if m.seg(2).get("prompt_final") != "KEEP-ME" or not m.seg(2).get("prompt_locked"):
        raise SystemExit(f"FAIL apply 清掉了锁定提示词 {m.seg(2)}")
    if m.seg(1).get("prompt_final"):
        raise SystemExit("FAIL 没锁定的段 apply 后应清空 prompt_final")
    print("OK apply：锁定段提示词保留，其它段清空")


def test_regenerate_uses_locked():
    tid, d, m = _make_task("T2203", n=2, status="collected")
    handwritten = "HANDWRITTEN-PROMPT-FOR-S1-LOCK-TEST"
    for i in (1, 2):
        fn = f"clips/T2203-s{i:03d}-p01.mp4"
        (d / fn).parent.mkdir(parents=True, exist_ok=True)
        (d / fn).write_bytes(b"\x00" * 400)
        m.seg(i)["takes"] = [{"file": fn, "at": "t"}]
        m.seg(i)["image"] = "p01"
        m.seg(i)["mark"] = True
    m.seg(1)["prompt_final"] = handwritten
    m.seg(1)["prompt_locked"] = True
    m.save(d)
    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    with patch("orante.services.generate.make_engine", side_effect=lambda acc, log: FakeEngine(acc, log)):
        with patch("orante.services.verify.after_download", return_value={"verdict": "ok", "frame_dist": 0}):
            genmod.regenerate(job, segs=[1])
    if FakeEngine.last is None:
        raise SystemExit("FAIL 没走 FakeEngine")
    got = [x for x in FakeEngine.last.submitted if x["s"] == 1]
    if not got or got[0]["prompt"] != handwritten:
        raise SystemExit(f"FAIL 提交没用手写版 {got}")
    if not any("用手写提示词" in ln and handwritten[:40] in ln for ln in job.lines):
        raise SystemExit(f"FAIL 日志没打手写前 40 字：{job.lines}")
    m2 = Manifest.load(d)
    if m2.seg(1).get("prompt_final") != handwritten or not m2.seg(1).get("prompt_locked"):
        raise SystemExit(f"FAIL 重生后锁定丢了 {m2.seg(1)}")
    print("OK 重生：提交用手写版，manifest 仍 locked")


def test_static():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    if "textsearch.js" not in html:
        raise SystemExit("FAIL index.html 没引入 textsearch.js")
    i18n = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("search.placeholder", "search.count", "search.none", "review.prompt_locked"):
        if k not in i18n:
            raise SystemExit(f"FAIL i18n 缺 {k}")
    paths = {getattr(r, "path", "") for r in servermod.app.routes}
    if "/api/tasks/{tid}/review/prompt" not in paths:
        raise SystemExit("FAIL 缺 POST /review/prompt")
    rv = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    if "swap-chip" not in rv or "startEdit" not in rv or "TextSearch.create" not in rv:
        raise SystemExit("FAIL review.js 缺换图 chip / 编辑 / 搜索")
    if 'class="swap"' in rv and "swap-chip" not in rv:
        raise SystemExit("FAIL 换图还在 .tools 里")
    tk = (ROOT / "ui" / "js" / "panels" / "track.js").read_text(encoding="utf-8")
    if "TextSearch.create" not in tk:
        raise SystemExit("FAIL track.js 没用共用搜索")
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    if "text-overflow:ellipsis" in css and ".panel-review .tools button" in css:
        # 工具条那一段不许 ellipsis
        block = css.split(".panel-review .tools button")[1].split(".panel-review")[0]
        if "ellipsis" in block:
            raise SystemExit("FAIL .tools button 还在 ellipsis")
    if "swap-chip" not in css:
        raise SystemExit("FAIL css 没有 swap-chip")
    for f in ("ui/js/parts/textsearch.js", "ui/js/panels/review.js", "ui/js/panels/track.js"):
        r = subprocess.run(["node", "--check", str(ROOT / f)], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"FAIL node --check {f}\n{r.stderr}")
    print("OK 静态：i18n / 路由 / 共用搜索 / 换图 chip / node --check")


def main():
    print(f"TEST_DATA={TEST_DATA}")
    test_search_t0013()
    test_set_prompt_and_lock()
    test_apply_keeps_locked()
    test_regenerate_uses_locked()
    test_static()
    print("\n规格 22 后端 + 搜索脚本全部通过")


if __name__ == "__main__":
    main()
