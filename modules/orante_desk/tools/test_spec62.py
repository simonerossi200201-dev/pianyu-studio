# -*- coding: utf-8 -*-
"""规格 62：ui.current_task / track.strip_view 落盘；应用后镜头序列 = manifest。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-62-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)

from orante import db, config, tasks as taskmod  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import arrange, track as trackmod  # noqa: E402

db.init()
load_all_services()


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


def _make_task(tid="T0022", n_seg=6, n_img=3, images_on_seg=None, collected=None):
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-62-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
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
    segs = []
    for i in range(1, n_seg + 1):
        img = ""
        if images_on_seg:
            img = images_on_seg[(i - 1) % len(images_on_seg)]
        st = "collected" if collected and i in collected else "planned"
        takes = []
        if st == "collected":
            rel = f"clips/{tid}-s{i:03d}-{img or 'p00'}.mp4"
            (task_dir / rel).write_bytes(b"\x00" * 200)
            takes = [{"file": rel}]
        segs.append({
            "s": i, "text": f"Stay #{i}", "duration": 8,
            "image": img, "role": "anchor" if i % 2 else "bridge",
            "shot": "", "prompt_final": "",
            "status": st, "gen_id": None, "takes": takes, "error": None,
        })
    m = Manifest.new(tid, "认真0913", "flow", "A001", "long", "copy")
    m.images = images
    m.segments = segs
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine=?, account_id=?, updated=? WHERE id=?",
               (str(task_dir), "described", "flow", "A001", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "认真0913", "flow", "A001", "long", "copy", str(task_dir / "images"),
             "", "described", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_config_defaults_and_persist():
    assert config.DEFAULTS.get("ui.current_task") == ""
    assert config.DEFAULTS.get("track.strip_view") == "pool"
    config.set("ui.current_task", "T0022")
    config.set("track.strip_view", "shots")
    assert config.get("ui.current_task") == "T0022"
    assert config.get("track.strip_view") == "shots"
    alls = config.all()
    assert alls["ui.current_task"] == "T0022"
    assert alls["track.strip_view"] == "shots"


def test_get_track_clip_on_collected():
    tid, d, m = _make_task(
        tid="T0062", n_seg=3, n_img=2,
        images_on_seg=["p01", "p02", "p01"],
        collected={2},
    )
    data = arrange.get_track(tid)
    segs = data["segments"]
    assert len(segs) == 3
    assert segs[1]["status"] == "collected"
    assert segs[1].get("clip_file", "").startswith("clips/")
    assert segs[1].get("clip_video") is True
    assert not segs[0].get("clip_file")


def test_preview_then_apply_matches_manifest():
    tid, d, m = _make_task(
        tid="T0063", n_seg=6, n_img=3,
        images_on_seg=["p01", "p01", "p01", "p02", "p02", "p03"],
    )
    prev = arrange.preview(tid, {
        "hold": 1, "jitter": 0, "unit": "seg", "seed": 7,
        "no_adjacent_repeat": False, "keep": False,
        "pool": ["p01", "p02", "p03"],
    })
    rows = prev["rows"]
    assert rows and len(rows) == 6
    # 预演非空 = 镜头序列走 p_new
    preview_ps = [r["p_new"] for r in rows]
    assert len(preview_ps) == 6

    class J:
        task_id = tid
        def log(self, msg):
            pass

    arrange.arrange(J(), {
        "hold": 1, "jitter": 0, "unit": "seg", "seed": 7,
        "no_adjacent_repeat": False, "keep": False,
        "pool": ["p01", "p02", "p03"],
    })
    m2 = Manifest.load(d)
    applied = [sg.get("image") for sg in m2.segments]
    assert applied == preview_ps
    # 应用之后镜头序列读 manifest，必须和生成用的那份一样
    data = arrange.get_track(tid)
    assert [sg.get("image") for sg in data["segments"]] == applied


def test_longest_run_three():
    # 同一张图连用 3 段：预演 rows 里能数出来
    tid, d, m = _make_task(
        tid="T0064", n_seg=5, n_img=3,
        images_on_seg=["p03", "p03", "p03", "p01", "p02"],
    )
    # 钉住前三段 p03，预演 keep
    prev = arrange.preview(tid, {
        "hold": 3, "jitter": 0, "unit": "seg", "seed": 1,
        "no_adjacent_repeat": False, "keep": True,
        "pins": {"1": "p03", "2": "p03", "3": "p03"},
        "pool": ["p01", "p02", "p03"],
    })
    ps = [r["p_new"] for r in prev["rows"]]
    assert ps[:3] == ["p03", "p03", "p03"], ps
    run, best = 0, 0
    cur = ""
    for p in ps:
        if p == cur:
            run += 1
        else:
            cur, run = p, 1
        if run > best:
            best = run
    assert best >= 3
