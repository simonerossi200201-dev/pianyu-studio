# -*- coding: utf-8 -*-
"""验收规格 15：图片复制 / 翻转 / 裁切 / 删除，以及路由和前端钩子。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-imgedit-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from PIL import Image as PILImage  # noqa: E402

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import expand, imgedit  # noqa: E402

db.init()

SEG_TEXT = "Stay for 7 seconds and type Amen."
W, H = 900, 1600  # 正好 9:16，短边远大于 256


def _paint(path: Path, left=(200, 30, 30), right=(30, 30, 200), size=(W, H)) -> None:
    """左半红、右半蓝，翻转后一眼能看出来。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = PILImage.new("RGB", size, right)
    im.paste(PILImage.new("RGB", (size[0] // 2, size[1]), left), (0, 0))
    im.save(path, "JPEG", quality=90)


def _ensure_account():
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()))


def _make(n_img=5, n_seg=4, tid="T1501") -> tuple[str, Path, Manifest]:
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-imgedit-"))
    (task_dir / "images").mkdir()
    images = []
    for i in range(1, n_img + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _paint(fp)
        images.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
            "role": "主体", "status": "ok", "desc": f"描述{p}", "voice": f"音色{p}",
            "eye": "闭眼", "scene": "常规",
            "measure": {"shot_size": "中景", "facing": "左侧"},
            "origin": "local", "src": str(fp),
        })
    segs = []
    for i in range(1, n_seg + 1):
        segs.append({
            "s": i, "text": f"{SEG_TEXT} #{i}", "duration": 6,
            "image": "p02" if i <= 2 else "p01",
            "role": "主体", "shot": "", "prompt_final": f"old-{i}",
            "status": "failed" if i == 1 else "planned",
            "gen_id": None, "takes": [], "error": "x" if i == 1 else None,
        })
    m = Manifest.new(tid, "图编辑测", "flow", "A001", "long", SEG_TEXT)
    m.images = images
    m.segments = segs
    m.track = {"pins": {"1": "p02"}, "pools": [], "hold": 3, "jitter": 1,
               "unit": "seg", "seed": 1}
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(task_dir), "arranged", "flow", "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "图编辑测", "flow", "A001", "long", SEG_TEXT, str(task_dir / "images"),
             "", "arranged", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_info_and_duplicate():
    tid, d, m = _make(n_img=8, tid="T1501")
    info = imgedit.image_info(tid, "p02")
    if info["w"] != W or info["h"] != H:
        raise SystemExit(f"FAIL info 尺寸 {info}")
    if info["file"] != "images/p02.jpg":
        raise SystemExit(f"FAIL info.file {info['file']}")
    pins_before = dict(m.track.get("pins") or {})
    segs_before = [(sg["s"], sg["image"]) for sg in m.segments]
    r = imgedit.duplicate(tid, "p02")
    rec = r["image"]
    if rec["p"] != "p09":
        raise SystemExit(f"FAIL 复制编号应为 p09，实际 {rec['p']}")
    if rec.get("origin") != "dup" or rec.get("origin_from") != "p02":
        raise SystemExit(f"FAIL origin {rec.get('origin')} from {rec.get('origin_from')}")
    if rec.get("desc") != "描述p02" or rec.get("voice") != "音色p02":
        raise SystemExit(f"FAIL 描述/音色没拷贝 {rec.get('desc')} {rec.get('voice')}")
    if rec.get("status") != "ok":
        raise SystemExit(f"FAIL 复制 status {rec.get('status')}")
    if rec.get("measure", {}).get("facing") != "左侧":
        raise SystemExit(f"FAIL measure 没拷贝 {rec.get('measure')}")
    dest = d / rec["file"]
    if not dest.is_file():
        raise SystemExit("FAIL 复制文件不在")
    m2 = Manifest.load(d)
    if not m2.image("p09"):
        raise SystemExit("FAIL manifest 没有 p09")
    if [(sg["s"], sg["image"]) for sg in m2.segments] != segs_before:
        raise SystemExit("FAIL 复制后配图轨变了")
    if dict(m2.track.get("pins") or {}) != pins_before:
        raise SystemExit("FAIL 复制后 pins 变了")
    if r.get("reset_segments"):
        raise SystemExit(f"FAIL 复制不该打回段 {r.get('reset_segments')}")
    print("OK 复制：p02 → p09，描述/音色/朝向拷贝，配图轨不动")


