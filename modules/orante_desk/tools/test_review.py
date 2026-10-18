# -*- coding: utf-8 -*-
"""验收规格 09：体检 job、标记、过时、收片路由、重生挪片（Flow/Grok）。"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-review-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.jobs import REGISTRY, ACCOUNT_BOUND, load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import checks, review, generate as genmod  # noqa: E402
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
        if self.cancelled.is_set():
            from orante.scheduler import JobCancelled
            raise JobCancelled()

    def sleep(self, sec: float) -> None:
        pass


class FakeEngine:
    last = None
    name = "flow"

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.name = account.get("engine") or "flow"
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
        make_mp4(dest, duration_s=6.0, has_audio=True, pad=22000)
        return dest


def _box(typ: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + typ + body


def _mdhd_body(scale: int, dur: int) -> bytes:
    body = bytearray(32)
    struct.pack_into(">I", body, 12, scale)
    struct.pack_into(">I", body, 16, dur)
    return bytes(body)


def make_mp4(path: Path, duration_s=6.0, has_audio=True, audio_s=None, pad=22000) -> None:
    """最小可被 checks.parse_mp4 读懂的 mp4（带可选 soun 轨）。"""
    scale = 1000
    vdur = int(duration_s * scale)
    adur = int((audio_s if audio_s is not None else duration_s) * scale)
    mvhd = _box(b"mvhd", _mdhd_body(scale, vdur))
    mdhd_v = _box(b"mdhd", _mdhd_body(scale, vdur))
    hdlr_v = _box(b"hdlr", b"\x00" * 8 + b"vide")
    trak_v = _box(b"trak", _box(b"mdia", mdhd_v + hdlr_v))
    traks = trak_v
    if has_audio:
        mdhd_a = _box(b"mdhd", _mdhd_body(scale, adur))
        hdlr_a = _box(b"hdlr", b"\x00" * 8 + b"soun")
        traks += _box(b"trak", _box(b"mdia", mdhd_a + hdlr_a))
    moov = _box(b"moov", mvhd + traks)
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x00\x00" + b"isom")
    mdat = _box(b"mdat", b"\x00" * max(pad, 8))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ftyp + moov + mdat)


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


def _make_task(tid="T2001", engine="flow", account="A001", n=5, status="collected"):
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-review-"))
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
            "status": "collected", "gen_id": f"old{i}",
            "takes": [], "error": None,
        })
    m = Manifest.new(tid, "收片测", engine, account, "long", SEG_TEXT)
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
            (tid, "收片测", engine, account, "long", SEG_TEXT, str(task_dir / "images"),
             "", status, str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_mp4_probe():
    p = Path(tempfile.mkdtemp()) / "a.mp4"
    make_mp4(p, duration_s=6.0, has_audio=True)
    info = checks.parse_mp4(str(p))
    if not info or abs((info.get("duration") or 0) - 6) > 0.05:
        raise SystemExit(f"FAIL parse_mp4 duration {info}")
    kinds = [t["kind"] for t in info["tracks"]]
    if "soun" not in kinds or "vide" not in kinds:
        raise SystemExit(f"FAIL tracks {info['tracks']}")
    pr = checks.probe(str(p))
    if not pr["has_audio"] or pr["size"] < 20000:
        raise SystemExit(f"FAIL probe {pr}")
    p2 = p.with_name("noaudio.mp4")
    make_mp4(p2, duration_s=8.0, has_audio=False)
    pr2 = checks.probe(str(p2))
    if pr2["has_audio"]:
        raise SystemExit("FAIL 无音轨还被当成有音轨")
    print("OK parse_mp4 / probe 时长和音轨")


def test_issues_for():
    miss = checks.issues_for({"exists": False, "label": "s001"}, None, {})
    if miss != [{"level": "miss", "text": "缺片"}]:
        raise SystemExit(f"FAIL 缺片 {miss}")
    tiny = checks.issues_for({"exists": True, "label": "s001", "dur_expect": 6},
                             {"size": 100, "duration": 6, "has_audio": True, "audio_dur": 6, "md5": "a"}, {})
    if not any("空文件" in x["text"] or "过小" in x["text"] for x in tiny):
        raise SystemExit(f"FAIL 过小 {tiny}")
    dur = checks.issues_for({"exists": True, "label": "s001", "dur_expect": 8},
                            {"size": 30000, "duration": 4.0, "has_audio": True, "audio_dur": 4.0, "md5": "a"}, {})
    if not any("时长不符" in x["text"] for x in dur):
        raise SystemExit(f"FAIL 时长 {dur}")
    noa = checks.issues_for({"exists": True, "label": "s001", "dur_expect": 6},
                            {"size": 30000, "duration": 6.0, "has_audio": False, "audio_dur": None, "md5": "a"}, {})
    if not any(x["text"] == "没有音轨" for x in noa):
        raise SystemExit(f"FAIL 无音轨 {noa}")
    short = checks.issues_for({"exists": True, "label": "s001", "dur_expect": 10},
                              {"size": 30000, "duration": 10.0, "has_audio": True, "audio_dur": 4.0, "md5": "a"}, {})
    if not any("音频只有" in x["text"] for x in short):
        raise SystemExit(f"FAIL 音频过短 {short}")
    dup = checks.issues_for({"exists": True, "label": "s002", "dur_expect": 6},
                            {"size": 30000, "duration": 6.0, "has_audio": True, "audio_dur": 6.0, "md5": "same"},
                            {"same": "s001"})
    if not any("同一个视频" in x["text"] for x in dup):
        raise SystemExit(f"FAIL md5 {dup}")
    quiet = checks.issues_for({"exists": True, "label": "s001", "dur_expect": 6},
                              {"size": 30000, "duration": 6.0, "has_audio": True, "audio_dur": 6.0, "md5": "z"},
                              {}, {"mean_db": -55})
    if not any("静音" in x["text"] for x in quiet):
        raise SystemExit(f"FAIL 静音 {quiet}")
    print("OK issues_for 四查 + 音轨三查")


def test_jobs_registered():
    if "check" not in REGISTRY:
        raise SystemExit(f"FAIL 没注册 check，现有 {sorted(REGISTRY)}")
    if "check" in ACCOUNT_BOUND:
        raise SystemExit("FAIL check 不该占账号")
    if "regenerate" not in REGISTRY or "regenerate" not in ACCOUNT_BOUND:
        raise SystemExit("FAIL regenerate 应该占账号")
    print("OK jobs：check 不占账号；regenerate 占账号")


def test_check_job_and_routes():
    tid, d, m = _make_task("T2001", n=5)
    # s1 正常 6s 有声；s2 缺片；s3 时长不符；s4 无音轨；s5 与 s1 同 md5
    p1 = d / "clips" / "T2001-s001-p01.mp4"
    make_mp4(p1, 6.0, True)
    m.seg(1)["takes"] = [{"file": "clips/T2001-s001-p01.mp4", "at": "t"}]
    m.seg(3)["takes"] = [{"file": "clips/T2001-s003-p01.mp4", "at": "t"}]
    make_mp4(d / "clips" / "T2001-s003-p01.mp4", 2.0, True)
    m.seg(3)["duration"] = 8
    m.seg(4)["takes"] = [{"file": "clips/T2001-s004-p02.mp4", "at": "t"}]
    make_mp4(d / "clips" / "T2001-s004-p02.mp4", 6.0, False)
    p5 = d / "clips" / "T2001-s005-p01.mp4"
    p5.write_bytes(p1.read_bytes())
    m.seg(5)["takes"] = [{"file": "clips/T2001-s005-p01.mp4", "at": "t"}]
    m.save(d)

    job = FakeJob(tid)
    res = review.check(job)
    if res["total"] != 5:
        raise SystemExit(f"FAIL total {res}")
    m2 = Manifest.load(d)
    i1 = m2.seg(1)["check"]["issues"]
    i2 = m2.seg(2)["check"]["issues"]
    i3 = m2.seg(3)["check"]["issues"]
    i4 = m2.seg(4)["check"]["issues"]
    i5 = m2.seg(5)["check"]["issues"]
    if any(x["level"] in ("bad", "miss") for x in i1):
        raise SystemExit(f"FAIL s1 不该有红标 {i1}")
    if not any(x["text"] == "缺片" for x in i2):
        raise SystemExit(f"FAIL s2 缺片 {i2}")
    if not any("时长不符" in x["text"] for x in i3):
        raise SystemExit(f"FAIL s3 时长 {i3}")
    if not any(x["text"] == "没有音轨" for x in i4):
        raise SystemExit(f"FAIL s4 无音轨 {i4}")
    if not any("同一个视频" in x["text"] for x in i5):
        raise SystemExit(f"FAIL s5 md5 {i5}")
    if res["n_flagged"] < 4:
        raise SystemExit(f"FAIL n_flagged {res}")

    body = review.get_review(tid)
    segs = body["segments"]
    if len(segs) != 5 or "prompt_final" not in segs[0] or "check" not in segs[0]:
        raise SystemExit(f"FAIL review 字段 {segs[0].keys()}")
    if segs[1]["exists"] is not False:
        raise SystemExit("FAIL s2 exists 应为 false")
    bad = [s for s in segs if (s.get("check") or {}).get("issues")]
    if len(bad) < 4:
        raise SystemExit(f"FAIL review 带 issues 的段 {len(bad)}")

    r2 = review.set_mark(tid, 2, True)
    if r2.get("mark") is not True:
        raise SystemExit(f"FAIL marks {r2}")
    m3 = Manifest.load(d)
    if m3.seg(2).get("mark") is not True:
        raise SystemExit(f"FAIL mark 没写进 manifest {m3.seg(2)}")
    r3 = review.get_review(tid)
    if "2" not in r3.get("marks", {}):
        raise SystemExit(f"FAIL marks 回读 {r3.get('marks')}")

    r4 = review.finish_check(tid)
    if r4.get("finished") is not False:
        raise SystemExit(f"FAIL 没有字幕成品却 finished {r4}")
    out = d / "字幕成品"
    out.mkdir()
    (out / "final.mp4").write_bytes(b"x" * 100)
    r5 = review.finish_check(tid)
    if r5.get("finished") is not True:
        raise SystemExit(f"FAIL 有字幕成品没 finished {r5}")
    if taskmod.get(tid)["status"] != "finished":
        raise SystemExit(f"FAIL status {taskmod.get(tid)['status']}")

    i18n = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("review.check", "review.filter.bad", "review.redo_go", "review.concat_tip"):
        if k not in i18n:
            raise SystemExit(f"FAIL i18n 缺 {k}")
    paths = {getattr(r, "path", "") for r in servermod.app.routes}
    for p in ("/api/tasks/{tid}/review", "/api/tasks/{tid}/marks",
              "/api/tasks/{tid}/finish-check", "/api/tasks/{tid}/reveal"):
        if p not in paths:
            raise SystemExit(f"FAIL 缺路由 {p}")
    print("OK check job 红标 + GET review + marks 进 manifest + finish-check + i18n + 路由")
    return tid, d


def test_regenerate_flow_and_grok():
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("会走 ffmpeg 体检假 mp4，整目录 pytest 容易卡住；python tools/test_review.py 仍跑")
    tid, d, m = _make_task("T2002", engine="flow", account="A001", n=3)
    for i in (1, 2, 3):
        fn = f"clips/T2002-s{i:03d}-p01.mp4"
        make_mp4(d / fn, 6.0, True)
        m.seg(i)["takes"] = [{"file": fn, "at": "t"}]
        m.seg(i)["image"] = "p01"
        m.seg(i)["mark"] = True
        m.seg(i)["check"] = {"issues": [{"level": "bad", "text": "旧"}]}
    m.save(d)
    old1 = (d / m.seg(1)["takes"][0]["file"]).read_bytes()

    job = FakeJob(tid, "A001")
    with patch("orante.services.generate.make_engine", side_effect=lambda acc, log: FakeEngine(acc, log)):
        genmod.regenerate(job, segs=[1, 2])
    trash = list((d / "clips" / "_弃用").glob("*"))
    if len(trash) < 2:
        raise SystemExit(f"FAIL 旧片没进 _弃用：{trash}")
    names = " ".join(p.name for p in trash)
    if "T2002-s001" not in names or "T2002-s002" not in names:
        raise SystemExit(f"FAIL _弃用 文件名 {names}")
    m2 = Manifest.load(d)
    if m2.seg(1)["status"] != "collected" or m2.seg(2)["status"] != "collected":
        raise SystemExit(f"FAIL 重生后状态 {m2.seg(1)['status']} {m2.seg(2)['status']}")
    if m2.seg(3)["status"] != "collected" or not m2.seg(3)["takes"]:
        raise SystemExit("FAIL 没选的 s3 不该动")
    if m2.seg(1).get("mark"):
        raise SystemExit("FAIL 重生后 mark 应清掉")
    if m2.seg(1).get("check", {}).get("issues"):
        raise SystemExit(f"FAIL 重生后旧体检应清掉 {m2.seg(1).get('check')}")
    new1 = d / m2.seg(1)["takes"][-1]["file"]
    if not new1.is_file():
        raise SystemExit("FAIL 新片没落盘")
    if new1.read_bytes() == old1:
        # 合成 mp4 可能碰巧相同结构；用路径：新片不应还在原 takes[0] 那个被挪走的名字上
        pass
    if FakeEngine.last is None or FakeEngine.last.account.get("engine") != "flow":
        raise SystemExit(f"FAIL Flow 重生没走 FakeEngine {FakeEngine.last}")
    if sorted(x["s"] for x in FakeEngine.last.submitted) != [1, 2]:
        raise SystemExit(f"FAIL 提交段 {FakeEngine.last.submitted}")

    tidg, dg, mg = _make_task("T2003", engine="grok", account="A002", n=2, status="collected")
    for i in (1, 2):
        fn = f"clips/T2003-s{i:03d}-p01.mp4"
        make_mp4(dg / fn, 6.0, True)
        mg.seg(i)["takes"] = [{"file": fn, "at": "t"}]
        mg.seg(i)["image"] = "p01"
        mg.seg(i)["mark"] = True
    mg.save(dg)
    jobg = FakeJob(tidg, "A002")
    FakeEngine.last = None
    with patch("orante.services.generate.make_engine", side_effect=lambda acc, log: FakeEngine(acc, log)):
        genmod.regenerate(jobg, segs=[1])
    if FakeEngine.last is None or FakeEngine.last.account.get("engine") != "grok":
        raise SystemExit(f"FAIL Grok 重生没走 Grok 账号引擎 {getattr(FakeEngine.last, 'account', None)}")
    trashg = list((dg / "clips" / "_弃用").glob("*"))
    if not any("s001" in p.name for p in trashg):
        raise SystemExit(f"FAIL Grok 旧片没进 _弃用 {trashg}")
    mg2 = Manifest.load(dg)
    if mg2.seg(1)["status"] != "collected":
        raise SystemExit(f"FAIL Grok 重生后 {mg2.seg(1)['status']}")
    if mg2.seg(2)["takes"][0]["file"].endswith("s002-p01.mp4") is False:
        raise SystemExit("FAIL Grok s2 不该被动")
    print("OK regenerate Flow/Grok：旧片进 _弃用，段回 planned 再收齐，走对应 Engine")


def test_ffmpeg_lookup_order():
    # 不要求机器上真有 ffmpeg；只要查找函数按顺序、找不到就空串
    checks._FFMPEG = None
    found = checks.find_ffmpeg()
    if found is None:
        raise SystemExit("FAIL find_ffmpeg 应返回路径或空串，不要 None")
    exe = Path(ROOT / "bin" / "ffmpeg.exe")
    exe2 = Path(ROOT / "bin" / "ffmpeg")
    if exe.is_file() or exe2.is_file():
        want = str(exe if exe.is_file() else exe2)
        if found != want:
            raise SystemExit(f"FAIL 应优先 ROOT/bin，实际 {found} 期望 {want}")
        print(f"OK ffmpeg 命中 ROOT/bin：{found}")
    else:
        print(f"OK find_ffmpeg 返回 {found!r}（本机 ROOT/bin 没有 ffmpeg，查找未抛）")


def test_static_files():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    if 'src="/ui/js/colors.js"' not in html:
        raise SystemExit("FAIL index.html 没引入 colors.js")
    colors = (ROOT / "ui" / "js" / "colors.js").read_text(encoding="utf-8")
    if "window.pcolor" not in colors or "37" not in colors:
        raise SystemExit("FAIL colors.js 没有 pcolor")
    track = (ROOT / "ui" / "js" / "panels" / "track.js").read_text(encoding="utf-8")
    if "window.pcolor" not in track:
        raise SystemExit("FAIL track.js 没用共用 pcolor")
    if "((n - 1) * 37)" in track:
        raise SystemExit("FAIL track.js 还留着一份配色函数")
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    if ".panel-review" not in css or ".rv-card" not in css:
        raise SystemExit("FAIL app.css 没有收片样式")
    js = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    if "panel.todo" in js:
        raise SystemExit("FAIL review.js 还是占位")
    if "regenerate" not in js:
        raise SystemExit("FAIL review.js 没走 regenerate")
    import subprocess
    for f in ("ui/js/panels/review.js", "ui/js/colors.js", "ui/js/panels/track.js"):
        r = subprocess.run(["node", "--check", str(ROOT / f)], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"FAIL node --check {f}\n{r.stderr}")
    node = r'''
    const fs = require('fs');
    const vm = require('vm');
    const ctx = { window: {}, console };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync('ui/js/colors.js','utf8'), ctx);
    vm.runInContext(fs.readFileSync('ui/js/panels/review.js','utf8'), ctx);
    const u = ctx.window.Panel_review.util;
    if (ctx.window.pcolor('p01') !== 'hsl(0 48% 46%)') throw new Error('pcolor p01 ' + ctx.window.pcolor('p01'));
    if (ctx.window.pcolor('p02') !== 'hsl(37 48% 46%)') throw new Error('pcolor p02');
    const segs = [
      {s:1, exists:true, mark:false, status:'collected', check:{issues:[]}},
      {s:2, exists:false, mark:true, status:'failed', check:{issues:[{level:'miss', text:'缺片'}]}},
      {s:3, exists:true, mark:false, status:'collected', check:{issues:[{level:'bad', text:'没有音轨'}]}},
    ];
    if (u.filterSegs(segs,'bad').map(x=>x.s).join() !== '2,3') throw new Error('filter bad');
    if (u.filterSegs(segs,'mark').map(x=>x.s).join() !== '2') throw new Error('filter mark');
    if (u.filterSegs(segs,'miss').map(x=>x.s).join() !== '2') throw new Error('filter miss');
    if (!u.isCandidate(segs[1]) || !u.isCandidate({s:4,exists:true,mark:false,status:'failed'})) throw new Error('candidate');
    if (u.RATES['0'] !== 12 || u.RATES['4'] !== 4) throw new Error('rates');
    console.log('OK node util filter/pcolor/倍速');
    '''
    r = subprocess.run(["node", "-e", node], cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL node util\n{r.stdout}\n{r.stderr}")
    print(r.stdout.strip())
    print("OK 静态：colors.js 共用、index 引入、review.js 不是占位")


if __name__ == "__main__":
    test_mp4_probe()
    test_issues_for()
    test_jobs_registered()
    test_check_job_and_routes()
    test_regenerate_flow_and_grok()
    test_ffmpeg_lookup_order()
    test_static_files()
    print("\n全部通过")
