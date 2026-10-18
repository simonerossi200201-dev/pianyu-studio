# -*- coding: utf-8 -*-
"""规格 71：⑤收片 进度条可拖 + 逐帧；fps 写入 payload / manifest。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec71-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)

from orante.models import Manifest  # noqa: E402
from orante.services import review  # noqa: E402


def test_backups_exist():
    for rel in (
        "ui/js/panels/review.js.bak-0917scrub",
        "ui/css/app.css.bak-0917scrub",
        "orante/services/review.py.bak-0917scrub",
        "orante/i18n/zh.json.bak-0917scrub",
    ):
        assert (ROOT / rel).is_file(), rel


def test_i18n_and_css():
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    assert zh["review.keys.frame"] == "上一帧 / 下一帧"
    assert zh["review.keys.sec"] == "后退 / 前进 1 秒"
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".panel-review .stage .bar-knob" in css
    assert ".panel-review .stage .bar-tip" in css
    assert ".panel-review .stage .bar-shade" in css
    assert "rgba(255,255,255,.28)" in css
    js = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    assert "window.ReviewScrubber" in js
    assert "fastSeek" not in js
    assert "wasPlaying" in js
    assert "@pointerenter=\"onStageEnter" in js


def _clip_task(tid="T0071"):
    d = Path(tempfile.mkdtemp(prefix="orante-task-71-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    img = d / "images" / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9xxxx")
    clip = d / "clips" / f"{tid}-s001-p01.mp4"
    clip.write_bytes(b"not-a-real-mp4" * 20)
    m = Manifest.new(tid, "进度条测", "flow", "A001", "long", "hello")
    m.images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "a" * 40,
        "role": "主体", "status": "ok", "desc": "", "voice": "",
        "eye": "闭眼", "scene": "常规", "measure": {},
        "origin": "local", "src": str(img),
    }]
    m.segments = [{
        "s": 1, "text": "hi", "duration": 8, "image": "p01",
        "role": "主体", "shot": "", "prompt_final": "",
        "status": "collected", "gen_id": None,
        "takes": [{"file": f"clips/{tid}-s001-p01.mp4"}],
        "error": None,
    }]
    m.save(d)
    return d, Manifest.load(d)


def test_fps_cached_from_probe():
    d, m = _clip_task("T0071a")
    calls = {"n": 0}

    def fake_info(path):
        calls["n"] += 1
        return {"fps": 30.0, "width": 8, "height": 8, "duration": 2.0, "nframes": 60}

    with patch("orante.services.tails.video_info", fake_info):
        p1 = review._seg_payload(d, m, m.segments[0])
        assert p1["fps"] == 30.0
        assert m.segments[0].get("fps") == 30.0
        p2 = review._seg_payload(d, m, m.segments[0])
        assert p2["fps"] == 30.0
        assert calls["n"] == 1
        review._flush_fps("T0071a", d, m.segments, archived=False)
    m2 = Manifest.load(d)
    assert m2.segments[0].get("fps") == 30.0


def test_fps_uses_existing_field_without_probe():
    d, m = _clip_task("T0071b")
    m.segments[0]["fps"] = 25
    m.save(d)
    m = Manifest.load(d)
    calls = {"n": 0}

    def fake_info(path):
        calls["n"] += 1
        return {"fps": 99.0, "width": 1, "height": 1, "duration": 1, "nframes": 1}

    with patch("orante.services.tails.video_info", fake_info):
        p = review._seg_payload(d, m, m.segments[0])
    assert p["fps"] == 25
    assert calls["n"] == 0


def test_fps_defaults_24_when_probe_fails():
    d, m = _clip_task("T0071c")

    def boom(path):
        raise RuntimeError("no ffmpeg")

    with patch("orante.services.tails.video_info", boom):
        p = review._seg_payload(d, m, m.segments[0])
    assert p["fps"] == 24
    assert not m.segments[0].get("fps")


def test_fps_missing_clip_is_24():
    d, m = _clip_task("T0071d")
    m.segments[0]["takes"] = []
    (d / "clips" / "T0071d-s001-p01.mp4").unlink(missing_ok=True)
    p = review._seg_payload(d, m, m.segments[0])
    assert p["exists"] is False
    assert p["fps"] == 24


def test_node_ui():
    script = ROOT / "tools" / "test_spec71_ui.js"
    r = subprocess.run(
        ["node", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    assert "spec71 ui ok" in (r.stdout or "")
    assert "hover show/hide" in (r.stdout or "")
    assert "click 60%" in (r.stdout or "")


def test_css_theme_check_still_passes():
    r = subprocess.run(
        ["python", str(ROOT / "tools" / "css_theme_check.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"css_theme_check 失败\n{r.stdout}\n{r.stderr}"