def test_flip():
    tid, d, m = _make(n_img=3, n_seg=3, tid="T1502")
    old_sha = m.image("p02")["sha1"]
    r = imgedit.flip(tid, "p02")
    rec = r["image"]
    if rec.get("flipped") is not True:
        raise SystemExit(f"FAIL flipped={rec.get('flipped')}")
    if rec.get("sha1") == old_sha:
        raise SystemExit("FAIL 翻转没换 sha1")
    if rec.get("measure", {}).get("facing") != "右侧":
        raise SystemExit(f"FAIL facing 没对调 {rec.get('measure')}")
    edits = rec.get("edits") or []
    if not edits or edits[-1].get("op") != "flip":
        raise SystemExit(f"FAIL edits {edits}")
    after = PILImage.open(d / "images" / "p02.jpg")
    if after.size != (W, H):
        raise SystemExit(f"FAIL 翻转后尺寸 {after.size}")
    al, ar = after.getpixel((10, 10)), after.getpixel((W - 10, 10))
    after.close()
    if al[2] < al[0] or ar[0] < ar[2]:
        raise SystemExit(f"FAIL 翻转像素不对 L={al} R={ar}（期望左蓝右红）")
    baks = list((d / "images" / "_replaced").glob("p02-*"))
    if not baks:
        raise SystemExit("FAIL 翻转没有备份到 _replaced/")
    reset = r.get("reset_segments") or []
    if 1 not in reset or 2 not in reset:
        raise SystemExit(f"FAIL reset_segments {reset}")
    m2 = Manifest.load(d)
    s1 = m2.seg(1)
    if s1["status"] != "planned" or s1.get("prompt_final"):
        raise SystemExit(f"FAIL s1 没打回 {s1}")
    if m2.seg(2).get("prompt_final"):
        raise SystemExit("FAIL s2 prompt_final 没清空")
    if m2.seg(1)["image"] != "p02" or m2.seg(2)["image"] != "p02":
        raise SystemExit("FAIL 翻转后配图指向变了")
    print(f"OK 翻转：像素镜像、facing 左→右、备份 {baks[0].name}、段 {reset} 打回")


def test_crop():
    tid, d, m = _make(n_img=3, n_seg=3, tid="T1503")
    # 9:16、右上、60%：最大框 900×1600，60% = 540×960，贴右上 x=360 y=0
    box = {"x": 360, "y": 0, "w": 540, "h": 960, "as_new": True}
    r = imgedit.crop(tid, "p02", box)
    rec = r["image"]
    if rec["p"] != "p04":
        raise SystemExit(f"FAIL 另存编号 {rec['p']}")
    if rec.get("origin") != "crop" or rec.get("origin_from") != "p02":
        raise SystemExit(f"FAIL origin {rec.get('origin')}")
    if rec.get("desc") != "描述p02":
        raise SystemExit("FAIL 裁切另存没拷描述")
    dest = d / rec["file"]
    with PILImage.open(dest) as pic:
        if pic.size != (540, 960):
            raise SystemExit(f"FAIL 另存像素 {pic.size}，应为 540×960")
    m2 = Manifest.load(d)
    if not (d / "images" / "p02.jpg").is_file():
        raise SystemExit("FAIL 另存却动了原图文件")
    if m2.image("p02")["sha1"] != m.image("p02")["sha1"]:
        raise SystemExit("FAIL 另存却改了原图 sha1")
    if r.get("reset_segments"):
        raise SystemExit(f"FAIL 另存不该打回 {r.get('reset_segments')}")
    print("OK 裁切另存：p04 = 540×960，原图不动")

    old_sha = m2.image("p02")["sha1"]
    r2 = imgedit.crop(tid, "p02", {"x": 360, "y": 0, "w": 540, "h": 960, "as_new": False})
    rec2 = r2["image"]
    if rec2["p"] != "p02":
        raise SystemExit(f"FAIL 覆盖编号变了 {rec2['p']}")
    with PILImage.open(d / "images" / "p02.jpg") as pic:
        if pic.size != (540, 960):
            raise SystemExit(f"FAIL 覆盖后像素 {pic.size}")
    if rec2.get("sha1") == old_sha:
        raise SystemExit("FAIL 覆盖没换 sha1")
    edits = rec2.get("edits") or []
    if not edits or edits[-1].get("op") != "crop" or edits[-1].get("box") != [360, 0, 540, 960]:
        raise SystemExit(f"FAIL 覆盖 edits {edits}")
    if not list((d / "images" / "_replaced").glob("p02-*")):
        raise SystemExit("FAIL 覆盖没有备份")
    if 1 not in (r2.get("reset_segments") or []):
        raise SystemExit(f"FAIL 覆盖应打回引用段 {r2.get('reset_segments')}")
    print("OK 裁切覆盖：原编号、有备份、edits 记下 box、引用段打回")


