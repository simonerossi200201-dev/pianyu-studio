# -*- coding: utf-8 -*-
"""规格 75：多模态卡片多选 / 同步描述语 / 打开图片。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec75-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import images as imgmod  # noqa: E402

db.init()

UI = ROOT / "ui"
PANELS = UI / "js" / "panels"
PARTS = UI / "js" / "parts"


def _jpeg(path: Path, color=(40, 80, 120), size=(48, 64)):
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, color).save(path, "JPEG", quality=90)


def _ins_acc(aid="Aflow"):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "t-flow", "flow", 9375, aid, "", "", "9:16", 5, 1, "{}", db.now()))


def _make(tid="T7501", n=6, n_broll=1):
    _ins_acc()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-75-"))
    (task_dir / "images").mkdir()
    (task_dir / "空镜头").mkdir()
    recs = []
    for i in range(1, n + 1):
        p = f"p{i:02d}"
        broll = i > n - n_broll
        folder = "空镜头" if broll else "images"
        fp = task_dir / folder / f"{p}.jpg"
        _jpeg(fp, (20 + i * 10, 40, 80))
        recs.append({
            "p": p, "file": f"{folder}/{p}.jpg", "sha1": sha1_file(fp),
            "role": "空镜" if broll else "主体", "status": "ok",
            "desc": f"描述{p}", "voice": f"音色{p}",
            "eye": "空镜" if broll else "闭眼", "scene": "常规",
            "measure": {}, "origin": "local", "src": str(fp),
        })
    m = Manifest.new(tid, "规格75", "flow", "Aflow", "short", "hello")
    m.images = recs
    m.segments = [{
        "s": 1, "text": "hi", "duration": 6, "image": "p01",
        "role": "主体", "shot": "", "prompt_final": "", "status": "planned",
        "gen_id": None, "takes": [], "error": None,
    }]
    m.save(task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "规格75", "flow", "Aflow", "short", "hello", str(task_dir / "images"),
         "", "described", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def test_backups_exist():
    for rel in (
        "ui/js/panels/gemini.js.bak-0920cardmenu",
        "ui/js/panels/track.js.bak-0920cardmenu",
        "ui/css/app.css.bak-0920cardmenu",
        "ui/index.html.bak-0920cardmenu",
        "orante/server.py.bak-0920cardmenu",
        "orante/services/images.py.bak-0920cardmenu",
        "orante/i18n/zh.json.bak-0920cardmenu",
    ):
        assert (ROOT / rel).is_file(), rel


def test_copy_desc_three_targets_source_untouched():
    tid, d, m = _make("T7501", n=5, n_broll=0)
    src_desc = m.image("p01")["desc"]
    src_voice = m.image("p01")["voice"]
    olds = {p: (m.image(p)["desc"], m.image(p)["voice"]) for p in ("p02", "p03", "p04")}
    r = imgmod.copy_desc(tid, "p01", ["p02", "p03", "p04"], ["desc"])
    assert r["n"] == 3
    got = {c["p"]: c for c in r["changed"]}
    for p, (od, ov) in olds.items():
        assert p in got
        assert got[p]["desc_old"] == od
        assert got[p]["voice_old"] == ov
    m2 = Manifest.load(d)
    assert m2.image("p01")["desc"] == src_desc
    assert m2.image("p01")["voice"] == src_voice
    for p in ("p02", "p03", "p04"):
        assert m2.image(p)["desc"] == src_desc
        assert m2.image(p)["voice"] == olds[p][1]


def test_copy_fields_desc_and_voice_or_voice_only():
    tid, d, m = _make("T7502", n=4, n_broll=0)
    src = m.image("p01")
    r = imgmod.copy_desc(tid, "p01", ["p02"], ["desc", "voice"])
    assert r["n"] == 1
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] == src["desc"]
    assert m2.image("p02")["voice"] == src["voice"]

    tid2, d2, m2b = _make("T7502b", n=4, n_broll=0)
    old_desc = m2b.image("p03")["desc"]
    r2 = imgmod.copy_desc(tid2, "p01", ["p03"], ["voice"])
    assert r2["n"] == 1
    m3 = Manifest.load(d2)
    assert m3.image("p03")["voice"] == m2b.image("p01")["voice"]
    assert m3.image("p03")["desc"] == old_desc


def test_copy_missing_target_skipped_rest_ok():
    tid, d, m = _make("T7503", n=4, n_broll=0)
    r = imgmod.copy_desc(tid, "p01", ["p02", "p99", "p03"], ["desc"])
    assert r["n"] == 2
    skipped = {s["p"]: s["why"] for s in r["skipped"]}
    assert "p99" in skipped
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] == m.image("p01")["desc"]
    assert m2.image("p03")["desc"] == m.image("p01")["desc"]


def test_copy_restore_roundtrip():
    tid, d, m = _make("T7504", n=4, n_broll=0)
    before = {p: (m.image(p)["desc"], m.image(p)["voice"]) for p in ("p02", "p03")}
    r = imgmod.copy_desc(tid, "p01", ["p02", "p03"], ["desc", "voice"])
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] == m.image("p01")["desc"]
    imgmod.restore_desc(tid, r["changed"])
    m3 = Manifest.load(d)
    for p, (od, ov) in before.items():
        assert m3.image(p)["desc"] == od
        assert m3.image(p)["voice"] == ov


def test_copy_excludes_source_even_if_in_to_ps():
    tid, d, m = _make("T7505", n=3, n_broll=0)
    src = m.image("p02")["desc"]
    r = imgmod.copy_desc(tid, "p02", ["p01", "p02", "p03"], ["desc"])
    assert "p02" not in [c["p"] for c in r["changed"]]
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] == src


def test_open_reveal_reject_path_traversal_no_subprocess():
    with patch("orante.services.images.subprocess.Popen") as popen, \
            patch("orante.services.images.os.startfile", create=True) as startfile:
        with pytest.raises(OranteError) as ei:
            imgmod.open_image("T7501", "../../something")
        assert "任务目录" in ei.value.message or "路径" in ei.value.message
        with pytest.raises(OranteError):
            imgmod.reveal_image("T7501", "../../something")
        with pytest.raises(OranteError):
            imgmod.open_image("T7501", "..\\..\\secret")
        assert popen.call_count == 0
        assert startfile.call_count == 0


def test_open_missing_file_orante_error():
    tid, d, m = _make("T7506", n=2, n_broll=0)
    fp = d / m.image("p01")["file"]
    fp.unlink()
    with patch("orante.services.images.subprocess.Popen") as popen, \
            patch("orante.services.images.os.startfile", create=True) as startfile:
        with pytest.raises(OranteError) as ei:
            imgmod.open_image(tid, "p01")
        assert "不存在" in ei.value.message
        assert popen.call_count == 0
        assert startfile.call_count == 0


def test_open_file_outside_task_dir_rejected():
    tid, d, m = _make("T7507", n=1, n_broll=0)
    outside = Path(tempfile.mkdtemp(prefix="orante-out-75-")) / "secret.jpg"
    _jpeg(outside)
    m.images[0]["file"] = os.path.relpath(outside, d).replace("\\", "/")
    if not m.images[0]["file"].startswith(".."):
        m.images[0]["file"] = "../" + outside.name
    m.save(d)
    with patch("orante.services.images.subprocess.Popen") as popen, \
            patch("orante.services.images.os.startfile", create=True) as startfile:
        with pytest.raises(OranteError) as ei:
            imgmod.open_image(tid, "p01")
        assert "任务目录" in ei.value.message or "路径" in ei.value.message
        assert popen.call_count == 0
        assert startfile.call_count == 0


def _await(coro):
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _json_req(payload: dict):
    from starlette.requests import Request

    raw = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/", "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 123), "server": ("127.0.0.1", 80),
    }
    return Request(scope, receive)


def test_routes_and_http_copy_and_missing_not_500():
    from orante.server import app, task_image_open, task_images_copy_desc

    paths_ = {getattr(r, "path", None) for r in app.routes}
    assert "/api/tasks/{tid}/images/copy-desc" in paths_
    assert "/api/tasks/{tid}/image/{p}/open" in paths_
    assert "/api/tasks/{tid}/image/{p}/reveal" in paths_

    tid, d, m = _make("T7508", n=4, n_broll=0)
    body = _await(task_images_copy_desc(
        tid, _json_req({"from_p": "p01", "to_ps": ["p02", "p03"], "fields": ["desc"]})))
    assert body.get("ok") is True
    assert body.get("n") == 2

    with pytest.raises(OranteError) as ei:
        task_image_open(tid, "p99")
    assert "不存在" in ei.value.message or "没有" in ei.value.message

    r3 = _await(task_images_copy_desc(tid, _json_req({"restore": body["changed"]})))
    assert r3.get("ok") is True
    m2 = Manifest.load(d)
    assert m2.image("p02")["desc"] == m.image("p02")["desc"]


def test_http_path_traversal_not_500():
    from orante.server import task_image_open, task_image_reveal

    tid, _d, _m = _make("T7509", n=1, n_broll=0)
    with patch("orante.services.images.subprocess.Popen") as popen, \
            patch("orante.services.images.os.startfile", create=True) as startfile:
        with pytest.raises(OranteError):
            task_image_open(tid, "../../something")
        with pytest.raises(OranteError):
            task_image_reveal(tid, "../../something")
        assert popen.call_count == 0
        assert startfile.call_count == 0


def test_index_loads_ctxmenu_before_panels():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "/ui/js/parts/ctxmenu.js" in html
    assert html.index("/ui/js/parts/ctxmenu.js") < html.index("/ui/js/panels/gemini.js")
    assert html.index("/ui/js/parts/ctxmenu.js") < html.index("/ui/js/panels/track.js")


def test_track_uses_shared_menu():
    src = (PANELS / "track.js").read_text(encoding="utf-8")
    assert "window.openCtxMenu" in src
    assert "window.placeMenu" in src
    assert "function placeMenu(x, y, w, h, vw, vh, pad)" not in src


def test_gemini_hooks():
    src = (PANELS / "gemini.js").read_text(encoding="utf-8")
    assert "window.GeminiCardLogic" in src
    assert "images/copy-desc" in src
    assert "openCtxMenu" in src
    assert "img-check" in src
    assert "gemini.ctx.sync_desc" in src or "t('gemini.ctx.sync_desc'" in src


def test_zh_keys():
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k, v in (
        ("gemini.sel.count", "已选 {n} 张"),
        ("gemini.ctx.off.need_sel", "先勾选要拷贝到的卡片"),
        ("gemini.copy.undo", "撤销"),
        ("gemini.ctx.open", "打开图片"),
        ("gemini.ctx.reveal", "打开图片位置"),
    ):
        assert zh[k] == v, k


def test_node_syntax():
    files = [
        PARTS / "ctxmenu.js",
        PANELS / "gemini.js",
        PANELS / "track.js",
    ]
    for p in files:
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{p.name} {r.stderr or r.stdout}"


def test_node_logic():
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "test_spec75_ui.js")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert "LOGIC_OK" in (r.stdout or "")
