# -*- coding: utf-8 -*-
"""规格 84：时长 / 画幅。测试前由 conftest 设 ORANTE_DATA。"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-84-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante.engines import caps, flow_api
from orante.services import split as splitmod


def _load_bak_split():
    bak = ROOT / "orante" / "services" / "split.py.bak-0921duraspect"
    assert bak.is_file(), f"缺少备份 {bak}"
    tmp = Path(tempfile.mkdtemp(prefix="splitbak84-")) / "split_bak_84.py"
    shutil.copy2(bak, tmp)
    spec = importlib.util.spec_from_file_location("split_bak_84", tmp)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "orante.services"
    sys.modules["split_bak_84"] = mod
    spec.loader.exec_module(mod)
    return mod


def _seg_key(segs):
    out = []
    for s in segs:
        text = s.get("text") if isinstance(s, dict) else getattr(s, "text", "")
        dur = s.get("duration") if isinstance(s, dict) else getattr(s, "duration", 0)
        out.append((text, int(dur)))
    return out


def _run_split(mod, copy: str, engine: str, duration_pref=""):
    return mod.preview(copy, engine)


@pytest.fixture(autouse=True)
def _iso():
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    yield


COPIES = {
    "grok_short": "今天天气不错，我们出去走走。",
    "grok_mid": "今天天气不错，我们出去走走。" * 8,
    "grok_long": "这是一段比较长的中文文案，用来测试按字数拆段和定档是否保持原样。" * 20,
    "flow_short": "Hello world, this is a short line for flow.",
    "flow_long": (
        "Hello world, this is a longer flow copy with many words to force multi "
        "segment split behavior across several clauses, commas, and periods. "
    ) * 6,
}


def test_with_copy_matches_backup_split():
    """有文案：段数/每段文字/时长必须与改前 split.py.bak-0921duraspect 完全一致。"""
    bak = _load_bak_split()
    for key, engine, pref in [
        ("grok_short", "grok", ""),
        ("grok_mid", "grok", "6"),
        ("grok_long", "grok", "15"),
        ("flow_short", "flow", "4"),
        ("flow_long", "flow", "8"),
    ]:
        copy = COPIES[key]
        assert _seg_key(_run_split(splitmod, copy, engine, pref)) == _seg_key(
            _run_split(bak, copy, engine, pref)
        ), (key, engine, pref)


def test_grok_with_copy_ignores_pref_15():
    copy = "测" * 100
    bak = _load_bak_split()
    assert _seg_key(_run_split(splitmod, copy, "grok", "15")) == _seg_key(
        _run_split(bak, copy, "grok", "15")
    )


def test_no_copy_duration_grok_15():
    assert splitmod.no_copy_duration({"duration_pref": "15"}, "grok") == 15


def test_no_copy_duration_default_flow():
    assert splitmod.no_copy_duration({"duration_pref": ""}, "flow") == 8


def test_no_copy_duration_flow_15_raises():
    with pytest.raises(Exception) as ei:
        splitmod.no_copy_duration({"duration_pref": "15"}, "flow")
    assert "8" in str(ei.value)


def test_flow_with_copy_pref_15_still_splits():
    bak = _load_bak_split()
    copy = COPIES["flow_short"]
    assert _seg_key(_run_split(splitmod, copy, "flow", "15")) == _seg_key(
        _run_split(bak, copy, "flow", "15")
    )


def test_model_id_for_rejects_10():
    with pytest.raises(ValueError):
        flow_api.model_id_for("x", 10)


def test_aspect_for_image_basic(tmp_path):
    land = tmp_path / "a.png"; Image.new("RGB", (1920, 1080)).save(land)
    port = tmp_path / "b.png"; Image.new("RGB", (1080, 1920)).save(port)
    sq = tmp_path / "c.png"; Image.new("RGB", (1024, 1024)).save(sq)
    assert caps.aspect_for_image(land) == "16:9"
    assert caps.aspect_for_image(port) == "9:16"
    assert caps.aspect_for_image(sq) == "16:9"


def test_crop_matches_capture_landscape(tmp_path):
    p = tmp_path / "land.png"
    Image.new("RGB", (5504, 3072)).save(p)
    crop = flow_api._crop_box(p)
    assert crop[0] is None and crop[2] == 1
    assert abs(crop[1] - 0.0038759689922481244) < 1e-5
    assert abs(crop[3] - 0.9961240310077519) < 1e-5
    assert caps.flow_aspect_code(caps.aspect_for_image(p)) == 2


def test_crop_portrait_never_cross_aspect(tmp_path):
    p = tmp_path / "port.png"
    Image.new("RGB", (768, 1376)).save(p)
    crop = flow_api._crop_box(p)
    assert crop[1] is None and crop[3] == 1
    assert caps.flow_aspect_code(caps.aspect_for_image(p)) == 1


def test_landscape_never_cropped_to_portrait(tmp_path):
    for wh in [(1920, 1080), (5504, 3072), (1280, 720), (3000, 2000)]:
        p = tmp_path / f"{wh[0]}x{wh[1]}.png"
        Image.new("RGB", wh).save(p)
        crop = flow_api._crop_box(p)
        assert caps.aspect_for_image(p) == "16:9"
        top, left, bottom, right = crop
        if left is not None or (right is not None and right != 1):
            assert top is None and bottom == 1
        if top is not None or (bottom is not None and bottom != 1):
            assert left is None and right == 1


def test_first_last_frame_mismatch_guard_present():
    src = Path(flow_api.__file__).read_text(encoding="utf-8")
    assert "横" in src and "竖" in src and "不支持" in src


def test_account_aspect_ignored_for_crop(tmp_path):
    p = tmp_path / "l.png"
    Image.new("RGB", (1600, 900)).save(p)
    crop = flow_api._crop_box(p, aspect="9:16")
    assert crop[0] is None and crop[2] == 1


def test_no_account_get_aspect_in_engines_services():
    bad = []
    for folder in ("orante/engines", "orante/services"):
        for fp in (ROOT / folder).rglob("*.py"):
            if fp.name == "caps.py":
                continue
            t = fp.read_text(encoding="utf-8")
            if 'account.get("aspect")' in t or "account.get('aspect')" in t:
                bad.append(str(fp.relative_to(ROOT)))
    assert not bad, bad


def test_durations_single_source():
    for rel in ["orante/services/split.py", "orante/services/generate.py"]:
        fp = ROOT / rel
        if not fp.exists():
            continue
        t = fp.read_text(encoding="utf-8")
        assert "(4, 6, 8)" not in t
        assert "(6, 10, 15)" not in t


def test_capture_file_readable():
    cap = ROOT / "data" / "capture" / "flow_capture_0921-183725.jsonl"
    assert cap.is_file()
    assert len(cap.read_text(encoding="utf-8", errors="replace").splitlines()) >= 36


# ===== 补丁：C/D/F =====
from orante.services import duration_edit as duredit
from orante.models import Manifest
from orante.engines import caps


def _tmp_manifest(engine="flow", segs=None, images=None):
    d = Path(tempfile.mkdtemp(prefix="orante-84p-"))
    (d / "images").mkdir(exist_ok=True)
    from PIL import Image
    Image.new("RGB", (64, 64), (1, 2, 3)).save(d / "images" / "p01.png")
    m = Manifest.new("t84", "t84", engine, "acc", "short", "hello world")
    m.images = images if images is not None else [
        {"p": "p01", "file": "images/p01.png", "role": "主体", "status": "ok"},
    ]
    if segs is not None:
        m.segments = segs
    else:
        m.segments = [
            {"s": 1, "text": "hello world", "duration": 6, "image": "p01",
             "role": "主体", "status": "planned"},
            {"s": 2, "text": "x" * 200, "duration": 6, "image": "p01",
             "role": "主体", "status": "collected"},
        ]
    m.save(d)
    return d, m


def test_duration_warn_too_long_flow():
    sg = {"text": "word " * 40, "duration": 4}
    w = duredit.duration_warn(sg, "flow")
    assert "念不完" in w


def test_duration_warn_too_short_grok():
    sg = {"text": "短", "duration": 15}
    w = duredit.duration_warn(sg, "grok")
    assert "停顿" in w


def test_aspect_label_landscape_portrait(tmp_path=None):
    from PIL import Image
    d = Path(tempfile.mkdtemp(prefix="asp84-"))
    land = d / "a.png"
    port = d / "b.png"
    Image.new("RGB", (320, 180)).save(land)
    Image.new("RGB", (180, 320)).save(port)
    assert duredit.aspect_label_for_path(land) == "横 16:9"
    assert duredit.aspect_label_for_path(port) == "竖 9:16"


def test_preserve_manual_durations():
    old = [{"s": 1, "text": "a", "duration": 8, "duration_manual": True},
           {"s": 2, "text": "b", "duration": 6}]
    new = [{"s": 1, "text": "a", "duration": 4},
           {"s": 2, "text": "b", "duration": 4}]
    out = duredit.preserve_manual_durations(old, new)
    assert out[0]["duration"] == 8 and out[0]["duration_manual"] is True
    assert out[1]["duration"] == 4 and not out[1].get("duration_manual")


def test_enrich_seg_aspect_and_options():
    from PIL import Image
    d = Path(tempfile.mkdtemp(prefix="enr84-"))
    (d / "images").mkdir()
    img = d / "images" / "p01.png"
    Image.new("RGB", (400, 200)).save(img)
    m = Manifest.new("t", "t", "flow", "a", "short", "hi")
    m.images = [{"p": "p01", "file": "images/p01.png", "role": "主体", "status": "ok"}]
    m.segments = [{"s": 1, "text": "hi", "duration": 6, "image": "p01", "status": "planned"}]
    m.save(d)
    m2 = Manifest.load(d)
    item = duredit.enrich_seg(d, m2, m2.segments[0])
    assert item["aspect_label"] == "横 16:9"
    assert item["duration_options"] == [4, 6, 8]


def test_set_seg_duration_manual_and_stale(monkeypatch):
    """用临时目录 stub broll.load_synced / taskmod。"""
    d, m = _tmp_manifest()

    class T:
        @staticmethod
        def get(tid):
            return {"id": tid, "engine": "flow", "copy": "hello"}
        @staticmethod
        def ensure_writable(tid):
            return True
        @staticmethod
        def remember_counts(d, m):
            return {}

    def fake_load(tid):
        return d, Manifest.load(d), []

    monkeypatch.setattr(duredit, "taskmod", T)
    monkeypatch.setattr(duredit.broll, "load_synced", fake_load)
    monkeypatch.setattr(duredit.pipeline, "stamp_waits", lambda m: None)
    monkeypatch.setattr(duredit.events, "emit", lambda *a, **k: None)

    r = duredit.set_seg_durations("t84", [{"s": 1, "duration": 8}, {"s": 2, "duration": 4}])
    assert 1 in r["changed"] and 2 in r["changed"]
    m2 = Manifest.load(d)
    s1 = next(x for x in m2.segments if x["s"] == 1)
    s2 = next(x for x in m2.segments if x["s"] == 2)
    assert s1["duration"] == 8 and s1.get("duration_manual") is True
    assert s2["duration"] == 4 and s2.get("duration_manual") is True
    assert s2.get("duration_stale") is True
    assert s2["status"] == "collected"  # 不打回


def test_set_seg_skip_collected(monkeypatch):
    d, m = _tmp_manifest()

    class T:
        @staticmethod
        def get(tid):
            return {"id": tid, "engine": "flow", "copy": ""}
        @staticmethod
        def ensure_writable(tid):
            return True
        @staticmethod
        def remember_counts(d, m):
            return {}

    monkeypatch.setattr(duredit, "taskmod", T)
    monkeypatch.setattr(duredit.broll, "load_synced", lambda tid: (d, Manifest.load(d), []))
    monkeypatch.setattr(duredit.pipeline, "stamp_waits", lambda m: None)
    monkeypatch.setattr(duredit.events, "emit", lambda *a, **k: None)

    r = duredit.set_seg_durations("t84", [{"s": 2, "duration": 8}], skip_collected=True)
    assert 2 in r["skipped_collected"]
    m2 = Manifest.load(d)
    s2 = next(x for x in m2.segments if x["s"] == 2)
    assert s2["duration"] == 6  # 未改


def test_restore_clears_manual(monkeypatch):
    d, m = _tmp_manifest()
    m.segments[0]["duration"] = 8
    m.segments[0]["duration_manual"] = True
    m.save(d)

    class T:
        @staticmethod
        def get(tid):
            return {"id": tid, "engine": "flow", "copy": "hello world"}
        @staticmethod
        def ensure_writable(tid):
            return True
        @staticmethod
        def remember_counts(d, m):
            return {}

    monkeypatch.setattr(duredit, "taskmod", T)
    monkeypatch.setattr(duredit.broll, "load_synced", lambda tid: (d, Manifest.load(d), []))
    monkeypatch.setattr(duredit.pipeline, "stamp_waits", lambda m: None)
    monkeypatch.setattr(duredit.events, "emit", lambda *a, **k: None)

    r = duredit.restore_seg_durations("t84", [1])
    m2 = Manifest.load(d)
    s1 = next(x for x in m2.segments if x["s"] == 1)
    assert not s1.get("duration_manual")
