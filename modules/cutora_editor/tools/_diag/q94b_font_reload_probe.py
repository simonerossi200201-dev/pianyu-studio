# -*- coding: utf-8 -*-
"""Q94b step 0: does repeated load_embedded_fonts() exhaust the process?

Read-only. One QApplication, 120 calls. Stops at the first False.
The family list from round 1 is the before-set for the dedupe.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication

app = QApplication([])
from cutora.ui.panels import fonts as F

for i in range(1, 121):
    ok = F.load_embedded_fonts()
    print(i, ok, F.LAST_EMBEDDED_FONT_LOAD_COUNT, len(F.FONT_FILES), F.FONT_DIR, flush=True)
    if i == 1 and ok:
        print("FAMILIES", len(F.FONT_FILES), flush=True)
        for name in sorted(F.FONT_FILES):
            print("FAMILY", name, flush=True)
    if not ok:
        break
