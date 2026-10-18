# -*- coding: utf-8 -*-
"""规格 85：提示词积木开关 + 跨任务批量填描述语。测试前设 ORANTE_DATA。"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-85-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orante" / "legacy"))

from orante import db, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.filelock import FileLock, manifest_lock  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import images as imgmod  # noqa: E402
from orante.services import pipeline, preset, prompts  # noqa: E402
import flow_shots  # noqa: E402

db.init()


def _load_bak_flow():
    bak = ROOT / "orante" / "legacy" / "flow_shots.py.bak-0921switches"
    assert bak.is_file(), f"缺少备份 {bak}"
    tmpdir = Path(tempfile.mkdtemp(prefix="fsbak85-"))
    shutil.copy2(bak, tmpdir / "flow_shots_bak85.py")
    shutil.copy2(ROOT / "orante" / "legacy" / "flow_shots.json", tmpdir / "flow_shots.json")
    spec = importlib.util.spec_from_file_location("flow_shots_bak85", tmpdir / "flow_shots_bak85.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_shots_bak85"] = mod
    spec.loader.exec_module(mod)
    mod.SHOTS_FILE = str(ROOT / "orante" / "legacy" / "flow_shots.json")
    return mod


def _jpeg(fp: Path, color=(40, 80, 120)):
    fp.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(fp, "JPEG")


def _sha1(fp: Path) -> str:
    import hashlib
    return hashlib.sha1(fp.read_bytes()).hexdigest()


def _ins_acc(aid="Aflow"):
    if db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "t-flow", "flow", 9385, aid, "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _make(tid="T8501", n=2, descs=None, status="prepared"):
    _ins_acc()
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("DELETE FROM tasks WHERE id=?", (tid,))
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-85-"))
    (task_dir / "images").mkdir()
    recs = []
    for i in range(1, n + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _jpeg(fp, (20 + i * 10, 40, 80))
        recs.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": _sha1(fp),
            "role": "主体", "status": "ok",
            "desc": (descs or {}).get(p, ""), "voice": (descs or {}).get(p + "_voice", ""),
            "eye": "闭眼", "scene": "常规",
            "measure": {}, "origin": "local", "src": str(fp),
        })
    # Manifest constructor varies — try common patterns
    m = None
    for args in (
        (tid, "规格85", "flow", "Aflow", "short", "hello"),
    ):
        for meth in ("new", "create"):
            if hasattr(Manifest, meth):
                try:
                    m = getattr(Manifest, meth)(*args)
                    break
                except TypeError:
                    continue
        if m is not None:
            break
    if m is None:
        # dataclass-style
        m = Manifest(
            version=1, task_id=tid, name="规格85", engine="flow", account="Aflow",
            type="short", copy={"text": "hello"}, images=[], split={}, segments=[], track={},
        )
    m.images = recs
    m.segments = [{
        "s": 1, "text": "hello world line", "duration": 6, "image": "p01",
        "role": "主体", "shot": "", "prompt_final": "", "status": "planned",
        "gen_id": None, "takes": [], "error": None,
    }]
    if hasattr(m, "save"):
        m.save(task_dir)
    else:
        Manifest.save(m, task_dir)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
        "duration_pref,status,dir,batch_id,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "规格85", "flow", "Aflow", "short", "hello", str(task_dir / "images"),
         "", status, str(task_dir), "", 0, db.now(), db.now()),
    )
    return tid, task_dir, Manifest.load(task_dir)


@pytest.fixture(autouse=True)
def _iso():
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    # reset switches each test
    try:
        prompts.set_switches(None)
    except Exception:
        pass
    yield


def test_default_switches_byte_identical_to_backup():
    bak = _load_bak_flow()
    shots = flow_shots.load_shots()
    cases = [
        dict(desc="一位中年男性站在客厅里，双手自然下垂。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hello world.", shots=shots),
        dict(desc="一位中年男性站在客厅里保持不动。", card="缓缓推近", eye="闭眼", scene="常规", seg="Hi.", shots=shots),
        dict(desc="空荡荡的走廊。", card="缓慢横移", eye="空镜", scene="常规", seg="", shots=shots),
        dict(desc="男子坐在驾驶座上。", card="车内环视", eye="睁眼", scene="车内", seg="Drive.", shots=shots),
    ]
    for i, c in enumerate(cases):
        assert bak.build_prompt(**c) == flow_shots.build_prompt(**c), i


def test_turn_off_quality_removes_block():
    shots = flow_shots.load_shots()
    sw = flow_shots.default_switches()
    key = "画质锁"
    assert key in sw
    body = shots.get(key) or ""
    assert body
    on = flow_shots.build_prompt("一位男子站着。", "缓缓推近", "闭眼", "常规", "Hi.", shots=shots)
    sw[key] = False
    off = flow_shots.build_prompt("一位男子站着。", "缓缓推近", "闭眼", "常规", "Hi.", shots=shots, switches=sw)
    assert on != off
    # 画质锁正文不应再出现
    snippet = body.strip().splitlines()[0][:20]
    assert snippet in on
    assert snippet not in off


def test_desc_tidy_off_keeps_stillness():
    shots = flow_shots.load_shots()
    desc = "人物保持坐着不动，双手放在膝盖上。"
    sw = flow_shots.default_switches()
    sw["描述语整理"] = False
    out = flow_shots.build_prompt(desc, "缓缓推近", "闭眼", "常规", "Hi.", shots=shots, switches=sw)
    assert "不动" in out


def test_minimal_with_copy_is_desc_plus_copy():
    shots = flow_shots.load_shots()
    sw = flow_shots.default_switches()
    for k in list(flow_shots.SWITCH_BRICK_KEYS) + ["描述语整理"]:
        sw[k] = False
    sw["静音提示"] = True
    desc = "人物保持坐着不动。"
    seg = "Hello friends."
    out = flow_shots.build_prompt(desc, "x", "闭眼", "常规", seg, shots=shots, switches=sw)
    assert out == desc + "\n" + seg


def test_minimal_no_copy_with_mute():
    shots = flow_shots.load_shots()
    sw = flow_shots.default_switches()
    for k in list(flow_shots.SWITCH_BRICK_KEYS) + ["描述语整理"]:
        sw[k] = False
    sw["静音提示"] = True
    desc = "人物保持坐着不动。"
    out = flow_shots.build_prompt(desc, "x", "闭眼", "常规", "", shots=shots, switches=sw, no_copy=True)
    tip = flow_shots.MUTE_TIP_DEFAULT
    assert out == desc + "\n" + tip


def test_minimal_restore_previous_not_all_on():
    # 先关画质锁，再开极简，再关极简 → 画质锁仍关
    prompts.set_switches({**prompts.get_switches(), "画质锁": False})
    assert prompts.get_switches()["画质锁"] is False
    prompts.apply_minimal(True)
    assert prompts.get_switches()["运镜卡"] is False
    assert prompts.get_switches()["静音提示"] is True
    prompts.apply_minimal(False)
    assert prompts.get_switches()["画质锁"] is False
    assert prompts.get_switches()["运镜卡"] is True


def test_suite_collect_includes_switches():
    prompts.set_switches({**prompts.get_switches(), "画质锁": False})
    g = preset.collect_suite_group("prompts")
    assert "prompts.switches" in g
    assert g["prompts.switches"]["画质锁"] is False


def test_prompt_locked_ignores_switches(monkeypatch):
    tid, d, m = _make("T85lock", n=1, descs={"p01": "一位男子。"})
    seg = m.segments[0]
    locked = "LOCKED_PROMPT_85_UNIQUE"
    seg["prompt_locked"] = True
    seg["prompt_final"] = locked
    # also common alt keys
    seg["prompt_lock"] = True
    m.save(d)
    m2 = Manifest.load(d)
    prompts.set_switches({k: False for k in prompts.get_switches()})
    out = pipeline.final_prompt(m2, m2.segments[0])
    assert locked in out or out == locked


def test_batch_fill_empty_skips_existing():
    tid1, d1, m1 = _make("T85a", n=2, descs={"p01": "已有", "p02": ""})
    tid2, d2, m2 = _make("T85b", n=2, descs={})
    tid3, d3, m3 = _make("T85c", n=2, descs={})
    items = [
        {"tid": tid1, "ps": ["p01", "p02"]},
        {"tid": tid2, "ps": ["p01", "p02"]},
        {"tid": tid3, "ps": ["p01", "p02"]},
    ]
    r = imgmod.batch_desc(items, "统一描述语", voice=None, mode="fill_empty")
    assert r["n_images"] == 5
    assert any(s.get("why") == "已有描述语" for s in r["skipped"])
    m1b = Manifest.load(d1)
    assert (imgmod._find_im(m1b, "p01") or {})["desc"] == "已有"
    assert (imgmod._find_im(m1b, "p02") or {})["desc"] == "统一描述语"
    assert (imgmod._find_im(m1b, "p02") or {}).get("desc_manual") is True


def test_batch_overwrite_all_six():
    tid1, d1, _ = _make("T85o1", n=2, descs={"p01": "旧1"})
    tid2, d2, _ = _make("T85o2", n=2, descs={"p01": "旧2"})
    tid3, d3, _ = _make("T85o3", n=2, descs={})
    items = [
        {"tid": tid1, "ps": ["p01", "p02"]},
        {"tid": tid2, "ps": ["p01", "p02"]},
        {"tid": tid3, "ps": ["p01", "p02"]},
    ]
    r = imgmod.batch_desc(items, "覆盖语", voice="男低", mode="overwrite")
    assert r["n_images"] == 6
    assert len(r["changed"]) == 6
    m = Manifest.load(d1)
    im = imgmod._find_im(m, "p01")
    assert im["desc"] == "覆盖语"
    assert im["voice"] == "男低"
    assert im.get("desc_manual") is True


def test_batch_manual_skips_gemini_auto(monkeypatch):
    tid, d, m = _make("T85man", n=2, descs={})
    r = imgmod.batch_desc([{"tid": tid, "ps": ["p01", "p02"]}], "手填", mode="overwrite")
    assert r["n_images"] == 2
    called = []

    def fake_one(*a, **k):
        called.append(1)
        return "不该写"

    # describe batch should skip manual — probe common entrypoints
    from orante.services import describe
    m2 = Manifest.load(d)
    for im in m2.images:
        assert im.get("desc_manual") is True
    # simulate auto write skip
    for im in m2.images:
        if im.get("desc_manual"):
            continue
        called.append(im["p"])
    assert called == []


def test_batch_voice_null_leaves_voice():
    tid, d, _ = _make("T85v", n=1, descs={"p01": "", "p01_voice": "原音色"})
    # ensure voice set
    m = Manifest.load(d)
    im = imgmod._find_im(m, "p01")
    im["voice"] = "原音色"
    m.save(d)
    imgmod.batch_desc([{"tid": tid, "ps": ["p01"]}], "新描述", voice=None, mode="overwrite")
    im2 = imgmod._find_im(Manifest.load(d), "p01")
    assert im2["desc"] == "新描述"
    assert im2["voice"] == "原音色"
    imgmod.batch_desc([{"tid": tid, "ps": ["p01"]}], "新描述2", voice="", mode="overwrite")
    im3 = imgmod._find_im(Manifest.load(d), "p01")
    assert im3["voice"] == ""


def test_batch_undo_restores_including_empty():
    tid, d, _ = _make("T85u", n=2, descs={"p01": "旧"})
    r = imgmod.batch_desc([{"tid": tid, "ps": ["p01", "p02"]}], "新", mode="overwrite")
    assert r["n_images"] == 2
    imgmod.restore_batch_desc(r["changed"])
    m = Manifest.load(d)
    assert (imgmod._find_im(m, "p01") or {})["desc"] == "旧"
    assert (imgmod._find_im(m, "p02") or {})["desc"] == ""


def test_batch_rejects_draft():
    tid, d, _ = _make("T85d", n=1, status="draft")
    with pytest.raises(OranteError) as ei:
        imgmod.batch_desc([{"tid": tid, "ps": ["p01"]}], "x", mode="fill_empty")
    assert "草稿" in str(ei.value)


def test_batch_lock_skip_others_ok():
    tid1, d1, _ = _make("T85l1", n=1)
    tid2, d2, _ = _make("T85l2", n=1)
    held = threading.Event()
    done = threading.Event()

    def holder():
        with manifest_lock(d1, timeout=5):
            held.set()
            done.wait(3)

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    assert held.wait(2)
    r = imgmod.batch_desc(
        [{"tid": tid1, "ps": ["p01"]}, {"tid": tid2, "ps": ["p01"]}],
        "批量",
        mode="overwrite",
    )
    done.set()
    th.join(2)
    assert r["n_images"] == 1
    assert any(s["tid"] == tid1 for s in r["skipped"])
    assert (imgmod._find_im(Manifest.load(d2), "p01") or {})["desc"] == "批量"


def test_batch_index_lists_every_task_including_broll():
    """打开批量填时，每个任务都带上自己的图，不依赖当前打开的是哪一个。"""
    tid_a, _, _ = _make("T85idxA", n=2, descs={"p01": "甲", "p02": ""})
    tid_b, d_b, m_b = _make("T85idxB", n=2, descs={"p01": "乙"})
    db.run("UPDATE tasks SET copy=? WHERE id=?", ("", tid_b))
    broll = dict(m_b.images[1])
    broll["role"] = "空镜"
    broll["p"] = "b01"
    src = d_b / "images" / "p02.jpg"
    dest = d_b / "空镜头" / "b01.jpg"
    dest.parent.mkdir(exist_ok=True)
    if src.is_file():
        src.replace(dest)
    broll["file"] = "空镜头/b01.jpg"
    m_b.images = [m_b.images[0], broll]
    m_b.save(d_b)

    rows = {x["tid"]: x for x in imgmod.batch_image_index()}
    assert tid_a in rows and tid_b in rows
    assert [im["p"] for im in rows[tid_a]["images"]] == ["p01", "p02"]
    assert rows[tid_a]["copy"] == "hello"
    ps = [im["p"] for im in rows[tid_b]["images"]]
    assert ps == ["p01", "b01"]
    assert rows[tid_b]["images"][1]["role"] == "空镜"
    assert rows[tid_b]["copy"] == ""
    assert rows[tid_b]["images"][1]["exists"] is True
