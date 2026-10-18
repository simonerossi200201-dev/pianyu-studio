# -*- coding: utf-8 -*-
"""BUG-16: preview frame_at passes video QImage as glass backdrop."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G11 = os.path.join(REPO, "tests", "golden", "g11_vertical_basic_fx", "project.asproj.json")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["bug16-glass"])


def _load_glass_task():
    from cutora.model.project_io import deserialize_render_task
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    data = json.loads(open(G11, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    apply_pack_to_task(task, pack)
    assert (task.get("style") or {}).get("fx", {}).get("bg", {}).get("type") == "glass"
    return task


def _rgb(img):
    from PySide6.QtGui import QImage
    import numpy as np
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


def test_glass_preview_box_variance_and_export_mad(qapp):
    import numpy as np
    from cutora.preview.engine import PreviewEngine
    from cutora.render.export_renderer import ExportRenderer
    from cutora.model.canvas import canvas_wh
    task = _load_glass_task()
    w, h = canvas_wh(task)
    eng = PreviewEngine()
    try:
        glass = eng.frame_at(task, 2.0)
        assert glass is not None and not glass.isNull()
        # solid fallback: same compositor, no backdrop
        def _bg(p):
            p.fillRect(0, 0, int(w), int(h), glass.pixel(0, 0))
        solid = ExportRenderer.render_frame(
            task, int(w), int(h), 2.0, draw_background=_bg, backdrop=None)
        ga, sa = _rgb(glass), _rgb(solid)
        y0, y1 = 980, 1280
        x0, x1 = 200, 880
        gband = ga[y0:y1, x0:x1]
        sband = sa[y0:y1, x0:x1]
        gvar = float(gband.astype(np.float64).var())
        assert gvar > 0, gvar
        mad = float(np.mean(np.abs(gband.astype(np.int16) - sband.astype(np.int16)))) / 255.0
        assert mad > 0.002, "glass preview should differ from solid-fallback box"
        # preview vs export compositor with same video backdrop
        export = eng.frame_at(task, 2.0)
        ea = _rgb(export)
        mad_ex = float(np.mean(np.abs(
            ga[y0:y1, x0:x1].astype(np.int16) - ea[y0:y1, x0:x1].astype(np.int16)
        ))) / 255.0
        assert mad_ex <= 0.012, mad_ex
    finally:
        eng.close()
