# -*- coding: utf-8 -*-
"""规格 27：工程文件导出 / 导入 / 清空 / 解冻 / 自动归档。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec27-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import load_all_services, start as job_start  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import project  # noqa: E402

db.init()
load_all_services()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _jpeg(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9" + os.urandom(12))


def _clip(path: Path, payload: bytes | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload if payload is not None else (b"mp4-fake-" + os.urandom(32)))


def _ensure_account():
    if db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _insert_batch(bid, root, day, version, bdir):
    Path(bdir).mkdir(parents=True, exist_ok=True)
    if db.one("SELECT 1 FROM batches WHERE id=?", (bid,)):
        db.run("UPDATE batches SET root=?,day=?,version=?,dir=? WHERE id=?",
               (str(root), day, version, str(bdir), bid))
        return
    db.run(
        "INSERT INTO batches(id,root,day,version,dir,created) VALUES(?,?,?,?,?,?)",
        (bid, str(root), day, version, str(bdir), db.now()),
    )


def _insert_task(tid, name, bdir, bid, status="finished", updated=None):
    _ensure_account()
    tdir = Path(bdir) / f"{tid}-{name}"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "images").mkdir(exist_ok=True)
    (tdir / "clips").mkdir(exist_ok=True)
    _jpeg(tdir / "images" / "p01.jpg")
    clip = tdir / "clips" / f"{tid}-s001-p01.mp4"
    _clip(clip)
    m = Manifest.new(tid, name, "flow", "A001", "long", "hello world " * 20)
    m.images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "x", "role": "主体", "status": "ok",
        "desc": "", "voice": "", "eye": "闭眼", "scene": "常规", "measure": {}, "origin": "download",
    }]
    m.segments = [{
        "s": 1, "text": "hello world", "duration": 6, "image": "p01", "role": "主体",
        "shot": "", "prompt_final": "", "status": "collected", "gen_id": None,
        "takes": [{"file": f"clips/{tid}-s001-p01.mp4", "at": "now"}], "error": None,
    }]
    m.save(tdir)
    now = updated or db.now()
    db.run("DELETE FROM jobs WHERE task_id=?", (tid,))
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated,meta,priority,archived)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, name, "flow", "A001", "long", "hello world " * 20, str(tdir / "images"),
         "", status, str(tdir), bid, 0, now, now, "{}", 0, 0),
    )
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,finished,log,error)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (f"J{tid[1:]}x", tid, "arrange", "done", now, now, "", ""),
    )
    return tdir, clip


def _tree(p: Path) -> list[str]:
    out = []
    if not p.exists():
        return out
    for root, dirs, files in os.walk(p):
        dirs.sort()
        for f in sorted(files):
            rel = str(Path(root, f).relative_to(p)).replace("\\", "/")
            st = Path(root, f).stat()
            out.append(f"{rel}|{st.st_size}")
    return out


def test_defaults():
    print("== 设置默认值 ==")
    from orante import config
    if config.DEFAULTS.get("archive.auto_on") is not False:
        bad("archive.auto_on 默认应是 false")
        return
    if int(config.DEFAULTS.get("archive.auto_days") or 0) != 7:
        bad("archive.auto_days 默认应是 7")
        return
    ok("archive.auto_on=false, archive.auto_days=7")


def test_migrate():
    print("== archived 字段迁移 ==")
    cols = {r["name"] for r in db.conn().execute("PRAGMA table_info(tasks)").fetchall()}
    if "archived" not in cols:
        bad("tasks 表没有 archived 列")
        return
    ok("tasks.archived 在")


def test_export_clear_import():
    print("== 导出 / 清空不删盘 / 导入 ==")
    root = Path(tempfile.mkdtemp(prefix="orante-proj-"))
    bdir = root / "2026-09-07" / "B"
    _insert_batch("B027", root, "2026-09-07", "B", bdir)
    dirs = {}
    clips = {}
    for i, name in enumerate(["alpha", "bravo", "charlie", "delta"], start=10):
        tid = f"T00{i}"
        tdir, clip = _insert_task(tid, name, bdir, "B027")
        dirs[tid] = tdir
        clips[tid] = clip
    before = _tree(bdir)

    r = project.export(task_ids=["T0010", "T0011", "T0012", "T0013"])
    if not r["files"]:
        bad("没写出工程文件")
        return
    dest = Path(r["files"][0]["path"])
    expect = bdir / "2026-09-07B.orproj.json"
    if dest.resolve() != expect.resolve():
        bad(f"路径不对：{dest} 期望 {expect}")
        return
    data = json.loads(dest.read_text(encoding="utf-8"))
    if data.get("format") != 1:
        bad(f"format={data.get('format')}")
        return
    raw = dest.read_text(encoding="utf-8")
    if "base64" in raw.lower() or "data:video" in raw:
        bad("工程文件里像有打包的视频")
        return
    ids = {t["id"] for t in data["tasks"]}
    if ids != {"T0010", "T0011", "T0012", "T0013"}:
        bad(f"任务不全：{ids}")
        return
    man = data["manifests"].get("T0010") or {}
    if not man.get("clips"):
        bad("没有 clips 摘要")
        return
    c0 = man["clips"][0]
    if not c0.get("sha256") or len(c0["sha256"]) != 64:
        bad(f"sha256 不像哈希：{c0.get('sha256')}")
        return
    if not c0.get("file", "").startswith("clips/"):
        bad(f"clip 路径不是相对的：{c0.get('file')}")
        return
    if dest.stat().st_size > 200_000:
        bad(f"工程文件太大 {dest.stat().st_size}，可能把片子写进去了")
        return
    ok(f"写出 {dest.name} · {r['files'][0]['tasks']} 任务 · {r['files'][0]['bytes']} 字节")

    # 没导出就清空 → 拦住
    _insert_task("T0099", "not-exported", bdir, "B027", status="finished")
    blocked = project.clear(["T0099"], force=False)
    if blocked.get("ok") is not False or "T0099" not in (blocked.get("need_export") or []):
        bad(f"未导出应被拦住：{blocked}")
        return
    if not db.one("SELECT 1 FROM tasks WHERE id=?", ("T0099",)):
        bad("拦住之后记录不该被删")
        return
    ok("未导出清空被拦住，记录还在")

    # 清空已导出的：库里没了，磁盘一个文件都不少
    gone = project.clear(["T0010", "T0011", "T0012", "T0013"], force=False)
    if not gone.get("ok") or gone.get("removed") != 4:
        bad(f"清空失败：{gone}")
        return
    after = _tree(bdir)
    # 工程文件是新写的；T0099 是后来加的。原先 4 条的 clips 必须原样。
    clip_before = [x for x in before if "/clips/" in x]
    clip_after = [x for x in after if "/clips/" in x]
    missing = [x for x in clip_before if x not in clip_after]
    if missing:
        bad(f"clips 少了：{missing}")
        return
    for tid in ("T0010", "T0011", "T0012", "T0013"):
        if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
            bad(f"{tid} 清空后还在库里")
            return
        if db.one("SELECT 1 FROM jobs WHERE task_id=?", (tid,)):
            bad(f"{tid} 的 jobs 还在")
            return
        if not dirs[tid].is_dir() or not clips[tid].is_file():
            bad(f"{tid} 磁盘文件丢了")
            return
    listed = [t["id"] for t in taskmod.list_all(include_finished=False)]
    if any(x in listed for x in ("T0010", "T0011", "T0012", "T0013")):
        bad(f"list_all 还看到已清空的：{listed}")
        return
    ok("清空后库里没了，clips 一个没少")

    # 导入
    imp = project.do_import([str(dest)])
    if imp["imported"] != 4:
        bad(f"导入条数 {imp['imported']} {imp}")
        return
    t = taskmod.get("T0010")
    if not t.get("archived"):
        bad("导入后 archived 不是 1")
        return
    if t["status"] != "finished":
        bad(f"导入后 status={t['status']}，应是 finished")
        return
    if t.get("missing_dir"):
        bad("目录还在却标了 missing_dir")
        return
    extra = taskmod.list_all(include_finished=True)
    ids = {x["id"] for x in extra if x.get("archived")}
    if not {"T0010", "T0011", "T0012", "T0013"} <= ids:
        bad(f"过时档看不到导入的：{ids}")
        return
    if any(x["id"] == "T0010" for x in taskmod.list_all(include_finished=False)):
        bad("归档任务不该出现在进行中")
        return
    ok("导入 4 条，archived=1，进过时档")

    # id 冲突跳过
    again = project.do_import([str(dest)])
    if again["imported"] != 0:
        bad(f"冲突时应跳过，却导入了 {again['imported']}")
        return
    if len(again["skipped"]) != 4:
        bad(f"skipped={again['skipped']}")
        return
    ok("同 id 跳过，不覆盖")

    # 解冻
    u = project.unfreeze(["T0010"])
    if u.get("unfrozen") != 1:
        bad(f"解冻：{u}")
        return
    t = taskmod.get("T0010")
    if t.get("archived"):
        bad("解冻后 archived 还是 1")
        return
    if t["status"] != "collected":
        bad(f"解冻后 status={t['status']}")
        return
    try:
        taskmod.ensure_writable("T0010")
    except OranteError:
        bad("解冻后 ensure_writable 还拦")
        return
    try:
        taskmod.ensure_writable("T0011")
        bad("归档任务 ensure_writable 没拦")
        return
    except OranteError:
        pass
    ok("解冻 T0010 → collected，能写；T0011 仍只读")

    # 归档 job 被拦
    try:
        job_start("generate", "T0011")
        bad("归档任务 generate 没拦")
        return
    except OranteError as e:
        if "只读" not in e.message:
            bad(f"错误文案不对：{e.message}")
            return
    ok("归档任务不能 generate")


def test_merge_and_missing():
    print("== 合并工程文件 / 缺目录 / 改动文件 ==")
    root = Path(tempfile.mkdtemp(prefix="orante-proj2-"))
    bdir = root / "2026-09-09" / "A"
    cdir = root / "2026-09-09" / "C"
    _insert_batch("B028", root, "2026-09-09", "A", bdir)
    _insert_batch("B029", root, "2026-09-09", "C", cdir)
    _insert_task("T0020", "one", bdir, "B028")
    _insert_task("T0021", "two", bdir, "B028")
    _insert_task("T0022", "three", cdir, "B029")

    r1 = project.export(task_ids=["T0020"])
    r2 = project.export(task_ids=["T0021"])
    p = Path(r1["files"][0]["path"])
    data = json.loads(p.read_text(encoding="utf-8"))
    ids = {t["id"] for t in data["tasks"]}
    if ids != {"T0020", "T0021"}:
        bad(f"合并后应有两条，实际 {ids}")
        return
    ok("同批次两次导出合并，同 id 以本次为准、旧的还在")

    r3 = project.export(task_ids=["T0020", "T0022"])
    if len(r3["files"]) != 2:
        bad(f"跨批次应写 2 个文件，实际 {r3['files']}")
        return
    ok("跨 2 个批次写 2 个工程文件")

    # 清空后改目录名再导入
    project.clear(["T0020", "T0021", "T0022"], force=True)
    tdir = bdir / "T0020-one"
    renamed = bdir / "T0020-one-moved"
    tdir.rename(renamed)
    # 工程文件里 dir 还是旧路径
    imp = project.do_import([str(p), str(Path(r3["files"][1]["path"]))])
    if "T0020" not in (imp.get("missing_dir") or []):
        bad(f"改名后应标缺目录：{imp}")
        return
    t = taskmod.get("T0020")
    if not t.get("missing_dir"):
        bad("get() 没带 missing_dir")
        return
    if not taskmod.get("T0022").get("archived"):
        bad("另一个文件没导进来")
        return
    stub = project.stub_manifest(t)
    if not stub.get("segments"):
        bad("缺目录时 stub manifest 没有段")
        return
    ok("缺目录标 🚫 元数据仍可看；另一个文件正常导入")

    # sha256 对不上
    project.clear(["T0020", "T0021", "T0022"], force=True)
    # 把 T0021 的片子改掉（目录还在，路径对）
    clip = bdir / "T0021-two" / "clips" / "T0021-s001-p01.mp4"
    if clip.is_file():
        clip.write_bytes(b"changed-bytes-" + os.urandom(16))
    imp2 = project.do_import([str(p)])
    changed = [x for x in (imp2.get("changed_files") or []) if x.get("id") == "T0021"]
    if not changed:
        bad(f"改过的片子应进 changed_files：{imp2}")
        return
    ok("sha256 对不上标「文件已改动」，仍导入")


def test_auto_archive():
    print("== 自动归档（只动 finished） ==")
    from orante import config
    root = Path(tempfile.mkdtemp(prefix="orante-proj3-"))
    bdir = root / "2026-09-01" / "A"
    _insert_batch("B030", root, "2026-09-01", "A", bdir)
    old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = db.now()
    _insert_task("T0030", "old-fin", bdir, "B030", status="finished", updated=old)
    _insert_task("T0031", "new-fin", bdir, "B030", status="finished", updated=fresh)
    _insert_task("T0032", "old-col", bdir, "B030", status="collected", updated=old)

    config.set("archive.auto_on", False)
    r = project.auto_archive_on_startup()
    if r is not None:
        bad("默认关时不应归档")
        return
    if not db.one("SELECT 1 FROM tasks WHERE id=?", ("T0030",)):
        bad("关着的时候把 T0030 弄没了")
        return
    ok("auto_on=false 启动扫描不动")

    config.set("archive.auto_on", True)
    config.set("archive.auto_days", 7)
    r = project.auto_archive_on_startup()
    if not r or r.get("archived") != 1:
        bad(f"应只归档 1 条 finished 且过期的：{r}")
        return
    if db.one("SELECT 1 FROM tasks WHERE id=?", ("T0030",)):
        bad("过期 finished 没从库里移出")
        return
    if not db.one("SELECT 1 FROM tasks WHERE id=?", ("T0031",)):
        bad("新完成的不该归档")
        return
    if not db.one("SELECT 1 FROM tasks WHERE id=? AND status='collected'", ("T0032",)):
        bad("collected 不该归档")
        return
    dest = bdir / "2026-09-01A.orproj.json"
    if not dest.is_file():
        bad("自动归档没写工程文件")
        return
    ok("只归档过期 finished；collected / 新完成的留下")
    config.set("archive.auto_on", False)


def test_force_export_and_clear():
    print("== force 导出并清空 ==")
    root = Path(tempfile.mkdtemp(prefix="orante-proj4-"))
    bdir = root / "2026-09-03" / "D"
    _insert_batch("B031", root, "2026-09-03", "D", bdir)
    tdir, clip = _insert_task("T0040", "force-me", bdir, "B031")
    before = clip.is_file()
    r = project.clear(["T0040"], force=True)
    if not r.get("ok"):
        bad(f"force 清空失败：{r}")
        return
    if db.one("SELECT 1 FROM tasks WHERE id=?", ("T0040",)):
        bad("force 清空后还在库里")
        return
    if not before or not clip.is_file():
        bad("force 清空动了磁盘文件")
        return
    dest = bdir / "2026-09-03D.orproj.json"
    if not dest.is_file():
        bad("force 没有先写出工程文件")
        return
    data = json.loads(dest.read_text(encoding="utf-8"))
    if "T0040" not in {t["id"] for t in data["tasks"]}:
        bad("工程文件里没有 T0040")
        return
    ok("现在就导出并清空：先写文件再删库，磁盘不动")


if __name__ == "__main__":
    print("TEST_DATA", TEST_DATA)
    test_defaults()
    test_migrate()
    test_export_clear_import()
    test_merge_and_missing()
    test_auto_archive()
    test_force_export_and_clear()
    print()
    if fail:
        print(f"FAIL {fail}")
        sys.exit(1)
    print("ALL OK")