def test_crop_bad():
    tid, d, m = _make(n_img=2, n_seg=1, tid="T1504")
    try:
        imgedit.crop(tid, "p01", {"x": -1, "y": 0, "w": 400, "h": 400, "as_new": True})
        raise SystemExit("FAIL 越界没抛")
    except OranteError as e:
        if "裁切矩形越界" not in e.message:
            raise SystemExit(f"FAIL 越界原话不对：{e.message}")
    try:
        imgedit.crop(tid, "p01", {"x": 0, "y": 0, "w": 100, "h": 100, "as_new": True})
        raise SystemExit("FAIL 短边<256 没抛")
    except OranteError as e:
        if "裁切区域短边小于 256 像素" not in e.message:
            raise SystemExit(f"FAIL 短边原话不对：{e.message}")
    print("OK 裁切非法：越界 / 短边<256 都是规定原话")


def test_discard_ok_image():
    tid, d, m = _make(n_img=3, n_seg=3, tid="T1505")
    expand.discard_image(tid, "p02")
    m2 = Manifest.load(d)
    if m2.image("p02"):
        raise SystemExit("FAIL 删除后 p02 还在")
    if not (d / "images" / "_弃用" / "p02.jpg").is_file():
        raise SystemExit("FAIL 删除文件没进 _弃用")
    for sg in m2.segments:
        if sg["s"] in (1, 2) and sg.get("image"):
            raise SystemExit(f"FAIL 引用段没回到未配图 s{sg['s']}={sg.get('image')}")
    if m2.track.get("pins", {}).get("1") == "p02":
        raise SystemExit("FAIL pins 还钉着 p02")
    print("OK 删除 ok 图：记录去掉、文件进 _弃用、引用段清空")


def test_broll_and_routes():
    tid, d, m = _make(n_img=2, n_seg=1, tid="T1506")
    m = Manifest.load(d)
    m.image("p02")["role"] = "空镜"
    m.image("p02")["eye"] = "空镜"
    m.segments[0]["image"] = "p01"
    m.save(d)
    r = imgedit.duplicate(tid, "p02")
    if r["image"]["role"] != "空镜" or r["image"]["status"] != "ok":
        raise SystemExit(f"FAIL 空镜复制 {r['image']}")
    rel = (r["image"]["file"] or "").replace("\\", "/")
    if not rel.startswith("空镜头/"):
        raise SystemExit(f"FAIL 空镜复制路径 {rel}")
    from orante import server
    paths = {getattr(rt, "path", "") for rt in server.app.routes}
    need = (
        "/api/tasks/{tid}/image/{p}/info",
        "/api/tasks/{tid}/image/{p}/duplicate",
        "/api/tasks/{tid}/image/{p}/flip",
        "/api/tasks/{tid}/image/{p}/crop",
        "/api/tasks/{tid}/image/{p}/discard",
    )
    for p in need:
        if p not in paths:
            raise SystemExit(f"FAIL 缺路由 {p}")
    print("OK 空镜可复制进 空镜头/；五条路由都在")


def test_frontend():
    import subprocess
    js = ROOT / "ui" / "js" / "panels" / "track.js"
    crop = ROOT / "ui" / "js" / "parts" / "cropper.js"
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    txt = js.read_text(encoding="utf-8")
    if "openImgMenu" not in txt or "track.ctx.copy" not in txt:
        raise SystemExit("FAIL track.js 没有右键菜单")
    if "openCropper" not in txt:
        raise SystemExit("FAIL track.js 没调裁切编辑器")
    if "imgMenuOff" not in txt:
        raise SystemExit("FAIL track.js 没有空镜/blocked 灰项")
    if "?v=" not in txt and "im.sha1" not in txt:
        raise SystemExit("FAIL 缩略图没破缓存")
    if "cropper.js" not in html:
        raise SystemExit("FAIL index.html 没引入 cropper.js")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("track.ctx.copy", "track.ctx.flip", "track.ctx.crop", "track.ctx.discard",
              "track.ctx.discard_used", "crop.save_new", "crop.overwrite", "crop.warn_small"):
        if k not in zh:
            raise SystemExit(f"FAIL zh.json 缺 {k}")
    for f in (js, crop):
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"FAIL node --check {f.name}\n{r.stderr}")
    print("OK 前端：右键菜单、破缓存、cropper 入口、i18n、node --check")


def main():
    print(f"TEST_DATA={TEST_DATA}")
    test_info_and_duplicate()
    test_flip()
    test_crop()
    test_crop_bad()
    test_discard_ok_image()
    test_broll_and_routes()
    test_frontend()
    print("\n全部通过")


if __name__ == "__main__":
    main()
