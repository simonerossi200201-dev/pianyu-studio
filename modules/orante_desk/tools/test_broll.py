# -*- coding: utf-8 -*-
"""验收规格 13（0923 改）：准备阶段不再判空镜，空镜由用户自己标；
重编号、磁盘同步、b01、拦住生成照旧。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-broll-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, config, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import broll, describe, prepare, generate  # noqa: E402
from orante.services import track as trackmod  # noqa: E402
from orante.services.gemini import Gemini  # noqa: E402

db.init()
load_all_services()

SEG_TEXT = "Stay for 7 seconds and type Amen. God bless you."
T0003 = Path(r"D:\1_PROJECT\2026-09-06\A\T0003-董瑞01-v04")


class FakeJob:
    def __init__(self, task_id):
        self.task_id = task_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "Jbroll"

    def log(self, msg: str) -> None:
        print(f"  {msg}")
        self.lines.append(msg)

    def set_progress(self, **kw) -> None:
        pass

    def check(self) -> None:
        if self.cancelled.is_set():
            from orante.scheduler import JobCancelled
            raise JobCancelled()


class FakeGemini:
    """p02 / 文件名含 broll → 空镜。记下每次判定。"""

    calls = []

    def __init__(self, api_key=None, model=None):
        if api_key is not None and str(api_key).strip() == "":
            raise OranteError("还没填 Gemini API Key", "设置 → Gemini")
        self.api_key = api_key or "fake"
        self.model = model or "gemini-3.5-flash-lite"

    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        text = str(prompt or "")
        stem = Path(image_path).stem.lower()
        FakeGemini.calls.append((stem, "is_broll" in text, json_mode))
        if json_mode and "is_broll" in text:
            is_broll = stem in ("p02", "broll") or "broll" in stem
            return json.dumps({
                "is_broll": is_broll,
                "reason": "几千人的会场，讲员很小" if is_broll else "有明确主角",
                "shot_size": "远景" if is_broll else "中景",
                "people": 800 if is_broll else 1,
                "crowd": is_broll,
                "subject_height": 0.08 if is_broll else 0.45,
            }, ensure_ascii=False)
        if json_mode:
            return json.dumps({
                "shot_size": "中景", "facing": "正面", "people": 1,
                "crowd": False, "seated": False, "level": "平视",
                "room_left": "中", "room_right": "中", "headroom": "中", "depth": "中",
            }, ensure_ascii=False)
        return "一位中年男性基督徒站在原位【闭眼|常规】\n【音色】非裔|男|中年|中低音|美国口音"


class NoKeyGemini:
    def __init__(self, api_key=None, model=None):
        raise OranteError("还没填 Gemini API Key", "设置 → Gemini")


def _jpeg(path: Path, color=(40, 80, 60)) -> None:
    from PIL import Image as PILImage
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", (400, 640), color).save(path, "JPEG", quality=80)


def _ensure_account():
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-broll", "flow", 9396, "A001", "", "", "9:16", 5, 1, "{}", db.now()))


def _insert_task(tid, name, src, status="draft", task_dir=None):
    _ensure_account()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, copy=?, image_source=?, updated=? WHERE id=?",
               (str(task_dir or ""), status, SEG_TEXT, str(src), db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, name, "flow", "A001", "long", SEG_TEXT, str(src),
             "", status, str(task_dir or ""), "", 0, db.now(), db.now()))


def test_prompt_and_parse() -> None:
    p = broll.BROLL_PROMPT
    for bit in ("1/4", "大屏幕", "远景", "subject_height", "面朝镜头"):
        if bit not in p:
            raise SystemExit(f"FAIL 提示词缺 {bit!r}")
    got = broll.parse_broll(json.dumps({
        "is_broll": True, "reason": "大全景", "shot_size": "远景",
        "people": 2000, "crowd": True, "subject_height": 0.12,
    }, ensure_ascii=False))
    if not got["is_broll"] or got["subject_height"] != 0.12:
        raise SystemExit(f"FAIL parse：{got}")
    got2 = broll.parse_broll('{"is_broll": false, "reason": "x", "shot_size": "中景"}')
    if got2["is_broll"] or got2["subject_height"] != 0.0:
        raise SystemExit(f"FAIL parse 缺 height：{got2}")
    print("OK 提示词景别优先 + parse subject_height")


def test_fmt_p() -> None:
    if trackmod.fmt_p("b01") != "b01":
        raise SystemExit(f"FAIL fmt_p b01 → {trackmod.fmt_p('b01')}")
    if trackmod.fmt_p("b1") != "b01":
        raise SystemExit(f"FAIL fmt_p b1 → {trackmod.fmt_p('b1')}")
    if trackmod.fmt_p("p03") != "p03":
        raise SystemExit(f"FAIL fmt_p p03 → {trackmod.fmt_p('p03')}")
    if trackmod.fmt_p(2) != "p02":
        raise SystemExit(f"FAIL fmt_p 2 → {trackmod.fmt_p(2)}")
    images = [
        {"p": "p01", "role": "主体", "status": "ok"},
        {"p": "b01", "role": "空镜", "status": "ok"},
        {"p": "p02", "role": "主体", "status": "ok"},
    ]
    pool = trackmod.subject_pool(images)
    if pool != ["p01", "p02"]:
        raise SystemExit(f"FAIL 空镜不进池：{pool}")
    print("OK fmt_p 认 b01，空镜不进配图轨")


def test_relocate_and_renumber() -> None:
    d = Path(tempfile.mkdtemp(prefix="orante-renum-"))
    (d / "images").mkdir()
    (d / "空镜头").mkdir()
    files = []
    images = []
    for i, color in enumerate([(40, 80, 60), (80, 40, 40), (40, 40, 90)], 1):
        fp = d / "images" / f"p{i:02d}.jpg"
        _jpeg(fp, color)
        files.append(fp)
        images.append({
            "p": f"p{i:02d}", "file": f"images/p{i:02d}.jpg", "sha1": sha1_file(fp),
            "role": "空镜" if i == 2 else "主体", "status": "ok",
            "desc": "", "voice": "", "eye": "空镜" if i == 2 else "闭眼",
            "scene": "常规", "measure": {}, "origin": "local", "src": fp.name,
        })
    broll.relocate(d, images[1], "空镜")
    if not (d / "空镜头" / "p02.jpg").is_file():
        raise SystemExit("FAIL relocate 没搬到 空镜头/")
    if (d / "images" / "p02.jpg").is_file():
        raise SystemExit("FAIL relocate 后 images/ 还留着 p02")
    images = broll.renumber(d, images)
    by = {im["p"]: im for im in images}
    if set(by) != {"p01", "p02", "b01"}:
        raise SystemExit(f"FAIL 重编号 {set(by)}")
    if by["p01"]["role"] != "主体" or by["p02"]["role"] != "主体" or by["b01"]["role"] != "空镜":
        raise SystemExit(f"FAIL role {[(k, v['role']) for k, v in by.items()]}")
    if by["b01"]["file"] != "空镜头/b01.jpg":
        raise SystemExit(f"FAIL b01 file {by['b01']['file']}")
    if not (d / "空镜头" / "b01.jpg").is_file():
        raise SystemExit("FAIL 空镜头/b01.jpg 不在")
    if (d / "空镜头" / "p02.jpg").is_file():
        raise SystemExit("FAIL 旧名 p02 还在 空镜头/")
    if not (d / "images" / "p01.jpg").is_file() or not (d / "images" / "p02.jpg").is_file():
        raise SystemExit("FAIL 主体文件名没跟上")
    print("OK relocate + 重编号 p01/p02 主体、b01 空镜")


def test_screen_no_key() -> None:
    d = Path(tempfile.mkdtemp(prefix="orante-nokey-"))
    (d / "images").mkdir()
    images = []
    for i in range(1, 3):
        fp = d / "images" / f"p{i:02d}.jpg"
        _jpeg(fp)
        images.append({"p": f"p{i:02d}", "file": f"images/p{i:02d}.jpg",
                       "role": "主体", "status": "ok"})
    job = FakeJob("Tnk")
    with patch("orante.services.gemini.Gemini", NoKeyGemini):
        out = broll.screen(job, d, images)
    if any(im.get("role") == "空镜" for im in out):
        raise SystemExit("FAIL 没 Key 不该判空镜")
    if not any("留到描述语阶段判" in ln for ln in job.lines):
        raise SystemExit(f"FAIL 没 Key 日志不对：{job.lines}")
    print("OK 没 Key 空镜留到描述语阶段判")


def test_prepare_three() -> None:
    """0923：准备阶段把图全部收下当主体，一次 Gemini 都不调；空镜由用户自己标。"""
    src = TEST_DATA / "src3"
    src.mkdir(exist_ok=True)
    used_real = False
    if (T0003 / "images" / "p01.jpeg").is_file() and (T0003 / "空镜头" / "p02.jpeg").is_file():
        shutil.copy2(T0003 / "images" / "p01.jpeg", src / "p01.jpeg")
        shutil.copy2(T0003 / "空镜头" / "p02.jpeg", src / "p02.jpeg")
        shutil.copy2(T0003 / "images" / "p03.jpeg", src / "p03.jpeg")
        used_real = True
    else:
        _jpeg(src / "p01.jpeg", (40, 80, 60))
        _jpeg(src / "p02.jpeg", (80, 40, 40))
        _jpeg(src / "p03.jpeg", (40, 40, 90))
    tid = "T9003"
    _insert_task(tid, "董瑞空镜测", src, status="draft")
    batch = TEST_DATA / "batch"
    batch.mkdir(exist_ok=True)
    FakeGemini.calls = []
    job = FakeJob(tid)
    config.set("gemini.concurrency", 2)
    with patch("orante.services.gemini.Gemini", FakeGemini), \
         patch("orante.services.aikeys.list_all", lambda enabled_only=True: [{"id": "k1"}]), \
         patch("orante.services.ai.client", lambda *a, **k: FakeGemini()):
        res = prepare.prepare(job, batch_dir=str(batch), batch_id="Btest")
    if FakeGemini.calls:
        raise SystemExit(f"FAIL 准备阶段不该调 Gemini：{FakeGemini.calls}")
    t = taskmod.get(tid)
    d = Path(t["dir"])
    m = Manifest.load(d)
    by = {im["p"]: im for im in m.images}
    if set(by) != {"p01", "p02", "p03"}:
        raise SystemExit(f"FAIL prepare 编号 {set(by)} files={[im['file'] for im in m.images]}")
    if any(im.get("role") != "主体" for im in m.images):
        raise SystemExit(f"FAIL 应该全是主体：{[(im['p'], im.get('role')) for im in m.images]}")
    for p in ("p01", "p02", "p03"):
        if not (d / by[p]["file"]).is_file():
            raise SystemExit(f"FAIL {p} 文件不在：{by[p]['file']}")
    if res.get("subjects") != 3 or res.get("brolls") != 0:
        raise SystemExit(f"FAIL 返回 {res}")
    if not any("不判空镜" in ln for ln in job.lines):
        raise SystemExit(f"FAIL 日志没说不判空镜：{job.lines}")
    meta = t.get("meta") or {}
    if meta.get("brolls") != 0 or meta.get("subjects") != 3:
        raise SystemExit(f"FAIL task.meta {meta}")

    # 用户自己把 p02 标成空镜：搬家 + 重编号 → b01，后面几步照这个状态走
    m = Manifest.load(d)
    for im in m.images:
        if im["p"] == "p02":
            im["role"] = "空镜"
            im["eye"] = "空镜"
            broll.relocate(d, im, "空镜")
    m.images = broll.renumber(d, m.images)
    m.save(d)
    by = {im["p"]: im for im in m.images}
    if set(by) != {"p01", "p02", "b01"}:
        raise SystemExit(f"FAIL 手标空镜后编号 {set(by)}")
    if by["b01"]["role"] != "空镜" or not (d / by["b01"]["file"]).is_file():
        raise SystemExit(f"FAIL b01 {by['b01']}")
    note = "（T0003 真图）" if used_real else "（色块）"
    print(f"OK prepare 三张全收下不判空镜 {note}；手标 p02 → {by['b01']['file']}")
    return tid, d, m


def test_describe_skip(tid: str, d: Path) -> None:
    m = Manifest.load(d)
    # 清掉描述语，保留 broll_check，让 describe 再跑
    for im in m.images:
        if im.get("role") == "主体":
            im["desc"] = ""
            im["voice"] = ""
    m.save(d)
    FakeGemini.calls = []
    job = FakeJob(tid)
    with patch("orante.services.ai.client", lambda *a, **k: FakeGemini()), \
         patch("orante.services.aikeys.list_all",
               lambda enabled_only=True: [{"id": "k1", "max_inflight": 2}]):
        describe.describe(job)
    judged = [c for c in FakeGemini.calls if c[1]]
    if judged:
        raise SystemExit(f"FAIL 描述语阶段也不该再问空镜：{judged}")
    m2 = Manifest.load(d)
    roles = {im["p"]: im.get("role") for im in m2.images}
    if roles.get("p01") != "主体" or roles.get("p02") != "主体":
        raise SystemExit(f"FAIL 描述语阶段把主体图改了 role：{roles}")
    print("OK describe 不再自动判空镜，role 保持用户标的")


def test_sync_disk(tid: str, d: Path) -> None:
    m = Manifest.load(d)
    subj = next(im for im in m.images if im.get("role") == "主体")
    name = Path(subj["file"]).name
    src = d / subj["file"]
    dest = d / "空镜头" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    logs = broll.sync_with_disk(d, m)
    if not any("空镜头" in ln and subj["p"] in ln for ln in logs):
        raise SystemExit(f"FAIL 搬进空镜头日志：{logs}")
    im = m.image(subj["p"])
    if im["role"] != "空镜" or not str(im["file"]).replace("\\", "/").startswith("空镜头/"):
        raise SystemExit(f"FAIL 同步后 {im}")
    if im["p"] != subj["p"]:
        raise SystemExit("FAIL 同步后不该改编号")
    # 拖回去
    back = d / "images" / name
    shutil.move(str(d / im["file"]), str(back))
    logs2 = broll.sync_with_disk(d, m)
    im = m.image(subj["p"])
    if im["role"] != "主体":
        raise SystemExit(f"FAIL 拖回后 role {im['role']}")
    if not any("images/" in ln for ln in logs2):
        raise SystemExit(f"FAIL 拖回日志：{logs2}")
    # 文件不在
    gone = d / im["file"]
    gone.unlink()
    logs3 = broll.sync_with_disk(d, m)
    im = m.image(subj["p"])
    if im.get("exists") is not False:
        raise SystemExit(f"FAIL 缺文件应 exists=false：{im}")
    # 把文件放回去免得后面炸
    _jpeg(gone, (40, 80, 60))
    broll.sync_with_disk(d, m)
    # 新文件
    extra_b = d / "空镜头" / "crowd.jpeg"
    extra_p = d / "images" / "newface.jpeg"
    _jpeg(extra_b, (10, 10, 10))
    _jpeg(extra_p, (20, 20, 80))
    logs4 = broll.sync_with_disk(d, m)
    codes = {im["p"] for im in m.images}
    if "b02" not in codes and "b01" not in {im["p"] for im in m.images if im.get("origin") == "manual"}:
        # 原来 prepare 已经有 b01，新空镜应是 b02
        manuals = [im for im in m.images if im.get("origin") == "manual"]
        if not any(im["p"].startswith("b") and im["role"] == "空镜" for im in manuals):
            raise SystemExit(f"FAIL 新空镜没登记 {codes} logs={logs4} manuals={manuals}")
    if not any(im.get("origin") == "manual" and im.get("role") == "主体" for im in m.images):
        raise SystemExit(f"FAIL 新主体没登记 logs={logs4}")
    print("OK sync_with_disk 搬进/搬出/缺文件/新登记")
    m.save(d)


def test_generate_blocks(tid: str, d: Path) -> None:
    m = Manifest.load(d)
    # 随便拿一张空镜，让一段指向它
    br = next(im for im in m.images if im.get("role") == "空镜")
    if not m.segments:
        raise SystemExit("FAIL 没有段")
    m.segments[0]["image"] = br["p"]
    m.segments[0]["status"] = "planned"
    m.save(d)
    job = FakeJob(tid)
    try:
        generate.generate(job)
    except OranteError as e:
        if "空镜" not in (e.message or ""):
            raise SystemExit(f"FAIL 生成拦住原文不对：{e.message}")
        print(f"OK 生成拦住空镜图：{e.message} / {e.hint}")
    else:
        raise SystemExit("FAIL 带着空镜图生成应该抛错")
    # 清掉，免得污染
    subj = next(im for im in m.images if im.get("role") == "主体")
    m.segments[0]["image"] = subj["p"]
    m.save(d)


def test_colors_js() -> None:
    import subprocess
    js = r"""
