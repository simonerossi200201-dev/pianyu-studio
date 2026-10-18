# -*- coding: utf-8 -*-
"""规格 46：mtime 破缓存、按磁盘重建图库、浅层刷新不跑多模态。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec46-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file, sha256_file  # noqa: E402
from orante.services import images as imgmod, rescan, refresh  # noqa: E402

db.init()
load_all_services()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _blob(tag: bytes) -> bytes:
    return b"\xff\xd8\xff\xd9" + tag + os.urandom(24)


def _ensure_account():
    if db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()))


def _make(n_img=9, n_seg=4, tid="T4601", same_25=False) -> tuple[str, Path, Manifest]:
    """n_img 张图进 manifest；same_25 时 p02/p05 内容相同。"""
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-46-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
    images = []
    dup = _blob(b"DUP-25")
    for i in range(1, n_img + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpeg"
        if same_25 and i in (2, 5):
            fp.write_bytes(dup)
        else:
            fp.write_bytes(_blob(("IMG-%02d" % i).encode()))
        images.append({
            "p": p, "file": f"images/{p}.jpeg",
            "sha1": sha1_file(fp), "sha256": sha256_file(fp),
            "role": "主体", "status": "ok", "desc": f"描述{p}", "voice": "",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": fp.name, "pose": {"text": f"pose-{p}"},
        })
    segs = []
    for i in range(1, n_seg + 1):
        segs.append({
            "s": i, "text": f"seg {i}", "duration": 6,
            "image": f"p{min(i, n_img):02d}", "role": "主体", "shot": "",
            "prompt_final": "", "status": "planned", "gen_id": None,
            "takes": [], "error": None, "bridge": {},
        })
    m = Manifest.new(tid, "重扫测", "flow", "A001", "long", "hello")
    m.images = images
    m.segments = segs
    m.track = {
        "pins": {"1": "p01", "2": "p03"},
        "pools": [{"from": 1, "to": 4, "images": ["p01", "p03", "p09"]}],
        "hold": 1, "jitter": 0, "unit": "seg", "seed": 1,
    }
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, updated=? WHERE id=?",
               (str(task_dir), "arranged", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "重扫测", "flow", "A001", "long", "hello", str(task_dir / "images"),
             "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_mtime_ms_cachebust():
    print("== A. 换内容不换名 → mtime 变，src 查询串跟着变 ==")
    tid, d, m = _make(3, 2, tid="T4601")
    fp = d / "images" / "p03.jpeg"
    a = imgmod.annotate_images(d, m.images)
    by = {x["p"]: x for x in a}
    if not by["p03"].get("mtime") or by["p03"]["mtime"] < 10 ** 11:
        bad(f"mtime 该是毫秒：{by['p03'].get('mtime')}")
        return
    old = by["p03"]["mtime"]
    time.sleep(0.02)
    fp.write_bytes(_blob(b"NEW-P03"))
    os.utime(fp, (time.time() + 2, time.time() + 2))
    b = imgmod.annotate_images(d, m.images)
    by2 = {x["p"]: x for x in b}
    if by2["p03"]["mtime"] == old:
        bad(f"换内容后 mtime 没变：{old}")
        return
    js = (ROOT / "ui" / "js" / "media.js").read_text(encoding="utf-8")
    if "srcWithV" not in js or "?v=" not in js:
        bad("media.js 没有 srcWithV")
        return
    for name in ("gemini.js", "track.js", "review.js"):
        txt = (ROOT / "ui" / "js" / "panels" / name).read_text(encoding="utf-8")
        if "srcWithV" not in txt and "mediaUrl(" not in txt:
            bad(f"{name} 没走 srcWithV / mediaUrl")
            return
        if "im.mtime || im.sha1" in txt:
            bad(f"{name} 还在用旧 sha1 破缓存")
            return
    ok(f"p03 mtime {old} → {by2['p03']['mtime']}")


def test_skip_underscore_dirs():
    print("== 扫盘跳过 _replaced / _弃用 / 空镜头 ==")
    tid, d, m = _make(2, 1, tid="T4602")
    (d / "images" / "_replaced").mkdir()
    (d / "images" / "_replaced" / "old.jpeg").write_bytes(_blob(b"OLD"))
    (d / "images" / "_弃用").mkdir()
    (d / "images" / "_弃用" / "x.jpeg").write_bytes(_blob(b"DUMP"))
    (d / "空镜头").mkdir()
    (d / "空镜头" / "b01.jpeg").write_bytes(_blob(b"BROLL"))
    files = imgmod.list_library_files(d / "images")
    names = [p.name for p in files]
    if "old.jpeg" in names or "x.jpeg" in names or "b01.jpeg" in names:
        bad(f"不该扫到特殊目录：{names}")
        return
    if "p01.jpeg" not in names or "p02.jpeg" not in names:
        bad(f"该扫到主体图：{names}")
        return
    ok(f"只扫到 {names}")


def test_t0015_like_preview():
    print("== B. 磁盘 8 · 图库 9 · 删 p09 · p02/p05 相同 ==")
    tid, d, m = _make(9, 4, tid="T4603", same_25=True)
    (d / "images" / "p09.jpeg").unlink()
    m.segments[3]["image"] = "p09"  # s004 用着已删的
    m.save(d)
    prev = rescan.preview(tid)
    if prev["disk_n"] != 8:
        bad(f"disk_n={prev['disk_n']} 该是 8")
        return
    if prev["library_n"] != 9:
        bad(f"library_n={prev['library_n']} 该是 9")
        return
    gone = [x for x in prev["deleted"] if x.get("old_p") == "p09"]
    if len(gone) != 1:
        bad(f"该报 1 张已删除原 p09：{prev['deleted']}")
        return
    if "s004" not in (gone[0].get("refs") or []):
        bad(f"该点名 s004：{gone[0]}")
        return
    dupes = prev.get("dupes") or []
    names = []
    for u in dupes:
        names.extend(u.get("files") or [])
    if "p02.jpeg" not in names or "p05.jpeg" not in names:
        bad(f"该点名 p02/p05 相同：{dupes}")
        return
    ok(f"磁盘 {prev['disk_n']} · 图库 {prev['library_n']} · 删 {gone[0]['old_p']} · 重复 {names}")


def test_rename_keeps_desc_and_remap():
    print("== C. 改名靠内容认亲，描述语保留，段和钉跟着改 ==")
    tid, d, m = _make(4, 3, tid="T4604")
    src = d / "images" / "p03.jpeg"
    dest = d / "images" / "p30.jpeg"
    src.rename(dest)
    old_desc = m.image("p03")["desc"]
    old_pose = m.image("p03")["pose"]
    m.segments[1]["image"] = "p03"  # s002
    m.track["pins"]["2"] = "p03"
    m.segments[1]["bridge"] = {"to_image": "p03"}
    m.save(d)
    prev = rescan.preview(tid)
    hit = [x for x in prev["recognized"] if x.get("old_p") == "p03"]
    if not hit:
        bad(f"没认出原 p03：{prev['recognized']}")
        return
    if not hit[0].get("desc_kept"):
        bad(f"描述语没标保留：{hit[0]}")
        return
    out = rescan.apply(tid)
    m2 = Manifest.load(d)
    rec = next(im for im in m2.images if Path(im["file"]).name == "p30.jpeg")
    if rec.get("desc") != old_desc:
        bad(f"描述语丢了：{rec.get('desc')!r} vs {old_desc!r}")
        return
    if rec.get("pose") != old_pose:
        bad(f"pose 丢了：{rec.get('pose')}")
        return
    new_p = rec["p"]
    if m2.seg(2).get("image") != new_p:
        bad(f"s002 该跟着改成 {new_p}，实际 {m2.seg(2).get('image')}")
        return
    if (m2.track.get("pins") or {}).get("2") != new_p:
        bad(f"钉 2 该跟着改成 {new_p}：{m2.track.get('pins')}")
        return
    if (m2.seg(2).get("bridge") or {}).get("to_image") != new_p:
        bad(f"bridge.to_image 没跟着改：{m2.seg(2).get('bridge')}")
        return
    if not out.get("backup") or not (d / out["backup"]).is_file():
        bad(f"没生成备份：{out.get('backup')}")
        return
    bak = json.loads((d / out["backup"]).read_text(encoding="utf-8"))
    if not any(im.get("p") == "p03" and "p03.jpeg" in (im.get("file") or "")
               for im in bak.get("images") or []):
        bad("备份不是改之前的内容")
        return
    ok(f"p30.jpeg → {new_p}，desc 还在，s002/钉/锚点跟着改，备份 {out['backup']}")


def test_delete_clears_planned_not_silent():
    print("== D. 删被引用的图 → 未出片段清空，不改挂别的图 ==")
    tid, d, m = _make(3, 3, tid="T4605")
    (d / "images" / "p02.jpeg").unlink()
    m.segments[0]["image"] = "p02"
    m.segments[1]["image"] = "p02"
    m.segments[2]["image"] = "p01"
    m.save(d)
    rescan.apply(tid)
    m2 = Manifest.load(d)
    if m2.seg(1).get("image") or m2.seg(2).get("image"):
        bad(f"s001/s002 该清空：{m2.seg(1).get('image')} {m2.seg(2).get('image')}")
        return
    note = m2.seg(1).get("image_note") or ""
    if "配图已删除" not in note:
        bad(f"该打标记：{note!r}")
        return
    if m2.seg(3).get("image") != "p01":
        bad(f"s003 不该动：{m2.seg(3).get('image')}")
        return
    living = [im["p"] for im in m2.images if im.get("status") != "missing"]
    if m2.seg(1).get("image") in living:
        bad("清空后还指向活图")
        return
    ok("未出片段配图清空并打标记，没有改挂别的图")


def test_collected_untouched():
    print("== E. 已收片段的配图一个字都没变（删图走 stub） ==")
    tid, d, m = _make(3, 3, tid="T4606")
    (d / "images" / "p02.jpeg").unlink()
    m.segments[0]["image"] = "p02"
    m.segments[0]["status"] = "collected"
    m.segments[0]["prompt_final"] = "KEEP-ME"
    m.segments[1]["image"] = "p02"
    m.segments[1]["status"] = "planned"
    m.save(d)
    old_img = dict(m.seg(1))
    rescan.apply(tid)
    m2 = Manifest.load(d)
    sg = m2.seg(1)
    if sg.get("status") != "collected":
        bad(f"已收片状态被改了：{sg.get('status')}")
        return
    if sg.get("prompt_final") != "KEEP-ME":
        bad("成品提示词被改了")
        return
    if sg.get("image") != "p02":
        bad(f"已收片段配图编号变了：{sg.get('image')}")
        return
    im = m2.image(sg["image"])
    if not im or im.get("status") != "missing":
        bad(f"已收片该挂缺失占位：{im}")
        return
    if m2.seg(2).get("image"):
        bad(f"未出片段该清空：{m2.seg(2).get('image')}")
        return
    ok(f"s001 仍是 collected / {sg.get('image')} missing stub；s002 已清空")
    _ = old_img


def test_adopt_and_drop():
    print("== C. 文件不在：挑一张替换 / 删掉这一条 ==")
    tid, d, m = _make(3, 2, tid="T4607")
    (d / "images" / "p03.jpeg").unlink()
    rec = rescan.adopt_image(tid, "p03", "p01")
    fp = d / rec["file"]
    if not fp.is_file():
        bad(f"adopt 之后文件还不在：{fp}")
        return
    if rec.get("p") != "p03":
        bad(f"编号不该变：{rec.get('p')}")
        return
    m2 = Manifest.load(d)
    if sha256_file(fp) != sha256_file(d / m2.image("p01")["file"]):
        bad("adopt 拷过去的内容和 p01 不一样")
        return
    from orante.services import expand
    expand.discard_image(tid, "p03")
    m3 = Manifest.load(d)
    if m3.image("p03"):
        bad("discard 之后 p03 还在")
        return
    ok("adopt 拷文件、编号不变；discard 删掉这一条")


def test_shallow_refresh_fast_no_gemini():
    print("== D. 浅层刷新 < 300ms，不问多模态 ==")
    tid, d, m = _make(3, 2, tid="T4608")
    called = []

    class Boom:
        def __init__(self, *a, **k):
            called.append("init")

        def ask_image(self, *a, **k):
            called.append("ask")
            raise AssertionError("浅层刷新不该问多模态")

        def ask(self, *a, **k):
            called.append("ask")
            raise AssertionError("浅层刷新不该问多模态")

    with patch("orante.services.gemini.Gemini", Boom):
        t0 = time.perf_counter()
        out = refresh.run(deep=False, tid=tid)
        ms = (time.perf_counter() - t0) * 1000
    if called:
        bad(f"浅层刷新碰到了 Gemini：{called}")
        return
    if out.get("deep"):
        bad("浅层不该 deep=true")
        return
    if out.get("files_rescanned", 0) < 3:
        bad(f"该 stat 到图：{out}")
        return
    report_ms = out.get("ms", ms)
    if ms > 300:
        bad(f"浅层刷新 {ms:.0f}ms > 300ms")
        return
    ok(f"浅层 {ms:.0f}ms（接口报 {report_ms}ms），files_rescanned={out['files_rescanned']}，无多模态")


def test_nat_sort():
    print("== 编号按自然排序，p2 在 p10 前 ==")
    tid, d, m = _make(1, 1, tid="T4609")
    img = d / "images"
    for name in ("p10.jpeg", "p2.jpeg"):
        (img / name).write_bytes(_blob(name.encode()))
    (img / "p01.jpeg").unlink()
    m.images = []
    m.segments[0]["image"] = ""
    m.save(d)
    rescan.apply(tid)
    m2 = Manifest.load(d)
    names = [Path(im["file"]).name for im in m2.images if im.get("role") != "空镜"]
    ps = [im["p"] for im in m2.images if im.get("role") != "空镜"]
    if names[0] != "p2.jpeg" or names[1] != "p10.jpeg":
        bad(f"自然排序错了：{names}")
        return
    if ps != ["p01", "p02"]:
        bad(f"重编号错了：{ps} / {names}")
        return
    ok(f"{names} → {ps}")


def test_frontend_hooks():
    print("== 前端：重扫按钮、右键菜单、文件不在三键 ==")
    gem = (ROOT / "ui" / "js" / "panels" / "gemini.js").read_text(encoding="utf-8")
    tr = (ROOT / "ui" / "js" / "panels" / "track.js").read_text(encoding="utf-8")
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    if "rescan.btn" not in gem or "openRescan" not in gem:
        bad("gemini 面板没有重扫目录按钮")
        return
    if "rescan.missing_pick" not in gem or "rescan.missing_drop" not in gem:
        bad("文件不在卡片缺少三个动作")
        return
    if "openRescan" not in tr or "rescan.btn" not in tr:
        bad("编排面板没有重扫目录按钮")
        return
    if "top.refresh_rescan" not in html and "openRescan" not in html:
        bad("顶栏右键没有重扫目录")
        return
    if "rescan.js" not in html or "media.js" not in html:
        bad("index.html 没引入 rescan.js / media.js")
        return
    for k in ("rescan.btn", "rescan.title", "rescan.apply", "rescan.missing_pick",
              "top.refresh_rescan"):
        if k not in zh:
            bad(f"缺文案 {k}")
            return
    ok("按钮、三键、右键菜单、文案都在")


if __name__ == "__main__":
    test_mtime_ms_cachebust()
    test_skip_underscore_dirs()
    test_t0015_like_preview()
    test_rename_keeps_desc_and_remap()
    test_delete_clears_planned_not_silent()
    test_collected_untouched()
    test_adopt_and_drop()
    test_shallow_refresh_fast_no_gemini()
    test_nat_sort()
    test_frontend_hooks()
    print()
    if fail:
        print(f"FAILED {fail}")
        sys.exit(1)
    print("ALL OK")
