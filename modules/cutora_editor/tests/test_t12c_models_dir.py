# -*- coding: utf-8 -*-
"""T12c 提交 1：设置里模型目录可编辑，校验可写。"""
from __future__ import annotations

import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_apply_models_dir_writable_and_rejects_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.asr.models import apply_models_dir, models_dir

    target = tmp_path / "my_models"
    got = apply_models_dir(str(target))
    assert os.path.isdir(got)
    assert models_dir() == os.path.abspath(got)

    notdir = tmp_path / "a_file.txt"
    notdir.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        apply_models_dir(str(notdir))


def test_settings_models_dir_is_line_edit_not_label():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    code = """
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget
from cutora.ui.settings_dialog import SettingsDialog
app = QApplication.instance() or QApplication([])
host = QWidget()
host._ui_mode = 'pro'
host.chk_gpu = None
dlg = SettingsDialog(host)
assert hasattr(dlg, 'ed_models_dir')
assert isinstance(dlg.ed_models_dir, QLineEdit)
assert dlg.ed_models_dir.isReadOnly() is False
assert hasattr(dlg, 'btn_models_dir')
print('ok')
"""
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out
