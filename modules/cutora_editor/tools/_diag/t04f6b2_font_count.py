# -*- coding: utf-8 -*-
"""Print [FONT] Loaded N using this tree's cutora + resources/fonts."""
from __future__ import annotations

import os
import sys
import tempfile

_tree = os.environ.get("CUTORA_TREE")
if _tree:
    sys.path.insert(0, os.path.abspath(_tree))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_fontcount_")

from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)

from PySide6.QtWidgets import QApplication
from cutora.core.paths import REPO_ROOT, FONT_DIR
from cutora.ui.panels.fonts import _iter_font_files, load_embedded_fonts

app = QApplication.instance() or QApplication(["fontcount"])
files = _iter_font_files()
print("REPO_ROOT", REPO_ROOT, flush=True)
print("FONT_DIR", FONT_DIR, flush=True)
print("iter_files", len(files), flush=True)
print(
    "noto_in_iter",
    any(("Noto_Color" in f) or ("NotoColor" in f) for f in files),
    flush=True,
)
ok = load_embedded_fonts()
print("load_ok", ok, flush=True)