const fs = require('fs');
const vm = require('vm');
const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('ui/js/colors.js','utf8'), ctx);
const p01 = ctx.window.pcolor('p01');
const p02 = ctx.window.pcolor('p02');
const b01 = ctx.window.pcolor('b01');
if (p01 !== 'hsl(0 48% 46%)') throw new Error('pcolor p01 ' + p01);
if (p02 !== 'hsl(37 48% 46%)') throw new Error('pcolor p02 ' + p02);
if (!b01 || b01.startsWith('hsl')) throw new Error('pcolor b01 应是灰色，得到 ' + b01);
console.log('OK colors.js p01/p02 色环，b01 灰色 ' + b01);
"""
    r = subprocess.run(["node", "-e", js], cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL colors.js：{r.stderr or r.stdout}")
    print((r.stdout or "").strip())


def test_i18n_and_js_syntax() -> None:
    import subprocess
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("tasks.prepare_result", "tasks.broll_n", "sync.disk",
              "gemini.missing", "track.broll_need_replan", "gen.broll_block"):
        if k not in zh:
            raise SystemExit(f"FAIL i18n 缺 {k}")
    for f in ("ui/js/panels/tasks.js", "ui/js/panels/gemini.js",
              "ui/js/panels/track.js", "ui/js/panels/generate.js",
              "ui/js/panels/review.js", "ui/js/colors.js", "ui/js/store.js"):
        r = subprocess.run(["node", "--check", f], cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"FAIL node --check {f}：{r.stderr}")
    print("OK i18n 键 + JS 语法")


def test_prompt_not_in_describe() -> None:
    src = (ROOT / "orante" / "services" / "describe.py").read_text(encoding="utf-8")
    if "BROLL_PROMPT" in src or "def _parse_broll" in src or "def _relocate" in src:
        raise SystemExit("FAIL describe.py 还留着抽出的空镜函数")
    exp = (ROOT / "orante" / "services" / "expand.py").read_text(encoding="utf-8")
    if "最小版" in exp or "from .describe import BROLL_PROMPT" in exp:
        raise SystemExit("FAIL expand.py 还留着最小版判定")
    print("OK describe/expand 已改 import broll")


def main() -> None:
    test_prompt_and_parse()
    test_fmt_p()
    test_relocate_and_renumber()
    test_screen_no_key()
    test_prompt_not_in_describe()
    test_i18n_and_js_syntax()
    test_colors_js()
    tid, d, m = test_prepare_three()
    test_describe_skip(tid, d)
    test_sync_disk(tid, d)
    test_generate_blocks(tid, d)
    print("\n全部通过")


if __name__ == "__main__":
    main()
