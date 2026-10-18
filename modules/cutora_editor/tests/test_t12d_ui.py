# -*- coding: utf-8 -*-
"""T12d 提交 3：批量纠正下拉 + 模型页分组（离屏子进程）。"""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _run(code: str, timeout: int = 180) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out
    return out


def test_batch_results_combo_corrected_ids():
    _run(r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication
from cutora.ui.asr_batch_results import AsrBatchResults
app = QApplication.instance() or QApplication(['t12d-batch'])
panel = AsrBatchResults()
panel.set_rows([
    {'media_id': 0, 'path': r'C:\a.mp4', 'language': 'en', 'confidence': 0.9, 'warn': False},
    {'media_id': 1, 'path': r'C:\b.mp4', 'language': 'it', 'confidence': 0.4, 'warn': True},
])
assert panel.corrected_ids() == []
combo = panel.table.cellWidget(1, 1)
idx = combo.findData('en')
assert idx >= 0
combo.setCurrentIndex(idx)
assert panel.language_for(1) == 'en'
assert panel.corrected_ids() == [1]
print('ok')
""")


def test_models_page_grouped_align_collapsed():
    _run(r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication, QWidget
from cutora.core.commands import get_bus
from cutora.ui.panels.models import ModelsPage
app = QApplication.instance() or QApplication(['t12d-models'])
host = QWidget()
host.bus = get_bus()
page = ModelsPage(host)
assert 'asr' in page.sections and 'align' in page.sections and 'vad' in page.sections
assert page.sections['asr'].isExpanded() is True
assert page.sections['vad'].isExpanded() is True
assert page.sections['align'].isExpanded() is False
title = page.sections['align']._title_label.text()
assert '已启用' in title
assert '共' in title
assert page.tables['asr'].rowCount() >= 1
assert page.tables['align'].rowCount() >= 2
assert page.tables['vad'].rowCount() >= 1
print('ok')
""")
