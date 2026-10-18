# -*- coding: utf-8 -*-
"""Dark export monitor + stop-export flag (T13a-fix 2b)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback

from cutora.render import export_guard as guard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")


def test_heartbeat_writes_child_pids(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    state = tmp_path / "export_state.json"
    hb = guard.HeartbeatWriter(str(state), [str(out)], 3, 2, 1, str(tmp_path))
    hb.pids = [111, 222]
    hb.update(0, status="running")
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["pids"] == [111, 222]
    assert data["child_pids"] == [111, 222]


def test_stop_flag_roundtrip(tmp_path):
    state = tmp_path / "export_state.json"
    state.write_text("{}", encoding="utf-8")
    assert not guard.stop_flag_exists(str(state))
    guard.write_stop_flag(str(state))
    assert guard.stop_flag_exists(str(state))
    assert (tmp_path / guard.STOP_NAME).is_file()
    guard.clear_stop_flag(str(state))
    assert not guard.stop_flag_exists(str(state))


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def run_guard_ui() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_guard_ui_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication, QMessageBox
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

    app = QApplication.instance() or QApplication(["cutora-guard-ui"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    qss, ico = guard._guard_asset_paths()
    assert os.path.isfile(qss), qss
    extra = (
        "QPushButton#btnStopExport { color: #B4544A; border: 1px solid #B4544A; }"
    )
    with open(qss, "r", encoding="utf-8") as fh:
        app.setStyleSheet(fh.read() + extra)

    tmp = tempfile.mkdtemp(prefix="guard_ui_")
    state = os.path.join(tmp, "export_state.json")
    payload = {
        "status": "running", "done": 1, "total": 3,
        "pids": [], "child_pids": [], "parent_pid": os.getpid(),
        "started_at": 0, "updated_at": 0, "out_dirs": [tmp],
    }
    with open(state, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    GuardWindow = guard.guard_window_class()
    win = GuardWindow(state)
    win.show()
    app.processEvents()
    assert win.windowTitle() == "片语 Studio · 导出中"
    assert win.btn_stop.text() == "停止导出"
    assert win.btn_stop.objectName() == "btnStopExport"
    assert "强关" in win.lbl_hint.toolTip()

    QMessageBox.exec = lambda self: QMessageBox.StandardButton.Yes
    win._request_stop()
    app.processEvents()
    assert guard.stop_flag_exists(state)
    assert win.btn_stop.text() == "已请求停止…"
    assert not win.btn_stop.isEnabled()

    os.makedirs(IMG_DIR, exist_ok=True)
    shot = os.path.join(IMG_DIR, "T13a_export_monitor_dark.png")
    win.grab().save(shot)
    assert os.path.isfile(shot)
    print("SHOT", shot, flush=True)
    win.close()

    other_dir = os.path.join(tmp, "close_only")
    os.makedirs(other_dir, exist_ok=True)
    other = os.path.join(other_dir, "export_state.json")
    with open(other, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    win2 = GuardWindow(other)
    win2.show()
    app.processEvents()
    win2.close()
    assert not guard.stop_flag_exists(other)
    print("GUARD_UI_OK", flush=True)
    os._exit(0)


def test_guard_window_dark_and_stop():
    proc = subprocess.run(
        [VENV_PY, os.path.join(REPO, "tests", "test_ui_export_monitor.py")],
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
    assert "GUARD_UI_OK" in (proc.stdout or ""), out
    assert os.path.isfile(os.path.join(IMG_DIR, "T13a_export_monitor_dark.png"))


if __name__ == "__main__":
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        raise SystemExit(run_guard_ui())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
