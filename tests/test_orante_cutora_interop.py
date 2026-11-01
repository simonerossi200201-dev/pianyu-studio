# -*- coding: utf-8 -*-
"""Orante ⇄ CUTORA 双向无缝唤醒与数据互通端到端测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

LTX_ROOT = Path(__file__).resolve().parents[1]
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))
ORANTE_ROOT = LTX_ROOT / "modules" / "orante_desk"
if str(ORANTE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORANTE_ROOT))
CUTORA_ROOT = LTX_ROOT / "modules" / "cutora_editor"
if str(CUTORA_ROOT) not in sys.path:
    sys.path.insert(0, str(CUTORA_ROOT))


def test_orante_js_api_and_send_to_cutora_wiring():
    """验证 Orante 前端 api.js 同时暴露 window.API 与 window.api，且 review.js 调用 API.post。"""
    api_js = (ORANTE_ROOT / "ui" / "js" / "api.js").read_text(encoding="utf-8")
    review_js = (ORANTE_ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    store_js = (ORANTE_ROOT / "ui" / "js" / "store.js").read_text(encoding="utf-8")

    assert "window.API =" in api_js
    assert "window.api =" in api_js
    assert "API.post('/api/tasks/send-to-cutora-stitch'" in review_js
    assert "es.addEventListener('ui.focus'" in store_js
    assert "reviewForceAllMarked" in store_js
    assert "reviewForceAllMarked" in review_js


def test_orante_send_to_cutora_and_return_roundtrip(tmp_path, monkeypatch):
    """验证 Orante -> CUTORA 投递唤醒与 CUTORA -> Orante 缺词回传唤醒闭环。"""
    from fastapi.testclient import TestClient
    from orante.server import app
    import core.table_sync as ts

    # 构造模拟任务目录与 clips
    task_dir = tmp_path / "2026-11-01" / "A" / "T0999-TestFrench"
    clips_dir = task_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    (clips_dir / "T0999-s001-p01.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (clips_dir / "T0999-s002-p01.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    manifest = {
        "task_id": "T0999",
        "name": "TestFrench",
        "copy": {"text": "Bonjour tout le monde. Que Dieu vous bénisse aujourd'hui."},
        "segments": [
            {"s": 1, "text": "Bonjour tout le monde.", "chosen": "clips/T0999-s001-p01.mp4", "mark": False},
            {"s": 2, "text": "Que Dieu vous bénisse aujourd'hui.", "chosen": "clips/T0999-s002-p01.mp4", "mark": False},
        ],
    }
    (task_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    handoff_file = tmp_path / "cutora_stitch_handoff.json"
    monkeypatch.setattr(ts, "HANDOFF_FILE", handoff_file)

    fake_rec = ts.load_task_from_manifest(task_dir / "manifest.json")
    assert fake_rec is not None
    monkeypatch.setattr(ts, "discover_orante_batch_tasks", lambda **_kw: [fake_rec])

    # 模拟 CUTORA 已在运行，拦截 call_cutora_api 与窗口激活
    api_calls = []
    monkeypatch.setattr(ts, "is_cutora_running", lambda: True)
    monkeypatch.setattr(
        ts,
        "call_cutora_api",
        lambda cmd, params=None: (api_calls.append((cmd, params)) or {"ok": True}),
    )
    monkeypatch.setattr(ts, "_bring_window_foreground_by_keywords", lambda *a, **k: True)

    client = TestClient(app, base_url="http://127.0.0.1:9400")
    # 先访问首页拿到 orante_session Cookie
    r_home = client.get("/")
    assert r_home.status_code == 200
    assert "orante_session" in client.cookies

    # 1. Orante -> CUTORA: 调用 /api/tasks/send-to-cutora-stitch
    r_send = client.post(
        "/api/tasks/send-to-cutora-stitch",
        json={
            "mode": "current",
            "tid": "T0999",
            "range_text": "T0999",
            "auto_start": True,
            "auto_ai": False,
        },
    )
    assert r_send.status_code == 200
    body = r_send.json()
    assert body["ok"] is True
    assert body["task_count"] == 1
    assert body["launched"] is True
    assert ("ui.focus", {"panel": "stitch"}) in api_calls

    # 验证交接单没有被 launch_cutora_room 覆盖，保留了 range_text="T0999" 与 auto_ai=False
    hdata = json.loads(handoff_file.read_text(encoding="utf-8"))
    assert hdata["consumed"] is False
    assert hdata["source"] == "orante"
    assert hdata["range_text"] == "T0999"
    assert hdata["auto_ai"] is False
    assert hdata["clip_dirs"] == [str(clips_dir)]

    # 2. CUTORA -> Orante: 内部无 Cookie 回传缺词标记 (/api/tasks/sync-stitch-marks) 与唤醒 (/api/ui/focus)
    bare_client = TestClient(app, base_url="http://127.0.0.1:9400")
    r_sync = bare_client.post(
        "/api/tasks/sync-stitch-marks",
        json={"marked_tasks": [{"task_id": "T0999", "marked_segments": [2]}]},
        headers={"X-Pianyu-Internal": "1"},
    )
    assert r_sync.status_code == 200
    assert r_sync.json()["ok"] is True
    assert r_sync.json()["synced"] == 1

    r_focus = bare_client.post(
        "/api/ui/focus",
        json={"node": "review", "filter": "marked", "tid": "T0999"},
        headers={"X-Pianyu-Internal": "1"},
    )
    assert r_focus.status_code == 200
    assert r_focus.json()["ok"] is True
    assert r_focus.json()["node"] == "review"

    # 验证跨站浏览器伪造请求（带外部 Origin）仍被 403 拦截
    r_csrf = bare_client.post(
        "/api/ui/focus",
        json={"node": "review"},
        headers={"Origin": "http://evil.example.com", "X-Pianyu-Internal": "1"},
    )
    assert r_csrf.status_code == 403


def test_cutora_ui_focus_command_registered():
    """验证 CUTORA 命令总线已注册 ui.focus 命令。"""
    from cutora.core.commands import get_registry
    spec = get_registry().get("ui.focus")
    assert spec is not None
    assert spec.name == "ui.focus"


def test_cutora_align_downloaded_fallback_to_native(monkeypatch):
    """验证当专用语种对齐模型未下载时，AnalyzeWorker 自动启用内置声学词级对齐引擎而不报错中断。"""
    import os
    import cutora.asr.align_fetch as af
    from cutora.batch.script_stitcher import AnalyzeWorker

    monkeypatch.delenv("CUTORA_ALLOW_NATIVE_ALIGN", raising=False)
    monkeypatch.setattr(af, "fetch_plan", lambda _code: {"id": "align-fr", "language": "fr"})
    aw = AnalyzeWorker({}, {}, lambda: None, lambda _l=None: (None, {}), language="fr")
    # 未挂载 _align_fetcher 时也不应抛 StitchLanguageError，而是自动开启 CUTORA_ALLOW_NATIVE_ALIGN=1
    aw._ensure_align_downloaded("fr")
    assert os.environ.get("CUTORA_ALLOW_NATIVE_ALIGN") == "1"


if __name__ == "__main__":
    import os
    import shutil
    import tempfile

    class _MonkeyPatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, val):
            old = getattr(obj, name)
            self._undo.append((obj, name, old))
            setattr(obj, name, val)

        def delenv(self, key, raising=True):
            os.environ.pop(key, None)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo.clear()

    print("1/4 Running test_orante_js_api_and_send_to_cutora_wiring ...")
    test_orante_js_api_and_send_to_cutora_wiring()
    print("  [PASS]")

    print("2/4 Running test_orante_send_to_cutora_and_return_roundtrip ...")
    tmp_dir = Path(tempfile.mkdtemp(prefix="pianyu_interop_"))
    mp = _MonkeyPatch()
    try:
        test_orante_send_to_cutora_and_return_roundtrip(tmp_dir, mp)
        print("  [PASS]")
    finally:
        mp.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("3/4 Running test_cutora_ui_focus_command_registered ...")
    test_cutora_ui_focus_command_registered()
    print("  [PASS]")

    print("4/4 Running test_cutora_align_downloaded_fallback_to_native ...")
    mp2 = _MonkeyPatch()
    try:
        test_cutora_align_downloaded_fallback_to_native(mp2)
        print("  [PASS]")
    finally:
        mp2.undo()

    print("\nALL 4 INTEROP & ALIGNMENT TESTS PASSED!")


