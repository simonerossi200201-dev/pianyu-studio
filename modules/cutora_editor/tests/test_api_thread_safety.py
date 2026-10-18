# -*- coding: utf-8 -*-
"""Offscreen MainWindow + API: flood app.version and set_playhead from another thread."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
G01_MP4 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def _http(port, method, path, token, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=20)
    headers = {"Authorization": "Bearer " + token}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw.decode("utf-8"))


def run_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_api_thr_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.api.lifecycle import start_api, stop_api
    from cutora.api.token import ensure_token, rotate_token
    from cutora.core import secrets as secrets_mod

    class _Mem:
        priority = 5
        def __init__(self):
            self.d = {}
        def get_password(self, s, n):
            return self.d.get((s, n))
        def set_password(self, s, n, v):
            self.d[(s, n)] = v
        def delete_password(self, s, n):
            self.d.pop((s, n), None)

    secrets_mod.use_backend(_Mem())
    rotate_token()
    token = ensure_token()

    app = QApplication.instance() or QApplication(["cutora-api-thr"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()
    win.show_editor()
    win._ensure_timeline_template()
    if os.path.isfile(G01_MP4):
        win.on_drop([G01_MP4])
        win.curr_idx = 0
    app.processEvents()

    srv = start_api(0)
    port = srv.port
    errors = []

    def worker():
        try:
            for _ in range(200):
                st, data = _http(port, "POST", "/v1/commands/app.version", token, {})
                if st != 200 or not data.get("ok"):
                    errors.append(("version", st, data))
                    return
            for i in range(20):
                st, data = _http(
                    port, "POST", "/v1/commands/timeline.set_playhead",
                    token, {"time": 0.1 * i})
                if st != 200 or not data.get("ok"):
                    errors.append(("playhead", st, data))
                    return
        except Exception:
            errors.append(traceback.format_exc())

    def call_and_pump(method, path, body=None):
        box = {}

        def w():
            box["r"] = _http(port, method, path, token, body)

        t = threading.Thread(target=w)
        t.start()
        while t.is_alive():
            app.processEvents()
            t.join(0.02)
        return box["r"]

    th = threading.Thread(target=worker)
    th.start()
    while th.is_alive():
        app.processEvents()
        th.join(0.02)

    if win.tasks:
        win.tasks[0]["segments"] = [{"start": 0.0, "end": 1.0, "text": "old"}]
        st, data = call_and_pump(
            "POST", "/v1/commands/subtitle.set_text",
            {"seg_id": 0, "text": "from-api", "media_id": 0})
        if st != 200 or win.tasks[0]["segments"][0]["text"] != "from-api":
            errors.append(("set_text", st, data))
        else:
            win.undo()
            app.processEvents()
            if win.tasks[0]["segments"][0]["text"] != "old":
                errors.append(("undo", win.tasks[0]["segments"][0]["text"]))
            call_and_pump("POST", "/v1/redo", {})
            app.processEvents()
            if win.tasks[0]["segments"][0]["text"] != "from-api":
                errors.append(("redo", win.tasks[0]["segments"][0]["text"]))
    stop_api()
    if errors:
        print("THR_FAIL", errors[:3], flush=True)
        return 1
    print("THR_OK", flush=True)
    try:
        win.close()
    except Exception:
        pass
    os._exit(0)


def test_api_thread_safety_offscreen():
    proc = subprocess.run(
        [VENV_PY, os.path.join(REPO, "tests", "test_api_thread_safety.py")],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "THR_OK" in (proc.stdout or ""), out


if __name__ == "__main__":
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        raise SystemExit(run_suite())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
