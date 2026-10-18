# -*- coding: utf-8 -*-
"""One windows-platform load. Compare scan families vs register()'s map.

Not the exhaustion probe. Answers whether the second addApplicationFont
adds family names the scan did not already return (q37 named instances).
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

os.environ.pop("QT_QPA_PLATFORM", None)
from PySide6.QtWidgets import QApplication

app = QApplication(["q94b-win"])
print("platform", app.platformName(), flush=True)
from cutora.ui.panels import fonts as F

ok = F.load_embedded_fonts()
mgr = F.get_font_manager()
scan = set()
for fams in F.FONT_FILE_FAMILIES.values():
    scan.update(fams or ())
mapped = set(mgr._family_name_map)
print("ok", ok, flush=True)
print("count", F.LAST_EMBEDDED_FONT_LOAD_COUNT, flush=True)
print("font_files", len(F.FONT_FILES), flush=True)
print("scan_families", len(scan), flush=True)
print("map", len(mapped), flush=True)
print("HAS_THIN", "Roboto Slab Thin" in mapped, flush=True)
print("THIN_IN_SCAN", "Roboto Slab Thin" in scan, flush=True)
only_map = sorted(mapped - scan)
only_scan = sorted(scan - set(F.FONT_FILES))
print("ONLY_IN_MAP", len(only_map), flush=True)
for name in only_map:
    print("MAP_ONLY", name, flush=True)
print("ONLY_IN_SCAN_NOT_FILES", len(only_scan), flush=True)
for name in only_scan:
    print("SCAN_ONLY", name, flush=True)
